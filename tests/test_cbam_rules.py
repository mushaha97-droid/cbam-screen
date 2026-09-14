"""Task 1 tests: the shipped cbam_rules.yaml.

The task list asks for two checks: both branches reach zero free allocation at
their end year, and the exempt origins are present. The rest of the tests here
guard the things that would quietly produce a wrong number if they drifted.
"""

from __future__ import annotations

import pytest

from src.config import Config

EXPECTED_EXEMPT_COUNTRIES = {"IS", "LI", "NO", "CH"}
EXPECTED_EXEMPT_TERRITORIES = {
    "Busingen",
    "Heligoland",
    "Livigno",
    "Ceuta",
    "Melilla",
}

# Directive 2003/87/EC Article 10a(1a), as inserted by Directive (EU) 2023/959.
ADOPTED_SCHEDULE = {
    2026: 0.975,
    2027: 0.95,
    2028: 0.90,
    2029: 0.775,
    2030: 0.515,
    2031: 0.39,
    2032: 0.265,
    2033: 0.14,
    2034: 0.0,
}

# COM(2026) 616 final, Article 1(15)(b)(i). A proposal.
PROPOSED_SCHEDULE = {
    2026: 0.975,
    2027: 0.95,
    2028: 0.915,
    2029: 0.81,
    2030: 0.59,
    2031: 0.48,
    2032: 0.375,
    2033: 0.27,
    2034: 0.15,
    2035: 0.15,
    2036: 0.15,
    2037: 0.15,
    2038: 0.0,
}


# The two checks the task list names


def test_both_branches_reach_zero_free_allocation_at_their_end_year(
    real_config: Config,
) -> None:
    schedules = real_config.free_allocation()
    for name, block in schedules.items():
        end_year = block["end_year"]
        share = block["by_year"][end_year]["free_allocation"]
        assert share == 0.0, f"{name} should reach zero free allocation in {end_year}"


def test_exempt_origins_are_present(real_config: Config) -> None:
    block = real_config.exempt_origins()
    countries = {entry["code"].upper() for entry in block["countries"]}
    territories = {entry["name"] for entry in block["territories"]}
    assert countries == EXPECTED_EXEMPT_COUNTRIES
    assert territories == EXPECTED_EXEMPT_TERRITORIES


# The schedules, year by year


@pytest.mark.parametrize("year,share", sorted(ADOPTED_SCHEDULE.items()))
def test_adopted_free_allocation_matches_the_directive(
    real_config: Config, year: int, share: float
) -> None:
    assert real_config.free_allocation_share("adopted", year) == pytest.approx(share)


@pytest.mark.parametrize("year,share", sorted(PROPOSED_SCHEDULE.items()))
def test_proposed_free_allocation_matches_the_commission_proposal(
    real_config: Config, year: int, share: float
) -> None:
    assert real_config.free_allocation_share("proposal", year) == pytest.approx(share)


def test_free_allocation_never_rises(real_config: Config) -> None:
    """A phase-out only goes one way. Tier 1 costing depends on this."""
    for name, block in real_config.free_allocation().items():
        years = sorted(block["by_year"])
        shares = [block["by_year"][year]["free_allocation"] for year in years]
        assert shares == sorted(shares, reverse=True), f"{name} free allocation rises"


def test_the_proposal_is_never_tighter_than_adopted_law(real_config: Config) -> None:
    """The July 2026 proposal slows the phase-out, so it cannot give less."""
    for year in range(2026, 2035):
        adopted = real_config.free_allocation_share("adopted", year)
        proposed = real_config.free_allocation_share("proposal", year)
        assert proposed >= adopted, f"{year}: proposal {proposed} below adopted {adopted}"


def test_the_two_branches_diverge_somewhere(real_config: Config) -> None:
    """If they were identical the whole branch feature would be pointless."""
    differences = [
        year
        for year in range(2026, 2035)
        if real_config.free_allocation_share("adopted", year)
        != real_config.free_allocation_share("proposal", year)
    ]
    assert differences, "the two branches never differ"


def test_cbam_factor_and_free_allocation_agree(real_config: Config) -> None:
    """In the Directive the CBAM factor is the share retained, not withdrawn.

    Getting this backwards would invert every cost in the tool, so it gets its
    own test.
    """
    for name, block in real_config.free_allocation().items():
        for year, entry in block["by_year"].items():
            assert entry["cbam_factor"] == entry["free_allocation"], (
                f"{name} {year}: cbam_factor and free_allocation must be the same "
                f"number. The Directive's CBAM factor is the retained share."
            )


