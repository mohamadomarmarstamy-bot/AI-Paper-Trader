"""Tests for Pro Ticker PAPER-only admission policy."""
import unittest

from pro_ticker_paper_policy import check_pro_ticker_paper_entry


class PaperAdmissionTests(unittest.TestCase):
    def eligible(self, **overrides):
        kwargs = dict(
            symbol="AAPL", proposed_notional=1000.0,
            pro_ticker_committed=0.0, broker_buying_power=10000.0,
            open_position_symbols=set(), open_order_symbols=set(),
            market_open=True, broker_paper=True, data_age_seconds=5.0,
        )
        kwargs.update(overrides)
        return check_pro_ticker_paper_entry(**kwargs)

    def test_eligible_paper_entry(self):
        self.assertTrue(self.eligible().allowed)

    def test_live_broker_rejected(self):
        self.assertEqual(self.eligible(broker_paper=False).reason, "paper_account_required")

    def test_missing_broker_mode_rejected(self):
        self.assertFalse(self.eligible(broker_paper=None).allowed)

    def test_unknown_broker_mode_rejected(self):
        self.assertFalse(self.eligible(broker_paper="paper").allowed)

    def test_numeric_broker_mode_rejected(self):
        self.assertFalse(self.eligible(broker_paper=1).allowed)

    def test_closed_market_rejected(self):
        self.assertFalse(self.eligible(market_open=False).allowed)

    def test_market_open_requires_explicit_true(self):
        for value in (None, 0, 1, "true", "open", [], {}):
            with self.subTest(value=value):
                decision = self.eligible(market_open=value)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, "regular_market_closed")

    def test_stale_data_rejected(self):
        self.assertEqual(self.eligible(data_age_seconds=300).reason, "stale_market_data")

    def test_open_position_and_order_rejected(self):
        self.assertEqual(self.eligible(open_position_symbols={"AAPL"}).reason, "existing_position")
        self.assertEqual(self.eligible(open_order_symbols={"aapl"}).reason, "existing_open_order")

    def test_virtual_budget_rejected(self):
        self.assertFalse(self.eligible(pro_ticker_committed=49500, proposed_notional=1000).allowed)

    def test_broker_buying_power_rejected(self):
        self.assertFalse(self.eligible(broker_buying_power=50).allowed)

    def test_malformed_symbols_fail_closed(self):
        for symbol in ("", " ", "AAPL!", "AA PL", ".", "..", "AAPL/US"):
            with self.subTest(symbol=symbol):
                decision = self.eligible(symbol=symbol)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, "invalid_symbol")

    def test_missing_or_malformed_broker_collision_snapshots_fail_closed(self):
        for value in (None, [], {}, "AAPL", {None}, {""}):
            for field in ("open_position_symbols", "open_order_symbols"):
                with self.subTest(value=value, field=field):
                    decision = self.eligible(**{field: value})
                    self.assertFalse(decision.allowed)
                    self.assertEqual(decision.reason, "invalid_broker_collision_snapshot")

    def test_non_ascii_symbol_is_rejected(self):
        decision = self.eligible(symbol="AÁPL")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "invalid_symbol")

    def test_valid_dotted_ticker_is_accepted(self):
        self.assertTrue(self.eligible(symbol="BRK.B").allowed)

    def test_nan_rejected(self):
        self.assertFalse(self.eligible(proposed_notional=float("nan")).allowed)


if __name__ == "__main__":
    unittest.main()
