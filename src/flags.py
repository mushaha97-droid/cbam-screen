"""Risk flags and the materiality band.

Flags say why a borrower deserves attention, in words a credit officer can act
on. Every flag carries a reason string, because a flag with no reason is noise.

All bands and parameters come from thresholds.yaml, which says plainly that they
are the author's assumptions rather than law.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.config import Config, ConfigError, require
from src.cost import CostResult
from src.schema import Borrower, DeclarantStatus, PassThrough, SupplierData
from src.tiering import TierResult, eu_country_codes, exempt_country_codes

NO_DECLARANT = "NO_DECLARANT"
DEFAULT_VALUES = "DEFAULT_VALUES"
LOW_PASS_THROUGH = "LOW_PASS_THROUGH"
SUPPLIER_CONCENTRATION = "SUPPLIER_CONCENTRATION"
ESTIMATED_INPUTS = "ESTIMATED_INPUTS"
RATING_GAP = "RATING_GAP"
MONITOR = "MONITOR"
NEGATIVE_EBITDA = "NEGATIVE_EBITDA"
ORIGIN_UNKNOWN = "ORIGIN_UNKNOWN"


@dataclass(frozen=True)
class Flag:
    """One raised flag."""

    code: str
    reason: str


@dataclass(frozen=True)
class Materiality:
    """Where a cost lands against EBITDA."""

    band: str
    ratio: float | None
    label: str
    ebitda_is_positive: bool


# ---------------------------------------------------------------------------
# Materiality
# ---------------------------------------------------------------------------


def band_order(config: Config) -> list[str]:
    """Band codes from least to most material."""
    block = require(config.thresholds, "materiality_bands", "thresholds.yaml")
    return [entry["band"] for entry in require(block, "bands", "materiality_bands")]


def materiality_band(cost_eur: float, ebitda_eur: float, config: Config) -> Materiality:
    """Place a cost in a band.

    A non-positive EBITDA has no usable ratio, so the band comes from the
    negative_ebitda_band rule in config rather than from a division.
    """
    block = require(config.thresholds, "materiality_bands", "thresholds.yaml")
    bands = require(block, "bands", "thresholds.yaml materiality_bands")

    if ebitda_eur <= 0:
        code = require(block, "negative_ebitda_band", "thresholds.yaml materiality_bands")
        label = next(
            (entry["label"] for entry in bands if entry["band"] == code), str(code)
        )
        return Materiality(band=str(code), ratio=None, label=label, ebitda_is_positive=False)

    ratio = cost_eur / ebitda_eur
    for entry in bands:
        low = float(entry["min_ratio"])
        high = entry["max_ratio"]
        if ratio >= low and (high is None or ratio < float(high)):
            return Materiality(
                band=str(entry["band"]),
                ratio=ratio,
                label=str(entry["label"]),
                ebitda_is_positive=True,
            )
    raise ConfigError(
        f"thresholds.yaml materiality_bands: no band covers a ratio of {ratio}. "
        f"The bands must span every non-negative value."
    )


def band_gap(cbam_band: str, bank_band: str, config: Config) -> int:
    """How many bands the CBAM band sits above the bank's own rating."""
    order = band_order(config)
    if cbam_band not in order or bank_band not in order:
        raise ConfigError(
            f"unknown band in comparison: {cbam_band!r} against {bank_band!r}, "
            f"known bands are {order}"
        )
    return order.index(cbam_band) - order.index(bank_band)


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def _params(config: Config, code: str) -> Mapping[str, Any]:
    flags = require(config.thresholds, "flags", "thresholds.yaml")
    if code not in flags:
        raise ConfigError(f"thresholds.yaml flags: no entry for {code!r}")
    return flags[code].get("params") or {}


