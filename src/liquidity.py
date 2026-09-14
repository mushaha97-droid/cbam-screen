"""When the CBAM cash actually leaves the borrower.

An annual cost number hides the shape of the cash flow, and the shape is the
interesting part for a lender. Two things drive it, both from cbam_rules.yaml
and thresholds.yaml rather than from anything hard-coded here:

  No CBAM certificate can be bought before 1 February 2027, so the whole 2026
  obligation lands as one block between February and September 2027.

  From 1 January 2027 a declarant must hold certificates covering at least half
  its embedded emissions at the end of each quarter, so from the 2027 obligation
  onward part of the cash goes out during the year of import and the balance
  settles at surrender the following September.

Put those together and 2027 carries two obligations at once: the 2026 block and
the 2027 prepayments. That double load is the 2027 liquidity shock.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from src.config import Config, require


@dataclass(frozen=True)
class CashFlow:
    """One payment, and what it was for."""

    payment_year: int
    period_label: str
    amount_eur: float
    obligation_year: int
    kind: str  # "block", "prepayment" or "settlement"


@dataclass(frozen=True)
class LiquidityProfile:
    """The whole payment schedule, plus the year that hurts most."""

    flows: tuple[CashFlow, ...]
    by_payment_year: Mapping[int, float]
    working_capital_eur: float | None
    share_of_working_capital: Mapping[int, float]
    peak_year: int | None
    peak_amount_eur: float

    def amount_in(self, year: int) -> float:
        return self.by_payment_year.get(year, 0.0)

    def share_in(self, year: int) -> float | None:
        return self.share_of_working_capital.get(year)


def liquidity_settings(config: Config) -> dict[str, object]:
    return require(config.thresholds, "liquidity", "thresholds.yaml")


def cash_flows_for_obligation(
    obligation_year: int, amount_eur: float, config: Config
) -> list[CashFlow]:
    """Turn one year's obligation into the payments that settle it."""
    if amount_eur <= 0:
        return []

    settings = liquidity_settings(config)
    block = settings["block_payment"]  # type: ignore[index]
    quarterly = settings["quarterly"]  # type: ignore[index]

    if obligation_year in block["applies_to_obligation_years"]:  # type: ignore[index]
        payment_year = obligation_year + int(block["payment_year_offset"])  # type: ignore[index]
        return [
            CashFlow(
                payment_year=payment_year,
                period_label=f"{payment_year} February to September",
                amount_eur=amount_eur,
                obligation_year=obligation_year,
                kind="block",
            )
        ]

    if obligation_year < int(quarterly["applies_from_obligation_year"]):  # type: ignore[index]
        # Before the first obligation year there is nothing to pay.
        return []

    prepayment_share = float(quarterly["prepayment_share"])  # type: ignore[index]
    instalments = int(quarterly["instalments_in_obligation_year"])  # type: ignore[index]
    settlement_year = obligation_year + int(quarterly["settlement_offset_years"])  # type: ignore[index]

    prepaid_total = amount_eur * prepayment_share
    per_instalment = prepaid_total / instalments
    flows = [
        CashFlow(
            payment_year=obligation_year,
            period_label=f"{obligation_year} Q{quarter}",
            amount_eur=per_instalment,
            obligation_year=obligation_year,
            kind="prepayment",
        )
        for quarter in range(1, instalments + 1)
    ]
    flows.append(
        CashFlow(
            payment_year=settlement_year,
            period_label=f"{settlement_year} settlement by 30 September",
            amount_eur=amount_eur - prepaid_total,
            obligation_year=obligation_year,
            kind="settlement",
        )
    )
    return flows


def liquidity_profile(
    cost_by_obligation_year: Mapping[int, float],
    config: Config,
    working_capital_eur: float | None = None,
) -> LiquidityProfile:
    """Build the full payment schedule from a run of annual costs."""
    flows: list[CashFlow] = []
    for obligation_year in sorted(cost_by_obligation_year):
        flows.extend(
            cash_flows_for_obligation(
                obligation_year, float(cost_by_obligation_year[obligation_year]), config
            )
        )

    by_year: dict[int, float] = {}
    for flow in flows:
        by_year[flow.payment_year] = by_year.get(flow.payment_year, 0.0) + flow.amount_eur

    shares: dict[int, float] = {}
    if working_capital_eur and working_capital_eur > 0:
        shares = {
            year: amount / working_capital_eur for year, amount in by_year.items()
        }

    peak_year = max(by_year, key=lambda y: by_year[y]) if by_year else None
    peak_amount = by_year[peak_year] if peak_year is not None else 0.0

    return LiquidityProfile(
        flows=tuple(sorted(flows, key=lambda f: (f.payment_year, f.period_label))),
        by_payment_year=by_year,
        working_capital_eur=working_capital_eur,
        share_of_working_capital=shares,
        peak_year=peak_year,
        peak_amount_eur=peak_amount,
    )


def shock_year(config: Config) -> int:
    """The year the first block and the first prepayments collide."""
    settings = liquidity_settings(config)
    block = settings["block_payment"]  # type: ignore[index]
    first_obligation = min(block["applies_to_obligation_years"])  # type: ignore[index]
    return int(first_obligation) + int(block["payment_year_offset"])  # type: ignore[index]
