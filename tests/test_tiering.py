"""Task 4 tests: one row per tier, plus the precedence rule 2 over 1 over 3 over 4.

All of these run against tests/fixtures/, not against config/.
"""

from __future__ import annotations

import pytest

from src.config import Config
from src.tiering import (
    SimpleTierInput,
    assign_tier,
    longest_matching_prefix,
    nace_matches,
    normalise_nace,
)


def borrower(**kwargs: object) -> SimpleTierInput:
    """A borrower with sensible defaults, overridden per test."""
    defaults: dict[str, object] = {
        "nace_code": "62.01",
        "country": "NL",
        "top_supplier_country": None,
        "cbam_import_quantities": {},
    }
    defaults.update(kwargs)
    return SimpleTierInput(**defaults)  # type: ignore[arg-type]


# NACE prefix matching


@pytest.mark.parametrize(
    "code,expected",
    [("24.10", "2410"), ("2410", "2410"), ("23.5", "235"), ("C24.10", "2410")],
)
def test_normalise_nace_keeps_digits_only(code: str, expected: str) -> None:
    assert normalise_nace(code) == expected


@pytest.mark.parametrize(
    "prefix,code,expected",
    [
        ("24", "24.10", True),
        ("23.5", "23.51", True),
        ("23.5", "23.61", False),
        ("20.15", "20.15", True),
        ("20.15", "20.16", False),
        ("35.11", "35.11", True),
        ("24", "25.11", False),
    ],
)
def test_nace_matches_on_prefix(prefix: str, code: str, expected: bool) -> None:
    assert nace_matches(prefix, code) is expected


def test_longest_matching_prefix_prefers_the_more_specific_entry() -> None:
    assert longest_matching_prefix(["24", "24.10"], "24.10") == "24.10"


# One row per tier


def test_tier_1_eu_producer(fixture_config: Config) -> None:
    result = assign_tier(borrower(nace_code="24.10"), fixture_config)
    assert result.tier == 1
    assert result.rule == "eu_producer"


def test_tier_2_direct_import_from_non_exempt_origin(fixture_config: Config) -> None:
    result = assign_tier(
        borrower(
            nace_code="25.11",
            top_supplier_country="IN",
            cbam_import_quantities={"steel": 1000.0},
        ),
        fixture_config,
    )
    assert result.tier == 2
    assert result.rule == "direct_import"
    assert result.origin_unknown is False


def test_tier_3_downstream_only_on_the_extension_branch(fixture_config: Config) -> None:
    downstream = borrower(nace_code="25.11")
    on_adopted = assign_tier(downstream, fixture_config, branch="adopted")
    on_proposal = assign_tier(downstream, fixture_config, branch="proposal")
    assert on_proposal.tier == 3
    assert on_proposal.rule == "downstream_extension"
    # On the adopted branch the same borrower is not Tier 3. In this fixture it
    # has no tier 4 input share either, so it falls out of scope.
    assert on_adopted.tier == 0


def test_tier_4_material_input_share(fixture_config: Config) -> None:
    result = assign_tier(borrower(nace_code="41.20"), fixture_config)
    assert result.tier == 4
    assert result.rule == "input_share"


def test_tier_0_when_nothing_applies(fixture_config: Config) -> None:
    result = assign_tier(borrower(nace_code="62.01"), fixture_config)
    assert result.tier == 0
    assert result.rule == "out_of_scope"


# Origin rules


@pytest.mark.parametrize("origin", ["NO", "IS", "CH", "LI"])
def test_import_from_exempt_origin_is_not_tier_2(
    fixture_config: Config, origin: str
) -> None:
    result = assign_tier(
        borrower(
            nace_code="25.11",
            top_supplier_country=origin,
            cbam_import_quantities={"steel": 5000.0},
        ),
        fixture_config,
    )
    assert result.tier != 2


@pytest.mark.parametrize("origin", ["DE", "BE", "PL"])
def test_import_from_inside_the_eu_is_not_tier_2(
    fixture_config: Config, origin: str
) -> None:
    result = assign_tier(
        borrower(
            nace_code="25.11",
            top_supplier_country=origin,
            cbam_import_quantities={"steel": 5000.0},
        ),
        fixture_config,
    )
    assert result.tier != 2


