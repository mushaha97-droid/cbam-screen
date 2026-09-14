"""Tests for the Power BI star-schema exporter.

The exporter is a shape, not a formula. So these tests check three things:

  the shape is complete, that is every borrower reaches every table it should
  the numbers are the engine's numbers, checked against the hand example and
    against thresholds.yaml rather than against the exporter's own arithmetic
  the gates hold, that is a gated price file fails loudly and a fixture price
    file cannot be labelled as anything but FIXTURE

The fixture portfolio in data/powerbi_fixture/ is deliberately small enough that
every row count below can be worked out on paper.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.config import Config
from src.export_powerbi import (
    TIER_LABEL_PLAIN,
    ExportError,
    ExportRequest,
    build_export,
    export,
    read_borrower_rows,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"
PORTFOLIO = REPO_ROOT / "data" / "powerbi_fixture" / "borrowers_fixture.csv"
PRICES = REPO_ROOT / "data" / "powerbi_fixture" / "prices_fixture.yaml"

YEARS = list(range(2026, 2036))
SCENARIOS = ["current_policies", "delayed_transition", "net_zero_2050"]
BRANCHES = ["adopted", "proposal"]


def make_request(out_dir: Path) -> ExportRequest:
    return ExportRequest(
        borrowers_path=PORTFOLIO,
        out_dir=out_dir,
        config_dir=FIXTURE_DIR,
        prices_path=PRICES,
        data_label="FIXTURE",
    )


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory):
    """One build, reused. Nothing in these tests mutates it."""
    out = tmp_path_factory.mktemp("export")
    return build_export(make_request(out))


def meta_value(result, key: str) -> str:
    for row in result.meta:
        if row["key"] == key:
            return row["value"]
    raise AssertionError(f"meta.csv has no key {key!r}")


# ---------------------------------------------------------------------------
# Reading the fixture portfolio
# ---------------------------------------------------------------------------


def test_comment_lines_in_the_fixture_csv_are_not_borrowers() -> None:
    rows = read_borrower_rows(PORTFOLIO)
    assert len(rows) == 13
    assert all(not row["borrower_id"].startswith("#") for row in rows)
    assert rows[0]["borrower_id"] == "B001"


def test_the_fixture_portfolio_is_labelled_fixture_in_the_file_itself() -> None:
    """The label must survive someone opening the csv without the docs."""
    first_line = PORTFOLIO.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("#")
    assert "FIXTURE" in first_line.upper()
    assert "fixture_not_a_forecast" in PRICES.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Row counts. Each one is a multiplication that can be checked on paper.
# ---------------------------------------------------------------------------


def test_dimension_row_counts(result) -> None:
    assert result.row_count("dim_borrower") == 13
    assert result.row_count("dim_scenario") == 3
    assert result.row_count("dim_branch") == 2
    # 2026 to 2035 are obligation years, and a 2035 obligation settles in 2036.
    assert result.row_count("dim_year") == 11


def test_fact_cost_is_a_full_cross_join(result) -> None:
    assert result.row_count("fact_cost") == 13 * 10 * 3 * 2 == 780
    keys = {
        (row["borrower_id"], row["year"], row["scenario"], row["branch"])
        for row in result.tables["fact_cost"]
    }
    assert len(keys) == 780


def test_fact_flags_is_one_row_per_borrower_and_flag(
    result, fixture_config: Config
) -> None:
    codes = sorted(fixture_config.thresholds["flags"].keys())
    assert result.row_count("fact_flags") == 13 * len(codes)
    pairs = {(row["borrower_id"], row["flag"]) for row in result.tables["fact_flags"]}
    assert len(pairs) == 13 * len(codes)


def test_every_borrower_reaches_every_table_that_should_hold_it(result) -> None:
    ids = {row["borrower_id"] for row in result.tables["dim_borrower"]}
    assert len(ids) == 13
    assert {row["borrower_id"] for row in result.tables["fact_cost"]} == ids
    assert {row["borrower_id"] for row in result.tables["fact_flags"]} == ids
    # fact_liquidity is deliberately not a cross join. A borrower that owes
    # nothing in any year has no payments, and inventing a zero payment row
    # would make the liquidity page look like it had something to say.
    paying = {row["borrower_id"] for row in result.tables["fact_liquidity"]}
    assert paying <= ids
    assert "B001" in paying
    assert "B013" not in paying  # tier 0, no imports, nothing to pay


def test_dim_year_covers_every_year_used_by_a_fact(result) -> None:
    years = {int(row["year"]) for row in result.tables["dim_year"]}
    assert {int(row["year"]) for row in result.tables["fact_cost"]} <= years
    assert {int(row["year"]) for row in result.tables["fact_liquidity"]} <= years
    shock = [row["year"] for row in result.tables["dim_year"] if row["is_liquidity_shock_year"] == "true"]
    assert shock == ["2027"]


# ---------------------------------------------------------------------------
# The hand example, written out literally
# ---------------------------------------------------------------------------


def test_the_claude_md_hand_example_appears_in_fact_cost(result) -> None:
    """1,000 t steel * 1.9 tCO2/t * price 80 * (1 - 0.975) = 3,800 EUR.

    CLAUDE.md Task 6 states this example. The fixture price file sets Delayed
    Transition to a flat 80 so the exported table reproduces it exactly, which
    means a wrong price, a wrong factor or a wrong free-allocation share all
    show up here rather than in a chart the owner has to eyeball.
    """
    rows = [
        row
        for row in result.tables["fact_cost"]
        if row["borrower_id"] == "B001"
        and row["year"] == "2026"
        and row["scenario"] == "delayed_transition"
        and row["branch"] == "adopted"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert float(row["cost_eur"]) == 3800.0
    assert float(row["price_eur"]) == 80.0
    assert float(row["free_allocation_share"]) == 0.975
    assert float(row["counted_tonnes"]) == 1000.0
    assert row["tier"] == "2"
    assert row["cost_basis"] == "direct_obligation"


# ---------------------------------------------------------------------------
# Bands come from thresholds.yaml, not from the exporter
# ---------------------------------------------------------------------------


def band_from_yaml(ratio: float | None, thresholds: dict) -> str:
    """Independent band lookup, so the test does not reuse the engine's code."""
    block = thresholds["materiality_bands"]
    if ratio is None:
        return str(block["negative_ebitda_band"])
    for entry in block["bands"]:
        low = float(entry["min_ratio"])
        high = entry["max_ratio"]
        if ratio >= low and (high is None or ratio < float(high)):
            return str(entry["band"])
    raise AssertionError(f"no band covers ratio {ratio}")


