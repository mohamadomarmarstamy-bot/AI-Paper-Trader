"""Unit tests for PAPER-only exit preflight."""
import unittest
from pro_ticker_exit_plan import plan_paper_exit


class ExitPlanTests(unittest.TestCase):
    def setUp(self):
        self.base = dict(
            symbol="AAPL", entry_price=100, current_price=100,
            held_shares=10, owned_shares=4, quote_age_seconds=2,
            market_open=True, open_sell_order=False,
            strategy_ownership_verified=True,
        )

    def plan(self, **changes):
        return plan_paper_exit(**{**self.base, **changes})

    def test_stop_loss(self):
        result = self.plan(current_price=97)
        self.assertEqual(result["trigger"], "stop_loss")
        self.assertEqual(result["sell_shares"], 4)
        self.assertFalse(result["order_submitted"])

    def test_take_profit(self):
        result = self.plan(current_price=105)
        self.assertEqual(result["trigger"], "take_profit")

    def test_no_exit_without_trigger(self):
        self.assertFalse(self.plan()["eligible_for_further_review"])

    def test_never_sell_unowned_shares(self):
        result = self.plan(owned_shares=11, current_price=90)
        self.assertIn("invalid_position_quantity", result["block_reasons"])

    def test_unverified_strategy_is_blocked(self):
        result = self.plan(strategy_ownership_verified=False, current_price=90)
        self.assertIn("strategy_ownership_unverified", result["block_reasons"])

    def test_existing_sell_is_blocked(self):
        result = self.plan(open_sell_order=True, current_price=90)
        self.assertIn("existing_sell_order", result["block_reasons"])

    def test_stale_price_is_blocked(self):
        result = self.plan(quote_age_seconds=45, current_price=90)
        self.assertIn("stale_quote", result["block_reasons"])

    def test_closed_market_is_blocked(self):
        result = self.plan(market_open=False, current_price=90)
        self.assertIn("market_closed", result["block_reasons"])

    def test_invalid_numbers_blocked(self):
        for value in (float("nan"), float("inf"), -5, True):
            with self.subTest(value=value):
                self.assertFalse(self.plan(current_price=value)["eligible_for_further_review"])


if __name__ == "__main__":
    unittest.main()
