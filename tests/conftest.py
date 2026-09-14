"""Shared test fixtures.

Engine tests run against tests/fixtures/*.yaml, not against config/*.yaml. That
keeps the engine tests stable while the real configs are still being sourced,
and it keeps the fixtures small enough to check by eye. Separate tests assert
things about the real config files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Config, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
REAL_CONFIG_DIR = REPO_ROOT / "config"


@pytest.fixture(scope="session")
def fixture_config() -> Config:
    """Config loaded from tests/fixtures/, with small readable numbers."""
    return load_config(FIXTURE_DIR)


@pytest.fixture(scope="session")
def real_config() -> Config:
    """Config loaded from config/, the files that ship."""
    return load_config(REAL_CONFIG_DIR)
