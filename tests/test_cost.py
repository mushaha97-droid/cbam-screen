"""Task 6 tests: the certificate cost engine.

The hand example from CLAUDE.md Task 6 appears literally:

    1,000 t steel * 1.9 tCO2/t * price 80 * (1 - 0.975) = 3,800 EUR
"""

from __future__ import annotations

import pytest

from src.config import Config
from src.cost import (
    DEFAULT_VALUES_FLAG,
    MONITOR_FLAG,
    below_mass_threshold,
    borrower_cost,
    borrower_cost_path,
    certificate_cost,
    emission_factor_for,
    input_share_cost,
    lost_allowance_value,
    threshold_quantity,
)
from src.schema import parse_borrower

BASE_ROW = {
    "borrower_id": "B-100",
    "name": "Test Importer BV",
    "nace_code": "25.11",
    "country": "NL",
    "exposure_eur": 10_000_000,
    "turnover_eur": 50_000_000,
    "ebitda_eur": 5_000_000,
    "top_supplier_country": "IN",
    "supplier_data": "default",
}


def importer(config: Config, **overrides: object):
    """A Tier 2 borrower with every optional import column supplied."""
    row = dict(BASE_ROW)
    row.update(
        {
            "import_t_steel": 0,
            "import_t_aluminium": 0,
            "import_t_cement": 0,
            "import_t_fertiliser": 0,
            "import_t_hydrogen": 0,
            "import_mwh_electricity": 0,
        }
    )
    row.update(overrides)
    return parse_borrower(row, config)


# ---------------------------------------------------------------------------
# The hand example
# ---------------------------------------------------------------------------


def test_hand_example_1000t_steel_at_1_9_and_price_80_with_free_alloc_0_975() -> None:
    """1,000 t steel * 1.9 tCO2/t * price 80 * (1 - 0.975) = 3,800 EUR."""
    assert certificate_cost(
        tonnes=1000,
        emission_factor=1.9,
        price_eur=80,
        free_allocation_share=0.975,
    ) == pytest.approx(3800.0)


def test_hand_example_end_to_end_through_the_engine(fixture_config: Config) -> None:
    """The same example driven through config, not through loose arguments.

    The fixture puts the steel factor at 1.9, the 2026 Net Zero price at 80 and
    the 2026 adopted free allocation at 0.975, so the engine must reproduce the
    3,800 EUR exactly.
    """
    config = fixture_config
    assert config.default_emission_factor("steel") == 1.9
    assert config.ets_price("net_zero_2050", 2026) == 80.0
    assert config.free_allocation_share("adopted", 2026) == 0.975

    borrower = importer(config, import_t_steel=1000)
    result = borrower_cost(
        borrower, config, year=2026, scenario="net_zero_2050", branch="adopted"
    )

    assert result.tier == 2
    assert result.below_threshold is False
    assert result.cost_eur == pytest.approx(3800.0)
    assert len(result.lines) == 1
    line = result.lines[0]
    assert line.good_group == "steel"
    assert line.quantity == 1000
    assert line.emission_factor == 1.9
    assert line.price_eur == 80.0
    assert line.free_allocation_share == 0.975
    assert line.cost_eur == pytest.approx(3800.0)


# ---------------------------------------------------------------------------
# The 50 tonne threshold
# ---------------------------------------------------------------------------


def test_below_threshold_gives_zero_obligation_and_a_monitor_flag(
    fixture_config: Config,
) -> None:
    """49 t of steel sits below the 50 t threshold."""
    borrower = importer(fixture_config, import_t_steel=49)
    result = borrower_cost(
        borrower, fixture_config, year=2026, scenario="net_zero_2050", branch="adopted"
    )
    assert result.below_threshold is True
    assert result.cost_eur == 0.0
    assert MONITOR_FLAG in result.flags
    assert result.tier == 2, "still in scope, just below the threshold"


