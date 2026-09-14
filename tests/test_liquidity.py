"""Task 7 tests: the 2027 liquidity shock."""

from __future__ import annotations

import pytest

from src.config import Config
from src.liquidity import cash_flows_for_obligation, liquidity_profile, shock_year


def test_the_2026_obligation_lands_as_one_block_in_2027(
    fixture_config: Config,
) -> None:
    """No certificate exists to buy before 1 February 2027."""
    flows = cash_flows_for_obligation(2026, 100_000.0, fixture_config)
    assert len(flows) == 1
    flow = flows[0]
    assert flow.payment_year == 2027
    assert flow.kind == "block"
    assert flow.amount_eur == pytest.approx(100_000.0)
    assert "February to September" in flow.period_label


def test_the_2027_obligation_is_paid_quarterly_then_settled(
    fixture_config: Config,
) -> None:
    """Half across four quarters in 2027, the balance at surrender in 2028."""
    flows = cash_flows_for_obligation(2027, 100_000.0, fixture_config)
    prepayments = [f for f in flows if f.kind == "prepayment"]
    settlements = [f for f in flows if f.kind == "settlement"]
    assert len(prepayments) == 4
    assert len(settlements) == 1
    assert all(f.payment_year == 2027 for f in prepayments)
    assert all(f.amount_eur == pytest.approx(12_500.0) for f in prepayments)
    assert settlements[0].payment_year == 2028
    assert settlements[0].amount_eur == pytest.approx(50_000.0)


def test_every_obligation_is_fully_paid(fixture_config: Config) -> None:
    """The schedule must not lose or invent money."""
    for year in range(2026, 2036):
        flows = cash_flows_for_obligation(year, 250_000.0, fixture_config)
        assert sum(f.amount_eur for f in flows) == pytest.approx(250_000.0), year


def test_a_zero_obligation_generates_no_payment(fixture_config: Config) -> None:
    assert cash_flows_for_obligation(2027, 0.0, fixture_config) == []


def test_2027_carries_both_the_2026_block_and_the_2027_prepayments(
    fixture_config: Config,
) -> None:
    """This double load is the shock the tool exists to show."""
    profile = liquidity_profile(
        {2026: 100_000.0, 2027: 100_000.0, 2028: 100_000.0}, fixture_config
    )
    # 2027 pays the whole 2026 block plus half the 2027 obligation.
    assert profile.amount_in(2027) == pytest.approx(150_000.0)
    # 2028 pays the 2027 balance plus half the 2028 obligation.
    assert profile.amount_in(2028) == pytest.approx(100_000.0)
    assert profile.amount_in(2026) == 0.0


def test_the_peak_year_is_2027_on_a_flat_cost_path(fixture_config: Config) -> None:
    flat = {year: 100_000.0 for year in range(2026, 2036)}
    profile = liquidity_profile(flat, fixture_config)
    assert profile.peak_year == 2027
    assert profile.peak_amount_eur == pytest.approx(150_000.0)


def test_shock_year_is_2027(fixture_config: Config) -> None:
    assert shock_year(fixture_config) == 2027


def test_share_of_working_capital_is_reported_when_supplied(
    fixture_config: Config,
) -> None:
    profile = liquidity_profile(
        {2026: 100_000.0, 2027: 100_000.0},
        fixture_config,
        working_capital_eur=1_000_000.0,
    )
    assert profile.share_in(2027) == pytest.approx(0.15)


def test_share_is_absent_rather_than_guessed_when_working_capital_is_missing(
    fixture_config: Config,
) -> None:
    """A guessed denominator would make the headline number meaningless."""
    profile = liquidity_profile({2026: 100_000.0}, fixture_config)
    assert profile.share_of_working_capital == {}
    assert profile.share_in(2027) is None


def test_zero_working_capital_does_not_divide_by_zero(fixture_config: Config) -> None:
    profile = liquidity_profile(
        {2026: 100_000.0}, fixture_config, working_capital_eur=0.0
    )
    assert profile.share_in(2027) is None


def test_flows_are_sorted_by_payment_year(fixture_config: Config) -> None:
    profile = liquidity_profile(
        {2026: 10_000.0, 2027: 10_000.0, 2028: 10_000.0}, fixture_config
    )
    years = [flow.payment_year for flow in profile.flows]
    assert years == sorted(years)


def test_total_paid_equals_total_owed(fixture_config: Config) -> None:
    costs = {2026: 10_000.0, 2027: 20_000.0, 2028: 30_000.0, 2029: 40_000.0}
    profile = liquidity_profile(costs, fixture_config)
    assert sum(profile.by_payment_year.values()) == pytest.approx(sum(costs.values()))