def test_2026_free_allocation_is_97_5_percent(real_config: Config) -> None:
    """The single number the hand example leans on."""
    assert real_config.free_allocation_share("adopted", 2026) == 0.975


# Branches


def test_branches_are_adopted_and_proposal(real_config: Config) -> None:
    assert real_config.branches() == ["adopted", "proposal"]


def test_only_the_proposal_branch_turns_on_the_downstream_extension(
    real_config: Config,
) -> None:
    assert real_config.downstream_extension_applies("adopted") is False
    assert real_config.downstream_extension_applies("proposal") is True


def test_the_adopted_branch_is_marked_adopted_and_the_other_proposal(
    real_config: Config,
) -> None:
    assert real_config.branch("adopted")["status"] == "adopted"
    assert real_config.branch("proposal")["status"] == "proposal"


# Goods and the threshold


def test_every_good_group_has_cn_codes_a_source_and_a_status(
    real_config: Config,
) -> None:
    for key, entry in real_config.goods().items():
        assert entry["cn_codes"], f"{key} has no CN codes"
        assert entry["source"], f"{key} has no source"
        assert entry["status"] in {"adopted", "proposal"}, key


def test_mass_threshold_is_50_tonnes(real_config: Config) -> None:
    threshold = real_config.mass_threshold()
    assert threshold["value"] == 50
    assert threshold["status"] == "adopted"


def test_electricity_and_hydrogen_are_outside_the_mass_threshold(
    real_config: Config,
) -> None:
    """Article 2a(4). This is the rule the cost engine has to get exactly right."""
    goods = real_config.goods()
    assert goods["electricity"]["counts_toward_mass_threshold"] is False
    assert goods["hydrogen"]["counts_toward_mass_threshold"] is False
    excluded = set(real_config.mass_threshold()["excluded_goods"])
    assert excluded == {"electricity", "hydrogen"}


def test_everything_else_counts_toward_the_mass_threshold(real_config: Config) -> None:
    goods = real_config.goods()
    for key in ("steel", "aluminium", "cement", "fertiliser"):
        assert goods[key]["counts_toward_mass_threshold"] is True


def test_only_cement_and_fertiliser_carry_indirect_emissions(
    real_config: Config,
) -> None:
    """Annex II is the direct-only list. Electricity was moved into it in 2025."""
    goods = real_config.goods()
    with_indirect = {
        key
        for key, entry in goods.items()
        if entry["emissions_covered"] == "direct_and_indirect"
    }
    assert with_indirect == {"cement", "fertiliser"}
    assert goods["electricity"]["emissions_covered"] == "direct"


# Timeline and pricing


def test_certificate_sales_start_on_1_february_2027(real_config: Config) -> None:
    timeline = real_config.cbam_rules["timeline"]
    assert timeline["certificate_sales_start"]["date"] == "2027-02-01"


def test_first_surrender_deadline_is_30_september_2027(real_config: Config) -> None:
    timeline = real_config.cbam_rules["timeline"]
    assert timeline["first_surrender_deadline"]["date"] == "2027-09-30"
    assert timeline["first_declaration_deadline"]["date"] == "2027-09-30"


def test_2026_certificates_use_a_quarterly_average(real_config: Config) -> None:
    method = real_config.cbam_rules["certificate_price_method"]["year_2026"]
    assert method["applies_to_year"] == 2026
    assert method["basis"] == "quarterly_average_of_quarter_of_import"
    assert method["status"] == "adopted"


def test_downstream_extension_starts_in_2028_and_is_a_proposal(
    real_config: Config,
) -> None:
    extension = real_config.cbam_rules["downstream_extension"]
    assert extension["start_year"] == 2028
    assert extension["status"] == "proposal"


def test_downstream_extension_records_that_it_uses_no_nace_codes(
    real_config: Config,
) -> None:
    """A trap worth a test. The NACE mapping is the author's, not the Commission's."""
    extension = real_config.cbam_rules["downstream_extension"]
    assert extension["scope_defined_by"] == "cn_code"
    assert extension["nace_codes"] == []
    assert "author" in extension["nace_codes_note"]


# EU origins


def test_eu_customs_territory_lists_27_member_states(real_config: Config) -> None:
    codes = real_config.cbam_rules["eu_customs_territory"]["codes"]
    assert len(codes) == 27
    assert len(set(codes)) == 27
    assert "NL" in codes


def test_no_exempt_origin_is_also_an_eu_member_state(real_config: Config) -> None:
    eu = set(real_config.cbam_rules["eu_customs_territory"]["codes"])
    exempt = {entry["code"].upper() for entry in real_config.exempt_origins()["countries"]}
    assert not (eu & exempt)
