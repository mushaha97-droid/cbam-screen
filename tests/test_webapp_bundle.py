"""Tests for the copy of the engine that ships inside webapp/.

The page runs the engine in the browser. That is only true while the files in
webapp/engine/ are the files in src/, so these tests hold the two together and
fail the build if they drift. A stale copy would not error anywhere: it would
quietly answer with last week's formula, which is exactly the failure this
project exists to avoid.

They also check the things about the bundle that cannot be seen from Python
alone:

    the bundled modules import nothing the browser cannot carry
    the bundled config really is the gated price file, so the gate on the page
        is the live one rather than a mock of it
    the bundle, run as the page runs it, reproduces the CLAUDE.md hand example
    the page's own scripts parse, and the page actually loads them, so a right
        answer from the engine has somewhere to be drawn
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.engine_api import ScreenRequest, has_prices, screen
from src.config import load_config
from tools.bundle_webapp import (
    CONFIG_FILES,
    ENGINE_MODULES,
    manifest_is_current,
    normalised,
    sha256_bytes,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WEBAPP = REPO_ROOT / "webapp"
ENGINE_DIR = WEBAPP / "engine" / "src"
CFG_DIR = WEBAPP / "cfg"
MANIFEST = CFG_DIR / "manifest.json"

# Modules the browser has no way to provide, or that would drag in a wheel the
# page does not load. Anything on this list appearing in a bundled engine file
# means the in-browser run would die on import.
FORBIDDEN_IMPORTS = {
    "pandas",
    "numpy",
    "streamlit",
    "plotly",
    "subprocess",
    "tomllib",
    "openpyxl",
    "multiprocessing",
    "socket",
    "sqlite3",
}


def sha256(path: Path) -> str:
    """Hash of the file's bytes with line endings normalised to LF.

    The bundler writes LF and hashes LF, so that a Windows checkout and a Linux
    checkout produce the same manifest. These tests have to agree with it or
    they would fail on whichever platform did not write the manifest.
    """
    return sha256_bytes(normalised(path))


@pytest.fixture(scope="module")
def manifest() -> dict:
    assert MANIFEST.is_file(), "run python -m tools.bundle_webapp"
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_the_bundle_matches_its_sources():
    """The one test that keeps the page honest. Run the bundler if it fails."""
    current, stale = manifest_is_current()
    assert current, (
        "webapp/ is out of date with src/ and config/. Stale: "
        + ", ".join(stale)
        + ". Run: python -m tools.bundle_webapp"
    )


@pytest.mark.parametrize("module", ENGINE_MODULES)
def test_each_bundled_engine_module_is_byte_identical(module: str):
    source = REPO_ROOT / "src" / module
    bundled = ENGINE_DIR / module
    assert bundled.is_file(), f"{module} was not bundled"
    assert sha256(bundled) == sha256(source)


@pytest.mark.parametrize("module", ENGINE_MODULES)
def test_no_bundled_module_imports_something_the_browser_lacks(module: str):
    tree = ast.parse((ENGINE_DIR / module).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    offending = sorted(imported & FORBIDDEN_IMPORTS)
    assert not offending, (
        f"{module} imports {offending}, which the in-browser engine cannot load. "
        f"Keep that code out of the engine_api path."
    )


def test_every_manifest_entry_hashes_what_is_actually_on_disk(manifest: dict):
    """The page re-hashes these in the browser, so they have to be exact.

    Hashed without normalising here, on purpose. The bundler writes LF and
    .gitattributes pins these paths to LF, so the bytes the browser fetches are
    the bytes on disk. If a checkout ever rewrote them, this fails, which is the
    point.
    """
    assert manifest["files"], "the manifest lists no files"
    for entry in manifest["files"]:
        path = WEBAPP / entry["path"]
        assert path.is_file(), f"{entry['path']} is in the manifest but not on disk"
        assert sha256_bytes(path.read_bytes()) == entry["sha256"], (
            f"{entry['path']} does not match its hash. If the line endings were "
            f"rewritten on checkout, check .gitattributes."
        )
        assert len(entry["sha256"]) == 64
        assert entry["bytes"] == path.stat().st_size


def test_the_manifest_names_the_source_of_every_file(manifest: dict):
    """A bundled file with no stated origin is a number with no provenance."""
    for entry in manifest["files"]:
        assert entry["source"], f"{entry['path']} does not say where it came from"
        assert (REPO_ROOT / entry["source"]).is_file()


def test_every_config_file_the_engine_needs_is_in_the_bundle():
    for target_name in CONFIG_FILES.values():
        assert (CFG_DIR / target_name).is_file(), f"cfg/{target_name} is missing"


def test_the_questions_file_is_in_the_bundle_so_nothing_falls_back():
    """questions.py falls back to the repo's own config/ when a file is absent.

    In the browser there is no repo to fall back to, so the fallback would be a
    file-not-found at the moment a borrower page tries to show its questions.
    """
    assert (CFG_DIR / "questions.yaml").is_file()


def test_the_bundled_price_file_is_still_the_gated_one():
    """The page must exercise the real gate, not a stand-in for it."""
    assert has_prices(load_config(CFG_DIR)) is False


def test_the_bundled_fixture_prices_label_themselves_as_fixture(manifest: dict):
    entry = next(e for e in manifest["files"] if e["path"] == "cfg/prices_fixture.yaml")
    assert entry["meta"]["label"] == "fixture_not_a_forecast"
    assert entry["meta"]["status"] == "fixture"


def test_the_bundle_computes_the_hand_example_the_way_the_page_will():
    """The end-to-end claim: these bytes, in this folder, give 3,800 EUR.

    Same config directory, same price file and same entrypoint the page uses.
    If this passes and the browser disagrees, the problem is the page, not the
    engine.
    """
    result = screen(
        ScreenRequest(
            rows=[
                {
                    "borrower_id": "B001",
                    "name": "Hand Example Steel BV",
                    "nace_code": "46.72",
                    "country": "NL",
                    "exposure_eur": 12_000_000,
                    "turnover_eur": 48_000_000,
                    "ebitda_eur": 4_800_000,
                    "import_t_steel": 1000,
                    "top_supplier_country": "IN",
                    "declarant_status": "no",
                }
            ],
            config_dir=CFG_DIR,
            prices_path=CFG_DIR / "prices_fixture.yaml",
        )
    )
    assert result["ok"] is True
    assert result["priced"] is True
    row = next(
        r
        for r in result["tables"]["fact_cost"]
        if r["year"] == "2026"
        and r["scenario"] == "delayed_transition"
        and r["branch"] == "adopted"
    )
    assert float(row["cost_eur"]) == pytest.approx(3800.0)
    assert row["tier_label_plain"] == "Imports CBAM goods"


def test_the_borrower_template_is_offered_unchanged():
    assert sha256(WEBAPP / "template_borrowers.csv") == sha256(
        REPO_ROOT / "data" / "template_borrowers.csv"
    )


# The page's own scripts. Every test above this line can pass while the page
# shows nothing at all, because a script that does not parse never runs and a
# browser reports it only to its console. That happened: one string in
# screen.js opened with a double quote and closed with a single one, the whole
# file failed to parse, and the Screen a borrower view rendered an empty form
# with a dead button while pytest stayed green. These two parse the files the
# way the browser does, so that failure is a test failure and not a discovery.
PAGE_SCRIPTS = ("app.js", "screen.js")


@pytest.mark.parametrize("script", PAGE_SCRIPTS)
def test_each_page_script_parses(script: str):
    """Hand the file to a real JavaScript parser and see if it is a program.

    Skipped rather than failed where node is absent, because node is not a
    dependency of this project and a skip that says why is more use than a
    failure that means nothing. The browser check in the verification run is
    what this stands in for between browser runs.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH, so the page scripts cannot be parsed here")
    finished = subprocess.run(
        [node, "--check", str(WEBAPP / script)],
        capture_output=True,
        text=True,
    )
    assert finished.returncode == 0, (
        f"webapp/{script} is not valid JavaScript, so the browser would abandon "
        f"it and the page would render dead:\n{finished.stderr}"
    )


@pytest.mark.parametrize("script", PAGE_SCRIPTS)
def test_each_page_script_is_referenced_by_the_page(script: str):
    """A script that parses but is not on the page is just as invisible."""
    html = (WEBAPP / "index.html").read_text(encoding="utf-8")
    assert f'src="{script}"' in html, f"index.html does not load {script}"
