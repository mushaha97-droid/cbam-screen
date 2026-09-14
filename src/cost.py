"""CBAM certificate cost engine.

The formula, from CLAUDE.md section 6:

    cost = sum over goods of
           tonnes * emission_factor * ets_price[scenario, year]
                  * (1 - free_alloc[branch, year])

Worked example from the task list, which appears literally in the tests:

    1,000 t steel * 1.9 tCO2/t * price 80 * (1 - 0.975) = 3,800 EUR

The mass threshold: when a borrower's CBAM tonnes in a year fall below the
threshold in cbam_rules.yaml, the obligation is zero and the row carries a
MONITOR flag. Electricity and hydrogen do not count toward that test, which is
driven by the counts_toward_mass_threshold field on each good in cbam_rules.yaml
rather than by a name check in this file.

Per tier:
  Tier 2  direct obligation on imported goods.
  Tier 3  the same, but only on a branch where the downstream extension applies.
  Tier 1  lost allowance value, that is production tonnes * factor * price *
          (free_alloc[year - 1] - free_alloc[year]).
  Tier 4  input share times the upstream sector cost uplift. That uplift comes
          from the Tier 2 results across the portfolio, so it is computed in
          portfolio.py and passed in here.
  Tier 0  zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from src.config import Config, ConfigError
from src.schema import Borrower, SupplierData
from src.tiering import ADOPTED_BRANCH, TierResult, assign_tier, longest_matching_prefix

MONITOR_FLAG = "MONITOR"
DEFAULT_VALUES_FLAG = "DEFAULT_VALUES"

VERIFIED = "verified"
DEFAULT = "default"


# ---------------------------------------------------------------------------
# The pure arithmetic. No config, no borrower, no side effects.
# ---------------------------------------------------------------------------


def certificate_cost(
    tonnes: float,
    emission_factor: float,
    price_eur: float,
    free_allocation_share: float,
) -> float:
    """Cost in EUR of surrendering certificates for one good in one year.

    free_allocation_share is the share of free allocation still granted, so
    1 - free_allocation_share is the share of embedded emissions that must be
    covered by certificates.
    """
    if not 0.0 <= free_allocation_share <= 1.0:
        raise ValueError(
            f"free_allocation_share must be 0.0 to 1.0, got {free_allocation_share}"
        )
    if tonnes < 0 or emission_factor < 0 or price_eur < 0:
        raise ValueError("tonnes, emission_factor and price_eur must not be negative")
    return tonnes * emission_factor * price_eur * (1.0 - free_allocation_share)


def lost_allowance_value(
    production_tonnes: float,
    emission_factor: float,
    price_eur: float,
    free_allocation_previous_year: float,
    free_allocation_this_year: float,
) -> float:
    """Value of free allocation withdrawn from an EU producer in one year.

    This is the Tier 1 cost. It is the year-on-year step down in free
    allocation, valued at the carbon price, not the whole obligation.
    """
    step = free_allocation_previous_year - free_allocation_this_year
    if step < 0:
        raise ValueError(
            "free allocation increased year on year, which the phase-out does not do"
        )
    return production_tonnes * emission_factor * price_eur * step


def input_share_cost(input_share: float, upstream_cost_uplift_eur: float) -> float:
    """The Tier 4 cost: a share of an upstream sector's cost uplift."""
    if not 0.0 <= input_share <= 1.0:
        raise ValueError(f"input_share must be 0.0 to 1.0, got {input_share}")
    return input_share * upstream_cost_uplift_eur


# ---------------------------------------------------------------------------
# The mass threshold
# ---------------------------------------------------------------------------


def threshold_quantity(quantities: Mapping[str, float], config: Config) -> float:
    """Tonnes that count toward the mass threshold.

    Goods flagged counts_toward_mass_threshold false in cbam_rules.yaml are left
    out. Under the adopted rule those are electricity and hydrogen.
    """
    goods = config.goods()
    total = 0.0
    for group, quantity in quantities.items():
        if group not in goods:
            raise ConfigError(
                f"cbam_rules.yaml goods: no entry for good group {group!r}, so the "
                f"mass threshold cannot be evaluated"
            )
        if goods[group].get("counts_toward_mass_threshold"):
            total += float(quantity)
    return total


def below_mass_threshold(
    quantities: Mapping[str, float], config: Config
) -> tuple[bool, float, float]:
    """Returns (is_below, counted_tonnes, threshold_value)."""
    threshold = float(config.mass_threshold()["value"])
    counted = threshold_quantity(quantities, config)
    return counted < threshold, counted, threshold


