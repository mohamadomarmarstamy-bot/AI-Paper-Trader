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

    def test_closed_market_rejected(self):
        self.assertFalse(self.eligible(market_open=False).allowed)

    def test_stale_data_rejected(self):
        self.assertEqual(self.eligible(data_age_seconds=300).reason, "stale_market_data")

    def test_open_position_and_order_rejected(self):
        self.assertEqual(self.eligible(open_position_symbols={"AAPL"}).reason, "existing_position")
        self.assertEqual(self.eligible(open_order_symbols={"aapl"}).reason, "existing_open_order")

    def test_virtual_budget_rejected(self):
        self.assertFalse(self.eligible(pro_ticker_committed=49500, proposed_notional=1000).allowed)

    def test_broker_buying_power_rejected(self):
        self.assertFalse(self.eligible(broker_buying_power=50).allowed)

    def test_nan_rejected(self):
        self.assertFalse(self.eligible(proposed_notional=float("nan")).allowed)


if __name__ == "__main__":
    unittest.main()