def evaluate_flags(
    borrower: Borrower,
    cost: CostResult,
    config: Config,
    tier: TierResult | None = None,
    materiality: Materiality | None = None,
) -> list[Flag]:
    """Every flag that applies to one borrower in one costed year."""
    raised: list[Flag] = []
    band = materiality or materiality_band(cost.cost_eur, borrower.ebitda_eur, config)

    # Below the threshold there is no obligation, but the row stays on a watch list.
    if MONITOR in cost.flags:
        raised.append(
            Flag(
                MONITOR,
                f"{cost.counted_tonnes:g} t of counting CBAM goods against a "
                f"{cost.threshold_tonnes:g} t threshold, so nothing is owed this "
                f"year. A small rise in volume creates a full-year obligation "
                f"backdated to 1 January.",
            )
        )

    # An authorisation gap is an operational cliff, not a cost question.
    if (
        cost.tier in (2, 3)
        and not cost.below_threshold
        and borrower.declarant_status is not DeclarantStatus.YES
    ):
        raised.append(
            Flag(
                NO_DECLARANT,
                f"carries a direct obligation above the threshold but declarant "
                f"status is {borrower.declarant_status.value}. Only an authorised "
                f"CBAM declarant may import these goods.",
            )
        )

    if borrower.supplier_data is not SupplierData.VERIFIED:
        raised.append(
            Flag(
                DEFAULT_VALUES,
                f"supplier data is {borrower.supplier_data.value}, so default "
                f"emission values were used. Defaults are conservative, so the "
                f"cost is more likely high than low, and verified data would "
                f"reduce it.",
            )
        )

    max_share = float(_params(config, LOW_PASS_THROUGH).get("max_share", 0.0))
    effective_share = borrower.pass_through_share_effective
    if borrower.pass_through is PassThrough.LOW:
        raised.append(
            Flag(LOW_PASS_THROUGH, "the bank reports a low ability to pass cost on.")
        )
    elif (
        borrower.pass_through is PassThrough.UNKNOWN
        and effective_share is not None
        and effective_share < max_share
    ):
        raised.append(
            Flag(
                LOW_PASS_THROUGH,
                f"no pass-through was reported and the sector default share of "
                f"{effective_share:.0%} sits below {max_share:.0%}.",
            )
        )

    min_share = float(_params(config, SUPPLIER_CONCENTRATION).get("min_share", 1.0))
    origin = borrower.top_supplier_country
    if (
        borrower.share_top_supplier is not None
        and borrower.share_top_supplier > min_share
        and origin is not None
        and origin not in eu_country_codes(config)
    ):
        exempt = origin in exempt_country_codes(config)
        raised.append(
            Flag(
                SUPPLIER_CONCENTRATION,
                f"{borrower.share_top_supplier:.0%} of supply comes from {origin}, "
                f"above the {min_share:.0%} parameter"
                + (
                    ", though that origin is outside CBAM scope."
                    if exempt
                    else ", which is outside the EU and in CBAM scope."
                ),
            )
        )

    if borrower.estimated:
        raised.append(
            Flag(
                ESTIMATED_INPUTS,
                f"filled from sector averages: {', '.join(borrower.estimated_fields)}. "
                f"The cost is indicative only.",
            )
        )

    if (tier or cost) and getattr(tier, "origin_unknown", False):
        raised.append(
            Flag(
                ORIGIN_UNKNOWN,
                "CBAM imports were reported with no supplier origin, so the tool "
                "could not test whether the origin is exempt. Treated as in scope.",
            )
        )

    if not band.ebitda_is_positive:
        raised.append(
            Flag(
                NEGATIVE_EBITDA,
                f"EBITDA is {borrower.ebitda_eur:,.0f}, so the cost to EBITDA ratio "
                f"has no meaning and the band was set to {band.band} by rule.",
            )
        )

    min_gap = int(_params(config, RATING_GAP).get("min_band_gap", 2))
    if borrower.bank_transition_rating is not None:
        gap = band_gap(band.band, borrower.bank_transition_rating.value, config)
        if gap >= min_gap:
            raised.append(
                Flag(
                    RATING_GAP,
                    f"CBAM band {band.band} sits {gap} bands above the bank's own "
                    f"transition rating of {borrower.bank_transition_rating.value}. "
                    f"The existing rating may not reflect this risk.",
                )
            )

    return raised


def flag_codes(flags: list[Flag]) -> tuple[str, ...]:
    """Just the codes, in a stable order, for tables and roll-ups."""
    return tuple(sorted({flag.code for flag in flags}))
