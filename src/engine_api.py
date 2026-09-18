"""One entrypoint that turns borrower rows into screening results.

This module exists so that there is exactly one way into the engine, whoever is
calling it. The Power BI exporter calls it. The pytest suite calls it. The web
page calls it, through Pyodide, running this same file in the browser. No
formula is written twice, so no formula can drift.

What lives here:

    the plain-language labels a result is shown under
    the table builders that shape an engine answer into the star schema
    screen(), which takes plain dicts and gives back plain dicts

What deliberately does not live here:

    any regulatory number. Those are in config/*.yaml, as CLAUDE.md section 2
    requires, and reach this file through Config.

    pandas, subprocess, tomllib or any other import the browser cannot carry.
    The only third-party imports on this path are pyyaml and pydantic, which is
    what keeps the in-browser run the same code as the tested run.

THE PRICE GATE

config/scenarios.yaml carries no carbon prices, because the NGFS Phase V EU
prices could not be fetched. So screen() runs in one of two modes, and says
which one it is in:

    priced      a price set was supplied or the config has one. Cost, cost
                lines, materiality band and liquidity are all computed.

    unpriced    no price exists. Tier, the mass threshold, MONITOR, the
                estimated-field notice, the client questions and every flag
                that does not depend on a cost are still computed. Nothing
                that needs a price is reported at all, rather than reported
                as a zero.

PRICE_DEPENDENT_FLAGS names the flags that are withheld in unpriced mode. It is
deliberately short, and it is checked by a test, so a new price-dependent flag
cannot quietly start appearing without a price behind it.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.config import CONFIG_FILES, Config, ConfigError, load_config, load_yaml_mapping
from src.cost import CostResult, below_mass_threshold, borrower_cost
from src.flags import (
    MONITOR,
    RATING_GAP,
    band_order,
    evaluate_flags,
    materiality_band,
)
from src.liquidity import liquidity_profile, shock_year
from src.questions import (
    QuestionsError,
    as_rows as question_rows,
    check_covers_flags,
    load_questions,
    questions_path,
)
from src.schema import (
    IMPORT_COLUMNS,
    Borrower,
    DeclarantStatus,
    PassThrough,
    SupplierData,
    TransitionRating,
    parse_borrowers,
)
from src.tiering import ADOPTED_BRANCH, TierResult, assign_tier

import re

# schema.py owns the mapping from a good group to the borrower column that
# carries its quantity. Aliased here so the form and the engine cannot disagree
# about which box a number goes in.
IMPORT_COLUMNS_BY_GROUP = IMPORT_COLUMNS

REPO_ROOT = Path(__file__).resolve().parents[1]

# The cost path CLAUDE.md asks for, 2026 to 2035 inclusive.
DEFAULT_FIRST_YEAR = 2026
DEFAULT_LAST_YEAR = 2035

# The five plain-language tier labels from CLAUDE.md Task 10, Borrowers page.
# CLAUDE.md section 9 forbids showing a tier number where a label exists, so the
# label travels with every row rather than being rebuilt in DAX or in JavaScript.
TIER_LABEL_PLAIN: dict[int, str] = {
    0: "Not affected",
    1: "Makes CBAM goods in the EU",
    2: "Imports CBAM goods",
    3: "May be caught from 2028",
    4: "Pays via input prices",
}

# What a cost row actually rests on. This exists so that a zero in fact_cost can
# always be read correctly: a zero because nothing is owed is not the same as a
# zero because the engine could not compute the number yet.
COST_BASIS_LABELS: dict[str, str] = {
    "direct_obligation": "Certificates on imported goods",
    "below_mass_threshold": "Below the mass threshold, nothing owed this year",
    "lost_allocation": "Value of free allocation withdrawn this year",
    "lost_allocation_no_production": (
        "Producer with no sector production intensity in config, so no cost "
        "could be computed"
    ),
    "input_share_uplift_unavailable": (
        "Cost reaches this borrower through supplier prices. Needs the upstream "
        "sector uplift from portfolio.py, which is not built yet"
    ),
    "out_of_scope": "Not in scope, nothing owed",
}

# The share of embedded emissions a cost line actually charges for. Two bases
# charge on different shares, and a report that printed one formula for both
# would show a number that does not multiply out.
CHARGED_SHARE_LABELS: dict[str, str] = {
    "direct_obligation": "share not covered by free allocation",
    "lost_allocation": "free allocation withdrawn this year",
}

# Flags whose value depends on a carbon price, so they cannot be raised at all
# when no price set exists. RATING_GAP compares the materiality band against the
# bank's own rating, and the band comes from a cost. Every other flag in
# thresholds.yaml rests on tonnage, origin, declarant status, supplier data or
# EBITDA, none of which needs a price. tests/test_engine_api.py holds this list
# to that claim.
PRICE_DEPENDENT_FLAGS: frozenset[str] = frozenset({RATING_GAP})

DATA_LABELS = ("FIXTURE", "SYNTHETIC", "UPLOADED")

TABLE_ORDER = (
    "dim_borrower",
    "dim_scenario",
    "dim_branch",
    "dim_year",
    "fact_cost",
    "fact_cost_line",
    "fact_liquidity",
    "fact_flags",
    "questions",
)

_QUARTER_IN_LABEL = re.compile(r"Q(\d+)\s*$")


class EngineApiError(ValueError):
    """The request cannot be answered, and the reason is the caller's to fix."""


