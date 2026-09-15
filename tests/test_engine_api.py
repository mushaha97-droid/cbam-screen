"""Tests for src/engine_api.py, the one entrypoint into the engine.

This file matters more than its size suggests. engine_api.screen_json is the
exact function the web page calls inside Pyodide, with the exact payload shape
the page sends. So every assertion below is also an assertion about what a user
sees in a browser, and a formula that drifted in the browser would have to drift
here first.

Three things are checked:

    the hand example from CLAUDE.md Task 6 comes back out of the JSON entrypoint
    unchanged, along with the threshold cases either side of it

    a borrower with only the seven required columns gets a full result and is
    marked estimated, which is the CLAUDE.md section 8 definition-of-done item

    the price gate degrades honestly. With no price set, nothing priced is
    reported at all, and every flag that does not depend on a price is raised
    exactly as it would be with one.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.config import load_config
from src.engine_api import (
    PRICE_DEPENDENT_FLAGS,
    TIER_LABEL_PLAIN,
    ScreenRequest,
    config_provenance,
    has_prices,
    screen,
    screen_json,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"
REAL_CONFIG_DIR = REPO_ROOT / "config"
PRICES = REPO_ROOT / "data" / "powerbi_fixture" / "prices_fixture.yaml"

# The CLAUDE.md Task 6 hand example, written as a borrower row:
# 1,000 t steel * 1.9 tCO2/t * price 80 * (1 - 0.975) = 3,800 EUR in 2026.
# The factor is the fixture default for steel and the price is the fixture
# Delayed Transition price, so the whole line is checkable by hand.
HAND_EXAMPLE = {
    "borrower_id": "B001",
    "name": "Hand Example Steel BV",
    "nace_code": "46.72",
    "country": "NL",
    "exposure_eur": 12_000_000,
    "turnover_eur": 48_000_000,
    "ebitda_eur": 4_800_000,
    "import_t_steel": 1000,
    "top_supplier_country": "IN",
    "share_top_supplier": 0.72,
    "declarant_status": "no",
    "supplier_data": "default",
    "pass_through": "low",
    "bank_transition_rating": "M",
    "working_capital_eur": 9_000_000,
}

# The same borrower under the 50 t mass threshold. Everything else is held
# constant, so the only thing that can explain a different answer is the tonnage.
BELOW_THRESHOLD = dict(HAND_EXAMPLE, borrower_id="B002", import_t_steel=40)

# Only the seven required columns from CLAUDE.md section 5. NACE 25.11 has a
# steel import intensity in the fixture sector map, so the tool has something to
# estimate from and the row must come back marked estimated.
REQUIRED_ONLY = {
    "borrower_id": "B003",
    "name": "Seven Columns BV",
    "nace_code": "25.11",
    "country": "NL",
    "exposure_eur": 5_000_000,
    "turnover_eur": 20_000_000,
    "ebitda_eur": 1_800_000,
}


def payload(rows, **overrides) -> str:
    base = {
        "config_dir": FIXTURE_DIR.as_posix(),
        "prices_path": PRICES.as_posix(),
        "borrowers": rows,
        "data_label": "UPLOADED",
    }
    base.update(overrides)
    return json.dumps(base)


def run(rows, **overrides) -> dict:
    return json.loads(screen_json(payload(rows, **overrides)))


def cost_row(result: dict, borrower_id: str, year="2026",
             scenario="delayed_transition", branch="adopted") -> dict:
    matches = [
        row
        for row in result["tables"]["fact_cost"]
        if row["borrower_id"] == borrower_id
        and row["year"] == year
        and row["scenario"] == scenario
        and row["branch"] == branch
    ]
    assert len(matches) == 1, f"expected one cost row, got {len(matches)}"
    return matches[0]


def active_flags(result: dict, borrower_id: str) -> set[str]:
    return {
        row["flag"]
        for row in result["tables"]["fact_flags"]
        if row["borrower_id"] == borrower_id and row["active"] == "true"
    }


@pytest.fixture(scope="module")
def unpriced_config_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The fixture config with the real, gated scenarios.yaml dropped into it.

    This is the state the shipped app is in until the NGFS prices arrive, so it
    is worth testing against the actual gated file rather than an invented one.
    """
    directory = tmp_path_factory.mktemp("unpriced_config")
    for path in FIXTURE_DIR.glob("*.yaml"):
        shutil.copy(path, directory / path.name)
    shutil.copy(REAL_CONFIG_DIR / "scenarios.yaml", directory / "scenarios.yaml")
    return directory


# ---------------------------------------------------------------------------
# The hand example, through the browser's own entrypoint
# ---------------------------------------------------------------------------