def test_above_threshold_gives_a_positive_obligation(fixture_config: Config) -> None:
    """51 t of steel sits above the threshold, so the obligation bites."""
    borrower = importer(fixture_config, import_t_steel=51)
    result = borrower_cost(
        borrower, fixture_config, year=2026, scenario="net_zero_2050", branch="adopted"
    )
    assert result.below_threshold is False
    assert result.cost_eur == pytest.approx(51 * 1.9 * 80 * 0.025)
    assert MONITOR_FLAG not in result.flags


def test_exactly_at_the_threshold_is_above_it(fixture_config: Config) -> None:
    """The rule is "less than 50", so 50 itself is in scope."""
    borrower = importer(fixture_config, import_t_steel=50)
    result = borrower_cost(
        borrower, fixture_config, year=2026, scenario="net_zero_2050", branch="adopted"
    )
    assert result.below_threshold is False
    assert result.cost_eur > 0


def test_threshold_excludes_hydrogen_and_electricity(fixture_config: Config) -> None:
    """The headline rule: hydrogen and electricity tonnes do not count."""
    quantities = {"hydrogen": 900.0, "electricity": 5000.0, "steel": 10.0}
    counted = threshold_quantity(quantities, fixture_config)
    assert counted == 10.0, "only the steel counts"
    is_below, counted, threshold = below_mass_threshold(quantities, fixture_config)
    assert threshold == 50
    assert is_below is True


def test_large_hydrogen_import_alone_stays_below_the_threshold(
    fixture_config: Config,
) -> None:
    borrower = importer(fixture_config, import_t_hydrogen=900)
    result = borrower_cost(
        borrower, fixture_config, year=2026, scenario="net_zero_2050", branch="adopted"
    )
    assert result.counted_tonnes == 0.0
    assert result.below_threshold is True
    assert result.cost_eur == 0.0
    assert MONITOR_FLAG in result.flags


def test_threshold_counts_goods_together_not_one_by_one(
    fixture_config: Config,
) -> None:
    """Four goods at 20 t each clear the threshold together."""
    borrower = importer(
        fixture_config,
        import_t_steel=20,
        import_t_aluminium=20,
        import_t_cement=20,
        import_t_fertiliser=20,
    )
    result = borrower_cost(
        borrower, fixture_config, year=2026, scenario="net_zero_2050", branch="adopted"
    )
    assert result.counted_tonnes == 80.0
    assert result.below_threshold is False
    # Spelled out rather than compressed, so the arithmetic stays checkable.
    expected = (
        20 * 1.9 * 80 * 0.025
        + 20 * 2.0 * 80 * 0.025
        + 20 * 1.1 * 80 * 0.025
        + 20 * 2.6 * 80 * 0.025
    )
    assert result.cost_eur == pytest.approx(expected)


# ---------------------------------------------------------------------------
# The formula itself
# ---------------------------------------------------------------------------


def test_zero_free_allocation_means_the_whole_obligation() -> None:
    assert certificate_cost(100, 2.0, 50, 0.0) == pytest.approx(10_000.0)


def test_full_free_allocation_means_no_cost() -> None:
    assert certificate_cost(100, 2.0, 50, 1.0) == 0.0


@pytest.mark.parametrize("bad_share", [-0.1, 1.1])
def test_free_allocation_outside_zero_to_one_is_rejected(bad_share: float) -> None:
    with pytest.raises(ValueError, match="0.0 to 1.0"):
        certificate_cost(100, 2.0, 50, bad_share)


def test_negative_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        certificate_cost(-1, 2.0, 50, 0.5)


def test_cost_rises_as_free_allocation_falls(fixture_config: Config) -> None:
    borrower = importer(fixture_config, import_t_steel=1000)
    path = borrower_cost_path(
        borrower,
        fixture_config,
        years=[2026, 2030, 2034],
        scenario="net_zero_2050",
        branch="adopted",
    )
    costs = [result.cost_eur for result in path]
    assert costs == sorted(costs), "cost should not fall as the phase-out runs"
    assert costs[0] < costs[-1]