# ---------------------------------------------------------------------------
# Small helpers for writing values a csv reader or a JSON reader can trust
# ---------------------------------------------------------------------------


def _money(value: float | None) -> str:
    return "" if value is None else str(round(float(value), 2))


def _ratio(value: float | None) -> str:
    """Ratios and shares at full round-trip precision, deliberately not rounded.

    A materiality ratio decides which band a borrower lands in, and the bands
    have hard edges at 1, 3, 6 and 12 percent. Rounding a ratio before writing
    it can move a borrower across an edge, so the file would disagree with the
    band next to it in the same row. repr gives the shortest text that reads
    back as the same float, so the output and the engine always agree.
    """
    return "" if value is None else repr(float(value))


def _tonnes(value: float | None) -> str:
    return "" if value is None else str(round(float(value), 6))


def _flag(value: bool) -> str:
    """Booleans as lowercase text, which Power Query reads with Logical.FromText."""
    return "true" if value else "false"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Reading inputs
# ---------------------------------------------------------------------------


def load_price_override(path: Path) -> dict[str, Any]:
    """Load a scenarios-shaped yaml file to stand in for config/scenarios.yaml."""
    data = load_yaml_mapping(path)
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, dict) or not scenarios:
        raise EngineApiError(
            f"{path.name}: a price override must have a non-empty scenarios block, "
            f"shaped like config/scenarios.yaml"
        )
    for key, block in scenarios.items():
        by_year = (block or {}).get("by_year") or {}
        if not by_year:
            raise EngineApiError(
                f"{path.name}: scenario {key!r} has no prices in by_year, so it "
                f"cannot stand in for the gated file"
            )
    return data


def config_with_prices(config: Config, prices: Mapping[str, Any]) -> Config:
    """Return the same config with its scenarios block replaced."""
    return dataclasses.replace(config, scenarios=dict(prices))


def has_prices(config: Config) -> bool:
    """True when every scenario in the config carries at least one year's price.

    The gate is read off the data rather than off a status word, so a file that
    says one thing in its meta block and another in its scenarios cannot cause
    the engine to price a portfolio from nothing. The status word is still
    reported alongside, for the page to show.
    """
    scenarios = config.scenarios.get("scenarios") or {}
    if not scenarios:
        return False
    return all(((block or {}).get("by_year") or {}) for block in scenarios.values())


def price_status(config: Config) -> str:
    """The status word the price file states about itself, or a plain unknown."""
    return _text((config.scenarios.get("meta") or {}).get("status") or "unknown")


# ---------------------------------------------------------------------------
# What a cost rests on
# ---------------------------------------------------------------------------


def cost_basis(result: CostResult) -> str:
    if result.tier in (2, 3):
        return "below_mass_threshold" if result.below_threshold else "direct_obligation"
    if result.tier == 1:
        return "lost_allocation" if result.lines else "lost_allocation_no_production"
    if result.tier == 4:
        return "input_share_uplift_unavailable"
    return "out_of_scope"


def charged_share(config: Config, basis: str, branch: str, year: int, line: Any) -> float:
    """The share of embedded emissions this line is charged for.

    Both shares come straight from cbam_rules.yaml through config. Nothing is
    inferred from the cost, so a report can multiply the line out and get the
    cost back rather than dividing to find the missing factor.
    """
    if basis == "lost_allocation":
        # The Tier 1 cost is the year-on-year step down in free allocation,
        # valued at the carbon price, not the whole obligation.
        return config.free_allocation_share(
            branch, year - 1
        ) - config.free_allocation_share(branch, year)
    return 1.0 - float(line.free_allocation_share)


# Kept under their old private names so that src/export_powerbi.py, which these
# two were lifted out of, reads the same as it did before the move.
_cost_basis = cost_basis
_charged_share = charged_share


# ---------------------------------------------------------------------------
# Building the tables
# ---------------------------------------------------------------------------