def test_hand_example_costs_3800_in_2026():
    result = run([HAND_EXAMPLE])
    assert result["ok"] is True
    assert result["priced"] is True
    assert result["accepted"] == 1
    assert result["rejected"] == []

    row = cost_row(result, "B001")
    assert float(row["cost_eur"]) == pytest.approx(3800.0)
    assert float(row["price_eur"]) == pytest.approx(80.0)
    assert float(row["free_allocation_share"]) == pytest.approx(0.975)
    assert row["cost_basis"] == "direct_obligation"


def test_hand_example_is_shown_as_an_importer_not_as_a_tier_number():
    row = cost_row(run([HAND_EXAMPLE]), "B001")
    assert row["tier_label_plain"] == "Imports CBAM goods"
    assert row["tier_label_plain"] == TIER_LABEL_PLAIN[2]


def test_hand_example_cost_line_multiplies_out_to_the_cost():
    """The formula box shows this line, so it has to reproduce the total."""
    result = run([HAND_EXAMPLE])
    lines = [
        row
        for row in result["tables"]["fact_cost_line"]
        if row["borrower_id"] == "B001"
        and row["year"] == "2026"
        and row["scenario"] == "delayed_transition"
        and row["branch"] == "adopted"
    ]
    assert len(lines) == 1
    line = lines[0]
    assert line["good_group"] == "steel"
    assert float(line["quantity"]) == pytest.approx(1000.0)
    assert float(line["emission_factor"]) == pytest.approx(1.9)
    product = (
        float(line["quantity"])
        * float(line["emission_factor"])
        * float(line["price_eur"])
        * float(line["charged_share"])
    )
    assert product == pytest.approx(float(line["cost_eur"]))
    assert product == pytest.approx(3800.0)


def test_hand_example_is_above_the_threshold_and_has_no_declarant():
    result = run([HAND_EXAMPLE])
    row = cost_row(result, "B001")
    assert row["below_threshold"] == "false"
    assert float(row["counted_tonnes"]) == pytest.approx(1000.0)

    flags = active_flags(result, "B001")
    assert "MONITOR" not in flags
    assert "NO_DECLARANT" in flags


def test_below_the_threshold_owes_nothing_and_is_monitored():
    result = run([BELOW_THRESHOLD])
    row = cost_row(result, "B002")
    assert float(row["cost_eur"]) == 0.0
    assert row["below_threshold"] == "true"
    assert row["cost_basis"] == "below_mass_threshold"

    flags = active_flags(result, "B002")
    assert "MONITOR" in flags
    # Nothing is owed, so there is no obligation to be unauthorised for.
    assert "NO_DECLARANT" not in flags


def test_a_below_threshold_borrower_still_reports_its_tonnage():
    """A zero with no tonnage next to it cannot be checked by a credit officer."""
    detail = run([BELOW_THRESHOLD])["borrowers"][0]
    assert detail["counted_tonnes"] == pytest.approx(40.0)
    assert detail["threshold_tonnes"] == pytest.approx(50.0)
    assert detail["below_threshold"] is True


# ---------------------------------------------------------------------------
# The seven-required-columns case, CLAUDE.md section 8
# ---------------------------------------------------------------------------


def test_seven_required_columns_give_a_full_result():
    result = run([REQUIRED_ONLY])
    assert result["ok"] is True
    assert result["accepted"] == 1
    assert result["rejected"] == []
    row = cost_row(result, "B003")
    assert row["band"] in {"L", "ML", "M", "MH", "H"}
    assert row["tier_label_plain"] in set(TIER_LABEL_PLAIN.values())


def test_seven_required_columns_are_marked_estimated_and_named():
    result = run([REQUIRED_ONLY])
    detail = result["borrowers"][0]
    assert detail["estimated"] is True
    assert "import_t_steel" in detail["estimated_fields"]
    assert "ESTIMATED_INPUTS" in active_flags(result, "B003")
    assert detail["unfilled_fields"], "fields with no sector default must be named"


def test_an_estimated_import_volume_does_not_create_a_direct_obligation():
    """A sector average says nothing about where goods came from.

    schema.py says so in a comment. This holds the browser path to it, because
    a screening tool that turned every borrower in an importing sector into a
    customs declarant would be worse than useless.
    """
    detail = run([REQUIRED_ONLY])["borrowers"][0]
    assert detail["quantities"].get("steel"), "the volume was estimated"
    assert detail["supplied_quantities"] == {}
    assert detail["tier_by_branch"]["adopted"]["tier"] != 2


def test_a_bad_row_is_rejected_with_a_reason_and_the_good_rows_survive():
    bad = dict(HAND_EXAMPLE, borrower_id="B999", turnover_eur=-1)
    result = run([HAND_EXAMPLE, bad])
    assert result["accepted"] == 1
    assert len(result["rejected"]) == 1
    assert "B999" in result["rejected"][0]


# ---------------------------------------------------------------------------
# The price gate
# ---------------------------------------------------------------------------


def test_the_shipped_scenarios_file_is_still_gated():
    """If this fails the NGFS prices arrived, and the labels should change."""
    assert has_prices(load_config(REAL_CONFIG_DIR)) is False


