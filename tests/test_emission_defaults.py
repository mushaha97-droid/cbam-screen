"""Task 2 tests: the shipped emission_defaults.yaml.

The task list asks that every CN group in cbam_rules has a factor. One group,
electricity, does not, because the Commission workbook does not carry electricity
values. That gap is recorded rather than filled, so the tests below split the
check in two: every group must have an entry, and every entry that is not an
explicit TODO must carry a real number.
"""

from __future__ import annotations

import pytest

from src.config import Config, ConfigError

# Groups with no adopted default value, each with its reason recorded in config.
KNOWN_GAPS = {"electricity"}

# Values read from the fallback sheet of the Commission workbook.
EXPECTED_REPRESENTATIVES = {
    "cement": ("2523 29 00", 1.36, 0.08, 1.44),
    "fertiliser": ("3102 10 19", 2.6, 0.12, 2.72),
    "steel": ("7208", 4.049, None, 4.049),
    "aluminium": ("7601", 2.203, None, 2.203),
    "hydrogen": ("2804 10 00", 17.74, None, 17.74),
}


# The headline Task 2 check


def test_every_good_group_in_cbam_rules_has_an_emission_defaults_entry(
    real_config: Config,
) -> None:
    in_rules = set(real_config.goods())
    in_defaults = set(real_config.good_groups())
    missing = in_rules - in_defaults
    assert not missing, f"good groups with no emission_defaults entry: {missing}"


def test_every_good_group_without_a_recorded_gap_has_a_factor(
    real_config: Config,
) -> None:
    for key in real_config.goods():
        if key in KNOWN_GAPS:
            continue
        factor = real_config.default_emission_factor(key, basis="total")
        assert factor > 0, f"{key} has a non-positive default factor"


def test_electricity_is_a_recorded_gap_not_a_silent_zero(real_config: Config) -> None:
    """The gap must be explicit, must name the document, and must refuse to price."""
    entry = real_config.good_groups()["electricity"]
    assert entry["status"] == "TODO"
    assert entry["total"]["value"] is None
    assert entry["data_quality"] == 1
    assert "Annex III" in entry["todo_document_needed"]
    with pytest.raises(ConfigError, match="no adopted default value"):
        real_config.default_emission_factor("electricity")


def test_no_group_in_emission_defaults_is_unknown_to_cbam_rules(
    real_config: Config,
) -> None:
    extra = set(real_config.good_groups()) - set(real_config.goods())
    assert not extra, f"emission_defaults has groups cbam_rules does not: {extra}"


# The values themselves


@pytest.mark.parametrize("key,expected", sorted(EXPECTED_REPRESENTATIVES.items()))
def test_representative_values_match_the_workbook(
    real_config: Config, key: str, expected: tuple[str, float, float | None, float]
) -> None:
    cn_code, direct, indirect, total = expected
    entry = real_config.good_groups()[key]
    assert entry["representative_cn"] == cn_code
    assert entry["direct"]["value"] == pytest.approx(direct)
    assert entry["total"]["value"] == pytest.approx(total)
    if indirect is None:
        assert entry["indirect"]["value"] is None
    else:
        assert entry["indirect"]["value"] == pytest.approx(indirect)


def test_only_cement_and_fertiliser_carry_an_indirect_default(
    real_config: Config,
) -> None:
    """Matches Annex II of the Regulation, which is the direct-only list."""
    with_indirect = {
        key
        for key, entry in real_config.good_groups().items()
        if entry["indirect"]["value"] is not None
    }
    assert with_indirect == {"cement", "fertiliser"}


def test_direct_plus_indirect_equals_total(real_config: Config) -> None:
    for key, entry in real_config.good_groups().items():
        direct = entry["direct"]["value"]
        indirect = entry["indirect"]["value"] or 0.0
        total = entry["total"]["value"]
        if direct is None or total is None:
            continue
        assert total == pytest.approx(direct + indirect, abs=1e-6), key


# Provenance


def test_every_entry_carries_a_data_quality_score(real_config: Config) -> None:
    for key, entry in real_config.good_groups().items():
        quality = entry["data_quality"]
        assert isinstance(quality, int) and 1 <= quality <= 5, key


def test_representative_entries_score_three_not_five(real_config: Config) -> None:
    """The value is adopted, but choosing it to stand for a whole sector is not."""
    for key in EXPECTED_REPRESENTATIVES:
        entry = real_config.good_groups()[key]
        assert entry["data_quality"] == 3
        assert entry["selection_basis"], f"{key} does not say why this CN code"


def test_every_sourced_entry_cites_the_implementing_regulation(
    real_config: Config,
) -> None:
    for key in EXPECTED_REPRESENTATIVES:
        source = real_config.good_groups()[key]["source"]
        assert "2025/2621" in source, key
        assert "2026/1740" in source, key


def test_meta_records_the_workbook_version_and_the_disclaimer_conflict(
    real_config: Config,
) -> None:
    """The workbook's own disclaimer contradicts its version history.

    The Overview sheet, written for version 1, says the file excludes indirect
    default values. The version 2 history entry says it is based on Annexes I and
    II, and the sheet does carry indirect values for cement and fertilisers. The
    config has to say so rather than quietly pick a side.
    """
    meta = real_config.emission_defaults["meta"]
    assert "Version 2" in meta["workbook_version"]
    assert "2026/1740" in meta["workbook_version"]
    assert "indirect" in meta["caveat"]


# The audit trail


def test_by_cn_block_carries_the_full_fallback_sheet(real_config: Config) -> None:
    by_cn = real_config.emission_defaults["by_cn"]
    assert len(by_cn) == 260, "the fallback sheet has 260 CN code rows"


def test_every_representative_cn_code_appears_in_the_audit_trail(
    real_config: Config,
) -> None:
    by_cn = real_config.emission_defaults["by_cn"]
    for key, entry in real_config.good_groups().items():
        cn_code = entry["representative_cn"]
        if cn_code is None:
            continue
        assert cn_code in by_cn, f"{key} names {cn_code}, which is not in by_cn"
        assert by_cn[cn_code]["total"] == entry["total"]["value"]


def test_sector_ranges_bracket_the_representative_value(real_config: Config) -> None:
    for key in EXPECTED_REPRESENTATIVES:
        entry = real_config.good_groups()[key]
        spread = entry["sector_total_range"]
        total = entry["total"]["value"]
        assert spread["min"] <= total <= spread["max"], key
        assert spread["n_cn_codes"] >= 1


def test_steel_range_shows_the_spread_the_representative_hides(
    real_config: Config,
) -> None:
    """Worth its own test: steel spans an order of magnitude across CN codes."""
    spread = real_config.good_groups()["steel"]["sector_total_range"]
    assert spread["n_cn_codes"] == 200
    assert spread["min"] == pytest.approx(0.686)
    assert spread["max"] == pytest.approx(7.1)