def build_dim_year(
    years: Sequence[int], payment_years: Iterable[int], config: Config
) -> list[dict[str, str]]:
    """The year axis, wide enough to hold every payment year as well.

    A 2035 obligation settles in 2036, so the year dimension has to reach past
    the last obligation year or the liquidity fact would point at nothing.
    """
    shock = shock_year(config)
    all_years = sorted(set(years) | set(payment_years))
    return [
        {
            "year": str(year),
            "year_label": str(year),
            "is_obligation_year": _flag(year in set(years)),
            "is_payment_year": _flag(year in set(payment_years)),
            "is_liquidity_shock_year": _flag(year == shock),
        }
        for year in all_years
    ]


def build_dim_scenario(config: Config, default_scenario: str) -> list[dict[str, str]]:
    scenarios = config.scenarios.get("scenarios") or {}
    rows = []
    for order, key in enumerate(sorted(scenarios), start=1):
        block = scenarios[key] or {}
        rows.append(
            {
                "scenario": key,
                "scenario_label": _text(block.get("label") or key),
                "status": _text(block.get("status")),
                "source": _text(block.get("source")),
                "source_url": _text(block.get("source_url")),
                "region": _text(block.get("region")),
                "price_unit": _text(block.get("price_unit")),
                "sort_order": str(order),
                "is_default": _flag(key == default_scenario),
            }
        )
    return rows


def build_dim_branch(config: Config, default_branch: str) -> list[dict[str, str]]:
    branches = config.branch_definitions()
    rows = []
    for order, key in enumerate(sorted(branches), start=1):
        block = branches[key] or {}
        rows.append(
            {
                "branch": key,
                "branch_label": _text(block.get("label") or key),
                "status": _text(block.get("status")),
                "free_allocation_schedule": _text(block.get("free_allocation_schedule")),
                "downstream_extension_applies": _flag(
                    bool(block.get("downstream_extension_applies"))
                ),
                "description": " ".join(_text(block.get("description")).split()),
                "source": _text(block.get("source")),
                "sort_order": str(order),
                "is_default": _flag(key == default_branch),
            }
        )
    return rows


def build_dim_borrower(
    borrowers: Sequence[Borrower], tiers: Mapping[str, TierResult]
) -> list[dict[str, str]]:
    """One row per borrower.

    The tier here is the tier under one branch, named in meta as tier_branch.
    Tier 3 exists only where the downstream-extension proposal is switched on,
    so on the adopted branch no borrower carries it. The tier that varies by
    branch lives on fact_cost, which has a branch column.
    """
    rows = []
    for borrower in borrowers:
        tier = tiers[borrower.borrower_id]
        rows.append(
            {
                "borrower_id": borrower.borrower_id,
                "name": borrower.name,
                "nace_code": borrower.nace_code,
                "country": borrower.country,
                "exposure_eur": _money(borrower.exposure_eur),
                "turnover_eur": _money(borrower.turnover_eur),
                "ebitda_eur": _money(borrower.ebitda_eur),
                "working_capital_eur": _money(borrower.working_capital_eur),
                "tier": str(tier.tier),
                "tier_label_plain": TIER_LABEL_PLAIN[tier.tier],
                "tier_rule": tier.rule,
                "tier_reason": " ".join(tier.reason.split()),
                "estimated": _flag(borrower.estimated),
                "estimated_fields": "; ".join(borrower.estimated_fields),
                "unfilled_fields": "; ".join(borrower.unfilled_fields),
                "declarant_status": borrower.declarant_status.value,
                "supplier_data": borrower.supplier_data.value,
                "pass_through": borrower.pass_through.value,
                "pass_through_share_effective": _ratio(
                    borrower.pass_through_share_effective
                ),
                "top_supplier_country": _text(borrower.top_supplier_country),
                "share_top_supplier": _ratio(borrower.share_top_supplier),
                "bank_transition_rating": (
                    borrower.bank_transition_rating.value
                    if borrower.bank_transition_rating is not None
                    else ""
                ),
            }
        )
    return rows


