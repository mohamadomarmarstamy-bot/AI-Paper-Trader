"""Combined PAPER allocation and broker review tests."""
import unittest
from pro_ticker_combined_paper_audit import review_paper_allocation_and_broker


class CombinedPaperAuditTests(unittest.TestCase):
    def setUp(self):
        self.reservations = [{
            "symbol": "AAPL", "status": "submitted", "expected_shares": 5,
            "broker_order_id": "paper-entry",
        }]
        self.strategy = [{"symbol": "AAPL", "shares": 3}]
        self.broker = [{"symbol": "AAPL", "qty": "3"}]

    def review(self, reservations=None, strategy=None, broker=None):
        return review_paper_allocation_and_broker(
            self.reservations if reservations is None else reservations,
            self.strategy if strategy is None else strategy,
            self.broker if broker is None else broker,
        )

    def test_matching_shared_broker_does_not_prove_ownership(self):
        result = self.review()
        self.assertTrue(result["allocation_audit"]["ledger_consistent"])
        self.assertFalse(result["reconciled"])
        self.assertFalse(result["budget_released"])

    def test_internal_mismatch_blocks_broker_review(self):
        result = self.review(strategy=[{"symbol": "AAPL", "shares": 6}])
        self.assertEqual(result["reason"], "internal_allocation_audit_failed")
        self.assertIsNone(result["broker_audit"])

    def test_broker_deficit_never_releases_budget(self):
        result = self.review(broker=[])
        self.assertFalse(result["budget_released"])
        self.assertFalse(result["reconciled"])

    def test_broker_shortfall_is_promoted_to_top_level(self):
        result = self.review(broker=[{"symbol": "AAPL", "qty": "2"}])
        self.assertEqual(result["reason"], "broker_position_shortfall")
        self.assertFalse(result["broker_snapshot_consistent"])
        self.assertFalse(result["reconciled"])

    def test_matching_broker_is_still_not_reconciled(self):
        result = self.review()
        self.assertEqual(
            result["reason"],
            "broker_review_requires_verified_strategy_order_ownership",
        )
        self.assertTrue(result["broker_snapshot_consistent"])
        self.assertFalse(result["reconciled"])

    def test_malformed_broker_quantity_is_flagged(self):
        result = self.review(broker=[{"symbol": "AAPL", "qty": "NaN"}])
        self.assertEqual(result["reason"], "broker_snapshot_invalid")
        self.assertFalse(result["broker_snapshot_consistent"])

    def test_invalid_broker_snapshot_does_not_reconcile(self):
        result = self.review(broker=None)
        self.assertFalse(result["reconciled"])
        self.assertFalse(result["budget_released"])
        self.assertEqual(result["reason"], "broker_snapshot_invalid")


if __name__ == "__main__":
    unittest.main()
