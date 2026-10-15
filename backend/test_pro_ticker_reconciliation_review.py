"""Unit tests for read-only simulated PAPER reconciliation."""
import unittest

from pro_ticker_reconciliation_review import assess_reservation_state


class ReconciliationReviewTests(unittest.TestCase):
    def setUp(self):
        self.reservation = {"symbol": "AAPL", "status": "reserved", "broker_order_id": "paper-123"}
        self.order = {"id": "paper-123", "symbol": "AAPL", "status": "filled", "filled_qty": "5"}

    def test_missing_order_keeps_reservation(self):
        result = assess_reservation_state(self.reservation, None)
        self.assertFalse(result["safe_to_release"])

    def test_partial_fill_keeps_reservation(self):
        result = assess_reservation_state(self.reservation, {**self.order, "status": "canceled", "filled_qty": "2"}, position_exists=False)
        self.assertFalse(result["safe_to_release"])

    def test_filled_order_with_position_keeps_reservation(self):
        result = assess_reservation_state(self.reservation, self.order, position_exists=True)
        self.assertFalse(result["safe_to_release"])

    def test_position_absence_alone_does_not_release(self):
        result = assess_reservation_state(self.reservation, self.order, position_exists=False)
        self.assertFalse(result["safe_to_release"])
        self.assertEqual(result["reason"], "matched_exit_fill_required")

    def test_canceled_unfilled_and_no_position(self):
        result = assess_reservation_state(self.reservation, {**self.order, "status": "canceled", "filled_qty": "0"}, position_exists=False)
        self.assertEqual(result["suggested_status"], "failed")

    def test_mismatched_order_never_releases(self):
        result = assess_reservation_state(self.reservation, {**self.order, "id": "other"}, position_exists=False)
        self.assertFalse(result["safe_to_release"])


if __name__ == "__main__":
    unittest.main()
