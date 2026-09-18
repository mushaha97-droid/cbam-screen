"""Copy the engine and its config into webapp/, so the page can run them.

The web page runs the real Python engine in the browser under Pyodide. For that
to work, the engine's source files and its yaml config have to be fetchable over
http from the same folder as the page. This script puts them there, and writes a
manifest recording the sha256 of every file it copied.

Why copies rather than a symlink or a relative fetch: the webapp folder is
deployed on its own, to a static host, with nothing above it. A page that
fetched ../src/cost.py would work on a dev server and 404 in production, which
is the worst possible way to find out.

Why a manifest: the page shows the numbers this config produces, so it has to be
able to say which config. Every file's hash is computed from the bytes that were
copied, and the page prints them on the Screen a borrower view. A hash the page
computed itself could be a hash of something else.

    python -m tools.bundle_webapp            write the bundle
    python -m tools.bundle_webapp --check    fail if the bundle is out of date

WHICH CONFIG THE BUNDLE CARRIES, AND WHY

The rule files are the fixture set in tests/fixtures/, not config/. That is a
deliberate choice and it is stated on the page:

    config/nace_tiers.yaml is still a scaffold. CLAUDE.md Task 4 says the owner
    writes it by hand, and the draft beside it is placeholders marked
    TODO-owner. With no sector map there are no sector defaults, so no borrower
    could ever be marked estimated and Tier 1 and Tier 4 could not be reached
    at all.

    The dashboard views already ship a FIXTURE export built from tests/fixtures.
    A screening form computing on a different rule set from the dashboard next
    to it would put two incompatible numbers on one page.

scenarios.yaml is the real, gated config/scenarios.yaml, so the price gate in
the page is the live one rather than a simulation of it. prices_fixture.yaml
travels with it as the stand-in the page falls back to, loudly labelled.

questions.yaml is the real config/questions.yaml. It carries no numbers, so
there is no fixture version of it to keep separate.

WHEN THE GATES CLEAR

Fill config/scenarios.yaml with the NGFS prices and point SCENARIOS_SOURCE at
it, or just replace the bundled file: has_prices() in engine_api reads the
prices off the data, so the page stops labelling anything as fixture on its own.
Write config/nace_tiers.yaml and move the four rule files to REAL_CONFIG here.
No page code changes for either.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WEBAPP = REPO_ROOT / "webapp"
ENGINE_OUT = WEBAPP / "engine" / "src"
CFG_OUT = WEBAPP / "cfg"
MANIFEST = CFG_OUT / "manifest.json"

# The engine modules the in-browser path actually imports. Kept explicit rather
# than globbed, so that adding a module with a pandas import to src/ does not
# silently break the browser. export_powerbi.py and synth.py are deliberately
# absent: the first needs subprocess, and neither is on this path.
ENGINE_MODULES = (
    "__init__.py",
    "config.py",
    "tiering.py",
    "schema.py",
    "cost.py",
    "flags.py",
    "liquidity.py",
    "questions.py",
    "engine_api.py",
)

# source path in the repo -> name inside webapp/cfg/
CONFIG_FILES: dict[str, str] = {
    "tests/fixtures/cbam_rules.yaml": "cbam_rules.yaml",
    "tests/fixtures/emission_defaults.yaml": "emission_defaults.yaml",
    "tests/fixtures/nace_tiers.yaml": "nace_tiers.yaml",
    "tests/fixtures/thresholds.yaml": "thresholds.yaml",
    "config/scenarios.yaml": "scenarios.yaml",
    "config/questions.yaml": "questions.yaml",
    "data/powerbi_fixture/prices_fixture.yaml": "prices_fixture.yaml",
}

# The blank borrower template the page offers as a download.
TEMPLATE_SOURCE = "data/template_borrowers.csv"
TEMPLATE_NAME = "template_borrowers.csv"


def normalised(path: Path) -> bytes:
    """The file's bytes with line endings forced to LF.

    Every bundled file is text. Git on Windows checks the sources out with CRLF
    and on Linux with LF, so hashing the source bytes as they sit on disk would
    put a different manifest in the repo depending on who ran the bundler, and
    the page would then report a hash mismatch on a perfectly good bundle.
    Normalising first means the manifest records the bytes the page is actually
    served, on any machine.
    """
    return path.read_bytes().replace(b"\r\n", b"\n")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    """The hash of the file's normalised bytes, which is what gets written."""
    return sha256_bytes(normalised(path))


