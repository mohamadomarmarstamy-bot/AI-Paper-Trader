import unittest

from adaptive_entry_v4 import calculate_adaptive_entry_decision_v4


class AdaptiveV4Tests(unittest.TestCase):

    def setUp(self):
        self.candidate = {
            "rsi": 55,
            "atr_percent": 5.2,
            "risk": "HIGH",
        }

    def evidence(self, rows):
        return {
            "two_feature_groups": [],
            "three_feature_groups": rows,
        }

    def row(self, samples, win_rate, avg_return):
        return {
            "fingerprint": "rsi=50-59 | atr_percent=4-5.99% | risk=HIGH",
            "sample_size": samples,
            "win_rate_percent": win_rate,
            "average_return_percent": avg_return,
        }

    def test_no_evidence(self):
        result = calculate_adaptive_entry_decision_v4(
            self.candidate, self.evidence([])
        )
        self.assertEqual(result["decision"], "INSUFFICIENT_EVIDENCE")

    def test_small_sample(self):
        result = calculate_adaptive_entry_decision_v4(
            self.candidate, self.evidence([self.row(8, 80, 2)])
        )
        self.assertEqual(result["decision"], "INSUFFICIENT_EVIDENCE")

    def test_positive_evidence(self):
        result = calculate_adaptive_entry_decision_v4(
            self.candidate, self.evidence([self.row(40, 75, 1.5)])
        )
        self.assertEqual(result["decision"], "BUY")

    def test_negative_evidence(self):
        result = calculate_adaptive_entry_decision_v4(
            self.candidate, self.evidence([self.row(40, 30, -1.5)])
        )
        self.assertEqual(result["decision"], "SKIP")

    def test_research_only(self):
        result = calculate_adaptive_entry_decision_v4(
            self.candidate, self.evidence([])
        )
        self.assertFalse(result["automatic_strategy_changes"])
        self.assertFalse(result["real_money"])


if __name__ == "__main__":
    unittest.main()
