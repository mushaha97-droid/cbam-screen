"""Task 3 tests: carbon price scenarios.

Task 3 is GATED. The NGFS Phase V EU carbon prices are not published in any
document that could be fetched, so config/scenarios.yaml records the gap instead
of a number. Two things follow:

  - the ordering rule, Net Zero above Delayed above Current Policies from 2030,
    is tested against tests/fixtures/scenarios.yaml, so the rule itself is
    genuinely covered by a test;
  - the same rule against config/scenarios.yaml is marked strict xfail. It fails
    today because there are no prices. The moment someone fills the file in, the
    test passes, strict xfail turns that into a loud failure, and whoever filled
    the file has to come here and remove the marker. That is the gate.
"""

from __future__ import annotations

import pytest

from src.config import Config, ConfigError

ORDERING_FROM_YEAR = 2030
ORDERED_SCENARIOS = ["net_zero_2050", "delayed_transition", "current_policies"]

GATE_REASON = (
    "Task 3 is gated. NGFS Phase V EU carbon prices are not in any fetchable "
    "public document. Needed: variable Price|Carbon, EU or Europe region, "
    "annual 2026 to 2035, from the NGFS Scenario Explorer bulk download, which "
    "requires a guest login the owner must authorise."
)


def assert_price_ordering(config: Config, first_year: int = ORDERING_FROM_YEAR) -> int:
    """Net Zero above Delayed above Current Policies, every year from first_year.

    Returns the number of years checked, so a caller can tell a real pass from
    a vacuous one over an empty file.
    """
    scenarios = config.scenarios["scenarios"]
    years = sorted(
        year
        for year in scenarios[ORDERED_SCENARIOS[0]]["by_year"]
        if year >= first_year
    )
    for year in years:
        prices = [config.ets_price(key, year) for key in ORDERED_SCENARIOS]
        net_zero, delayed, current = prices
        assert net_zero > delayed, (
            f"{year}: Net Zero {net_zero} must sit above Delayed {delayed}"
        )
        assert delayed > current, (
            f"{year}: Delayed {delayed} must sit above Current Policies {current}"
        )
    return len(years)


# The rule itself, tested against a file that is known good.


def test_price_ordering_rule_against_fixture(fixture_config: Config) -> None:
    checked = assert_price_ordering(fixture_config)
    assert checked == 6, "expected 2030 through 2035 to be checked"


def test_ets_price_reads_the_configured_value(fixture_config: Config) -> None:
    assert fixture_config.ets_price("net_zero_2050", 2030) == 150.0


def test_unknown_scenario_raises(fixture_config: Config) -> None:
    with pytest.raises(ConfigError, match="unknown scenario"):
        fixture_config.ets_price("no_such_scenario", 2030)


def test_missing_year_raises(fixture_config: Config) -> None:
    with pytest.raises(ConfigError, match="no entry for year"):
        fixture_config.ets_price("net_zero_2050", 2099)


# The state of the real file. These pass today and describe the gate.


def test_real_scenarios_file_records_the_three_scenarios(real_config: Config) -> None:
    assert sorted(real_config.scenario_keys()) == sorted(ORDERED_SCENARIOS)


def test_real_scenarios_file_contains_no_invented_price(real_config: Config) -> None:
    """The whole point of the gate. No price may appear without a source."""
    for key, block in real_config.scenarios["scenarios"].items():
        assert block["by_year"] == {}, (
            f"{key} has prices. If they came from a real source, record the "
            f"source and retrieval date, then remove the strict xfail marker on "
            f"test_real_price_ordering_is_gated."
        )


def test_real_scenarios_file_states_the_document_needed(real_config: Config) -> None:
    meta = real_config.scenarios["meta"]
    assert meta["status"] == "gated"
    assert "NGFS" in meta["todo_document_needed"]
    assert "Price|Carbon" in meta["todo_document_needed"]


def test_asking_the_real_config_for_a_price_raises(real_config: Config) -> None:
    """A gated config must fail loudly, not return zero."""
    with pytest.raises(ConfigError, match="no entry for year"):
        real_config.ets_price("net_zero_2050", 2030)


# The gate.


@pytest.mark.xfail(strict=True, reason=GATE_REASON)
def test_real_price_ordering_is_gated(real_config: Config) -> None:
    checked = assert_price_ordering(real_config)
    assert checked > 0, "no years to check, the file is still empty"
