"""Task 7 tests: materiality bands and risk flags."""

from __future__ import annotations

import pytest

from src.config import Config
from src.cost import borrower_cost
from src.flags import (
    DEFAULT_VALUES,
    ESTIMATED_INPUTS,
    LOW_PASS_THROUGH,
    MONITOR,
    NEGATIVE_EBITDA,
    NO_DECLARANT,
    ORIGIN_UNKNOWN,
    RATING_GAP,
    SUPPLIER_CONCENTRATION,
    band_gap,
    band_order,
    evaluate_flags,
    flag_codes,
    materiality_band,
)
from src.schema import parse_borrower
from src.tiering import assign_tier

BASE_ROW = {
    "borrower_id": "B-900",
    "name": "Flag Test BV",
    "nace_code": "25.11",
    "country": "NL",
    "exposure_eur": 10_000_000,
    "turnover_eur": 50_000_000,
    "ebitda_eur": 5_000_000,
    "top_supplier_country": "IN",
    "supplier_data": "default",
    "import_t_steel": 0,
    "import_t_aluminium": 0,
    "import_t_cement": 0,
    "import_t_fertiliser": 0,
    "import_t_hydrogen": 0,
    "import_mwh_electricity": 0,
}


def flagged(config: Config, **overrides: object) -> tuple[tuple[str, ...], list]:
    """Cost a borrower for 2030 and return its flag codes and flag objects."""
    row = dict(BASE_ROW)
    row.update(overrides)
    borrower = parse_borrower(row, config)
    tier = assign_tier(borrower, config, branch="adopted")
    cost = borrower_cost(
        borrower, config, year=2030, scenario="net_zero_2050", branch="adopted", tier=tier
    )
    flags = evaluate_flags(borrower, cost, config, tier=tier)
    return flag_codes(flags), flags


# ---------------------------------------------------------------------------
# Materiality bands
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ratio,expected",
    [
        (0.000, "L"),
        (0.009, "L"),
        (0.010, "ML"),
        (0.029, "ML"),
        (0.030, "M"),
        (0.059, "M"),
        (0.060, "MH"),
        (0.119, "MH"),
        (0.120, "H"),
        (5.000, "H"),
    ],
)
def test_materiality_bands_and_their_boundaries(
    fixture_config: Config, ratio: float, expected: str
) -> None:
    """Boundaries are inclusive at the bottom, so 0.03 is M and not ML."""
    result = materiality_band(ratio * 1_000_000, 1_000_000, fixture_config)
    assert result.band == expected
    assert result.ratio == pytest.approx(ratio)


def test_band_order_runs_from_least_to_most_material(fixture_config: Config) -> None:
    assert band_order(fixture_config) == ["L", "ML", "M", "MH", "H"]


def test_negative_ebitda_gets_the_configured_band_and_no_ratio(
    fixture_config: Config,
) -> None:
    result = materiality_band(100_000, -500_000, fixture_config)
    assert result.band == "H"
    assert result.ratio is None
    assert result.ebitda_is_positive is False


def test_zero_ebitda_does_not_divide_by_zero(fixture_config: Config) -> None:
    result = materiality_band(100_000, 0, fixture_config)
    assert result.band == "H"
    assert result.ratio is None


def test_zero_cost_is_the_lowest_band(fixture_config: Config) -> None:
    assert materiality_band(0, 1_000_000, fixture_config).band == "L"


@pytest.mark.parametrize(
    "cbam,bank,expected", [("H", "L", 4), ("M", "ML", 1), ("L", "H", -4), ("M", "M", 0)]
)
def test_band_gap_counts_steps(
    fixture_config: Config, cbam: str, bank: str, expected: int
) -> None:
    assert band_gap(cbam, bank, fixture_config) == expected


# ---------------------------------------------------------------------------
# Individual flags
# ---------------------------------------------------------------------------


def test_no_declarant_fires_for_an_unauthorised_tier_2_above_threshold(
    fixture_config: Config,
) -> None:
    codes, _ = flagged(fixture_config, import_t_steel=5000, declarant_status="no")
    assert NO_DECLARANT in codes


def test_no_declarant_does_not_fire_when_authorised(fixture_config: Config) -> None:
    codes, _ = flagged(fixture_config, import_t_steel=5000, declarant_status="yes")
    assert NO_DECLARANT not in codes


