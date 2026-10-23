"""PAPER reservation snapshot integration tests (no broker or database writes)."""
import unittest
from pro_ticker_snapshot_review import review_paper_reservation


class SnapshotReviewTests(unittest.TestCase):
    def setUp(self):
        self.reservation = {
            "id": "reservation-1", "symbol": "AAPL",
            "status": "submitted", "broker_order_id": "broker-1",
        }
        self.order = {
            "id": "broker-1", "symbol": "AAPL", "side": "buy",
            "qty": "5", "filled_qty": "2", "status": "partially_filled",
        }

    def test_partial_fill_reports_owned_entry_fills_without_release(self):
        result = review_paper_reservation(
            self.reservation, self.order, position_exists=True,
        )
        self.assertTrue(result["fill_verified"])
        self.assertEqual(result["filled_shares"], 2)
        self.assertEqual(result["remaining_shares"], 3)
        self.assertFalse(result["safe_to_release"])

    def test_missing_broker_order_is_not_assumed_canceled(self):
        result = review_paper_reservation(
            self.reservation, None, position_exists=False,
        )
        self.assertFalse(result["fill_verified"])
        self.assertFalse(result["safe_to_release"])

    def test_different_order_cannot_claim_reservation(self):
        result = review_paper_reservation(
            self.reservation, {**self.order, "id": "other"}, position_exists=True,
        )
        self.assertFalse(result["fill_verified"])

    def test_filled_order_and_absent_position_do_not_release_budget(self):
        result = review_paper_reservation(
            self.reservation,
            {**self.order, "status": "filled", "filled_qty": "5"},
            position_exists=False,
        )
        self.assertTrue(result["fill_verified"])
        self.assertFalse(result["safe_to_release"])
        self.assertEqual(result["reason"], "matched_exit_fill_required")

    def test_invalid_broker_quantity_fails_closed(self):
        result = review_paper_reservation(
            self.reservation, {**self.order, "qty": "nan"},
            position_exists=True,
        )
        self.assertFalse(result["fill_verified"])


if __name__ == "__main__":
    unittest.main()