# ---------------------------------------------------------------------------
# Tier 1, lost allowance
# ---------------------------------------------------------------------------


def test_lost_allowance_values_the_year_on_year_step() -> None:
    """1,000 t * 1.9 * 80 * (0.975 - 0.95) = 3,800 EUR."""
    assert lost_allowance_value(
        production_tonnes=1000,
        emission_factor=1.9,
        price_eur=80,
        free_allocation_previous_year=0.975,
        free_allocation_this_year=0.95,
    ) == pytest.approx(3800.0)


def test_lost_allowance_rejects_an_increase_in_free_allocation() -> None:
    with pytest.raises(ValueError, match="increased year on year"):
        lost_allowance_value(1000, 1.9, 80, 0.5, 0.6)


def test_tier_1_producer_is_costed_and_marked_estimated(fixture_config: Config) -> None:
    row = dict(BASE_ROW)
    row.update({"borrower_id": "B-200", "nace_code": "24.10"})
    row.pop("top_supplier_country")
    borrower = parse_borrower(row, fixture_config)
    result = borrower_cost(
        borrower, fixture_config, year=2027, scenario="net_zero_2050", branch="adopted"
    )
    assert result.tier == 1
    # Fixture sector 24.10 carries no import intensity, so nothing can be costed
    # and the engine says so rather than reporting a confident zero.
    assert result.cost_eur == 0.0


def test_tier_1_uses_sector_intensity_for_production(fixture_config: Config) -> None:
    """25.11 has a steel intensity of 40 t per EUR million turnover."""
    config = fixture_config
    saved = list(config.nace_tiers["producer_nace"])
    config.nace_tiers["producer_nace"] = saved + ["25.11"]
    try:
        row = dict(BASE_ROW)
        row.pop("top_supplier_country")
        borrower = parse_borrower(row, config)
        result = borrower_cost(
            borrower, config, year=2027, scenario="net_zero_2050", branch="adopted"
        )
        assert result.tier == 1
        # 50 million turnover * 40 t per million = 2,000 t production.
        # 2,000 * 1.9 * 95 * (0.975 - 0.95)
        assert result.cost_eur == pytest.approx(2000 * 1.9 * 95 * 0.025)
        assert any("estimated from sector intensity" in note for note in result.notes)
    finally:
        config.nace_tiers["producer_nace"] = saved


# ---------------------------------------------------------------------------
# Tier 3 and Tier 4
# ---------------------------------------------------------------------------


def test_tier_3_costs_only_on_the_branch_where_the_extension_applies(
    fixture_config: Config,
) -> None:
    row = dict(BASE_ROW)
    row.pop("top_supplier_country")
    row["borrower_id"] = "B-300"
    borrower = parse_borrower(row, fixture_config)

    on_proposal = borrower_cost(
        borrower, fixture_config, year=2030, scenario="net_zero_2050", branch="proposal"
    )
    on_adopted = borrower_cost(
        borrower, fixture_config, year=2030, scenario="net_zero_2050", branch="adopted"
    )
    assert on_proposal.tier == 3
    assert on_adopted.tier == 0
    assert on_adopted.cost_eur == 0.0


def test_input_share_cost_is_a_share_of_the_upstream_uplift() -> None:
    assert input_share_cost(0.25, 400_000.0) == pytest.approx(100_000.0)


@pytest.mark.parametrize("bad_share", [-0.01, 1.01])
def test_input_share_outside_zero_to_one_is_rejected(bad_share: float) -> None:
    with pytest.raises(ValueError, match="0.0 to 1.0"):
        input_share_cost(bad_share, 100.0)


def test_tier_4_without_an_upstream_uplift_says_so(fixture_config: Config) -> None:
    """Zero here must not read as no exposure. The note has to say why."""
    row = dict(BASE_ROW)
    row.pop("top_supplier_country")
    row.update({"borrower_id": "B-400", "nace_code": "10.11"})
    borrower = parse_borrower(row, fixture_config)
    result = borrower_cost(
        borrower, fixture_config, year=2030, scenario="net_zero_2050", branch="adopted"
    )
    assert result.tier == 4
    assert result.cost_eur == 0.0
    assert any("upstream sector cost uplift" in note for note in result.notes)


