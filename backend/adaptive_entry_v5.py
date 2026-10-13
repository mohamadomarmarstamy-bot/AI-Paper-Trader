from __future__ import annotations

import math
from typing import Any

from adaptive_entry import _bucket

VERSION = "adaptive_entry_v5"

FEATURES = (
    "scanner_rank",
    "rsi",
    "volume_ratio",
    "atr_percent",
    "spread_percent",
    "one_day_change",
    "macd_position",
    "trend",
    "risk",
)


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def labels(candidate):
    result = {}

    for feature in FEATURES:
        value = candidate.get(feature)

        if feature == "macd_position" and value is None:
            macd = number(candidate.get("macd"))
            signal = number(candidate.get("macd_signal"))

            if macd is not None and signal is not None:
                value = (
                    "above_signal" if macd > signal
                    else "below_signal" if macd < signal
                    else "equal_signal"
                )

        label = _bucket(feature, value)

        if label is not None:
            result[feature] = label

    return result


def calculate_adaptive_entry_decision_v5(
    candidate: dict[str, Any],
    historical_trades: list[dict[str, Any]],
) -> dict[str, Any]:

    target = labels(candidate)
    matched = []
    seen = set()

    for trade in historical_trades:
        trade_id = trade.get("trade_book_id")

        if trade_id is None or trade_id in seen:
            continue

        entry = trade.get("entry")

        if not isinstance(entry, dict):
            continue

        historical = labels(entry)

        matching = sum(
            1
            for feature, label in target.items()
            if historical.get(feature) == label
        )

        # Require at least three matching indicators.
        if matching < 3:
            continue

        realized_return = number(
            trade.get("realized_return_percent")
        )

        if realized_return is None:
            continue

        seen.add(trade_id)
        matched.append(realized_return)

    samples = len(matched)

    if samples:
        wins = sum(1 for value in matched if value > 0)
        win_rate = wins / samples * 100
        average_return = sum(matched) / samples
    else:
        win_rate = 0.0
        average_return = 0.0

    # Shrink small-sample win rates toward 50%.
    adjusted_win_rate = (
        (samples * win_rate + 30 * 50.0)
        / (samples + 30)
    )

    score = (
        50.0
        + (adjusted_win_rate - 50.0) * 0.5
        + max(-10.0, min(10.0, average_return * 5.0))
    )

    score = round(max(0.0, min(100.0, score)), 2)

    if samples < 30:
        decision = "INSUFFICIENT_EVIDENCE"
        confidence = "LOW"
    elif score >= 60 and average_return > 0:
        decision = "BUY"
        confidence = "MEDIUM"
    else:
        decision = "SKIP"
        confidence = "MEDIUM"

    return {
        "version": VERSION,
        "decision": decision,
        "evidence_score": score,
        "historical_samples": samples,
        "historical_win_rate_percent": round(win_rate, 2),
        "historical_average_return_percent": round(
            average_return, 4
        ),
        "confidence": confidence,
        "paper": True,
        "read_only": True,
        "automatic_strategy_changes": False,
        "real_money": False,
    }
