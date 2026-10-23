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

    def test_unrounded_notional_cannot_exceed_budget_by_fraction_of_cent(self):
        result = self.plan(
            reference_price=100.002, requested_shares=2,
            available_budget=200.0, broker_buying_power=200.0,
        )
        self.assertEqual(result["estimated_notional"], 200.0)
        self.assertFalse(result["allowed_for_further_review"])
        self.assertIn("virtual_budget_exceeded", result["block_reasons"])
        self.assertIn("broker_buying_power_exceeded", result["block_reasons"])
        self.assertFalse(result["order_submitted"])

    def test_blocks_stale_quote(self):
        self.assertIn("stale_quote", self.plan(quote_age_seconds=31)["block_reasons"])

    def test_blocks_duplicate_position(self):
        self.assertIn("position_already_exists", self.plan(existing_position=True)["block_reasons"])

    def test_blocks_open_order(self):
        self.assertIn("open_order_already_exists", self.plan(existing_open_order=True)["block_reasons"])

    def test_blocks_closed_market(self):
        self.assertIn("market_closed", self.plan(market_open=False)["block_reasons"])

    def test_unknown_position_or_order_state_blocks(self):
        for value in (None, 0, 1, "false", [], {}):
            with self.subTest(value=value):
                self.assertFalse(self.plan(existing_position=value)["allowed_for_further_review"])
                self.assertFalse(self.plan(existing_open_order=value)["allowed_for_further_review"])

    def test_invalid_maximum_quote_age_blocks(self):
        for value in (None, 0, -1, True, float("nan"), float("inf"), "30"):
            with self.subTest(value=value):
                result = self.plan(maximum_quote_age_seconds=value)
                self.assertIn("invalid_maximum_quote_age", result["block_reasons"])
                self.assertFalse(result["order_submitted"])

    def test_market_open_must_be_explicit_boolean_true(self):
        for value in (None, 0, 1, "open"):
            with self.subTest(value=value):
                self.assertIn("market_closed", self.plan(market_open=value)["block_reasons"])

    def test_rejects_overflowing_or_zero_rounded_notional(self):
        for price, shares in ((1e308, 2), (0.00001, 1)):
            with self.subTest(price=price, shares=shares):
                result = self.plan(reference_price=price, requested_shares=shares)
                self.assertFalse(result["allowed_for_further_review"])
                self.assertIn("invalid_estimated_notional", result["block_reasons"])
                self.assertFalse(result["order_submitted"])

    def test_symbol_grammar_matches_workspace_and_broker_boundary(self):
        for symbol in [123,True,'123','ÄAPL','A..TOOLONG01','<AAPL>']:
            with self.subTest(symbol=symbol):self.assertIn('invalid_symbol',self.plan(symbol=symbol)['block_reasons'])
        self.assertTrue(self.plan(symbol='brk.b')['allowed_for_further_review'])

    def test_extreme_integer_inputs_fail_closed_without_overflow(self):
        for field in ['reference_price','broker_buying_power','available_budget','quote_age_seconds','maximum_quote_age_seconds','requested_shares']:
            with self.subTest(field=field):
                result=self.plan(**{field:10**1000})
                self.assertFalse(result['allowed_for_further_review']);self.assertFalse(result['order_submitted'])

    def test_blocks_bad_values(self):
        for value in (float("nan"), float("inf"), -1, True):
            with self.subTest(value=value):
                self.assertFalse(self.plan(reference_price=value)["allowed_for_further_review"])


if __name__ == "__main__":
    unittest.main()
