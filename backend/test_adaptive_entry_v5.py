import unittest

from adaptive_entry_v5 import (
    calculate_adaptive_entry_decision_v5,
)


class TestAdaptiveEntryV5(unittest.TestCase):

    def setUp(self):
        self.candidate = {
            "scanner_rank": 15,
            "rsi": 60,
            "trend": "STRONG BULLISH",
            "risk": "HIGH",
        }

    def make_trades(self, count, return_percent):
        return [
            {
                "trade_book_id": i + 1,
                "entry": self.candidate.copy(),
                "realized_return_percent": return_percent,
            }
            for i in range(count)
        ]

    def test_no_history(self):
        result = calculate_adaptive_entry_decision_v5(
            self.candidate, []
        )
        self.assertEqual(
            result["decision"], "INSUFFICIENT_EVIDENCE"
        )

    def test_small_sample(self):
        result = calculate_adaptive_entry_decision_v5(
            self.candidate, self.make_trades(10, 2.0)
        )
        self.assertEqual(
            result["decision"], "INSUFFICIENT_EVIDENCE"
        )

    def test_positive_history(self):
        result = calculate_adaptive_entry_decision_v5(
            self.candidate, self.make_trades(40, 2.0)
        )
        self.assertEqual(result["decision"], "BUY")

    def test_negative_history(self):
        result = calculate_adaptive_entry_decision_v5(
            self.candidate, self.make_trades(40, -2.0)
        )
        self.assertEqual(result["decision"], "SKIP")

    def test_duplicate_trade_not_counted(self):
        trades = self.make_trades(1, 2.0)
        trades.append(trades[0].copy())

        result = calculate_adaptive_entry_decision_v5(
            self.candidate, trades
        )

        self.assertEqual(result["historical_samples"], 1)

    def test_research_only(self):
        result = calculate_adaptive_entry_decision_v5(
            self.candidate, []
        )

        self.assertTrue(result["paper"])
        self.assertTrue(result["read_only"])
        self.assertFalse(result["automatic_strategy_changes"])
        self.assertFalse(result["real_money"])


if __name__ == "__main__":
    unittest.main()
