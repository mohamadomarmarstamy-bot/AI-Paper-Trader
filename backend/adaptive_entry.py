
from __future__ import annotations

import math
from typing import Any


ADAPTIVE_ENTRY_VERSION = "adaptive_entry_v3"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _bucket(feature: str, value: Any) -> str | None:
    number = _number(value)

    if feature == "scanner_rank":
        if number is None:
            return None
        if number < 6:
            return "1-5"
        if number < 11:
            return "6-10"
        if number < 21:
            return "11-20"
        return "21+"

    if feature == "score":
        if number is None:
            return None
        if number < 70:
            return "<70"
        if number < 75:
            return "70-74"
        if number < 80:
            return "75-79"
        if number < 90:
            return "80-89"
        return "90+"

    if feature == "confidence":
        if number is None:
            return None
        if number < 70:
            return "<70"
        if number < 80:
            return "70-79"
        if number < 90:
            return "80-89"
        return "90+"

    if feature == "rsi":
        if number is None:
            return None
        if number < 30:
            return "<30"
        if number < 50:
            return "30-49"
        if number < 60:
            return "50-59"
        if number < 70:
            return "60-69"
        if number < 80:
            return "70-79"
        return "80+"

    if feature == "volume_ratio":
        if number is None:
            return None
        if number < 0.70:
            return "<0.7x"
        if number < 1.00:
            return "0.7-0.99x"
        if number < 1.50:
            return "1.0-1.49x"
        if number < 2.00:
            return "1.5-1.99x"
        if number < 5.00:
            return "2.0-4.99x"
        return "5.0x+"

    if feature == "atr_percent":
        if number is None:
            return None
        if number < 1:
            return "<1%"
        if number < 2:
            return "1-1.99%"
        if number < 4:
            return "2-3.99%"
        if number < 6:
            return "4-5.99%"
        if number < 8:
            return "6-7.99%"
        return "8%+"

    if feature == "spread_percent":
        if number is None:
            return None
        if number < 0.10:
            return "<0.10%"
        if number < 0.25:
            return "0.10-0.24%"
        if number < 0.50:
            return "0.25-0.49%"
        if number < 1.00:
            return "0.50-0.99%"
        return "1.0%+"

    if feature == "one_day_change":
        if number is None:
            return None
        if number < 0:
            return "negative"
        if number < 5:
            return "0-4.99%"
        if number < 15:
            return "5-14.99%"
        if number < 30:
            return "15-29.99%"
        return "30%+"

    if feature == "momentum_move_percent":
        if number is None:
            return None
        if number < 30:
            return "<30%"
        if number < 50:
            return "30-49%"
        if number < 100:
            return "50-99%"
        if number < 200:
            return "100-199%"
        return "200%+"

    if feature in {
        "trend",
        "trend_strength",
        "risk",
        "market_regime",
        "news_sentiment",
        "strategy_version",
        "signal",
        "macd_position",
        "momentum_candidate",
        "negative_day_weak_volume",
        "negative_day_weak_volume_shadow_forward",
    }:
        if value is None:
            return None

        if feature == "momentum_candidate":
            if isinstance(value, bool):
                return "yes" if value else "no"

        if feature == "negative_day_weak_volume_shadow_forward":
            if isinstance(value, bool):
                return (
                    "matched"
                    if value
                    else "not_matched"
                )

        normalized = str(value).strip()
        return normalized or None

    return None