def test_no_declarant_does_not_fire_below_the_threshold(
    fixture_config: Config,
) -> None:
    """Nothing to declare yet, so the authorisation gap is not yet a cliff."""
    codes, _ = flagged(fixture_config, import_t_steel=10, declarant_status="no")
    assert NO_DECLARANT not in codes
    assert MONITOR in codes


def test_unknown_declarant_status_counts_as_not_authorised(
    fixture_config: Config,
) -> None:
    codes, _ = flagged(fixture_config, import_t_steel=5000)
    assert NO_DECLARANT in codes


def test_default_values_fires_unless_supplier_data_is_verified(
    fixture_config: Config,
) -> None:
    for supplier_data in ("default", "unknown"):
        codes, _ = flagged(
            fixture_config, import_t_steel=5000, supplier_data=supplier_data
        )
        assert DEFAULT_VALUES in codes, supplier_data
    codes, _ = flagged(fixture_config, import_t_steel=5000, supplier_data="verified")
    assert DEFAULT_VALUES not in codes


def test_low_pass_through_fires_on_the_reported_category(
    fixture_config: Config,
) -> None:
    codes, _ = flagged(fixture_config, import_t_steel=5000, pass_through="low")
    assert LOW_PASS_THROUGH in codes


def test_low_pass_through_does_not_fire_on_high(fixture_config: Config) -> None:
    codes, _ = flagged(fixture_config, import_t_steel=5000, pass_through="high")
    assert LOW_PASS_THROUGH not in codes


def test_supplier_concentration_fires_above_the_parameter(
    fixture_config: Config,
) -> None:
    codes, _ = flagged(
        fixture_config, import_t_steel=5000, share_top_supplier=0.75
    )
    assert SUPPLIER_CONCENTRATION in codes


def test_supplier_concentration_does_not_fire_at_or_below_the_parameter(
    fixture_config: Config,
) -> None:
    codes, _ = flagged(fixture_config, import_t_steel=5000, share_top_supplier=0.6)
    assert SUPPLIER_CONCENTRATION not in codes


def test_supplier_concentration_does_not_fire_for_an_eu_supplier(
    fixture_config: Config,
) -> None:
    codes, _ = flagged(
        fixture_config,
        import_t_steel=5000,
        top_supplier_country="DE",
        share_top_supplier=0.9,
    )
    assert SUPPLIER_CONCENTRATION not in codes


def test_supplier_concentration_on_an_exempt_origin_says_so(
    fixture_config: Config,
) -> None:
    """Concentration is still a dependency, but it carries no CBAM cost."""
    codes, flags = flagged(
        fixture_config,
        import_t_steel=5000,
        top_supplier_country="NO",
        share_top_supplier=0.9,
    )
    assert SUPPLIER_CONCENTRATION in codes
    reason = next(f.reason for f in flags if f.code == SUPPLIER_CONCENTRATION)
    assert "outside CBAM scope" in reason


def test_estimated_inputs_fires_when_a_field_was_filled(
    fixture_config: Config,
) -> None:
    row = {
        key: value
        for key, value in BASE_ROW.items()
        if not key.startswith("import_")
    }
    row["nace_code"] = "41.20"
    row.pop("top_supplier_country")
    borrower = parse_borrower(row, fixture_config)
    tier = assign_tier(borrower, fixture_config)
    cost = borrower_cost(
        borrower, fixture_config, year=2030, scenario="net_zero_2050", branch="adopted"
    )
    codes = flag_codes(evaluate_flags(borrower, cost, fixture_config, tier=tier))
    assert ESTIMATED_INPUTS in codes


def test_estimated_inputs_does_not_fire_on_a_fully_supplied_row(
    fixture_config: Config,
) -> None:
    """Fully supplied means pass_through too, not only the import columns.

    Leaving pass_through blank makes the tool reach for the sector share, which
    is an estimate and is meant to raise the flag. The next test covers that.
    """
    codes, _ = flagged(fixture_config, import_t_steel=5000, pass_through="high")
    assert ESTIMATED_INPUTS not in codes


def test_estimated_inputs_fires_when_only_pass_through_was_filled(
    fixture_config: Config,
) -> None:
    codes, _ = flagged(fixture_config, import_t_steel=5000)
    assert ESTIMATED_INPUTS in codes


def test_origin_unknown_fires_when_imports_have_no_origin(
    fixture_config: Config,
) -> None:
    row = dict(BASE_ROW)
    row.pop("top_supplier_country")
    row["import_t_steel"] = 5000
    borrower = parse_borrower(row, fixture_config)
    tier = assign_tier(borrower, fixture_config)
    cost = borrower_cost(
        borrower, fixture_config, year=2030, scenario="net_zero_2050", branch="adopted"
    )
    codes = flag_codes(evaluate_flags(borrower, cost, fixture_config, tier=tier))
    assert ORIGIN_UNKNOWN in codes


