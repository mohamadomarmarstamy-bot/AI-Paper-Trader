"""Unit tests for pure PAPER entry planning."""
import unittest
from pro_ticker_execution_plan import plan_paper_entry


class PaperEntryPlanTests(unittest.TestCase):
    def setUp(self):
        self.kwargs = dict(
            symbol="AAPL", reference_price=100.0, requested_shares=2,
            available_budget=1000.0, broker_buying_power=1000.0,
            market_open=True, quote_age_seconds=2.0,
            existing_position=False, existing_open_order=False,
        )

    def plan(self, **overrides):
        return plan_paper_entry(**{**self.kwargs, **overrides})

    def test_valid_plan_is_still_preview_only(self):
        result = self.plan()
        self.assertTrue(result["allowed_for_further_review"])
        self.assertFalse(result["order_submitted"])
        self.assertEqual(result["estimated_notional"], 200)

    def test_blocks_budget_overrun(self):
        self.assertIn("virtual_budget_exceeded", self.plan(available_budget=199)["block_reasons"])

    def test_blocks_broker_power_overrun(self):
        self.assertIn("broker_buying_power_exceeded", self.plan(broker_buying_power=199)["block_reasons"])

    def test_blocks_stale_quote(self):
        self.assertIn("stale_quote", self.plan(quote_age_seconds=31)["block_reasons"])

    def test_blocks_duplicate_position(self):
        self.assertIn("position_already_exists", self.plan(existing_position=True)["block_reasons"])

    def test_blocks_open_order(self):
        self.assertIn("open_order_already_exists", self.plan(existing_open_order=True)["block_reasons"])

    def test_blocks_closed_market(self):
        self.assertIn("market_closed", self.plan(market_open=False)["block_reasons"])

    def test_blocks_bad_values(self):
        for value in (float("nan"), float("inf"), -1, True):
            with self.subTest(value=value):
                self.assertFalse(self.plan(reference_price=value)["allowed_for_further_review"])


if __name__ == "__main__":
    unittest.main()