def cost_grid(
    borrowers: Sequence[Borrower],
    config: Config,
    years: Sequence[int],
    scenarios: Sequence[str],
    branches: Sequence[str],
) -> tuple[
    list[dict[str, str]],
    list[dict[str, str]],
    dict[tuple[str, str, str], dict[int, float]],
]:
    """Run the engine across every borrower, year, scenario and branch.

    Returns the fact_cost rows, the fact_cost_line rows underneath them, and the
    cost by obligation year for each borrower, scenario and branch, which the
    liquidity model needs.

    The line rows exist so that a report can print the formula with the
    borrower's own numbers in it. The emission factor is the one input to
    cost = tonnes x factor x price x (1 - free allocation) that is not on
    fact_cost, because a borrower can import two goods with two different
    factors and a single averaged figure on the summary row would be a number
    the engine never computed. These are the engine's own CostLine values,
    copied out, not recomputed.
    """
    rows: list[dict[str, str]] = []
    line_rows: list[dict[str, str]] = []
    paths: dict[tuple[str, str, str], dict[int, float]] = {}
    # Bands are text, and a report has to show them least to most material
    # rather than alphabetically. The order is the one thresholds.yaml lists, so
    # adding a band in config reorders the report without a change here.
    band_rank = {band: index + 1 for index, band in enumerate(band_order(config))}

    for borrower in borrowers:
        for branch in branches:
            tier = assign_tier(borrower, config, branch)
            for scenario in scenarios:
                by_year: dict[int, float] = {}
                for year in years:
                    result = borrower_cost(
                        borrower,
                        config,
                        year=year,
                        scenario=scenario,
                        branch=branch,
                        tier=tier,
                    )
                    band = materiality_band(
                        result.cost_eur, borrower.ebitda_eur, config
                    )
                    basis = cost_basis(result)
                    by_year[year] = result.cost_eur
                    rows.append(
                        {
                            "borrower_id": borrower.borrower_id,
                            "year": str(year),
                            "scenario": scenario,
                            "branch": branch,
                            "tier": str(result.tier),
                            "tier_label_plain": TIER_LABEL_PLAIN[result.tier],
                            "cost_eur": _money(result.cost_eur),
                            "materiality_ratio": _ratio(band.ratio),
                            "band": band.band,
                            "band_sort": str(band_rank[band.band]),
                            "band_label": band.label,
                            "price_eur": _money(config.ets_price(scenario, year)),
                            "free_allocation_share": _ratio(
                                config.free_allocation_share(branch, year)
                            ),
                            "counted_tonnes": _tonnes(result.counted_tonnes),
                            "threshold_tonnes": _tonnes(result.threshold_tonnes),
                            "below_threshold": _flag(result.below_threshold),
                            "estimated": _flag(result.estimated),
                            "cost_basis": basis,
                            "cost_basis_label": COST_BASIS_LABELS[basis],
                        }
                    )
                    for order, line in enumerate(result.lines, start=1):
                        charged = charged_share(config, basis, branch, year, line)
                        line_rows.append(
                            {
                                "borrower_id": borrower.borrower_id,
                                "year": str(year),
                                "scenario": scenario,
                                "branch": branch,
                                "line_order": str(order),
                                "good_group": line.good_group,
                                "quantity": _tonnes(line.quantity),
                                "emission_factor": _ratio(line.emission_factor),
                                "factor_basis": line.factor_basis,
                                "price_eur": _money(line.price_eur),
                                "free_allocation_share": _ratio(
                                    line.free_allocation_share
                                ),
                                "charged_share": _ratio(charged),
                                "charged_share_label": CHARGED_SHARE_LABELS[basis],
                                "cost_eur": _money(line.cost_eur),
                                "cost_basis": basis,
                            }
                        )
                paths[(borrower.borrower_id, scenario, branch)] = by_year
    return rows, line_rows, paths


# The exporter called this _cost_grid when it owned it.
_cost_grid = cost_grid


def build_fact_liquidity(
    borrowers: Sequence[Borrower],
    config: Config,
    paths: Mapping[tuple[str, str, str], Mapping[int, float]],
) -> list[dict[str, str]]:
    """Turn each cost path into the payments that settle it.

    liquidity.py decides the timing. This only flattens its answer. A borrower
    whose cost is zero in every year produces no payments at all, so the table
    is deliberately not a full cross join.
    """
    working_capital = {b.borrower_id: b.working_capital_eur for b in borrowers}
    rows: list[dict[str, str]] = []
    for (borrower_id, scenario, branch), by_year in sorted(paths.items()):
        capital = working_capital.get(borrower_id)
        profile = liquidity_profile(by_year, config, capital)
        for flow in profile.flows:
            quarter_match = _QUARTER_IN_LABEL.search(flow.period_label)
            share = flow.amount_eur / capital if capital and capital > 0 else None
            rows.append(
                {
                    "borrower_id": borrower_id,
                    "scenario": scenario,
                    "branch": branch,
                    "year": str(flow.payment_year),
                    "window": flow.period_label,
                    "quarter": quarter_match.group(1) if quarter_match else "",
                    "kind": flow.kind,
                    "obligation_year": str(flow.obligation_year),
                    "cash_out_eur": _money(flow.amount_eur),
                    "share_of_working_capital": _ratio(share),
                }
            )
    return rows