def test_every_band_matches_the_thresholds_file(result, fixture_config: Config) -> None:
    for row in result.tables["fact_cost"]:
        ratio = float(row["materiality_ratio"]) if row["materiality_ratio"] else None
        assert row["band"] == band_from_yaml(ratio, fixture_config.thresholds), row


def test_a_negative_ebitda_borrower_gets_the_configured_band(result) -> None:
    """B012 has negative EBITDA, so there is no ratio and the band is by rule."""
    rows = [r for r in result.tables["fact_cost"] if r["borrower_id"] == "B012"]
    assert rows
    assert all(row["materiality_ratio"] == "" for row in rows)
    assert {row["band"] for row in rows} == {"H"}


def test_the_ratio_is_cost_over_ebitda(result) -> None:
    ebitda = {
        row["borrower_id"]: float(row["ebitda_eur"])
        for row in result.tables["dim_borrower"]
    }
    for row in result.tables["fact_cost"]:
        if not row["materiality_ratio"]:
            continue
        expected = float(row["cost_eur"]) / ebitda[row["borrower_id"]]
        assert float(row["materiality_ratio"]) == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# Tiers and their plain-language labels
# ---------------------------------------------------------------------------


def test_tier_labels_are_the_five_plain_language_labels(result) -> None:
    assert set(TIER_LABEL_PLAIN.values()) == {
        "Not affected",
        "Makes CBAM goods in the EU",
        "Imports CBAM goods",
        "May be caught from 2028",
        "Pays via input prices",
    }
    for table in ("dim_borrower", "fact_cost"):
        for row in result.tables[table]:
            label = row["tier_label_plain"]
            assert label in set(TIER_LABEL_PLAIN.values())
            # CLAUDE.md section 9: no tier numbers where a label exists.
            assert "ier" not in label or "Tier" not in label


