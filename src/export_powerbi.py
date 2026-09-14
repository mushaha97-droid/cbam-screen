"""Star-schema export for Power BI.

Power BI Desktop is a GUI. Nothing about it can be reviewed in a pull request.
So the repo owns everything that can be text: the numbers, the schema, the
measures and the page blueprints. This module is the first of those. It runs the
existing engine over a borrower list and writes a small star schema of csv files
that a Power BI semantic model imports.

Nothing here computes a CBAM number. Every cost, band, flag and cash flow comes
from tiering.py, cost.py, flags.py and liquidity.py, which read config/*.yaml.
This module only decides what shape the answers are written in. If a formula
needs changing, it changes in the engine and this file does not move.

Tables written to the output directory:

    dim_borrower.csv    one row per borrower
    dim_scenario.csv    one row per carbon-price scenario
    dim_branch.csv      one row per rule branch, adopted or proposal
    dim_year.csv        one row per year on the axis
    fact_cost.csv       borrower x year x scenario x branch
    fact_liquidity.csv  borrower x scenario x branch x payment
    fact_flags.csv      borrower x flag, active true or false
    meta.csv            key and value, including the data label and file hashes

Two honest limits are stamped into the output rather than hidden:

    Tier 4 cost needs an upstream sector uplift that portfolio.py computes, and
    portfolio.py does not exist yet (CLAUDE.md Task 9). Tier 4 rows therefore
    carry a cost of zero and a cost_basis that says so. A zero here means "not
    computed", not "no exposure".

    config/scenarios.yaml is gated, because the NGFS Phase V EU prices are not
    published in any fetchable document. Without --prices the exporter fails
    loudly rather than writing zeros. With --prices it stamps the price file and
    its hash into meta.csv and refuses any data label other than FIXTURE.

Command line:

    python -m src.export_powerbi \
        --borrowers data/powerbi_fixture/borrowers_fixture.csv \
        --config-dir tests/fixtures \
        --prices data/powerbi_fixture/prices_fixture.yaml \
        --out data/powerbi_fixture/export

When the gates clear, drop --prices and --config-dir and the same command runs
on the real config with no change to this file.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.config import CONFIG_FILES, Config, ConfigError, load_config, load_yaml_mapping
from src.cost import CostResult, borrower_cost
from src.flags import band_order, evaluate_flags, materiality_band
from src.liquidity import liquidity_profile, shock_year
from src.schema import Borrower, parse_borrowers
from src.tiering import ADOPTED_BRANCH, TierResult, assign_tier

REPO_ROOT = Path(__file__).resolve().parents[1]

# The cost path CLAUDE.md asks for, 2026 to 2035 inclusive.
DEFAULT_FIRST_YEAR = 2026
DEFAULT_LAST_YEAR = 2035

# The five plain-language tier labels from CLAUDE.md Task 10, Borrowers page.
# CLAUDE.md section 9 forbids showing a tier number where a label exists, so the
# label travels with every row rather than being rebuilt in DAX.
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

DATA_LABELS = ("FIXTURE", "SYNTHETIC", "UPLOADED")

TABLE_ORDER = (
    "dim_borrower",
    "dim_scenario",
    "dim_branch",
    "dim_year",
    "fact_cost",
    "fact_liquidity",
    "fact_flags",
)

_QUARTER_IN_LABEL = re.compile(r"Q(\d+)\s*$")


class ExportError(ValueError):
    """The export cannot be produced, and the reason is the caller's to fix."""


# ---------------------------------------------------------------------------
# Reading inputs
# ---------------------------------------------------------------------------


def read_borrower_rows(path: Path) -> list[dict[str, Any]]:
    """Read a borrower csv, ignoring comment lines that start with #.

    Fixture files carry a FIXTURE header comment. csv.DictReader has no comment
    support, so the comment lines are stripped before parsing. A borrower whose
    id begins with # would be dropped, which is why ids are checked for it.
    """
    if not path.is_file():
        raise ExportError(f"borrower file not found: {path}")
    text = path.read_text(encoding="utf-8-sig")
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    if not lines:
        raise ExportError(f"{path.name} has no rows once comment lines are removed")
    reader = csv.DictReader(lines)
    return [dict(row) for row in reader]


