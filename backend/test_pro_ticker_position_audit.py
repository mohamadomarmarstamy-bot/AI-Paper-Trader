"""Shared PAPER account position audit tests."""
import unittest
from pro_ticker_position_audit import audit_strategy_paper_positions


class PaperPositionAuditTests(unittest.TestCase):
    def audit(self, strategy=None, broker=None):
        if strategy is None:
            strategy = [{"symbol": "AAPL", "shares": 3}]
        if broker is None:
            broker = [{"symbol": "AAPL", "qty": "5"}]
        return audit_strategy_paper_positions(strategy, broker)

    def test_shared_broker_surplus_is_not_proof_of_ownership(self):
        result = self.audit()
        self.assertTrue(result["broker_snapshot_consistent"])
        self.assertFalse(result["reconciled"])
        self.assertEqual(result["findings"][0]["unattributed_broker_shares"], 2)

    def test_shortfall_detected(self):
        result = self.audit(broker=[{"symbol": "AAPL", "qty": "1"}])
        self.assertEqual(result["reason"], "broker_position_shortfall")
        self.assertEqual(result["findings"][0]["shortfall_shares"], 2)

    def test_missing_broker_position_detected(self):
        result = self.audit(broker=[])
        self.assertEqual(result["findings"][0]["shortfall_shares"], 3)

    def test_fractional_broker_qty_fails_closed(self):
        result = self.audit(broker=[{"symbol": "AAPL", "qty": "3.5"}])
        self.assertEqual(result["reason"], "invalid_snapshot")

    def test_duplicate_broker_symbol_fails_closed(self):
        result = self.audit(broker=[
            {"symbol": "AAPL", "qty": "3"}, {"symbol": "AAPL", "qty": "4"},
        ])
        self.assertEqual(result["reason"], "invalid_snapshot")

    def test_duplicate_strategy_symbol_fails_closed(self):
        result = self.audit(strategy=[
            {"symbol": "AAPL", "shares": 2}, {"symbol": "AAPL", "shares": 1},
        ])
        self.assertEqual(result["reason"], "invalid_snapshot")

    def test_negative_broker_qty_fails_closed(self):
        result = self.audit(broker=[{"symbol": "AAPL", "qty": "-2"}])
        self.assertEqual(result["reason"], "invalid_snapshot")

    def test_no_strategy_positions_is_consistent_but_unreconciled(self):
        result = self.audit(strategy=[])
        self.assertTrue(result["broker_snapshot_consistent"])
        self.assertFalse(result["reconciled"])


if __name__ == "__main__":
    unittest.main()