def test_the_fixture_portfolio_covers_every_tier(result) -> None:
    """Tier 3 exists only where the downstream-extension proposal is switched on."""
    adopted = {
        row["tier"] for row in result.tables["fact_cost"] if row["branch"] == "adopted"
    }
    proposal = {
        row["tier"] for row in result.tables["fact_cost"] if row["branch"] == "proposal"
    }
    assert adopted == {"0", "1", "2", "4"}
    assert proposal == {"0", "1", "2", "3", "4"}
    assert adopted | proposal == {"0", "1", "2", "3", "4"}


def test_the_branch_changes_the_tier_for_a_downstream_borrower(result) -> None:
    """B009 is out of scope under adopted law and Tier 3 under the proposal."""
    tiers = {
        row["branch"]: row["tier"]
        for row in result.tables["fact_cost"]
        if row["borrower_id"] == "B009" and row["year"] == "2028"
        and row["scenario"] == "delayed_transition"
    }
    assert tiers == {"adopted": "0", "proposal": "3"}


def test_a_tier_4_cost_says_it_is_not_computed_rather_than_showing_a_bare_zero(
    result,
) -> None:
    rows = [r for r in result.tables["fact_cost"] if r["tier"] == "4"]
    assert rows
    assert all(float(row["cost_eur"]) == 0.0 for row in rows)
    assert all(row["cost_basis"] == "input_share_uplift_unavailable" for row in rows)
    assert "not computed" in meta_value(result, "tier_4_cost_note")


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def test_every_flag_in_config_is_exported_and_at_least_one_is_active(
    result, fixture_config: Config
) -> None:
    codes = set(fixture_config.thresholds["flags"].keys())
    exported = {row["flag"] for row in result.tables["fact_flags"]}
    assert exported == codes
    active = {row["flag"] for row in result.tables["fact_flags"] if row["active"] == "true"}
    assert active == codes, f"fixture portfolio never raises {sorted(codes - active)}"


def test_rating_gap_and_monitor_are_both_present(result) -> None:
    active = {
        (row["borrower_id"], row["flag"])
        for row in result.tables["fact_flags"]
        if row["active"] == "true"
    }
    assert ("B002", "RATING_GAP") in active
    assert ("B012", "RATING_GAP") in active
    assert ("B003", "MONITOR") in active


def test_an_active_flag_always_carries_a_reason(result) -> None:
    for row in result.tables["fact_flags"]:
        if row["active"] == "true":
            assert row["reason"].strip(), row
        else:
            assert row["reason"] == ""


def test_the_flag_reference_point_is_recorded_on_every_row(result) -> None:
    for row in result.tables["fact_flags"]:
        assert row["evaluated_year"] == "2027"
        assert row["evaluated_scenario"] == "delayed_transition"
        assert row["evaluated_branch"] == "adopted"
    assert meta_value(result, "flag_reference_year") == "2027"


# ---------------------------------------------------------------------------
# Liquidity
# ---------------------------------------------------------------------------


def test_the_2026_obligation_is_one_block_paid_in_2027(result) -> None:
    blocks = [
        row
        for row in result.tables["fact_liquidity"]
        if row["obligation_year"] == "2026" and row["borrower_id"] == "B001"
    ]
    assert blocks
    for row in blocks:
        assert row["kind"] == "block"
        assert row["year"] == "2027"
        assert row["quarter"] == ""


def test_a_quarterly_obligation_has_four_instalments_and_a_settlement(result) -> None:
    rows = [
        row
        for row in result.tables["fact_liquidity"]
        if row["borrower_id"] == "B001"
        and row["scenario"] == "delayed_transition"
        and row["branch"] == "adopted"
        and row["obligation_year"] == "2027"
    ]
    kinds = sorted(row["kind"] for row in rows)
    assert kinds == ["prepayment"] * 4 + ["settlement"]
    assert sorted(row["quarter"] for row in rows) == ["", "1", "2", "3", "4"]


def test_cash_out_adds_up_to_the_cost_path(result) -> None:
    """Every euro of cost leaves the borrower exactly once."""
    key = ("B001", "delayed_transition", "adopted")
    cost = sum(
        float(row["cost_eur"])
        for row in result.tables["fact_cost"]
        if (row["borrower_id"], row["scenario"], row["branch"]) == key
    )
    cash = sum(
        float(row["cash_out_eur"])
        for row in result.tables["fact_liquidity"]
        if (row["borrower_id"], row["scenario"], row["branch"]) == key
    )
    assert cash == pytest.approx(cost, rel=1e-6)


