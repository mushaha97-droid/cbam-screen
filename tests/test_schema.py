"""Task 5 tests: validation and sector-default filling."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.config import Config
from src.schema import (
    Borrower,
    BorrowerValidationError,
    DeclarantStatus,
    PassThrough,
    SupplierData,
    TransitionRating,
    parse_borrower,
    parse_borrowers,
    sector_defaults,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_ONLY = {
    "borrower_id": "B-001",
    "name": "Test Construction BV",
    "nace_code": "41.20",
    "country": "NL",
    "exposure_eur": 5_000_000,
    "turnover_eur": 20_000_000,
    "ebitda_eur": 2_000_000,
}

FULLY_SUPPLIED = REQUIRED_ONLY | {
    "borrower_id": "B-002",
    "nace_code": "25.11",
    "import_t_steel": 1200,
    "import_t_aluminium": 0,
    "import_t_cement": 0,
    "import_t_fertiliser": 0,
    "import_t_hydrogen": 0,
    "import_mwh_electricity": 0,
    "top_supplier_country": "in",
    "share_top_supplier": 0.75,
    "declarant_status": "no",
    "supplier_data": "default",
    "pass_through": "low",
    "bank_transition_rating": "M",
    "working_capital_eur": 4_000_000,
}


# The headline Task 5 test


def test_row_with_only_required_fields_validates_and_is_marked_estimated(
    fixture_config: Config,
) -> None:
    borrower = parse_borrower(REQUIRED_ONLY, fixture_config)
    assert borrower.borrower_id == "B-001"
    assert borrower.estimated is True
    # 41.20 falls under fixture sector "41", which has a cement intensity of
    # 120 t per EUR million turnover. Turnover is 20 million, so 2400 tonnes.
    assert borrower.import_t_cement == pytest.approx(2400.0)
    assert "import_t_cement" in borrower.estimated_fields


def test_fully_supplied_row_is_not_marked_estimated(fixture_config: Config) -> None:
    borrower = parse_borrower(FULLY_SUPPLIED, fixture_config)
    assert borrower.estimated is False
    assert borrower.estimated_fields == ()
    assert borrower.import_t_steel == 1200


# Validation


@pytest.mark.parametrize(
    "field", ["borrower_id", "name", "nace_code", "country", "turnover_eur"]
)
def test_missing_required_field_is_rejected(fixture_config: Config, field: str) -> None:
    row = dict(REQUIRED_ONLY)
    row.pop(field)
    with pytest.raises(BorrowerValidationError):
        parse_borrower(row, fixture_config)


def test_blank_required_field_is_rejected(fixture_config: Config) -> None:
    with pytest.raises(BorrowerValidationError):
        parse_borrower(REQUIRED_ONLY | {"name": "   "}, fixture_config)


def test_negative_exposure_is_rejected(fixture_config: Config) -> None:
    with pytest.raises(BorrowerValidationError):
        parse_borrower(REQUIRED_ONLY | {"exposure_eur": -1}, fixture_config)


def test_zero_turnover_is_rejected(fixture_config: Config) -> None:
    """Turnover divides the sector-intensity fill, so it cannot be zero."""
    with pytest.raises(BorrowerValidationError):
        parse_borrower(REQUIRED_ONLY | {"turnover_eur": 0}, fixture_config)


def test_negative_ebitda_is_allowed(fixture_config: Config) -> None:
    """A loss-making borrower is a real case, and a very relevant one here."""
    borrower = parse_borrower(REQUIRED_ONLY | {"ebitda_eur": -500_000}, fixture_config)
    assert borrower.ebitda_eur == -500_000


def test_supplier_share_above_one_is_rejected(fixture_config: Config) -> None:
    row = REQUIRED_ONLY | {"top_supplier_country": "IN", "share_top_supplier": 1.4}
    with pytest.raises(BorrowerValidationError):
        parse_borrower(row, fixture_config)


def test_supplier_share_without_an_origin_is_rejected(fixture_config: Config) -> None:
    with pytest.raises(BorrowerValidationError):
        parse_borrower(REQUIRED_ONLY | {"share_top_supplier": 0.8}, fixture_config)


def test_unknown_column_is_rejected(fixture_config: Config) -> None:
    """A typo in a header should not be silently ignored."""
    with pytest.raises(BorrowerValidationError):
        parse_borrower(REQUIRED_ONLY | {"import_t_steal": 100}, fixture_config)


def test_bad_enum_value_is_rejected(fixture_config: Config) -> None:
    with pytest.raises(BorrowerValidationError):
        parse_borrower(REQUIRED_ONLY | {"declarant_status": "maybe"}, fixture_config)


# Normalisation and defaults


def test_country_codes_are_upper_cased(fixture_config: Config) -> None:
    borrower = parse_borrower(FULLY_SUPPLIED, fixture_config)
    assert borrower.top_supplier_country == "IN"
    assert borrower.country == "NL"


def test_categorical_defaults_are_unknown_not_missing(fixture_config: Config) -> None:
    borrower = parse_borrower(REQUIRED_ONLY, fixture_config)
    assert borrower.declarant_status is DeclarantStatus.UNKNOWN
    assert borrower.supplier_data is SupplierData.UNKNOWN
    assert borrower.pass_through is PassThrough.UNKNOWN


def test_blank_cells_are_treated_as_absent(fixture_config: Config) -> None:
    """A csv writes an empty optional column as an empty string."""
    row = REQUIRED_ONLY | {"import_t_steel": "", "working_capital_eur": "  "}
    borrower = parse_borrower(row, fixture_config)
    assert borrower.working_capital_eur is None


def test_fields_with_no_sector_default_are_listed_as_unfilled(
    fixture_config: Config,
) -> None:
    borrower = parse_borrower(REQUIRED_ONLY, fixture_config)
    assert "working_capital_eur" in borrower.unfilled_fields
    assert "bank_transition_rating" in borrower.unfilled_fields
    # Fixture sector 41 has no steel intensity, so steel stays None.
    assert borrower.import_t_steel is None
    assert "import_t_steel" in borrower.unfilled_fields


def test_todo_owner_placeholder_is_not_treated_as_a_number(
    fixture_config: Config,
) -> None:
    """The owner draft uses the string TODO-owner. It must never fill a field."""
    config = fixture_config
    sectors = config.nace_tiers["sectors"]
    saved = sectors["41"]["import_intensity"]["cement"]["value"]
    sectors["41"]["import_intensity"]["cement"]["value"] = "TODO-owner"
    try:
        borrower = parse_borrower(REQUIRED_ONLY, config)
        assert borrower.import_t_cement is None
        assert "import_t_cement" in borrower.unfilled_fields
    finally:
        sectors["41"]["import_intensity"]["cement"]["value"] = saved


def test_unknown_nace_gets_no_sector_defaults(fixture_config: Config) -> None:
    borrower = parse_borrower(
        REQUIRED_ONLY | {"nace_code": "99.99"}, fixture_config
    )
    assert borrower.estimated is False
    assert borrower.cbam_import_quantities == {}


def test_sector_defaults_prefers_the_more_specific_prefix(
    fixture_config: Config,
) -> None:
    assert sector_defaults(fixture_config, "10.11")["tier"] == 4
    assert sector_defaults(fixture_config, "24.10")["tier"] == 1


# Derived views


def test_cbam_import_quantities_skips_zero_and_none(fixture_config: Config) -> None:
    borrower = parse_borrower(FULLY_SUPPLIED, fixture_config)
    assert borrower.cbam_import_quantities == {"steel": 1200.0}


def test_estimated_quantities_are_kept_out_of_the_supplied_view(
    fixture_config: Config,
) -> None:
    """An estimated volume must not look like evidence of an actual import.

    It still counts for costing, which is why the two views differ.
    """
    borrower = parse_borrower(REQUIRED_ONLY, fixture_config)
    assert borrower.cbam_import_quantities == {"cement": pytest.approx(2400.0)}
    assert borrower.supplied_cbam_import_quantities == {}


def test_estimated_import_does_not_make_a_borrower_tier_2(
    fixture_config: Config,
) -> None:
    from src.tiering import assign_tier

    borrower = parse_borrower(REQUIRED_ONLY, fixture_config)
    assert borrower.import_t_cement == pytest.approx(2400.0)
    assert assign_tier(borrower, fixture_config).tier == 4


def test_borrower_satisfies_the_tier_input_protocol(fixture_config: Config) -> None:
    from src.tiering import assign_tier

    borrower = parse_borrower(FULLY_SUPPLIED, fixture_config)
    assert assign_tier(borrower, fixture_config).tier == 2


# Batch parsing and the shipped template


def test_parse_borrowers_separates_good_rows_from_bad(fixture_config: Config) -> None:
    good, problems = parse_borrowers(
        [REQUIRED_ONLY, {"borrower_id": "B-bad"}], fixture_config
    )
    assert len(good) == 1
    assert len(problems) == 1
    assert "B-bad" in problems[0]


def test_shipped_template_csv_validates(fixture_config: Config) -> None:
    """data/template_borrowers.csv must parse with the shipped schema."""
    path = REPO_ROOT / "data" / "template_borrowers.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows, "the template should carry one example row"
    good, problems = parse_borrowers(rows, fixture_config)
    assert problems == []
    assert len(good) == len(rows)


def test_template_headers_match_the_model(fixture_config: Config) -> None:
    path = REPO_ROOT / "data" / "template_borrowers.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        headers = set(next(csv.reader(handle)))
    model_fields = set(Borrower.model_fields)
    unexpected = headers - model_fields
    assert not unexpected, f"template has columns the model rejects: {unexpected}"


def test_transition_rating_accepts_the_five_bands(fixture_config: Config) -> None:
    for rating in TransitionRating:
        borrower = parse_borrower(
            REQUIRED_ONLY | {"bank_transition_rating": rating.value}, fixture_config
        )
        assert borrower.bank_transition_rating is rating
