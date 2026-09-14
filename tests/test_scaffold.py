"""Task 0 check: the repo skeleton is present and every config file parses."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

CONFIG_FILES = [
    "cbam_rules.yaml",
    "emission_defaults.yaml",
    "scenarios.yaml",
    "nace_tiers.yaml",
    "thresholds.yaml",
]

EXPECTED_PATHS = [
    "CLAUDE.md",
    "README.md",
    "LICENSE",
    "requirements.txt",
    "pyproject.toml",
    ".gitignore",
    "data/SOURCES.md",
    "data/template_borrowers.csv",
    "docs/METHODOLOGY.md",
    "src/__init__.py",
]


@pytest.mark.parametrize("relative_path", EXPECTED_PATHS)
def test_scaffold_path_exists(relative_path: str) -> None:
    assert (REPO_ROOT / relative_path).is_file(), f"missing {relative_path}"


@pytest.mark.parametrize("filename", CONFIG_FILES)
def test_config_file_parses_and_has_meta(filename: str) -> None:
    path = REPO_ROOT / "config" / filename
    assert path.is_file(), f"missing config/{filename}"
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"config/{filename} must parse to a mapping"
    assert "meta" in loaded, f"config/{filename} must carry a meta block"


def test_no_em_dashes_in_docs_configs_or_source() -> None:
    """CLAUDE.md forbids em dashes in documentation."""
    checked = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "docs" / "METHODOLOGY.md",
        REPO_ROOT / "data" / "SOURCES.md",
    ]
    checked += sorted((REPO_ROOT / "config").glob("*.yaml"))
    checked += sorted((REPO_ROOT / "src").glob("*.py"))
    checked += sorted((REPO_ROOT / "tools").glob("*.py"))
    offenders = [p.name for p in checked if "—" in p.read_text(encoding="utf-8")]
    assert not offenders, f"em dash found in: {offenders}"


def test_raw_data_directory_is_gitignored() -> None:
    """Third-party source files must never be committed."""
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/raw/" in ignore