def test_unknown_origin_with_imports_is_treated_as_in_scope(
    fixture_config: Config,
) -> None:
    """A screening tool must not lose an exposure because a field was blank."""
    result = assign_tier(
        borrower(
            nace_code="25.11",
            top_supplier_country=None,
            cbam_import_quantities={"steel": 1000.0},
        ),
        fixture_config,
    )
    assert result.tier == 2
    assert result.origin_unknown is True


def test_zero_tonnes_is_not_an_import(fixture_config: Config) -> None:
    result = assign_tier(
        borrower(
            nace_code="62.01",
            top_supplier_country="IN",
            cbam_import_quantities={"steel": 0.0, "cement": 0.0},
        ),
        fixture_config,
    )
    assert result.tier == 0


# Precedence: 2 over 1 over 3 over 4


def test_precedence_tier_2_beats_tier_1(fixture_config: Config) -> None:
    """An EU steel producer that also imports steel is Tier 2."""
    result = assign_tier(
        borrower(
            nace_code="24.10",
            top_supplier_country="IN",
            cbam_import_quantities={"steel": 1000.0},
        ),
        fixture_config,
    )
    assert result.tier == 2


def test_precedence_tier_1_beats_tier_3(fixture_config: Config) -> None:
    """A NACE in both the producer and the downstream list is Tier 1."""
    config = fixture_config
    original = list(config.nace_tiers["downstream_nace"])
    config.nace_tiers["downstream_nace"] = original + ["24.10"]
    try:
        result = assign_tier(borrower(nace_code="24.10"), config, branch="proposal")
        assert result.tier == 1
    finally:
        config.nace_tiers["downstream_nace"] = original


def test_precedence_tier_3_beats_tier_4(fixture_config: Config) -> None:
    """25.11 is downstream and is also given a tier 4 input share in the fixture."""
    config = fixture_config
    sectors = config.nace_tiers["sectors"]
    saved = sectors["25.11"]["tier"]
    sectors["25.11"]["tier"] = 4
    try:
        result = assign_tier(borrower(nace_code="25.11"), config, branch="proposal")
        assert result.tier == 3
    finally:
        sectors["25.11"]["tier"] = saved


def test_full_precedence_chain_on_one_borrower(fixture_config: Config) -> None:
    """Turn the rules off one at a time and watch the tier fall 2, 1, 3, 4, 0."""
    config = fixture_config
    sectors = config.nace_tiers["sectors"]
    saved_downstream = list(config.nace_tiers["downstream_nace"])
    saved_producer = list(config.nace_tiers["producer_nace"])
    saved_tier = sectors["24.10"]["tier"]
    config.nace_tiers["downstream_nace"] = saved_downstream + ["24.10"]
    sectors["24.10"]["tier"] = 4
    try:
        # All four rules fire. Tier 2 wins.
        with_import = borrower(
            nace_code="24.10",
            top_supplier_country="IN",
            cbam_import_quantities={"steel": 1000.0},
        )
        assert assign_tier(with_import, config, branch="proposal").tier == 2

        # Drop the import. Tier 1 wins.
        no_import = borrower(nace_code="24.10")
        assert assign_tier(no_import, config, branch="proposal").tier == 1

        # Drop the producer listing. Tier 3 wins.
        config.nace_tiers["producer_nace"] = []
        assert assign_tier(no_import, config, branch="proposal").tier == 3

        # Drop the downstream listing. Tier 4 wins.
        config.nace_tiers["downstream_nace"] = []
        assert assign_tier(no_import, config, branch="proposal").tier == 4

        # Drop the input share. Tier 0.
        sectors["24.10"]["tier"] = 0
        assert assign_tier(no_import, config, branch="proposal").tier == 0
    finally:
        config.nace_tiers["downstream_nace"] = saved_downstream
        config.nace_tiers["producer_nace"] = saved_producer
        sectors["24.10"]["tier"] = saved_tier


def test_tier_result_carries_a_plain_english_reason(fixture_config: Config) -> None:
    result = assign_tier(
        borrower(
            nace_code="25.11",
            top_supplier_country="IN",
            cbam_import_quantities={"steel": 1000.0},
        ),
        fixture_config,
    )
    assert "steel" in result.reason
    assert "IN" in result.reason