def unpriced_cost_result(
    borrower: Borrower, config: Config, tier: TierResult
) -> CostResult:
    """What the engine can say about a borrower with no carbon price at all.

    The mass threshold is a tonnage test, so it holds without a price, and so
    does the MONITOR flag that follows from it. The cost is not zero here: it is
    not computed. cost_basis says so, and screen() never reports this object's
    cost_eur to a caller in unpriced mode.
    """
    is_below, counted, threshold = below_mass_threshold(
        borrower.cbam_import_quantities, config
    )
    flags: list[str] = []
    notes: list[str] = []
    if tier.tier in (2, 3) and is_below:
        flags.append(MONITOR)
        notes.append(
            f"{counted:g} t counted toward the {threshold:g} t threshold, which is "
            f"below it, so the obligation is zero"
        )
    notes.append(
        "no carbon price set was available, so no cost, band or cash flow was "
        "computed for this borrower"
    )
    return CostResult(
        borrower_id=borrower.borrower_id,
        year=0,
        scenario="",
        branch=tier.branch,
        tier=tier.tier,
        tier_rule=tier.rule,
        cost_eur=0.0,
        counted_tonnes=counted,
        threshold_tonnes=threshold,
        below_threshold=is_below,
        estimated=borrower.estimated,
        flags=tuple(flags),
        notes=tuple(notes),
    )


def build_fact_flags(
    borrowers: Sequence[Borrower],
    config: Config,
    year: int,
    scenario: str,
    branch: str,
) -> list[dict[str, str]]:
    """One row per borrower and flag, active true or false.

    A flag is a statement about a borrower in a given year under a given
    scenario and branch, because RATING_GAP and MONITOR both depend on the cost.
    The reference point is written into every row, so a chart built on this
    table can always say which year it is describing.

    The flag list comes from thresholds.yaml, not from a list in this file, so a
    new flag in config appears here without a code change.
    """
    codes = sorted((config.thresholds.get("flags") or {}).keys())
    if not codes:
        raise EngineApiError(
            "thresholds.yaml has no flags block, so no flags can be reported"
        )

    rows: list[dict[str, str]] = []
    for borrower in borrowers:
        tier = assign_tier(borrower, config, branch)
        result = borrower_cost(
            borrower, config, year=year, scenario=scenario, branch=branch, tier=tier
        )
        band = materiality_band(result.cost_eur, borrower.ebitda_eur, config)
        raised = {
            flag.code: " ".join(flag.reason.split())
            for flag in evaluate_flags(
                borrower, result, config, tier=tier, materiality=band
            )
        }
        for code in codes:
            rows.append(
                {
                    "borrower_id": borrower.borrower_id,
                    "flag": code,
                    "active": _flag(code in raised),
                    "reason": raised.get(code, ""),
                    "evaluated_year": str(year),
                    "evaluated_scenario": scenario,
                    "evaluated_branch": branch,
                }
            )
    return rows


def build_fact_flags_unpriced(
    borrowers: Sequence[Borrower], config: Config, branch: str
) -> list[dict[str, str]]:
    """The same table with no price, so the price-dependent flags are withheld.

    A withheld flag is written as inactive with a reason that says why it could
    not be evaluated, rather than being dropped from the table. A missing row
    and a false row look the same to a reader; a stated reason does not.
    """
    codes = sorted((config.thresholds.get("flags") or {}).keys())
    if not codes:
        raise EngineApiError(
            "thresholds.yaml has no flags block, so no flags can be reported"
        )

    rows: list[dict[str, str]] = []
    for borrower in borrowers:
        tier = assign_tier(borrower, config, branch)
        result = unpriced_cost_result(borrower, config, tier)
        band = materiality_band(0.0, borrower.ebitda_eur, config)
        raised = {
            flag.code: " ".join(flag.reason.split())
            for flag in evaluate_flags(
                borrower, result, config, tier=tier, materiality=band
            )
            if flag.code not in PRICE_DEPENDENT_FLAGS
        }
        for code in codes:
            withheld = code in PRICE_DEPENDENT_FLAGS
            rows.append(
                {
                    "borrower_id": borrower.borrower_id,
                    "flag": code,
                    "active": _flag(code in raised),
                    "reason": raised.get(
                        code,
                        (
                            "needs a carbon price to evaluate, and no price set "
                            "was available"
                        )
                        if withheld
                        else "",
                    ),
                    "evaluated_year": "",
                    "evaluated_scenario": "",
                    "evaluated_branch": branch,
                }
            )
    return rows


