"""Unit tests for read-only simulated PAPER reconciliation."""
import unittest

from pro_ticker_reconciliation_review import assess_reservation_state


class ReconciliationReviewTests(unittest.TestCase):
    def setUp(self):
        self.reservation = {"symbol": "AAPL", "status": "reserved", "broker_order_id": "paper-123"}
        self.order = {"id": "paper-123", "symbol": "AAPL", "side": "buy", "status": "filled", "filled_qty": "5"}

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

    def test_unlinked_reservation_never_releases(self):
        for order_id in (None, "", "   "):
            with self.subTest(order_id=order_id):
                reservation = {**self.reservation, "broker_order_id": order_id}
                order = {**self.order, "status": "canceled", "filled_qty": "0"}
                result = assess_reservation_state(reservation, order, position_exists=False)
                self.assertFalse(result["safe_to_release"])
                self.assertEqual(result["reason"], "reservation_order_unlinked")

    def test_sell_or_unknown_side_never_releases(self):
        for side in ("sell", None, "unknown", 1):
            with self.subTest(side=side):
                order = {**self.order, "side": side, "status": "canceled", "filled_qty": "0"}
                result = assess_reservation_state(self.reservation, order, position_exists=False)
                self.assertFalse(result["safe_to_release"])
                self.assertEqual(result["reason"], "entry_order_side_unverified")

    def test_missing_or_invalid_fill_quantity_cannot_release(self):
        for quantity in (None, True, False, "NaN", -1, 10 ** 1000):
            with self.subTest(quantity=quantity):
                order = {**self.order, "status": "canceled", "filled_qty": quantity}
                result = assess_reservation_state(self.reservation, order, position_exists=False)
                self.assertFalse(result["safe_to_release"])
                self.assertEqual(result["reason"], "invalid_filled_quantity")
        order = {key: value for key, value in self.order.items() if key != "filled_qty"}
        order["status"] = "canceled"
        result = assess_reservation_state(self.reservation, order, position_exists=False)
        self.assertFalse(result["safe_to_release"])
        self.assertEqual(result["reason"], "invalid_filled_quantity")

    def test_mismatched_order_never_releases(self):
        result = assess_reservation_state(self.reservation, {**self.order, "id": "other"}, position_exists=False)
        self.assertFalse(result["safe_to_release"])


if __name__ == "__main__":
    unittest.main()