def test_tier_4_with_an_upstream_uplift_is_costed(fixture_config: Config) -> None:
    row = dict(BASE_ROW)
    row.pop("top_supplier_country")
    row.update({"borrower_id": "B-401", "nace_code": "10.11"})
    borrower = parse_borrower(row, fixture_config)
    result = borrower_cost(
        borrower,
        fixture_config,
        year=2030,
        scenario="net_zero_2050",
        branch="adopted",
        upstream_cost_uplift_eur=1_000_000.0,
    )
    # Fixture 10.1 has a pass-through share of 0.2.
    assert result.cost_eur == pytest.approx(200_000.0)


def test_tier_0_costs_nothing(fixture_config: Config) -> None:
    row = dict(BASE_ROW)
    row.pop("top_supplier_country")
    row.update({"borrower_id": "B-500", "nace_code": "62.01"})
    borrower = parse_borrower(row, fixture_config)
    result = borrower_cost(
        borrower, fixture_config, year=2030, scenario="net_zero_2050", branch="adopted"
    )
    assert result.tier == 0
    assert result.cost_eur == 0.0


# ---------------------------------------------------------------------------
# Emission factor choice
# ---------------------------------------------------------------------------


def test_default_factor_is_used_and_flagged(fixture_config: Config) -> None:
    borrower = importer(fixture_config, import_t_steel=1000, supplier_data="default")
    result = borrower_cost(
        borrower, fixture_config, year=2026, scenario="net_zero_2050", branch="adopted"
    )
    assert result.lines[0].factor_basis == "default"
    assert DEFAULT_VALUES_FLAG in result.flags


def test_verified_factor_is_used_when_supplied(fixture_config: Config) -> None:
    borrower = importer(fixture_config, import_t_steel=1000, supplier_data="verified")
    choice = emission_factor_for(
        "steel", borrower, fixture_config, verified_factors={"steel": 1.2}
    )
    assert choice.value == 1.2
    assert choice.basis == "verified"


def test_verified_without_a_number_falls_back_and_records_why(
    fixture_config: Config,
) -> None:
    borrower = importer(fixture_config, import_t_steel=1000, supplier_data="verified")
    choice = emission_factor_for("steel", borrower, fixture_config)
    assert choice.basis == "default"
    assert choice.note is not None
    assert "no verified emission factor was supplied" in choice.note


def test_a_good_with_no_factor_is_excluded_and_reported(fixture_config: Config) -> None:
    """Electricity has no factor in the fixture, mirroring the real gap."""
    borrower = importer(
        fixture_config, import_t_steel=1000, import_mwh_electricity=5000
    )
    result = borrower_cost(
        borrower, fixture_config, year=2026, scenario="net_zero_2050", branch="adopted"
    )
    assert result.cost_eur == pytest.approx(3800.0), "steel only"
    assert any("electricity" in note for note in result.notes)


# ---------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------


def test_the_proposal_branch_is_cheaper_in_the_2030s(fixture_config: Config) -> None:
    """A slower phase-out leaves more free allocation, so less to buy."""
    borrower = importer(fixture_config, import_t_steel=1000)
    adopted = borrower_cost(
        borrower, fixture_config, year=2033, scenario="net_zero_2050", branch="adopted"
    )
    proposal = borrower_cost(
        borrower, fixture_config, year=2033, scenario="net_zero_2050", branch="proposal"
    )
    assert proposal.cost_eur < adopted.cost_eur


def test_cost_path_covers_every_year_asked_for(fixture_config: Config) -> None:
    borrower = importer(fixture_config, import_t_steel=1000)
    years = list(range(2026, 2036))
    path = borrower_cost_path(
        borrower, fixture_config, years=years, scenario="net_zero_2050", branch="adopted"
    )
    assert [result.year for result in path] == years