def build_questions(config: Config, config_dir: Path) -> tuple[list[dict[str, str]], Path]:
    """The flag to question mapping, flattened.

    The questions live in questions.yaml so the wording can change without a
    code change. They are a table of their own so that a reader never has to
    parse yaml, and because a question is not a property of a borrower: it is a
    property of a flag, and joining it onto fact_flags would repeat the same
    sentence once per borrower.
    """
    path = questions_path(config_dir)
    try:
        loaded = load_questions(path)
        check_covers_flags(loaded, config)
    except QuestionsError as exc:
        raise EngineApiError(f"the client questions could not be read: {exc}") from exc
    return question_rows(loaded), path


# ---------------------------------------------------------------------------
# screen(): the one call the browser makes
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ScreenRequest:
    """Everything a screening run needs, with no file paths but the config's.

    Borrower rows arrive as plain dicts, exactly as they come off a csv or off a
    web form, so the browser and a test hand the engine the same shape.
    """

    rows: Sequence[Mapping[str, Any]]
    config_dir: Path
    prices_path: Path | None = None
    first_year: int = DEFAULT_FIRST_YEAR
    last_year: int = DEFAULT_LAST_YEAR
    default_scenario: str = "delayed_transition"
    default_branch: str = ADOPTED_BRANCH
    tier_branch: str = ADOPTED_BRANCH
    data_label: str = "UPLOADED"

    @property
    def years(self) -> list[int]:
        return list(range(self.first_year, self.last_year + 1))


def _price_provenance(
    config: Config, prices_path: Path | None, config_dir: Path
) -> dict[str, str]:
    """Where the prices came from, in the words the price file uses about itself."""
    if prices_path is not None:
        raw = load_yaml_mapping(prices_path)
        meta = raw.get("meta") or {}
        return {
            "price_source": prices_path.as_posix(),
            "price_source_label": _text(meta.get("label") or "fixture"),
            "price_source_note": " ".join(_text(meta.get("note")).split()),
            "price_source_status": _text(meta.get("status") or "unknown"),
            "price_source_sha256": file_sha256(prices_path),
        }
    scenarios_file = config_dir / "scenarios.yaml"
    meta = config.scenarios.get("meta") or {}
    return {
        "price_source": scenarios_file.as_posix(),
        "price_source_label": _text(meta.get("label") or "config"),
        "price_source_note": " ".join(
            _text(meta.get("note") or meta.get("gate_reason")).split()
        ),
        "price_source_status": price_status(config),
        "price_source_sha256": (
            file_sha256(scenarios_file) if scenarios_file.is_file() else ""
        ),
    }


def config_provenance(config_dir: Path) -> list[dict[str, str]]:
    """Every config file in use, with its hash and the version it claims.

    A page that shows a number has to be able to say which files produced it.
    Hashing them here rather than in the page means the hash is of the bytes the
    engine actually parsed.
    """
    rows: list[dict[str, str]] = []
    names = sorted(set(CONFIG_FILES.values()) | {"questions.yaml"})
    for name in names:
        path = config_dir / name
        if not path.is_file():
            rows.append({"file": name, "present": "false", "sha256": "", "status": "", "config_version": ""})
            continue
        try:
            meta = (load_yaml_mapping(path).get("meta") or {})
        except ConfigError:
            meta = {}
        rows.append(
            {
                "file": name,
                "present": "true",
                "sha256": file_sha256(path),
                "status": _text(meta.get("status")),
                "config_version": _text(meta.get("config_version")),
            }
        )
    return rows


