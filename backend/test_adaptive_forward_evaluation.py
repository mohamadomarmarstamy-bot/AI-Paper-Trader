import unittest
from unittest.mock import patch

import database


class AdaptiveForwardEvaluationTests(unittest.TestCase):
    def test_future_and_still_open_outcomes_are_not_used(self):
        entry_events = [
            {
                "trade_book_id": 1,
                "timestamp": "2026-09-01T14:00:00Z",
                "details": {
                    "scanner_rank": 2,
                    "score": 85,
                    "confidence": 90,
                    "rsi": 65,
                    "volume_ratio": 3.0,
                    "atr_percent": 5.0,
                    "spread_percent": 0.2,
                    "one_day_change": 20.0,
                    "macd": 2.0,
                    "macd_signal": 1.0,
                    "trend": "STRONG BULLISH",
                    "risk": "HIGH",
                },
            },
            {
                "trade_book_id": 2,
                "timestamp": "2026-09-01T15:00:00Z",
                "details": {
                    "scanner_rank": 2,
                    "score": 85,
                    "confidence": 90,
                    "rsi": 65,
                    "volume_ratio": 3.0,
                    "atr_percent": 5.0,
                    "spread_percent": 0.2,
                    "one_day_change": 20.0,
                    "macd": 2.0,
                    "macd_signal": 1.0,
                    "trend": "STRONG BULLISH",
                    "risk": "HIGH",
                },
            },
        ]

        outcomes = [
            {
                "trade_book_id": 1,
                "symbol": "AAA",
                "won": True,
                "realized_return_percent": 2.0,
                # Trade 1 entered first but did not finish
                # until AFTER trade 2 entered.
                "created_at": "2026-09-01T16:00:00Z",
            },
            {
                "trade_book_id": 2,
                "symbol": "BBB",
                "won": False,
                "realized_return_percent": -1.0,
                "created_at": "2026-09-01T17:00:00Z",
            },
        ]

        captured_evidence = []

        def fake_decision(candidate, evidence):
            captured_evidence.append(evidence)

            sample_count = max(
                [
                    row.get("sample_size", 0)
                    for row in (
                        evidence.get("two_feature_groups", [])
                        + evidence.get("three_feature_groups", [])
                    )
                ],
                default=0,
            )

            return {
                "decision": "SKIP",
                "evidence_score": 50.0,
                "historical_samples": sample_count,
                "confidence": "MEDIUM",
            }

        with (
            patch.object(
                database,
                "load_learning_outcomes",
                return_value=outcomes,
            ),
            patch.object(
                database,
                "load_trade_book_events",
                return_value=entry_events,
            ),
            patch(
                "adaptive_entry.calculate_adaptive_entry_decision",
                side_effect=fake_decision,
            ),
        ):
            result = (
                database.calculate_adaptive_forward_evaluation(
                    minimum_group_size=1,
                    limit=100,
                )
            )

        self.assertEqual(result["evaluated_trades"], 2)
        self.assertTrue(result["time_safe"])
        self.assertEqual(
            result["evidence_rule"],
            "outcome.created_at < entry.timestamp",
        )

        # Trade 1 has no earlier completed outcomes.
        self.assertEqual(
            captured_evidence[0]["two_feature_groups"],
            [],
        )
        self.assertEqual(
            captured_evidence[0]["three_feature_groups"],
            [],
        )

        # Trade 1 was still open when trade 2 entered, so its
        # eventual result must NOT leak into trade 2's evidence.
        self.assertEqual(
            captured_evidence[1]["two_feature_groups"],
            [],
        )
        self.assertEqual(
            captured_evidence[1]["three_feature_groups"],
            [],
        )

    def test_completed_prior_outcome_becomes_available(self):
        shared_details = {
            "scanner_rank": 2,
            "score": 85,
            "confidence": 90,
            "rsi": 65,
            "volume_ratio": 3.0,
            "atr_percent": 5.0,
            "spread_percent": 0.2,
            "one_day_change": 20.0,
            "macd": 2.0,
            "macd_signal": 1.0,
            "trend": "STRONG BULLISH",
            "risk": "HIGH",
        }

        entry_events = [
            {
                "trade_book_id": 1,
                "timestamp": "2026-09-01T14:00:00Z",
                "details": dict(shared_details),
            },
            {
                "trade_book_id": 2,
                "timestamp": "2026-09-01T16:00:00Z",
                "details": dict(shared_details),
            },
        ]

        outcomes = [
            {
                "trade_book_id": 1,
                "symbol": "AAA",
                "won": True,
                "realized_return_percent": 2.0,
                # This outcome existed before trade 2 entered.
                "created_at": "2026-09-01T15:00:00Z",
            },
            {
                "trade_book_id": 2,
                "symbol": "BBB",
                "won": False,
                "realized_return_percent": -1.0,
                "created_at": "2026-09-01T17:00:00Z",
            },
        ]

        captured_evidence = []

        def fake_decision(candidate, evidence):
            captured_evidence.append(evidence)
            return {
                "decision": "SKIP",
                "evidence_score": 50.0,
                "historical_samples": 0,
                "confidence": "MEDIUM",
            }

        with (
            patch.object(
                database,
                "load_learning_outcomes",
                return_value=outcomes,
            ),
            patch.object(
                database,
                "load_trade_book_events",
                return_value=entry_events,
            ),
            patch(
                "adaptive_entry.calculate_adaptive_entry_decision",
                side_effect=fake_decision,
            ),
        ):
            database.calculate_adaptive_forward_evaluation(
                minimum_group_size=1,
                limit=100,
            )

        self.assertEqual(
            captured_evidence[0]["two_feature_groups"],
            [],
        )

        # Once trade 1 had actually completed, its feature
        # combinations are valid evidence for trade 2.
        self.assertGreater(
            len(captured_evidence[1]["two_feature_groups"]),
            0,
        )
        self.assertGreater(
            len(captured_evidence[1]["three_feature_groups"]),
            0,
        )

        for row in (
            captured_evidence[1]["two_feature_groups"]
            + captured_evidence[1]["three_feature_groups"]
        ):
            self.assertEqual(row["sample_size"], 1)


if __name__ == "__main__":
    unittest.main()