# ---------------------------------------------------------------------------
# Emission factors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FactorChoice:
    """Which emission factor was used for a good, and where it came from."""

    value: float
    basis: str  # "verified" or "default"
    note: str | None = None


def emission_factor_for(
    group: str,
    borrower: Borrower,
    config: Config,
    verified_factors: Mapping[str, float] | None = None,
) -> FactorChoice:
    """Pick the emission factor for one good group.

    CLAUDE.md says to use the verified value when supplier_data is verified and
    the config default otherwise. The input schema in CLAUDE.md section 5 has no
    column carrying a verified factor, so a caller that has one passes it in
    through verified_factors. When supplier_data says verified but no number was
    supplied, the default is used and the reason is recorded rather than hidden.
    """
    supplied = (verified_factors or {}).get(group)
    if borrower.supplier_data is SupplierData.VERIFIED and supplied is not None:
        return FactorChoice(value=float(supplied), basis=VERIFIED)

    note = None
    if borrower.supplier_data is SupplierData.VERIFIED and supplied is None:
        note = (
            "supplier_data says verified but no verified emission factor was "
            "supplied, so the config default was used"
        )
    return FactorChoice(
        value=config.default_emission_factor(group, basis="total"),
        basis=DEFAULT,
        note=note,
    )


# ---------------------------------------------------------------------------
# Per borrower, per year, per scenario, per branch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostLine:
    """One good group's contribution to one year's cost."""

    good_group: str
    quantity: float
    emission_factor: float
    factor_basis: str
    price_eur: float
    free_allocation_share: float
    cost_eur: float


@dataclass(frozen=True)
class CostResult:
    """What one borrower owes in one year under one scenario and branch."""

    borrower_id: str
    year: int
    scenario: str
    branch: str
    tier: int
    tier_rule: str
    cost_eur: float
    counted_tonnes: float
    threshold_tonnes: float
    below_threshold: bool
    estimated: bool
    flags: tuple[str, ...] = ()
    lines: tuple[CostLine, ...] = ()
    notes: tuple[str, ...] = field(default=())


def _direct_obligation(
    borrower: Borrower,
    config: Config,
    year: int,
    scenario: str,
    branch: str,
    verified_factors: Mapping[str, float] | None,
) -> tuple[float, list[CostLine], list[str]]:
    """Tier 2 and Tier 3 cost: certificates on the goods the borrower imports."""
    price = config.ets_price(scenario, year)
    free_share = config.free_allocation_share(branch, year)
    lines: list[CostLine] = []
    notes: list[str] = []
    total = 0.0
    for group, quantity in sorted(borrower.cbam_import_quantities.items()):
        try:
            choice = emission_factor_for(group, borrower, config, verified_factors)
        except ConfigError as exc:
            notes.append(f"{group}: no emission factor, excluded from the cost. {exc}")
            continue
        if choice.note:
            notes.append(f"{group}: {choice.note}")
        cost = certificate_cost(quantity, choice.value, price, free_share)
        total += cost
        lines.append(
            CostLine(
                good_group=group,
                quantity=quantity,
                emission_factor=choice.value,
                factor_basis=choice.basis,
                price_eur=price,
                free_allocation_share=free_share,
                cost_eur=cost,
            )
        )
    return total, lines, notes


def _producer_quantities(borrower: Borrower, config: Config) -> dict[str, float]:
    """Production tonnes for a Tier 1 borrower.

    CLAUDE.md says to use a turnover-based sector intensity when production is
    unknown. The input schema has no production column at all, so the sector
    intensity is always the source here, and the row is marked estimated.
    """
    sectors = config.nace_tiers.get("sectors") or {}
    match = longest_matching_prefix(list(sectors.keys()), borrower.nace_code)
    if match is None:
        return {}
    intensities = (sectors[match] or {}).get("import_intensity") or {}
    turnover_millions = borrower.turnover_eur / 1_000_000.0
    out: dict[str, float] = {}
    for group, block in intensities.items():
        value = (block or {}).get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[group] = float(value) * turnover_millions
    return out


