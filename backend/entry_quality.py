"""Deterministic entry checks, shared by ordinary and momentum candidates.

Confidence is a heuristic input, not a calibrated probability of profit.
This module never submits orders or learns parameters from trade outcomes.
"""

import math
from typing import Any

ENTRY_POLICY_VERSION = "entry_quality_v3_chase_guard"


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def tighten_score_minimum(current: float, increase: float) -> float:
    """Cap ordinary tightening at 90 without lowering a stricter minimum."""
    return max(current, min(90.0, current + max(0.0, increase)))


def evaluate_entry_quality(
    candidate: dict[str, Any],
    *,
    score_min: float,
    confidence_min: float,
    max_scanner_rank: int,
    max_atr_percent: float,
) -> dict[str, Any]:
    """Fail closed on invalid selection inputs; momentum grants no exemption."""
    failed = []
    score = finite_number(candidate.get("score"))
    confidence = finite_number(candidate.get("confidence"))
    rank_value = candidate.get("scanner_rank")
    if rank_value is None:
        rank_value = candidate.get("rank")
    rank = finite_number(rank_value)
    atr = finite_number(candidate.get("atr_percent"))
    rsi = finite_number(candidate.get("rsi"))
    one_day_change = finite_number(candidate.get("one_day_change"))

    if str(candidate.get("signal", "")).strip().upper() != "BUY":
        failed.append("signal_not_buy")
    if score is None or not 0 <= score <= 100:
        failed.append("score_missing_or_invalid")
    elif score < score_min:
        failed.append("score_below_adjusted_minimum")
    if confidence is None or not 0 <= confidence <= 100:
        failed.append("confidence_missing_or_invalid")
    elif confidence < confidence_min:
        failed.append("confidence_below_minimum")
    if rank is None or rank < 1 or not rank.is_integer():
        failed.append("scanner_rank_missing_or_invalid")
    elif rank > max_scanner_rank:
        failed.append("scanner_rank_above_maximum")
    if atr is None or atr <= 0:
        failed.append("atr_missing_or_invalid")
    elif atr > max_atr_percent:
        failed.append("atr_above_maximum")
    if candidate.get("scanner_stale") is not False:
        failed.append("scanner_stale_or_freshness_unknown")

    # Historical PAPER evidence showed poor follow-through when entries were
    # simultaneously overbought and already extremely extended.
    # Keep each metric optional: missing diagnostics do not create a new
    # rejection path beyond the existing required entry-quality fields.
    if (
        rsi is not None
        and one_day_change is not None
        and rsi >= 70.0
        and one_day_change >= 20.0
    ):
        failed.append("overextended_momentum_chase_risk")

    return {
        "policy_version": ENTRY_POLICY_VERSION,
        "passed": not failed,
        "failed_requirements": failed,
        "required_score": score_min,
        "required_confidence": confidence_min,
        "maximum_scanner_rank": max_scanner_rank,
        "maximum_atr_percent": max_atr_percent,
        "confidence_kind": "heuristic_not_win_probability",
    }
