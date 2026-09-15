"""Central loader for config/*.yaml.

Every rule, threshold, price and emission factor used by the engine comes
through here. Nothing else in src/ reads a yaml file directly, and nothing in
src/ hard-codes a regulatory number.

A malformed or incomplete config raises ConfigError straight away, so a bad
config fails on load rather than producing a quietly wrong number later.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"

CONFIG_FILES = {
    "cbam_rules": "cbam_rules.yaml",
    "emission_defaults": "emission_defaults.yaml",
    "scenarios": "scenarios.yaml",
    "nace_tiers": "nace_tiers.yaml",
    "thresholds": "thresholds.yaml",
}

VALID_STATUSES = {"adopted", "proposal", "assumption", "scenario", "TODO"}


class ConfigError(ValueError):
    """A config file is missing, unparseable or does not match its schema."""


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    """Read one yaml file and insist it is a mapping."""
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid yaml: {exc}") from exc
    if loaded is None:
        raise ConfigError(f"{path.name} is empty")
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path.name} must be a mapping at the top level")
    return loaded


def require(mapping: Mapping[str, Any], key: str, where: str) -> Any:
    """Fetch a required key, or say exactly what is missing and where."""
    if key not in mapping:
        raise ConfigError(f"{where}: missing required key {key!r}")
    return mapping[key]


def check_status(value: Any, where: str) -> str:
    """Every sourced entry carries a status from a closed vocabulary."""
    if value not in VALID_STATUSES:
        raise ConfigError(
            f"{where}: status {value!r} is not one of {sorted(VALID_STATUSES)}"
        )
    return str(value)


@dataclass(frozen=True)
class Config:
    """The five config files, parsed and checked."""

    cbam_rules: dict[str, Any]
    emission_defaults: dict[str, Any]
    scenarios: dict[str, Any]
    nace_tiers: dict[str, Any]
    thresholds: dict[str, Any]
    config_dir: Path

    # Convenience accessors. Each one fails loudly if the block is absent.

    def goods(self) -> dict[str, Any]:
        return require(self.cbam_rules, "goods", "cbam_rules.yaml")

    def good_groups(self) -> dict[str, Any]:
        return require(self.emission_defaults, "good_groups", "emission_defaults.yaml")

    def mass_threshold(self) -> dict[str, Any]:
        return require(self.cbam_rules, "mass_threshold", "cbam_rules.yaml")

    def exempt_origins(self) -> dict[str, Any]:
        return require(self.cbam_rules, "exempt_origins", "cbam_rules.yaml")

    def free_allocation(self) -> dict[str, Any]:
        return require(self.cbam_rules, "free_allocation", "cbam_rules.yaml")

    def branch_definitions(self) -> dict[str, Any]:
        """The rule branches the engine runs, from cbam_rules.yaml."""
        return require(self.cbam_rules, "branches", "cbam_rules.yaml")

    def branches(self) -> list[str]:
        return sorted(self.branch_definitions().keys())

    def branch(self, name: str) -> dict[str, Any]:
        branches = self.branch_definitions()
        if name not in branches:
            raise ConfigError(
                f"cbam_rules.yaml branches: unknown branch {name!r}, "
                f"have {sorted(branches)}"
            )
        return branches[name]

    def downstream_extension_applies(self, branch_name: str) -> bool:
        """Is the 2028 downstream extension proposal switched on for this branch?"""
        entry = self.branch(branch_name)
        return bool(
            require(
                entry,
                "downstream_extension_applies",
                f"cbam_rules.yaml branches.{branch_name}",
            )
        )

    def free_allocation_schedule_name(self, branch_name: str) -> str:
        """Which free-allocation schedule this branch uses."""
        entry = self.branch(branch_name)
        return str(
            require(
                entry,
                "free_allocation_schedule",
                f"cbam_rules.yaml branches.{branch_name}",
            )
        )

    def scenario_keys(self) -> list[str]:
        return sorted(require(self.scenarios, "scenarios", "scenarios.yaml").keys())

    def free_allocation_share(self, branch: str, year: int) -> float:
        """Share of free allocation still granted in a year, 0.0 to 1.0.

        branch is a branch name from the branches block. It is resolved to a
        schedule name, so two branches can share one schedule.
        """
        schedule_name = self.free_allocation_schedule_name(branch)
        schedules = self.free_allocation()
        if schedule_name not in schedules:
            raise ConfigError(
                f"cbam_rules.yaml free_allocation: branch {branch!r} points at "
                f"schedule {schedule_name!r}, which does not exist. "
                f"Have {sorted(schedules)}"
            )
        where = f"cbam_rules.yaml free_allocation.{schedule_name}"
        by_year = require(schedules[schedule_name], "by_year", where)
        if year not in by_year:
            raise ConfigError(f"{where}: no entry for year {year}")
        return float(
            require(by_year[year], "free_allocation", f"{where}.{year}")
        )

    def ets_price(self, scenario: str, year: int) -> float:
        """Carbon price in EUR per tonne CO2 for a scenario and year."""
        scenarios = require(self.scenarios, "scenarios", "scenarios.yaml")
        if scenario not in scenarios:
            raise ConfigError(
                f"scenarios.yaml: unknown scenario {scenario!r}, have {sorted(scenarios)}"
            )
        by_year = require(scenarios[scenario], "by_year", f"scenarios.{scenario}")
        if year not in by_year:
            raise ConfigError(f"scenarios.yaml {scenario}: no entry for year {year}")
        return float(require(by_year[year], "price", f"scenarios.{scenario}.{year}"))

    def default_emission_factor(self, group_key: str, basis: str = "total") -> float:
        """Default tCO2e per tonne for a good group.

        basis is "total", "direct" or "indirect". Raises if the group has no
        adopted value, which is the point: a missing factor must not silently
        become zero.
        """
        groups = self.good_groups()
        if group_key not in groups:
            raise ConfigError(
                f"emission_defaults.yaml: unknown good group {group_key!r}, "
                f"have {sorted(groups)}"
            )
        entry = groups[group_key]
        if entry.get("status") == "TODO":
            raise ConfigError(
                f"emission_defaults.yaml: good group {group_key!r} has no adopted "
                f"default value. {entry.get('todo_document_needed', '')}"
            )
        block = require(entry, basis, f"emission_defaults.good_groups.{group_key}")
        value = block.get("value")
        if value is None:
            raise ConfigError(
                f"emission_defaults.yaml: good group {group_key!r} has no "
                f"{basis} value"
            )
        return float(value)


def _check_cbam_rules(data: dict[str, Any]) -> None:
    """Structural checks that must hold once Task 1 has filled the file."""
    goods = data.get("goods")
    if goods is None:
        return  # scaffold state, nothing filled yet
    if not isinstance(goods, dict) or not goods:
        raise ConfigError("cbam_rules.yaml: goods must be a non-empty mapping")
    for key, entry in goods.items():
        where = f"cbam_rules.yaml goods.{key}"
        require(entry, "sector", where)
        require(entry, "cn_codes", where)
        require(entry, "source", where)
        check_status(require(entry, "status", where), where)
        if not isinstance(entry["cn_codes"], list) or not entry["cn_codes"]:
            raise ConfigError(f"{where}: cn_codes must be a non-empty list")

    free_alloc = data.get("free_allocation")
    if free_alloc is not None:
        for branch, block in free_alloc.items():
            where = f"cbam_rules.yaml free_allocation.{branch}"
            check_status(require(block, "status", where), where)
            by_year = require(block, "by_year", where)
            for year, entry in by_year.items():
                spot = f"{where}.{year}"
                if not isinstance(year, int):
                    raise ConfigError(f"{spot}: year keys must be integers")
                share = float(require(entry, "free_allocation", spot))
                if not 0.0 <= share <= 1.0:
                    raise ConfigError(f"{spot}: free_allocation must be 0.0 to 1.0")


def _check_emission_defaults(data: dict[str, Any]) -> None:
    groups = data.get("good_groups")
    if groups is None:
        return
    for key, entry in groups.items():
        where = f"emission_defaults.yaml good_groups.{key}"
        check_status(require(entry, "status", where), where)
        quality = require(entry, "data_quality", where)
        if not isinstance(quality, int) or not 1 <= quality <= 5:
            raise ConfigError(f"{where}: data_quality must be an integer 1 to 5")
        for basis in ("direct", "indirect", "total"):
            block = require(entry, basis, where)
            require(block, "value", f"{where}.{basis}")
            require(block, "unit", f"{where}.{basis}")


def _check_scenarios(data: dict[str, Any]) -> None:
    scenarios = data.get("scenarios")
    if scenarios is None:
        return
    for key, block in scenarios.items():
        where = f"scenarios.yaml scenarios.{key}"
        require(block, "source", where)
        by_year = require(block, "by_year", where)
        for year, entry in by_year.items():
            spot = f"{where}.{year}"
            if not isinstance(year, int):
                raise ConfigError(f"{spot}: year keys must be integers")
            price = require(entry, "price", spot)
            if not isinstance(price, (int, float)) or price < 0:
                raise ConfigError(f"{spot}: price must be a non-negative number")
            if "interpolated" not in entry:
                raise ConfigError(
                    f"{spot}: every year must say whether it was interpolated"
                )


CHECKS = {
    "cbam_rules": _check_cbam_rules,
    "emission_defaults": _check_emission_defaults,
    "scenarios": _check_scenarios,
}


def load_config(config_dir: Path | str | None = None) -> Config:
    """Load and check all five config files."""
    directory = Path(config_dir) if config_dir is not None else DEFAULT_CONFIG_DIR
    if not directory.is_dir():
        raise ConfigError(f"config directory not found: {directory}")
    parsed: dict[str, dict[str, Any]] = {}
    for name, filename in CONFIG_FILES.items():
        parsed[name] = load_yaml_mapping(directory / filename)
        if "meta" not in parsed[name]:
            raise ConfigError(f"{filename}: missing the meta block")
        check = CHECKS.get(name)
        if check is not None:
            check(parsed[name])
    return Config(config_dir=directory, **parsed)


@lru_cache(maxsize=4)
def cached_config(config_dir: str | None = None) -> Config:
    """load_config with memoisation, for the app and for repeated engine calls."""
    return load_config(config_dir)