def _lost_allowance(
    borrower: Borrower,
    config: Config,
    year: int,
    scenario: str,
    branch: str,
    verified_factors: Mapping[str, float] | None,
) -> tuple[float, list[CostLine], list[str]]:
    """Tier 1 cost: the value of free allocation withdrawn this year."""
    price = config.ets_price(scenario, year)
    this_year = config.free_allocation_share(branch, year)
    try:
        previous_year = config.free_allocation_share(branch, year - 1)
    except ConfigError:
        notes = [
            f"no free allocation entry for {year - 1}, so the step down into "
            f"{year} cannot be valued"
        ]
        return 0.0, [], notes

    lines: list[CostLine] = []
    notes: list[str] = []
    total = 0.0
    for group, tonnes in sorted(_producer_quantities(borrower, config).items()):
        try:
            choice = emission_factor_for(group, borrower, config, verified_factors)
        except ConfigError as exc:
            notes.append(f"{group}: no emission factor, excluded from the cost. {exc}")
            continue
        cost = lost_allowance_value(
            tonnes, choice.value, price, previous_year, this_year
        )
        total += cost
        lines.append(
            CostLine(
                good_group=group,
                quantity=tonnes,
                emission_factor=choice.value,
                factor_basis=choice.basis,
                price_eur=price,
                free_allocation_share=this_year,
                cost_eur=cost,
            )
        )
    if lines:
        notes.append(
            "production tonnes were estimated from sector intensity times "
            "turnover, because the input schema carries no production column"
        )
    return total, lines, notes


def borrower_cost(
    borrower: Borrower,
    config: Config,
    year: int,
    scenario: str,
    branch: str = ADOPTED_BRANCH,
    tier: TierResult | None = None,
    verified_factors: Mapping[str, float] | None = None,
    upstream_cost_uplift_eur: float | None = None,
) -> CostResult:
    """Cost for one borrower, one year, one scenario, one branch."""
    tier_result = tier if tier is not None else assign_tier(borrower, config, branch)
    quantities = borrower.cbam_import_quantities
    is_below, counted, threshold = below_mass_threshold(quantities, config)

    flags: list[str] = []
    notes: list[str] = []
    lines: list[CostLine] = []
    cost = 0.0

    if tier_result.tier in (2, 3) and is_below:
        # Below the mass threshold there is no obligation, whatever the tonnage
        # is worth. The borrower still belongs on a watch list.
        flags.append(MONITOR_FLAG)
        notes.append(
            f"{counted:g} t counted toward the {threshold:g} t threshold, which is "
            f"below it, so the obligation is zero"
        )
    elif tier_result.tier in (2, 3):
        cost, lines, extra = _direct_obligation(
            borrower, config, year, scenario, branch, verified_factors
        )
        notes.extend(extra)
    elif tier_result.tier == 1:
        cost, lines, extra = _lost_allowance(
            borrower, config, year, scenario, branch, verified_factors
        )
        notes.extend(extra)
    elif tier_result.tier == 4:
        if upstream_cost_uplift_eur is None:
            notes.append(
                "Tier 4 cost needs the upstream sector cost uplift, which is "
                "computed across the portfolio. None was supplied, so the cost "
                "is reported as zero and should not be read as no exposure"
            )
        else:
            share = borrower.pass_through_share_effective
            if share is None:
                notes.append(
                    "Tier 4 cost needs an input share for this sector and the "
                    "sector default is missing"
                )
            else:
                cost = input_share_cost(share, upstream_cost_uplift_eur)

    if any(line.factor_basis == DEFAULT for line in lines):
        flags.append(DEFAULT_VALUES_FLAG)

    return CostResult(
        borrower_id=borrower.borrower_id,
        year=year,
        scenario=scenario,
        branch=branch,
        tier=tier_result.tier,
        tier_rule=tier_result.rule,
        cost_eur=cost,
        counted_tonnes=counted,
        threshold_tonnes=threshold,
        below_threshold=is_below,
        estimated=borrower.estimated,
        flags=tuple(flags),
        lines=tuple(lines),
        notes=tuple(notes),
    )


def borrower_cost_path(
    borrower: Borrower,
    config: Config,
    years: list[int],
    scenario: str,
    branch: str = ADOPTED_BRANCH,
    **kwargs: object,
) -> list[CostResult]:
    """The cost for one borrower across a run of years."""
    tier_result = assign_tier(borrower, config, branch)
    return [
        borrower_cost(
            borrower,
            config,
            year=year,
            scenario=scenario,
            branch=branch,
            tier=tier_result,
            **kwargs,  # type: ignore[arg-type]
        )
        for year in years
    ]