def screen(request: ScreenRequest) -> dict[str, Any]:
    """Run the whole engine over some borrower rows and return plain data.

    The return value is JSON-safe: strings, numbers, booleans, lists and dicts.
    The tables are the same star schema the Power BI exporter writes, which is
    why a screened borrower can be dropped straight into a dashboard that was
    reading an exported folder.
    """
    config_dir = Path(request.config_dir)
    config = load_config(config_dir)

    if request.prices_path is not None:
        config = config_with_prices(
            config, load_price_override(Path(request.prices_path))
        )
    priced = has_prices(config)
    prices_meta = _price_provenance(config, request.prices_path, config_dir)

    borrowers, problems = parse_borrowers(list(request.rows), config)
    if not borrowers:
        return {
            "ok": False,
            "priced": priced,
            "prices": prices_meta,
            "config": config_provenance(config_dir),
            "accepted": 0,
            "rejected": list(problems),
            "tables": {},
            "meta": {},
            "borrowers": [],
        }

    years = request.years
    scenarios = config.scenario_keys()
    branches = config.branches()

    tier_results = {
        borrower.borrower_id: assign_tier(borrower, config, request.tier_branch)
        for borrower in borrowers
    }

    tables: dict[str, list[dict[str, str]]] = {
        "dim_borrower": build_dim_borrower(borrowers, tier_results),
        "dim_scenario": build_dim_scenario(config, request.default_scenario),
        "dim_branch": build_dim_branch(config, request.default_branch),
    }

    flag_reference: dict[str, str] = {}
    if priced:
        cost_rows, line_rows, paths = cost_grid(
            borrowers, config, years, scenarios, branches
        )
        liquidity_rows = build_fact_liquidity(borrowers, config, paths)
        flag_year = shock_year(config)
        if flag_year not in years:
            flag_year = years[0]
        tables["fact_cost"] = cost_rows
        tables["fact_cost_line"] = line_rows
        tables["fact_liquidity"] = liquidity_rows
        tables["fact_flags"] = build_fact_flags(
            borrowers, config, flag_year, request.default_scenario, request.default_branch
        )
        tables["dim_year"] = build_dim_year(
            years, {int(row["year"]) for row in liquidity_rows}, config
        )
        flag_reference = {
            "year": str(flag_year),
            "scenario": request.default_scenario,
            "branch": request.default_branch,
        }
    else:
        tables["fact_cost"] = []
        tables["fact_cost_line"] = []
        tables["fact_liquidity"] = []
        tables["fact_flags"] = build_fact_flags_unpriced(
            borrowers, config, request.default_branch
        )
        tables["dim_year"] = build_dim_year(years, [], config)
        flag_reference = {
            "year": "",
            "scenario": "",
            "branch": request.default_branch,
        }

    question_table, questions_file = build_questions(config, config_dir)
    tables["questions"] = question_table

    meta = {
        "data_label": request.data_label,
        "config_dir": config_dir.as_posix(),
        "years": f"{years[0]} to {years[-1]}",
        "scenarios": ", ".join(scenarios),
        "branches": ", ".join(branches),
        "default_scenario": request.default_scenario,
        "default_branch": request.default_branch,
        "tier_branch_for_dim_borrower": request.tier_branch,
        "liquidity_shock_year": str(shock_year(config)),
        "flag_reference_year": flag_reference["year"],
        "flag_reference_scenario": flag_reference["scenario"],
        "flag_reference_branch": flag_reference["branch"],
        "borrower_count": str(len(borrowers)),
        "borrower_rows_rejected": str(len(problems)),
        "questions_source": questions_file.as_posix(),
        "questions_sha256": file_sha256(questions_file),
        "materiality_note": (
            "Materiality bands are the author's assumptions in thresholds.yaml, "
            "not law and not supervisory guidance."
        ),
        "tier_4_cost_note": (
            "Tier 4 cost needs the upstream sector uplift from portfolio.py, which "
            "is not built yet (CLAUDE.md Task 9). Tier 4 rows carry a cost of zero "
            "and cost_basis input_share_uplift_unavailable. Zero means not "
            "computed, not no exposure."
        ),
        "unpriced_note": (
            ""
            if priced
            else (
                "No carbon price set was available, so no cost, materiality band "
                "or cash flow was computed. Tier, the mass threshold, the flags "
                "that do not need a price and the client questions still are."
            )
        ),
        "withheld_flags": "" if priced else ", ".join(sorted(PRICE_DEPENDENT_FLAGS)),
    }
    meta.update(prices_meta)

    return {
        "ok": True,
        "priced": priced,
        "prices": prices_meta,
        "config": config_provenance(config_dir),
        "accepted": len(borrowers),
        "rejected": list(problems),
        "tables": tables,
        "meta": meta,
        "borrowers": [_borrower_detail(b, config, branches) for b in borrowers],
    }


def _borrower_detail(
    borrower: Borrower, config: Config, branches: Sequence[str]
) -> dict[str, Any]:
    """The things a screening card shows that are not in the star schema.

    The tier per branch, what the tool had to estimate, what it could not fill
    at all, and the mass-threshold arithmetic. All of it is price free, so this
    block is the same whether or not a price set exists.
    """
    is_below, counted, threshold = below_mass_threshold(
        borrower.cbam_import_quantities, config
    )
    supplied = borrower.supplied_cbam_import_quantities
    return {
        "borrower_id": borrower.borrower_id,
        "name": borrower.name,
        "estimated": borrower.estimated,
        "estimated_fields": list(borrower.estimated_fields),
        "unfilled_fields": list(borrower.unfilled_fields),
        "counted_tonnes": counted,
        "threshold_tonnes": threshold,
        "below_threshold": is_below,
        "quantities": {k: float(v) for k, v in borrower.cbam_import_quantities.items()},
        "supplied_quantities": {k: float(v) for k, v in supplied.items()},
        "tier_by_branch": {
            branch: {
                "tier": assign_tier(borrower, config, branch).tier,
                "label": TIER_LABEL_PLAIN[assign_tier(borrower, config, branch).tier],
                "rule": assign_tier(borrower, config, branch).rule,
                "reason": " ".join(assign_tier(borrower, config, branch).reason.split()),
                "origin_unknown": assign_tier(borrower, config, branch).origin_unknown,
            }
            for branch in branches
        },
    }


