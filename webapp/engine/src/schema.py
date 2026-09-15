"""Borrower input validation and sector-default filling.

The input schema is CLAUDE.md section 5. Required fields must be present and
sane. Optional fields, when missing, are filled from the sector defaults in
nace_tiers.yaml and the row is marked estimated, so the UI can say which numbers
the bank supplied and which the tool guessed.

Filling never invents a number out of nothing. If a sector has no default for a
field, the field stays None and the row records that it could not be filled.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.config import Config
from src.tiering import longest_matching_prefix

# Which borrower column carries the quantity for each good group in config.
# Keys match the good group keys in cbam_rules.yaml and emission_defaults.yaml.
IMPORT_COLUMNS: dict[str, str] = {
    "steel": "import_t_steel",
    "aluminium": "import_t_aluminium",
    "cement": "import_t_cement",
    "fertiliser": "import_t_fertiliser",
    "hydrogen": "import_t_hydrogen",
    "electricity": "import_mwh_electricity",
}


class DeclarantStatus(str, Enum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class SupplierData(str, Enum):
    VERIFIED = "verified"
    DEFAULT = "default"
    UNKNOWN = "unknown"


class PassThrough(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class TransitionRating(str, Enum):
    L = "L"
    ML = "ML"
    M = "M"
    MH = "MH"
    H = "H"


class BorrowerValidationError(ValueError):
    """A borrower row does not meet the input schema."""


class Borrower(BaseModel):
    """One validated borrower row.

    Quantities in cbam_import_quantities are in tonnes for every good group
    except electricity, which is in MWh. The good group's own unit is recorded
    in emission_defaults.yaml, and the mass threshold only counts groups that
    cbam_rules.yaml flags as counting.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # Required
    borrower_id: str
    name: str
    nace_code: str
    country: str
    exposure_eur: float = Field(ge=0)
    turnover_eur: float = Field(gt=0)
    ebitda_eur: float  # may be negative for a loss-making borrower

    # Optional, filled from sector defaults when absent
    import_t_steel: float | None = Field(default=None, ge=0)
    import_t_aluminium: float | None = Field(default=None, ge=0)
    import_t_cement: float | None = Field(default=None, ge=0)
    import_t_fertiliser: float | None = Field(default=None, ge=0)
    import_t_hydrogen: float | None = Field(default=None, ge=0)
    import_mwh_electricity: float | None = Field(default=None, ge=0)
    top_supplier_country: str | None = None
    share_top_supplier: float | None = Field(default=None, ge=0, le=1)
    declarant_status: DeclarantStatus = DeclarantStatus.UNKNOWN
    supplier_data: SupplierData = SupplierData.UNKNOWN
    pass_through: PassThrough = PassThrough.UNKNOWN
    bank_transition_rating: TransitionRating | None = None
    working_capital_eur: float | None = None

    # Provenance, set by fill_sector_defaults
    estimated: bool = False
    estimated_fields: tuple[str, ...] = ()
    unfilled_fields: tuple[str, ...] = ()
    pass_through_share_effective: float | None = None

    @field_validator("borrower_id", "name", "nace_code", "country")
    @classmethod
    def not_blank(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("must not be blank")
        return text

    @field_validator("country", "top_supplier_country")
    @classmethod
    def upper_country(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip().upper()
        return text or None

    @model_validator(mode="after")
    def check_supplier_share(self) -> "Borrower":
        if self.share_top_supplier is not None and self.top_supplier_country is None:
            raise ValueError(
                "share_top_supplier was given without top_supplier_country, so "
                "there is no origin to attribute the share to"
            )
        return self

    @property
    def cbam_import_quantities(self) -> dict[str, float]:
        """Good group to quantity, skipping groups with no quantity.

        Includes quantities the tool estimated from sector intensity. Used for
        costing, where an estimated volume is better than no volume.
        """
        out: dict[str, float] = {}
        for group, column in IMPORT_COLUMNS.items():
            value = getattr(self, column, None)
            if value:
                out[group] = float(value)
        return out

    @property
    def supplied_cbam_import_quantities(self) -> dict[str, float]:
        """Only the quantities the bank actually supplied.

        The tier decision uses this, not cbam_import_quantities. A quantity the
        tool derived from a sector average is a guess about volume, and says
        nothing at all about where the goods came from. Letting such a guess
        put a borrower into Tier 2, which is the tier that carries a direct
        customs obligation, would turn every borrower in a sector with any
        import intensity into a declarant. Sector-level evidence belongs in
        Tier 1, 3 and 4, which is where it is used.
        """
        return {
            group: quantity
            for group, quantity in self.cbam_import_quantities.items()
            if IMPORT_COLUMNS[group] not in self.estimated_fields
        }


def _blank(value: Any) -> bool:
    """True for the many ways a csv says nothing."""
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    return str(value).strip() == ""


def clean_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Drop blank cells so pydantic sees absent rather than empty string."""
    return {key: value for key, value in row.items() if not _blank(value)}


def sector_defaults(config: Config, nace_code: str) -> dict[str, Any]:
    """The nace_tiers.yaml entry that covers this NACE code, or an empty dict."""
    sectors = config.nace_tiers.get("sectors") or {}
    match = longest_matching_prefix(list(sectors.keys()), nace_code)
    if match is None:
        return {}
    return sectors.get(match) or {}


def _usable_number(value: Any) -> float | None:
    """Sector defaults may hold the string TODO-owner. That is not a number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def fill_sector_defaults(borrower: Borrower, config: Config) -> Borrower:
    """Fill missing optional fields from the borrower's sector defaults.

    Import quantities are derived from turnover:

        tonnes = intensity (t per EUR million turnover) * turnover_eur / 1e6

    A field that has no sector default is left as None and named in
    unfilled_fields, so a missing number never becomes a silent zero.
    """
    defaults = sector_defaults(config, borrower.nace_code)
    updates: dict[str, Any] = {}
    filled: list[str] = []
    unfilled: list[str] = []

    intensities = defaults.get("import_intensity") or {}
    turnover_millions = borrower.turnover_eur / 1_000_000.0

    for group, column in IMPORT_COLUMNS.items():
        if getattr(borrower, column) is not None:
            continue
        intensity = _usable_number((intensities.get(group) or {}).get("value"))
        if intensity is None:
            unfilled.append(column)
            continue
        updates[column] = round(intensity * turnover_millions, 6)
        filled.append(column)

    share_block = defaults.get("pass_through_share") or {}
    share = _usable_number(share_block.get("value"))
    if share is not None:
        updates["pass_through_share_effective"] = share
        if borrower.pass_through is PassThrough.UNKNOWN:
            filled.append("pass_through_share_effective")
    else:
        unfilled.append("pass_through_share_effective")

    if borrower.working_capital_eur is None:
        unfilled.append("working_capital_eur")
    if borrower.bank_transition_rating is None:
        unfilled.append("bank_transition_rating")

    updates["estimated_fields"] = tuple(filled)
    updates["unfilled_fields"] = tuple(unfilled)
    updates["estimated"] = bool(filled)
    return borrower.model_copy(update=updates)


def parse_borrower(row: Mapping[str, Any], config: Config) -> Borrower:
    """Validate one raw row and fill its sector defaults."""
    try:
        borrower = Borrower(**clean_row(row))
    except Exception as exc:  # pydantic raises ValidationError
        identifier = row.get("borrower_id", "<no borrower_id>")
        raise BorrowerValidationError(f"borrower {identifier}: {exc}") from exc
    return fill_sector_defaults(borrower, config)


def parse_borrowers(
    rows: list[Mapping[str, Any]], config: Config
) -> tuple[list[Borrower], list[str]]:
    """Validate many rows. Returns the good ones and a message per bad one."""
    good: list[Borrower] = []
    problems: list[str] = []
    for row in rows:
        try:
            good.append(parse_borrower(row, config))
        except BorrowerValidationError as exc:
            problems.append(str(exc))
    return good, problems
