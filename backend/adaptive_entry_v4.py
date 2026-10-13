from __future__ import annotations

from typing import Any

from adaptive_entry import _matching_rows, _number

ADAPTIVE_ENTRY_VERSION = "adaptive_entry_v4"


def calculate_adaptive_entry_decision_v4(
    candidate: dict[str, Any],
    historical_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Conservative, research-only PAPER scoring."""

    three = _matching_rows(
        historical_evidence.get("three_feature_groups", []),
        candidate,
    )
    two = _matching_rows(
        historical_evidence.get("two_feature_groups", []),
        candidate,
    )

    # Prefer specific evidence without counting overlapping
    # historical groups as independent observations.
    rows = three if three else two

    valid = [
        row for row in rows
        if (_number(row.get("sample_size")) or 0) >= 5
        and _number(row.get("average_return_percent")) is not None
        and _number(row.get("win_rate_percent")) is not None
    ]

    if not valid:
        return {
            "version": ADAPTIVE_ENTRY_VERSION,
            "decision": "INSUFFICIENT_EVIDENCE",
            "evidence_score": 50.0,
            "historical_samples": 0,
            "confidence": "LOW",
            "paper": True,
            "read_only": True,
            "automatic_strategy_changes": False,
            "real_money": False,
        }

    # Select one historical pattern to prevent overlap
    # from artificially multiplying evidence.
    best = max(
        valid,
        key=lambda row: (
            _number(row.get("sample_size")) or 0,
            len(str(row.get("fingerprint") or "")),
        ),
    )

    samples = int(_number(best.get("sample_size")) or 0)
    win_rate = _number(best.get("win_rate_percent")) or 0.0
    avg_return = _number(best.get("average_return_percent")) or 0.0

    # Shrink noisy historical win rates toward 50%.
    adjusted_win_rate = (
        (samples * win_rate + 20 * 50.0)
        / (samples + 20)
    )

    # Conservative, bounded score.
    score = (
        50.0
        + (adjusted_win_rate - 50.0) * 0.5
        + max(-10.0, min(10.0, avg_return * 5.0))
    )

    score = round(max(0.0, min(100.0, score)), 2)

    if samples < 20:
        decision = "INSUFFICIENT_EVIDENCE"
        confidence = "LOW"
    elif score >= 60 and avg_return > 0:
        decision = "BUY"
        confidence = "MEDIUM"
    else:
        decision = "SKIP"
        confidence = "MEDIUM"

    return {
        "version": ADAPTIVE_ENTRY_VERSION,
        "decision": decision,
        "evidence_score": score,
        "historical_samples": samples,
        "confidence": confidence,
        "matched_fingerprint": best.get("fingerprint"),
        "historical_win_rate_percent": win_rate,
        "historical_average_return_percent": avg_return,
        "paper": True,
        "read_only": True,
        "automatic_strategy_changes": False,
        "real_money": False,
    }