def _meta_of(path: Path) -> dict[str, str]:
    """The meta block a yaml config states about itself, for the page to show."""
    if path.suffix != ".yaml":
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    meta = (loaded.get("meta") or {}) if isinstance(loaded, dict) else {}
    return {
        key: " ".join(str(meta[key]).split())
        for key in ("config_version", "status", "label", "last_updated", "note")
        if meta.get(key) is not None
    }


def build(check_only: bool = False) -> tuple[list[str], dict[str, Any]]:
    """Copy everything and build the manifest. Returns (stale files, manifest)."""
    stale: list[str] = []
    entries: list[dict[str, Any]] = []

    def place(source: Path, target: Path, kind: str, note: str = "") -> None:
        if not source.is_file():
            raise SystemExit(f"bundle source missing: {source}")
        payload = normalised(source)
        digest = sha256_bytes(payload)
        if not target.is_file() or target.read_bytes() != payload:
            stale.append(target.relative_to(WEBAPP).as_posix())
            if not check_only:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
        entries.append(
            {
                "kind": kind,
                "path": target.relative_to(WEBAPP).as_posix(),
                "source": source.relative_to(REPO_ROOT).as_posix(),
                "sha256": digest,
                "bytes": len(payload),
                "note": note,
                "meta": _meta_of(source),
            }
        )

    for module in ENGINE_MODULES:
        place(REPO_ROOT / "src" / module, ENGINE_OUT / module, "engine")

    for source_name, target_name in CONFIG_FILES.items():
        place(REPO_ROOT / source_name, CFG_OUT / target_name, "config")

    place(
        REPO_ROOT / TEMPLATE_SOURCE,
        WEBAPP / TEMPLATE_NAME,
        "template",
        "The blank borrower template, offered as a download on the screening view.",
    )

    manifest = {
        "generated_by": "python -m tools.bundle_webapp",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "engine_entrypoint": "src.engine_api.screen_json",
        "rule_config_note": (
            "The rule files in this bundle are the fixture set from "
            "tests/fixtures/, the same set the dashboard export was built from. "
            "config/nace_tiers.yaml is still a scaffold, so there is no sector "
            "map in config/ to bundle. See tools/bundle_webapp.py."
        ),
        "price_note": (
            "scenarios.yaml is the real gated file and carries no prices. "
            "prices_fixture.yaml stands in for it and is not a forecast."
        ),
        "files": entries,
    }

    if not check_only:
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        previous = None
        if MANIFEST.is_file():
            try:
                previous = json.loads(MANIFEST.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                previous = None
        # Only rewrite the manifest when something in it other than the
        # timestamp changed, so that running the bundler twice does not show up
        # as a diff in git.
        if previous is not None and _without_timestamp(previous) == _without_timestamp(
            manifest
        ):
            manifest = previous
        else:
            MANIFEST.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
    return stale, manifest


def _without_timestamp(manifest: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if key != "generated_at"}


def manifest_is_current() -> tuple[bool, list[str]]:
    """True when every bundled file matches its source and the manifest agrees."""
    stale, manifest = build(check_only=True)
    if not MANIFEST.is_file():
        return False, stale + ["cfg/manifest.json"]
    try:
        on_disk = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, stale + ["cfg/manifest.json"]
    if _without_timestamp(on_disk) != _without_timestamp(manifest):
        stale = stale + ["cfg/manifest.json"]
    return not stale, stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.bundle_webapp",
        description="Copy the engine and its config into webapp/ for Pyodide.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write anything, just report whether the bundle is current",
    )
    args = parser.parse_args(argv)

    if args.check:
        current, stale = manifest_is_current()
        if current:
            print("webapp bundle is up to date")
            return 0
        print("webapp bundle is out of date. Stale: " + ", ".join(stale))
        print("Run: python -m tools.bundle_webapp")
        return 1

    stale, manifest = build()
    total = sum(entry["bytes"] for entry in manifest["files"])
    print(f"bundled {len(manifest['files'])} files, {total:,} bytes, into webapp/")
    if stale:
        print("updated: " + ", ".join(stale))
    else:
        print("nothing changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