def _matching_rows(
    rows: Any,
    candidate: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []

    matches = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        fingerprint = str(
            row.get("fingerprint") or ""
        ).strip()

        if not fingerprint:
            continue

        matched = True

        for part in fingerprint.split("|"):
            if "=" not in part:
                matched = False
                break

            feature, expected = (
                x.strip()
                for x in part.split("=", 1)
            )

            value = candidate.get(feature)

            if (
                value is None
                and feature == "scanner_rank"
            ):
                value = candidate.get("rank")

            if _bucket(feature, value) != expected:
                matched = False
                break

        if matched:
            matches.append(row)

    return matches


def _weighted_average(
    rows: list[dict[str, Any]],
    field: str,
) -> float | None:
    total = 0.0
    weight_total = 0.0

    for row in rows:
        value = _number(row.get(field))
        sample = _number(
            row.get("sample_size")
        )

        if value is None:
            continue

        weight = max(
            1.0,
            min(sample or 1.0, 25.0),
        )

        total += value * weight
        weight_total += weight

    if weight_total == 0:
        return None

    return total / weight_total


def calculate_adaptive_entry_decision(
    candidate: dict[str, Any],
    historical_evidence: dict[str, Any],
) -> dict[str, Any]:

    two = _matching_rows(
        historical_evidence.get(
            "two_feature_groups",
            [],
        ),
        candidate,
    )

    three = _matching_rows(
        historical_evidence.get(
            "three_feature_groups",
            [],
        ),
        candidate,
    )

    # Prefer the more specific 3-feature evidence.
    # Two-feature evidence remains useful, but overlapping
    # patterns must not be treated as independent samples.
    specific_rows = three if three else two

    positive = []
    negative = []

    for row in specific_rows:
        result = _number(
            row.get(
                "average_return_percent"
            )
        )

        sample = _number(
            row.get("sample_size")
        )

        if result is None or sample is None:
            continue

        if sample < 5:
            continue

        if result > 0:
            positive.append(row)
        elif result < 0:
            negative.append(row)

    evidence_rows = positive + negative

    # Use the largest independent-looking historical group rather
    # than adding overlapping sample sizes together.
    historical_samples = 0

    if evidence_rows:
        historical_samples = int(
            max(
                _number(
                    row.get("sample_size")
                ) or 0.0
                for row in evidence_rows
            )
        )

    positive_return = _weighted_average(
        positive,
        "average_return_percent",
    )

    negative_return = _weighted_average(
        negative,
        "average_return_percent",
    )

    win_rates = [
        _number(
            row.get("win_rate_percent")
        )
        for row in evidence_rows
    ]

    win_rates = [
        value
        for value in win_rates
        if value is not None
    ]

    average_win_rate = (
        sum(win_rates) / len(win_rates)
        if win_rates
        else None
    )

    evidence_score = 50.0

    # Return evidence has the largest influence.
    if positive_return is not None:
        evidence_score += max(
            -10.0,
            min(
                20.0,
                positive_return * 8.0,
            ),
        )

    if negative_return is not None:
        evidence_score += max(
            -20.0,
            min(
                5.0,
                negative_return * 6.0,
            ),
        )

    # Win rate is supporting evidence, not the primary signal.
    if average_win_rate is not None:
        evidence_score += max(
            -15.0,
            min(
                15.0,
                (average_win_rate - 50.0)
                * 0.25,
            ),
        )

    # Specific 3-feature evidence gets a modest bonus,
    # but pattern count itself cannot manufacture confidence.
    if three:
        evidence_score += 3.0
    elif two:
        evidence_score += 1.0

    # Evidence quality penalty.
    if historical_samples < 10:
        evidence_score -= 15.0
    elif historical_samples < 20:
        evidence_score -= 5.0

    evidence_score = max(
        0.0,
        min(
            100.0,
            evidence_score,
        ),
    )

    if historical_samples < 10:
        decision = "INSUFFICIENT_EVIDENCE"
        confidence = "LOW"
    elif evidence_score >= 65:
        decision = "BUY"
        confidence = (
            "HIGH"
            if evidence_score >= 80
            and historical_samples >= 30
            else "MEDIUM"
        )
    else:
        decision = "SKIP"
        confidence = (
            "MEDIUM"
            if evidence_score >= 40
            else "HIGH"
        )

    return {
        "version": ADAPTIVE_ENTRY_VERSION,
        "decision": decision,
        "confidence": confidence,
        "evidence_score": round(
            evidence_score,
            2,
        ),
        "historical_samples": historical_samples,
        "score_components": {
            "base_score": 50.0,
            "positive_return_average": positive_return,
            "positive_return_contribution": (
                max(-10.0, min(20.0, positive_return * 8.0))
                if positive_return is not None
                else 0.0
            ),
            "negative_return_average": negative_return,
            "negative_return_contribution": (
                max(-20.0, min(5.0, negative_return * 6.0))
                if negative_return is not None
                else 0.0
            ),
            "average_win_rate_percent": average_win_rate,
            "win_rate_contribution": (
                max(
                    -15.0,
                    min(
                        15.0,
                        (average_win_rate - 50.0) * 0.25,
                    ),
                )
                if average_win_rate is not None
                else 0.0
            ),
            "specificity_bonus": (
                3.0 if three else (1.0 if two else 0.0)
            ),
            "sample_penalty": (
                -15.0
                if historical_samples < 10
                else (
                    -5.0
                    if historical_samples < 20
                    else 0.0
                )
            ),
        },
        "matching_two_feature_patterns": len(two),
        "matching_three_feature_patterns": len(three),
        "evidence_basis": (
            "three_feature"
            if three
            else (
                "two_feature"
                if two
                else "none"
            )
        ),
        "positive_patterns": [
            {
                "fingerprint": row.get("fingerprint"),
                "sample_size": row.get("sample_size"),
                "win_rate_percent": row.get(
                    "win_rate_percent"
                ),
                "average_return_percent": row.get(
                    "average_return_percent"
                ),
            }
            for row in positive
        ],
        "negative_patterns": [
            {
                "fingerprint": row.get("fingerprint"),
                "sample_size": row.get("sample_size"),
                "win_rate_percent": row.get(
                    "win_rate_percent"
                ),
                "average_return_percent": row.get(
                    "average_return_percent"
                ),
            }
            for row in negative
        ],
        "paper_decision_active": True,
        "real_money": False,
        "automatic_strategy_changes": False,
    }
