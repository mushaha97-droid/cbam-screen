"""CBAM tier decision tree.

Tiers, from CLAUDE.md section 6:

  Tier 1  NACE in the producer list. The borrower makes a CBAM good inside the
          EU and loses free allocation as the phase-out bites.
  Tier 2  The borrower imports a CBAM good from a non-exempt, non-EU origin, so
          it carries a direct CBAM obligation.
  Tier 3  NACE in the downstream-extension list and no direct import. Applies
          only under the extension branch, which is a proposal.
  Tier 4  NACE with a material CBAM-good input share and no direct import. The
          cost reaches the borrower through its suppliers, not through customs.
  Tier 0  None of the above.

Precedence is 2, then 1, then 3, then 4. A borrower that both produces and
imports is Tier 2, because the direct obligation is the nearer and harder cost.

Nothing here hard-codes a NACE code, a country or a threshold. All of it comes
from config through the Config object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from src.config import Config, ConfigError

# The default branch name. Which branches exist, and what each one turns on,
# is config, not code. See the branches block of cbam_rules.yaml.
ADOPTED_BRANCH = "adopted"


@runtime_checkable
class TierInput(Protocol):
    """The fields the tier decision needs.

    The pydantic Borrower model from schema.py satisfies this, and so does the
    small dataclass the tests use. Keeping it a Protocol means tiering.py does
    not import schema.py, so Task 4 does not depend on Task 5.
    """

    nace_code: str
    country: str
    top_supplier_country: str | None
    supplied_cbam_import_quantities: Mapping[str, float]


@dataclass(frozen=True)
class SimpleTierInput:
    """A minimal TierInput, used by tests and by callers that have loose data."""

    nace_code: str
    country: str = "NL"
    top_supplier_country: str | None = None
    cbam_import_quantities: Mapping[str, float] = ()  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not self.cbam_import_quantities:
            object.__setattr__(self, "cbam_import_quantities", {})

    @property
    def supplied_cbam_import_quantities(self) -> Mapping[str, float]:
        """Everything handed to this dataclass counts as supplied."""
        return self.cbam_import_quantities


@dataclass(frozen=True)
class TierResult:
    """The assigned tier and why."""

    tier: int
    rule: str
    reason: str
    branch: str
    origin_unknown: bool = False


def normalise_nace(code: str) -> str:
    """Reduce a NACE code to digits so prefixes compare cleanly.

    "23.51" and "2351" both become "2351", so the config prefix "23.5" ("235")
    matches either spelling.
    """
    return "".join(character for character in str(code) if character.isdigit())


def nace_matches(prefix: str, code: str) -> bool:
    """True when code sits under prefix in the NACE hierarchy."""
    normalised_prefix = normalise_nace(prefix)
    if not normalised_prefix:
        return False
    return normalise_nace(code).startswith(normalised_prefix)


def longest_matching_prefix(prefixes: list[str], code: str) -> str | None:
    """The most specific configured prefix that covers this NACE code."""
    matches = [p for p in prefixes if nace_matches(p, code)]
    if not matches:
        return None
    return max(matches, key=lambda p: len(normalise_nace(p)))


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def exempt_country_codes(config: Config) -> set[str]:
    """ISO2 codes whose origin is outside CBAM scope, from cbam_rules.yaml."""
    block = config.exempt_origins()
    codes: set[str] = set()
    for country in block.get("countries") or []:
        code = country.get("code") if isinstance(country, Mapping) else None
        if code:
            codes.add(str(code).upper())
    return codes


def eu_country_codes(config: Config) -> set[str]:
    """ISO2 codes inside the EU customs territory, from cbam_rules.yaml."""
    block = config.cbam_rules.get("eu_customs_territory")
    if not block:
        raise ConfigError(
            "cbam_rules.yaml: missing eu_customs_territory. The tier rule needs "
            "to know which origins are inside the EU."
        )
    return {str(code).upper() for code in block.get("codes") or []}


def imports_in_scope(
    borrower: TierInput, config: Config
) -> tuple[bool, bool, list[str]]:
    """Does this borrower import a CBAM good from a non-exempt, non-EU origin?

    Returns (in_scope, origin_unknown, good_groups_imported).

    Only quantities the bank supplied count here. See the note on
    supplied_cbam_import_quantities in schema.py for why an estimated volume
    must not create a direct obligation.

    An origin we do not know is treated as in scope. A screening tool that
    quietly drops unknown origins would understate the book, so the unknown is
    surfaced through origin_unknown rather than hidden by assuming EU supply.
    """
    imported = [
        group
        for group, quantity in (borrower.supplied_cbam_import_quantities or {}).items()
        if quantity and float(quantity) > 0
    ]
    if not imported:
        return False, False, []

    origin = (borrower.top_supplier_country or "").strip().upper()
    if not origin or origin == "UNKNOWN":
        return True, True, sorted(imported)

    if origin in eu_country_codes(config):
        return False, False, sorted(imported)
    if origin in exempt_country_codes(config):
        return False, False, sorted(imported)
    return True, False, sorted(imported)


def producer_prefixes(config: Config) -> list[str]:
    return _string_list(config.nace_tiers.get("producer_nace"))


def downstream_prefixes(config: Config) -> list[str]:
    return _string_list(config.nace_tiers.get("downstream_nace"))


def input_share_prefixes(config: Config) -> list[str]:
    """NACE prefixes with a material CBAM-good input share, for Tier 4."""
    sectors = config.nace_tiers.get("sectors") or {}
    return [key for key, entry in sectors.items() if (entry or {}).get("tier") == 4]


def assign_tier(
    borrower: TierInput, config: Config, branch: str = ADOPTED_BRANCH
) -> TierResult:
    """Assign a CBAM tier. Precedence is 2, then 1, then 3, then 4."""
    in_scope, origin_unknown, groups = imports_in_scope(borrower, config)

    if in_scope:
        origin = (borrower.top_supplier_country or "unknown") or "unknown"
        detail = (
            f"imports {', '.join(groups)} from {origin}, which is outside the EU "
            f"and not on the exempt list"
        )
        if origin_unknown:
            detail = (
                f"imports {', '.join(groups)} from an origin that was not "
                f"supplied, treated as in scope until the origin is confirmed"
            )
        return TierResult(
            tier=2,
            rule="direct_import",
            reason=detail,
            branch=branch,
            origin_unknown=origin_unknown,
        )

    producer_match = longest_matching_prefix(producer_prefixes(config), borrower.nace_code)
    if producer_match is not None:
        return TierResult(
            tier=1,
            rule="eu_producer",
            reason=(
                f"NACE {borrower.nace_code} sits under the producer prefix "
                f"{producer_match}, so free allocation is withdrawn as the "
                f"phase-out runs"
            ),
            branch=branch,
        )

    downstream_match = longest_matching_prefix(
        downstream_prefixes(config), borrower.nace_code
    )
    if downstream_match is not None:
        if config.downstream_extension_applies(branch):
            return TierResult(
                tier=3,
                rule="downstream_extension",
                reason=(
                    f"NACE {borrower.nace_code} sits under the downstream "
                    f"prefix {downstream_match}. This is a Commission proposal, "
                    f"not adopted law, and applies only on the extension branch"
                ),
                branch=branch,
            )
        # On the adopted branch a downstream borrower is not in scope through
        # Tier 3, but it may still reach Tier 4 through its input share.

    input_match = longest_matching_prefix(input_share_prefixes(config), borrower.nace_code)
    if input_match is not None:
        return TierResult(
            tier=4,
            rule="input_share",
            reason=(
                f"NACE {borrower.nace_code} sits under {input_match}, which has "
                f"a material CBAM-good input share, and the borrower reports no "
                f"direct import"
            ),
            branch=branch,
        )

    return TierResult(
        tier=0,
        rule="out_of_scope",
        reason="no producer NACE, no in-scope import and no material input share",
        branch=branch,
    )