def form_options(config_dir: Path) -> dict[str, Any]:
    """The choices a borrower form should offer, read out of the config in use.

    The form needs a NACE list, a country list, the good groups and the enum
    values for the four categorical fields. Every one of those is already stated
    in config, so the page asks the engine for them rather than parsing yaml in
    JavaScript or hard-coding a list that would go stale the moment config
    changed.

    The country lists are the ones the tier rule actually tests against. A
    fixture config lists a subset of the EU, and the page says so, because a
    form that quietly offered seven EU countries as if they were twenty-seven
    would be lying about the rule rather than about the list.
    """
    config = load_config(Path(config_dir))
    goods = config.goods()
    sectors = config.nace_tiers.get("sectors") or {}
    exempt = (config.exempt_origins().get("countries") or [])
    eu_block = config.cbam_rules.get("eu_customs_territory") or {}

    nace: list[dict[str, str]] = []
    for prefix in sorted(sectors):
        entry = sectors[prefix] or {}
        nace.append(
            {
                "code": str(prefix),
                "label": _text(entry.get("label")),
                "tier": str(entry.get("tier", "")),
                "confidence": _text(entry.get("confidence")),
            }
        )

    return {
        "nace": nace,
        "producer_nace": [str(p) for p in (config.nace_tiers.get("producer_nace") or [])],
        "downstream_nace": [
            str(p) for p in (config.nace_tiers.get("downstream_nace") or [])
        ],
        "eu_countries": [str(code).upper() for code in (eu_block.get("codes") or [])],
        "eu_countries_source": _text(eu_block.get("source")),
        "exempt_countries": [
            {"code": str(c.get("code", "")).upper(), "name": _text(c.get("name"))}
            for c in exempt
            if isinstance(c, Mapping)
        ],
        "goods": [
            {
                "group": key,
                "column": IMPORT_COLUMNS_BY_GROUP.get(key, ""),
                "label": _text((goods[key] or {}).get("label") or key),
                "counts_toward_mass_threshold": bool(
                    (goods[key] or {}).get("counts_toward_mass_threshold")
                ),
            }
            for key in sorted(goods)
            if key in IMPORT_COLUMNS_BY_GROUP
        ],
        "mass_threshold": {
            "value": float(config.mass_threshold()["value"]),
            "unit": _text(config.mass_threshold().get("unit")),
        },
        "enums": {
            "declarant_status": [item.value for item in DeclarantStatus],
            "supplier_data": [item.value for item in SupplierData],
            "pass_through": [item.value for item in PassThrough],
            "bank_transition_rating": [item.value for item in TransitionRating],
        },
        "bands": band_order(config),
        "scenarios": config.scenario_keys(),
        "branches": config.branches(),
        "shock_year": shock_year(config),
    }


def form_options_json(config_dir: str) -> str:
    """The form options as JSON, for the page."""
    return json.dumps(form_options(Path(config_dir)))


def screen_json(payload_json: str) -> str:
    """The browser's call. JSON in, JSON out, so nothing has to be converted.

    Pyodide can hand a JavaScript object straight to Python, but a JSON string
    round trip is the one shape whose behaviour is identical in the browser and
    in pytest, and this function is exercised by both.
    """
    payload = json.loads(payload_json)
    rows = payload.get("borrowers") or []
    if not isinstance(rows, list):
        raise EngineApiError("borrowers must be a list of row objects")
    prices = payload.get("prices_path")
    request = ScreenRequest(
        rows=rows,
        config_dir=Path(payload.get("config_dir") or (REPO_ROOT / "config")),
        prices_path=Path(prices) if prices else None,
        first_year=int(payload.get("first_year") or DEFAULT_FIRST_YEAR),
        last_year=int(payload.get("last_year") or DEFAULT_LAST_YEAR),
        default_scenario=str(payload.get("default_scenario") or "delayed_transition"),
        default_branch=str(payload.get("default_branch") or ADOPTED_BRANCH),
        tier_branch=str(payload.get("tier_branch") or ADOPTED_BRANCH),
        data_label=str(payload.get("data_label") or "UPLOADED"),
    )
    try:
        result = screen(request)
    except (EngineApiError, ConfigError) as exc:
        result = {
            "ok": False,
            "error": str(exc),
            "priced": False,
            "tables": {},
            "meta": {},
            "borrowers": [],
            "rejected": [],
            "accepted": 0,
        }
    return json.dumps(result)
