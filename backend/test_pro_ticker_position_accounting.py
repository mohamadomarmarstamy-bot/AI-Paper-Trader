"""Unit tests for simulated strategy position accounting."""
import unittest
from pro_ticker_position_accounting import apply_verified_paper_fill


class PositionAccountingTests(unittest.TestCase):
    def setUp(self):
        self.base = dict(
            symbol="AAPL", current_shares=4, filled_shares=2,
            side="buy", ownership_verified=True, fill_verified=True,
            already_accounted=False,
        )

    def review(self, **updates):
        return apply_verified_paper_fill(**{**self.base, **updates})

    def test_verified_buy_adds_shares(self):
        result = self.review()
        self.assertTrue(result["accepted"])
        self.assertEqual(result["next_shares"], 6)
        self.assertFalse(result["database_updated"])

    def test_verified_sell_subtracts_shares(self):
        self.assertEqual(self.review(side="sell")["next_shares"], 2)

    def test_rejects_oversell(self):
        self.assertEqual(
            self.review(side="sell", filled_shares=5)["reason"],
            "sell_exceeds_strategy_shares",
        )

    def test_rejects_unverified_ownership(self):
        self.assertFalse(self.review(ownership_verified=False)["accepted"])

    def test_rejects_unverified_fill(self):
        self.assertFalse(self.review(fill_verified=False)["accepted"])

    def test_rejects_duplicate_fill(self):
        self.assertFalse(self.review(already_accounted=True)["accepted"])

    def test_rejects_unknown_idempotency_state(self):
        self.assertFalse(self.review(already_accounted=None)["accepted"])

    def test_rejects_fractional_shares(self):
        self.assertFalse(self.review(filled_shares=1.5)["accepted"])

    def test_rejects_boolean_quantity(self):
        self.assertFalse(self.review(filled_shares=True)["accepted"])


if __name__ == "__main__":
    unittest.main()