def test_without_prices_nothing_priced_is_reported(unpriced_config_dir: Path):
    result = run(
        [HAND_EXAMPLE], config_dir=unpriced_config_dir.as_posix(), prices_path=None
    )
    assert result["ok"] is True
    assert result["priced"] is False
    assert result["tables"]["fact_cost"] == []
    assert result["tables"]["fact_cost_line"] == []
    assert result["tables"]["fact_liquidity"] == []
    assert result["meta"]["unpriced_note"]


def test_without_prices_the_unpriced_answers_are_still_given(unpriced_config_dir: Path):
    result = run(
        [HAND_EXAMPLE], config_dir=unpriced_config_dir.as_posix(), prices_path=None
    )
    detail = result["borrowers"][0]
    assert detail["tier_by_branch"]["adopted"]["label"] == "Imports CBAM goods"
    assert detail["counted_tonnes"] == pytest.approx(1000.0)
    assert detail["below_threshold"] is False
    assert "NO_DECLARANT" in active_flags(result, "B001")
    assert result["tables"]["questions"], "the client questions need no price"


def test_without_prices_a_small_importer_is_still_monitored(unpriced_config_dir: Path):
    result = run(
        [BELOW_THRESHOLD], config_dir=unpriced_config_dir.as_posix(), prices_path=None
    )
    assert "MONITOR" in active_flags(result, "B002")


def test_price_dependent_flags_are_withheld_with_a_stated_reason(
    unpriced_config_dir: Path,
):
    result = run(
        [HAND_EXAMPLE], config_dir=unpriced_config_dir.as_posix(), prices_path=None
    )
    for code in PRICE_DEPENDENT_FLAGS:
        rows = [
            row
            for row in result["tables"]["fact_flags"]
            if row["borrower_id"] == "B001" and row["flag"] == code
        ]
        assert rows, f"{code} must still appear in the table"
        assert rows[0]["active"] == "false"
        assert "carbon price" in rows[0]["reason"]
    assert result["meta"]["withheld_flags"] == ", ".join(sorted(PRICE_DEPENDENT_FLAGS))


@pytest.mark.parametrize("row", [HAND_EXAMPLE, BELOW_THRESHOLD, REQUIRED_ONLY])
def test_every_other_flag_answers_the_same_with_and_without_prices(
    row: dict, unpriced_config_dir: Path
):
    """The claim PRICE_DEPENDENT_FLAGS makes, tested rather than asserted.

    If a flag not on that list changes its answer when the prices disappear,
    then the unpriced mode is quietly showing a different result from the priced
    one, and the list is wrong.
    """
    priced = run([row])
    unpriced = run(
        [row], config_dir=unpriced_config_dir.as_posix(), prices_path=None
    )
    identifier = row["borrower_id"]
    assert (active_flags(priced, identifier) - PRICE_DEPENDENT_FLAGS) == (
        active_flags(unpriced, identifier) - PRICE_DEPENDENT_FLAGS
    )


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_every_config_file_in_use_is_hashed():
    rows = {row["file"]: row for row in config_provenance(FIXTURE_DIR)}
    for name in (
        "cbam_rules.yaml",
        "emission_defaults.yaml",
        "nace_tiers.yaml",
        "thresholds.yaml",
        "scenarios.yaml",
    ):
        assert rows[name]["present"] == "true"
        assert len(rows[name]["sha256"]) == 64


def test_the_price_source_names_itself_and_is_hashed():
    result = run([HAND_EXAMPLE])
    prices = result["prices"]
    assert prices["price_source"].endswith("prices_fixture.yaml")
    assert prices["price_source_label"] == "fixture_not_a_forecast"
    assert prices["price_source_status"] == "fixture"
    assert len(prices["price_source_sha256"]) == 64


def test_the_result_is_json_and_needs_no_conversion():
    """Pyodide hands the page a string. It must survive the round trip as is."""
    text = screen_json(payload([HAND_EXAMPLE]))
    assert isinstance(text, str)
    assert json.loads(text)["ok"] is True


def test_a_broken_config_directory_comes_back_as_a_message_not_a_traceback():
    result = json.loads(
        screen_json(
            json.dumps({"config_dir": "no/such/directory", "borrowers": [HAND_EXAMPLE]})
        )
    )
    assert result["ok"] is False
    # The separator is the platform's, so the directory name is checked rather
    # than the whole path.
    assert "config directory not found" in result["error"]
    assert "directory" in result["error"]


def test_screen_takes_a_request_object_as_well_as_json():
    """The exporter-shaped call, so a Python caller need not build JSON."""
    result = screen(
        ScreenRequest(
            rows=[HAND_EXAMPLE], config_dir=FIXTURE_DIR, prices_path=PRICES
        )
    )
    assert float(cost_row(result, "B001")["cost_eur"]) == pytest.approx(3800.0)
