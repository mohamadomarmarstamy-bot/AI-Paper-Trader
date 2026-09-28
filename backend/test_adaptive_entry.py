from adaptive_entry import (
    _bucket,
    calculate_adaptive_entry_decision,
)


def row(
    fingerprint,
    sample_size,
    win_rate,
    average_return,
):
    return {
        "fingerprint": fingerprint,
        "sample_size": sample_size,
        "win_rate_percent": win_rate,
        "average_return_percent": average_return,
    }


def evidence(two=None, three=None):
    return {
        "two_feature_groups": two or [],
        "three_feature_groups": three or [],
    }


def base_candidate():
    return {
        "scanner_rank": 7,
        "score": 85,
        "confidence": 82,
        "rsi": 55,
        "volume_ratio": 1.2,
        "atr_percent": 5.2,
        "spread_percent": 0.6,
        "one_day_change": 3.0,
        "momentum_move_percent": 6.0,
        "trend": "BULLISH",
        "trend_strength": "STRONG",
        "risk": "HIGH",
        "market_regime": "NEUTRAL",
        "news_sentiment": "NEUTRAL",
        "strategy_version": "rank20_v1",
        "signal": "BUY",
        "macd_position": "above_signal",
        "momentum_candidate": False,
    }


def test_categorical_bucket_matching():
    assert _bucket("trend", "BULLISH") == "BULLISH"
    assert _bucket("risk", "HIGH") == "HIGH"
    assert (
        _bucket(
            "macd_position",
            "above_signal",
        )
        == "above_signal"
    )
    assert (
        _bucket(
            "momentum_candidate",
            True,
        )
        == "yes"
    )
    assert (
        _bucket(
            "momentum_candidate",
            False,
        )
        == "no"
    )


def test_overlapping_samples_are_not_summed():
    candidate = base_candidate()

    result = calculate_adaptive_entry_decision(
        candidate,
        evidence(
            three=[
                row(
                    "rsi=50-59 | "
                    "atr_percent=4-5.99% | "
                    "trend=BULLISH",
                    8,
                    75,
                    1.2,
                ),
                row(
                    "rsi=50-59 | "
                    "atr_percent=4-5.99% | "
                    "risk=HIGH",
                    8,
                    75,
                    1.1,
                ),
            ],
        ),
    )

    assert result["historical_samples"] == 8
    assert (
        result["decision"]
        == "INSUFFICIENT_EVIDENCE"
    )


def test_positive_evidence_can_buy():
    candidate = base_candidate()

    result = calculate_adaptive_entry_decision(
        candidate,
        evidence(
            three=[
                row(
                    "rsi=50-59 | "
                    "atr_percent=4-5.99% | "
                    "risk=HIGH",
                    30,
                    70,
                    1.0,
                ),
            ],
        ),
    )

    assert result["historical_samples"] == 30
    assert result["decision"] == "BUY"
    assert result["evidence_score"] >= 65


def test_negative_evidence_skips():
    candidate = base_candidate()

    result = calculate_adaptive_entry_decision(
        candidate,
        evidence(
            three=[
                row(
                    "scanner_rank=6-10 | "
                    "rsi=50-59 | "
                    "macd_position=above_signal",
                    30,
                    35,
                    -0.8,
                ),
            ],
        ),
    )

    assert result["historical_samples"] == 30
    assert result["decision"] == "SKIP"
    assert result["evidence_score"] < 65


def test_no_evidence_is_insufficient():
    result = calculate_adaptive_entry_decision(
        base_candidate(),
        evidence(),
    )

    assert result["historical_samples"] == 0
    assert (
        result["decision"]
        == "INSUFFICIENT_EVIDENCE"
    )


def test_audit_metadata_is_accurate():
    result = calculate_adaptive_entry_decision(
        base_candidate(),
        evidence(),
    )

    assert result["version"] == "adaptive_entry_v3"
    assert result["paper_decision_active"] is True
    assert result["real_money"] is False
    assert (
        result["automatic_strategy_changes"]
        is False
    )


def test_database_bucket_parity():
    # one_day_change boundaries
    assert _bucket("one_day_change", -0.01) == "negative"
    assert _bucket("one_day_change", 0) == "0-4.99%"
    assert _bucket("one_day_change", 4.99) == "0-4.99%"
    assert _bucket("one_day_change", 5) == "5-14.99%"
    assert _bucket("one_day_change", 14.99) == "5-14.99%"
    assert _bucket("one_day_change", 15) == "15-29.99%"
    assert _bucket("one_day_change", 29.99) == "15-29.99%"
    assert _bucket("one_day_change", 30) == "30%+"

    # momentum_move_percent boundaries
    assert (
        _bucket("momentum_move_percent", -1)
        == "<30%"
    )
    assert (
        _bucket("momentum_move_percent", 0)
        == "<30%"
    )
    assert (
        _bucket("momentum_move_percent", 29.99)
        == "<30%"
    )
    assert (
        _bucket("momentum_move_percent", 30)
        == "30-49%"
    )
    assert (
        _bucket("momentum_move_percent", 49.99)
        == "30-49%"
    )
    assert (
        _bucket("momentum_move_percent", 50)
        == "50-99%"
    )
    assert (
        _bucket("momentum_move_percent", 99.99)
        == "50-99%"
    )
    assert (
        _bucket("momentum_move_percent", 100)
        == "100-199%"
    )
    assert (
        _bucket("momentum_move_percent", 199.99)
        == "100-199%"
    )
    assert (
        _bucket("momentum_move_percent", 200)
        == "200%+"
    )

if __name__ == "__main__":
    tests = [
        test_categorical_bucket_matching,
        test_overlapping_samples_are_not_summed,
        test_positive_evidence_can_buy,
        test_negative_evidence_skips,
        test_no_evidence_is_insufficient,
        test_audit_metadata_is_accurate,
        test_database_bucket_parity,
    ]

    for test in tests:
        test()
        print("PASS:", test.__name__)

    print(
        f"SUCCESS: {len(tests)}/{len(tests)} "
        "adaptive-entry regression tests passed."
    )