def test_negative_ebitda_flag_fires(fixture_config: Config) -> None:
    codes, _ = flagged(fixture_config, import_t_steel=5000, ebitda_eur=-100_000)
    assert NEGATIVE_EBITDA in codes


def test_monitor_fires_below_the_threshold(fixture_config: Config) -> None:
    codes, flags = flagged(fixture_config, import_t_steel=10)
    assert MONITOR in codes
    reason = next(f.reason for f in flags if f.code == MONITOR)
    assert "backdated to 1 January" in reason


# ---------------------------------------------------------------------------
# Rating gap
# ---------------------------------------------------------------------------


def test_rating_gap_fires_when_cbam_sits_two_bands_above_the_bank_rating(
    fixture_config: Config,
) -> None:
    """A large cost against a small EBITDA, rated L by the bank."""
    codes, flags = flagged(
        fixture_config,
        import_t_steel=50_000,
        ebitda_eur=1_000_000,
        bank_transition_rating="L",
    )
    assert RATING_GAP in codes
    reason = next(f.reason for f in flags if f.code == RATING_GAP)
    assert "may not reflect this risk" in reason


def test_rating_gap_does_not_fire_when_the_bank_already_rates_it_high(
    fixture_config: Config,
) -> None:
    codes, _ = flagged(
        fixture_config,
        import_t_steel=50_000,
        ebitda_eur=1_000_000,
        bank_transition_rating="H",
    )
    assert RATING_GAP not in codes


def test_rating_gap_does_not_fire_on_a_one_band_difference(
    fixture_config: Config,
) -> None:
    """The parameter is two bands, so one band is not enough."""
    codes, _ = flagged(
        fixture_config,
        import_t_steel=5000,
        ebitda_eur=5_000_000,
        bank_transition_rating="ML",
    )
    materiality = materiality_band(
        borrower_cost(
            parse_borrower(dict(BASE_ROW) | {"import_t_steel": 5000}, fixture_config),
            fixture_config,
            year=2030,
            scenario="net_zero_2050",
            branch="adopted",
        ).cost_eur,
        5_000_000,
        fixture_config,
    )
    if band_gap(materiality.band, "ML", fixture_config) < 2:
        assert RATING_GAP not in codes


def test_rating_gap_is_silent_when_the_bank_supplies_no_rating(
    fixture_config: Config,
) -> None:
    codes, _ = flagged(fixture_config, import_t_steel=50_000, ebitda_eur=1_000_000)
    assert RATING_GAP not in codes


# ---------------------------------------------------------------------------
# Shape of the output
# ---------------------------------------------------------------------------


def test_every_flag_carries_a_reason(fixture_config: Config) -> None:
    _, flags = flagged(
        fixture_config,
        import_t_steel=50_000,
        ebitda_eur=1_000_000,
        declarant_status="no",
        pass_through="low",
        share_top_supplier=0.9,
        bank_transition_rating="L",
    )
    assert flags
    for flag in flags:
        assert flag.reason.strip(), f"{flag.code} has no reason"


def test_flag_codes_are_unique_and_sorted(fixture_config: Config) -> None:
    codes, _ = flagged(
        fixture_config,
        import_t_steel=50_000,
        ebitda_eur=1_000_000,
        declarant_status="no",
        bank_transition_rating="L",
    )
    assert list(codes) == sorted(set(codes))


def test_a_clean_borrower_raises_only_the_expected_flags(
    fixture_config: Config,
) -> None:
    codes, _ = flagged(
        fixture_config,
        import_t_steel=5000,
        declarant_status="yes",
        supplier_data="verified",
        pass_through="high",
        share_top_supplier=0.2,
        bank_transition_rating="H",
    )
    assert codes == ()


def test_every_flag_code_the_engine_raises_exists_in_config(
    real_config: Config,
) -> None:
    """A flag with no config entry would have no description in the UI."""
    from src import flags as flags_module

    configured = set(real_config.thresholds["flags"])
    raised_by_engine = {
        getattr(flags_module, name)
        for name in dir(flags_module)
        if name.isupper() and isinstance(getattr(flags_module, name), str)
    }
    assert raised_by_engine <= configured, raised_by_engine - configured