def load_price_override(path: Path) -> dict[str, Any]:
    """Load a scenarios-shaped yaml file to stand in for config/scenarios.yaml."""
    data = load_yaml_mapping(path)
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, dict) or not scenarios:
        raise ExportError(
            f"{path.name}: a price override must have a non-empty scenarios block, "
            f"shaped like config/scenarios.yaml"
        )
    for key, block in scenarios.items():
        by_year = (block or {}).get("by_year") or {}
        if not by_year:
            raise ExportError(
                f"{path.name}: scenario {key!r} has no prices in by_year, so it "
                f"cannot stand in for the gated file"
            )
    return data


def config_with_prices(config: Config, prices: Mapping[str, Any]) -> Config:
    """Return the same config with its scenarios block replaced."""
    return dataclasses.replace(config, scenarios=dict(prices))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def engine_version() -> str:
    """Version from pyproject.toml, so it is stated in one place only."""
    pyproject = REPO_ROOT / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return "unknown"
    return str((data.get("project") or {}).get("version") or "unknown")


def engine_commit() -> str:
    """Short git commit of the checkout, or a plain statement that there is none."""
    try:
        finished = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown, git could not be run"
    if finished.returncode != 0:
        return "unknown, not a git checkout"
    return finished.stdout.strip() or "unknown"


# ---------------------------------------------------------------------------
# Small helpers for writing values a csv reader can trust
# ---------------------------------------------------------------------------


def _money(value: float | None) -> str:
    return "" if value is None else str(round(float(value), 2))


def _ratio(value: float | None) -> str:
    """Ratios and shares at full round-trip precision, deliberately not rounded.

    A materiality ratio decides which band a borrower lands in, and the bands
    have hard edges at 1, 3, 6 and 12 percent. Rounding a ratio before writing
    it can move a borrower across an edge, so the file would disagree with the
    band next to it in the same row. repr gives the shortest text that reads
    back as the same float, so the csv and the engine always agree.
    """
    return "" if value is None else repr(float(value))


def _tonnes(value: float | None) -> str:
    return "" if value is None else str(round(float(value), 6))


def _flag(value: bool) -> str:
    """Booleans as lowercase text, which Power Query reads with Logical.FromText."""
    return "true" if value else "false"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


# ---------------------------------------------------------------------------
# Building the tables
# ---------------------------------------------------------------------------


def _cost_basis(result: CostResult) -> str:
    if result.tier in (2, 3):
        return "below_mass_threshold" if result.below_threshold else "direct_obligation"
    if result.tier == 1:
        return "lost_allocation" if result.lines else "lost_allocation_no_production"
    if result.tier == 4:
        return "input_share_uplift_unavailable"
    return "out_of_scope"


def build_dim_year(years: Sequence[int], payment_years: Iterable[int], config: Config) -> list[dict[str, str]]:
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

    The tier here is the tier under one branch, named in meta.csv as
    tier_branch. Tier 3 exists only where the downstream-extension proposal is
    switched on, so on the adopted branch no borrower carries it. The tier that
    varies by branch lives on fact_cost, which has a branch column.
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


