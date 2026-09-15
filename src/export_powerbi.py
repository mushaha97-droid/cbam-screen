"""Star-schema export for Power BI.

Power BI Desktop is a GUI. Nothing about it can be reviewed in a pull request.
So the repo owns everything that can be text: the numbers, the schema, the
measures and the page blueprints. This module is the first of those. It runs the
existing engine over a borrower list and writes a small star schema of csv files
that a Power BI semantic model imports.

Nothing here computes a CBAM number, and since the web app was built, nothing
here shapes one either. Every cost, band, flag and cash flow comes from
tiering.py, cost.py, flags.py and liquidity.py through src/engine_api.py, which
is the single entrypoint the exporter, the tests and the in-browser run all
share. This module is now only a command line, a meta block and a csv writer
around engine_api. If a formula or a table shape needs changing, it changes
there and this file does not move.

Tables written to the output directory:

    dim_borrower.csv    one row per borrower
    dim_scenario.csv    one row per carbon-price scenario
    dim_branch.csv      one row per rule branch, adopted or proposal
    dim_year.csv        one row per year on the axis
    fact_cost.csv       borrower x year x scenario x branch
    fact_cost_line.csv  the same, broken down to one row per good group, so the
                        formula can be shown with the borrower's own numbers
    fact_liquidity.csv  borrower x scenario x branch x payment
    fact_flags.csv      borrower x flag, active true or false
    questions.csv       flag x question, from config/questions.yaml
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
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.config import CONFIG_FILES, Config, ConfigError, load_config
from src.engine_api import (
    CHARGED_SHARE_LABELS,
    COST_BASIS_LABELS,
    DATA_LABELS,
    DEFAULT_FIRST_YEAR,
    DEFAULT_LAST_YEAR,
    TABLE_ORDER,
    TIER_LABEL_PLAIN,
    EngineApiError,
    _charged_share,
    _cost_basis,
    _cost_grid,
    _flag,
    _money,
    _ratio,
    _text,
    _tonnes,
    build_dim_borrower,
    build_dim_branch,
    build_dim_scenario,
    build_dim_year,
    build_fact_flags,
    build_fact_liquidity,
    build_questions,
    config_with_prices,
    file_sha256,
    load_price_override,
)
from src.liquidity import shock_year
from src.schema import Borrower, parse_borrowers
from src.tiering import ADOPTED_BRANCH, assign_tier

REPO_ROOT = Path(__file__).resolve().parents[1]

# Everything above that is imported rather than defined was defined in this file
# until the web app needed it too. It moved to src/engine_api.py so that the
# browser, the exporter and pytest run one implementation. The names are
# re-exported here unchanged, so an existing caller of this module sees no
# difference.


class ExportError(EngineApiError):
    """The export cannot be produced, and the reason is the caller's to fix.

    A subclass of EngineApiError, so a caller that catches the engine's own
    error also catches this one and the command line needs one except clause.
    """


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


def _repo_relative(path: Path) -> str:
    """A path inside the repo, written the way the other meta rows write one."""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


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
        cost_rows, cost_line_rows, paths = _cost_grid(
            borrowers, config, years, scenarios, branches
        )
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
    question_table, questions_file = build_questions(config, request.config_dir)

    tables: dict[str, list[dict[str, str]]] = {
        "dim_borrower": build_dim_borrower(borrowers, tier_results),
        "dim_scenario": build_dim_scenario(config, request.default_scenario),
        "dim_branch": build_dim_branch(config, request.default_branch),
        "dim_year": build_dim_year(years, payment_years, config),
        "fact_cost": cost_rows,
        "fact_cost_line": cost_line_rows,
        "fact_liquidity": liquidity_rows,
        "fact_flags": flag_rows,
        "questions": question_table,
    }

    meta = _build_meta(
        questions_file=questions_file,
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
    questions_file: Path,
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
    entries.append(("questions_source", _repo_relative(questions_file)))
    entries.append(("questions_sha256", file_sha256(questions_file)))

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
    except (EngineApiError, ConfigError) as exc:
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