def test_no_payment_is_negative(result) -> None:
    assert all(
        float(row["cash_out_eur"]) >= 0 for row in result.tables["fact_liquidity"]
    )


def test_share_of_working_capital_is_blank_when_no_working_capital_is_known(
    result,
) -> None:
    known = {
        row["borrower_id"]
        for row in result.tables["dim_borrower"]
        if row["working_capital_eur"]
    }
    for row in result.tables["fact_liquidity"]:
        if row["borrower_id"] in known:
            assert row["share_of_working_capital"] != ""
        else:
            assert row["share_of_working_capital"] == ""


# ---------------------------------------------------------------------------
# meta.csv
# ---------------------------------------------------------------------------


def test_meta_records_the_label_the_price_source_and_the_config_hashes(result) -> None:
    assert meta_value(result, "data_label") == "FIXTURE"
    assert meta_value(result, "price_source").endswith("prices_fixture.yaml")
    assert meta_value(result, "price_source_label") == "fixture_not_a_forecast"
    assert len(meta_value(result, "price_source_sha256")) == 64
    for name in (
        "cbam_rules.yaml",
        "emission_defaults.yaml",
        "scenarios.yaml",
        "nace_tiers.yaml",
        "thresholds.yaml",
    ):
        assert len(meta_value(result, f"config_sha256_{name}")) == 64
    assert meta_value(result, "engine_version")
    assert meta_value(result, "borrower_count") == "13"
    assert "FIXTURE DATA" in meta_value(result, "fixture_warning")


def test_meta_row_counts_match_the_tables(result) -> None:
    for table, rows in result.tables.items():
        assert meta_value(result, f"rows_{table}") == str(len(rows))


# ---------------------------------------------------------------------------
# The gates
# ---------------------------------------------------------------------------


def test_a_fixture_price_file_cannot_be_labelled_as_real_data(tmp_path: Path) -> None:
    request = ExportRequest(
        borrowers_path=PORTFOLIO,
        out_dir=tmp_path,
        config_dir=FIXTURE_DIR,
        prices_path=PRICES,
        data_label="UPLOADED",
    )
    with pytest.raises(ExportError, match="only honest data label is FIXTURE"):
        build_export(request)


def test_the_gated_real_scenarios_file_fails_loudly_instead_of_writing_zeros(
    tmp_path: Path,
) -> None:
    """config/scenarios.yaml carries no prices. The export must stop, not guess."""
    request = ExportRequest(
        borrowers_path=PORTFOLIO,
        out_dir=tmp_path,
        config_dir=REPO_ROOT / "config",
        prices_path=None,
        data_label="UPLOADED",
    )
    with pytest.raises(ExportError, match="--prices"):
        build_export(request)
    assert not list(tmp_path.iterdir())


def test_an_unknown_scenario_name_is_rejected(tmp_path: Path) -> None:
    request = ExportRequest(
        borrowers_path=PORTFOLIO,
        out_dir=tmp_path,
        config_dir=FIXTURE_DIR,
        prices_path=PRICES,
        default_scenario="orderly_2050",
    )
    with pytest.raises(ExportError, match="default scenario"):
        build_export(request)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def test_export_writes_eight_readable_csv_files(tmp_path: Path) -> None:
    written = export(make_request(tmp_path))
    names = sorted(path.name for path in written.written)
    assert names == [
        "dim_borrower.csv",
        "dim_branch.csv",
        "dim_scenario.csv",
        "dim_year.csv",
        "fact_cost.csv",
        "fact_flags.csv",
        "fact_liquidity.csv",
        "meta.csv",
    ]
    for path in written.written:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows, f"{path.name} is empty"
        assert all(None not in row for row in rows), f"{path.name} has ragged rows"


def test_written_files_match_the_built_tables(tmp_path: Path) -> None:
    written = export(make_request(tmp_path))
    for table, rows in written.tables.items():
        with (tmp_path / f"{table}.csv").open(encoding="utf-8", newline="") as handle:
            on_disk = list(csv.DictReader(handle))
        assert len(on_disk) == len(rows)
        assert on_disk[0] == rows[0]


def test_booleans_are_written_as_lowercase_text(result) -> None:
    """Power Query reads these with Logical.FromText, which wants true or false."""
    for row in result.tables["fact_cost"]:
        assert row["below_threshold"] in {"true", "false"}
        assert row["estimated"] in {"true", "false"}