def _cost_grid(
    borrowers: Sequence[Borrower],
    config: Config,
    years: Sequence[int],
    scenarios: Sequence[str],
    branches: Sequence[str],
) -> tuple[list[dict[str, str]], dict[tuple[str, str, str], dict[int, float]]]:
    """Run the engine across every borrower, year, scenario and branch.

    Returns the fact_cost rows and, alongside them, the cost by obligation year
    for each borrower, scenario and branch, which the liquidity model needs.
    """
    rows: list[dict[str, str]] = []
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
                    basis = _cost_basis(result)
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
                paths[(borrower.borrower_id, scenario, branch)] = by_year
    return rows, paths


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
            share = (
                flow.amount_eur / capital if capital and capital > 0 else None
            )
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
    The reference point is written into every row and into meta.csv, so a chart
    built on this table can always say which year it is describing.

    The flag list comes from thresholds.yaml, not from a list in this file, so a
    new flag in config appears here without a code change.
    """
    codes = sorted((config.thresholds.get("flags") or {}).keys())
    if not codes:
        raise ExportError("thresholds.yaml has no flags block, so no flags can be exported")

    rows: list[dict[str, str]] = []
    for borrower in borrowers:
        tier = assign_tier(borrower, config, branch)
        result = borrower_cost(
            borrower, config, year=year, scenario=scenario, branch=branch, tier=tier
        )
        band = materiality_band(result.cost_eur, borrower.ebitda_eur, config)
        raised = {
            flag.code: " ".join(flag.reason.split())
            for flag in evaluate_flags(borrower, result, config, tier=tier, materiality=band)
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


# ---------------------------------------------------------------------------
# The export as a whole
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ExportRequest:
    """Everything the export needs, resolved from the command line or a test."""

    borrowers_path: Path
    out_dir: Path
    config_dir: Path
    prices_path: Path | None = None
    data_label: str = "FIXTURE"
    first_year: int = DEFAULT_FIRST_YEAR
    last_year: int = DEFAULT_LAST_YEAR
    default_scenario: str = "delayed_transition"
    default_branch: str = ADOPTED_BRANCH
    tier_branch: str = ADOPTED_BRANCH
    flag_year: int | None = None
    flag_scenario: str | None = None
    flag_branch: str | None = None

    @property
    def years(self) -> list[int]:
        return list(range(self.first_year, self.last_year + 1))


@dataclasses.dataclass(frozen=True)
class ExportResult:
    """What was written, so a caller can assert on it without reading files."""

    tables: dict[str, list[dict[str, str]]]
    meta: list[dict[str, str]]
    out_dir: Path
    written: list[Path]

    def row_count(self, table: str) -> int:
        return len(self.tables[table])


def _validate_request(request: ExportRequest, config: Config) -> None:
    if request.data_label not in DATA_LABELS:
        raise ExportError(
            f"data label {request.data_label!r} is not one of {list(DATA_LABELS)}"
        )
    if request.prices_path is not None and request.data_label != "FIXTURE":
        raise ExportError(
            "a price override file stands in for the gated config/scenarios.yaml, "
            "so the only honest data label is FIXTURE. Remove --prices or set "
            "--data-label FIXTURE."
        )
    if request.first_year > request.last_year:
        raise ExportError(
            f"first year {request.first_year} is after last year {request.last_year}"
        )
    known_scenarios = set(config.scenario_keys())
    known_branches = set(config.branches())
    for name, value, known in (
        ("default scenario", request.default_scenario, known_scenarios),
        ("flag scenario", request.flag_scenario or request.default_scenario, known_scenarios),
        ("default branch", request.default_branch, known_branches),
        ("tier branch", request.tier_branch, known_branches),
        ("flag branch", request.flag_branch or request.default_branch, known_branches),
    ):
        if value not in known:
            raise ExportError(
                f"{name} {value!r} is not in the config, which has {sorted(known)}"
            )


def build_export(request: ExportRequest) -> ExportResult:
    """Run the engine and build every table, without writing anything."""
    config = load_config(request.config_dir)
    prices_meta: dict[str, str] = {}
    if request.prices_path is not None:
        prices = load_price_override(request.prices_path)
        config = config_with_prices(config, prices)
        prices_meta = {
            "price_source": str(request.prices_path.as_posix()),
            "price_source_label": _text(
                (prices.get("meta") or {}).get("label") or "fixture"
            ),
            "price_source_note": " ".join(
                _text((prices.get("meta") or {}).get("note")).split()
            ),
            "price_source_sha256": file_sha256(request.prices_path),
        }
    else:
        prices_meta = {
            "price_source": f"{request.config_dir.as_posix()}/scenarios.yaml",
            "price_source_label": "config",
            "price_source_note": "",
            "price_source_sha256": file_sha256(request.config_dir / "scenarios.yaml"),
        }

    _validate_request(request, config)

    rows = read_borrower_rows(request.borrowers_path)
    borrowers, problems = parse_borrowers(rows, config)
    if not borrowers:
        raise ExportError(
            f"no borrower row in {request.borrowers_path.name} passed validation. "
            + (" ".join(problems) if problems else "")
        )

    years = request.years
    scenarios = config.scenario_keys()
    branches = config.branches()

    try:
        cost_rows, paths = _cost_grid(borrowers, config, years, scenarios, branches)
    except ConfigError as exc:
        raise ExportError(
            f"the engine could not price the portfolio: {exc} "
            f"If config/scenarios.yaml is still gated, pass --prices with a "
            f"fixture price file. The exporter will not write a zero in place of "
            f"a price it does not have."
        ) from exc

    liquidity_rows = build_fact_liquidity(borrowers, config, paths)
    flag_year = request.flag_year if request.flag_year is not None else shock_year(config)
    flag_scenario = request.flag_scenario or request.default_scenario
    flag_branch = request.flag_branch or request.default_branch
    if flag_year not in years:
        raise ExportError(
            f"flag reference year {flag_year} is outside the exported years "
            f"{years[0]} to {years[-1]}"
        )
    flag_rows = build_fact_flags(borrowers, config, flag_year, flag_scenario, flag_branch)

    tier_results = {
        borrower.borrower_id: assign_tier(borrower, config, request.tier_branch)
        for borrower in borrowers
    }
    payment_years = {int(row["year"]) for row in liquidity_rows}

    tables: dict[str, list[dict[str, str]]] = {
        "dim_borrower": build_dim_borrower(borrowers, tier_results),
        "dim_scenario": build_dim_scenario(config, request.default_scenario),
        "dim_branch": build_dim_branch(config, request.default_branch),
        "dim_year": build_dim_year(years, payment_years, config),
        "fact_cost": cost_rows,
        "fact_liquidity": liquidity_rows,
        "fact_flags": flag_rows,
    }

    meta = _build_meta(
        request=request,
        config=config,
        tables=tables,
        borrowers=borrowers,
        problems=problems,
        prices_meta=prices_meta,
        flag_year=flag_year,
        flag_scenario=flag_scenario,
        flag_branch=flag_branch,
        scenarios=scenarios,
        branches=branches,
        years=years,
    )
    return ExportResult(tables=tables, meta=meta, out_dir=request.out_dir, written=[])


def _build_meta(
    *,
    request: ExportRequest,
    config: Config,
    tables: Mapping[str, list[dict[str, str]]],
    borrowers: Sequence[Borrower],
    problems: Sequence[str],
    prices_meta: Mapping[str, str],
    flag_year: int,
    flag_scenario: str,
    flag_branch: str,
    scenarios: Sequence[str],
    branches: Sequence[str],
    years: Sequence[int],
) -> list[dict[str, str]]:
    """Key and value rows describing the run.

    Long form rather than one wide row, because the number of config files can
    change and a Power BI card reads a key just as easily as a column.
    """
    entries: list[tuple[str, str]] = [
        ("generated_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ("engine_version", engine_version()),
        ("engine_commit", engine_commit()),
        ("data_label", request.data_label),
        ("borrower_source", request.borrowers_path.as_posix()),
        ("borrower_count", str(len(borrowers))),
        ("borrower_rows_rejected", str(len(problems))),
        ("config_dir", request.config_dir.as_posix()),
        ("years", f"{years[0]} to {years[-1]}"),
        ("scenarios", ", ".join(scenarios)),
        ("branches", ", ".join(branches)),
        ("default_scenario", request.default_scenario),
        ("default_branch", request.default_branch),
        ("tier_branch_for_dim_borrower", request.tier_branch),
        ("flag_reference_year", str(flag_year)),
        ("flag_reference_scenario", flag_scenario),
        ("flag_reference_branch", flag_branch),
        ("liquidity_shock_year", str(shock_year(config))),
    ]
    entries.extend(sorted(prices_meta.items()))

    for name in sorted(CONFIG_FILES.values()):
        path = request.config_dir / name
        if path.is_file():
            entries.append((f"config_sha256_{name}", file_sha256(path)))

    for table in TABLE_ORDER:
        entries.append((f"rows_{table}", str(len(tables[table]))))

    entries.append(
        (
            "tier_4_cost_note",
            "Tier 4 cost needs the upstream sector uplift from portfolio.py, which "
            "is not built yet (CLAUDE.md Task 9). Tier 4 rows carry a cost of zero "
            "and cost_basis input_share_uplift_unavailable. Zero means not "
            "computed, not no exposure.",
        )
    )
    entries.append(
        (
            "materiality_note",
            "Materiality bands are the author's assumptions in thresholds.yaml, "
            "not law and not supervisory guidance.",
        )
    )
    if request.data_label == "FIXTURE":
        entries.append(
            (
                "fixture_warning",
                "FIXTURE DATA. Invented borrowers and invented flat prices. Not a "
                "bank's book, not a forecast, not anyone's exposure.",
            )
        )
    return [{"key": key, "value": value} for key, value in entries]


def write_export(result: ExportResult) -> ExportResult:
    """Write every table to the output directory as a csv."""
    result.out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for table in TABLE_ORDER:
        written.append(_write_csv(result.out_dir / f"{table}.csv", result.tables[table]))
    written.append(_write_csv(result.out_dir / "meta.csv", result.meta))
    return dataclasses.replace(result, written=written)


def _write_csv(path: Path, rows: Sequence[Mapping[str, str]]) -> Path:
    if not rows:
        raise ExportError(f"{path.name}: refusing to write a table with no rows")
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def export(request: ExportRequest) -> ExportResult:
    """Build and write, which is what the command line does."""
    return write_export(build_export(request))


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.export_powerbi",
        description=(
            "Export the CBAM screening engine's results as a star schema of csv "
            "files for Power BI. Costs, bands, flags and cash flows come from the "
            "engine and its config. Nothing is recomputed here."
        ),
    )
    parser.add_argument("--borrowers", required=True, type=Path, help="borrower csv")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=REPO_ROOT / "config",
        help="directory of the five config yaml files, default config/",
    )
    parser.add_argument(
        "--prices",
        type=Path,
        default=None,
        help=(
            "a scenarios-shaped yaml file to use instead of the config's "
            "scenarios.yaml, for while that file is gated. Forces the FIXTURE "
            "data label."
        ),
    )
    parser.add_argument(
        "--data-label",
        choices=DATA_LABELS,
        default="FIXTURE",
        help="what the data is, stamped into meta.csv and onto every report page",
    )
    parser.add_argument("--first-year", type=int, default=DEFAULT_FIRST_YEAR)
    parser.add_argument("--last-year", type=int, default=DEFAULT_LAST_YEAR)
    parser.add_argument("--default-scenario", default="delayed_transition")
    parser.add_argument("--default-branch", default=ADOPTED_BRANCH)
    parser.add_argument(
        "--tier-branch",
        default=ADOPTED_BRANCH,
        help="which branch's tier is written onto dim_borrower",
    )
    parser.add_argument(
        "--flag-year",
        type=int,
        default=None,
        help="year the flags are evaluated in, default the liquidity shock year",
    )
    parser.add_argument("--flag-scenario", default=None)
    parser.add_argument("--flag-branch", default=None)
    return parser


def request_from_args(args: argparse.Namespace) -> ExportRequest:
    return ExportRequest(
        borrowers_path=args.borrowers,
        out_dir=args.out,
        config_dir=args.config_dir,
        prices_path=args.prices,
        data_label=args.data_label,
        first_year=args.first_year,
        last_year=args.last_year,
        default_scenario=args.default_scenario,
        default_branch=args.default_branch,
        tier_branch=args.tier_branch,
        flag_year=args.flag_year,
        flag_scenario=args.flag_scenario,
        flag_branch=args.flag_branch,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = export(request_from_args(args))
    except (ExportError, ConfigError) as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 1
    label = next(
        (row["value"] for row in result.meta if row["key"] == "data_label"), "unknown"
    )
    print(f"wrote {len(result.written)} files to {result.out_dir} [{label}]")
    for table in TABLE_ORDER:
        print(f"  {table}.csv  {len(result.tables[table])} rows")
    print(f"  meta.csv  {len(result.meta)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
