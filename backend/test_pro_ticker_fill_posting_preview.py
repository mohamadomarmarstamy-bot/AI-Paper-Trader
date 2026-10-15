"""Read-only verified PAPER entry fill posting preview tests."""
import unittest
from pro_ticker_fill_posting_preview import preview_entry_fill_delta


class FillPostingPreviewTests(unittest.TestCase):
    def setUp(self):
        self.reservation = {
            "id": "reservation-1", "symbol": "AAPL",
            "broker_order_id": "paper-order-1",
        }
        self.order = {
            "id": "paper-order-1", "symbol": "AAPL", "side": "buy",
            "qty": "10", "filled_qty": "6", "status": "partially_filled",
        }

    def preview(self, recorded=0, **changes):
        order = {**self.order, **changes}
        return preview_entry_fill_delta(self.reservation, order, recorded)

    def test_reports_only_unposted_shares(self):
        result = self.preview(4)
        self.assertTrue(result["ready_for_posting"])
        self.assertEqual(result["delta_shares"], 2)
        self.assertFalse(result["database_updated"])

    def test_repeated_snapshot_is_idempotent(self):
        result = self.preview(6)
        self.assertFalse(result["ready_for_posting"])
        self.assertEqual(result["delta_shares"], 0)

    def test_stale_snapshot_is_blocked(self):
        self.assertEqual(self.preview(7)["reason"], "ledger_ahead_of_broker_snapshot")

    def test_mismatched_order_is_blocked(self):
        self.assertFalse(self.preview(0, id="other")["ready_for_posting"])

    def test_wrong_side_is_blocked(self):
        self.assertFalse(self.preview(0, side="sell")["ready_for_posting"])

    def test_unlinked_reservation_is_blocked(self):
        row = {**self.reservation, "broker_order_id": None}
        self.assertFalse(preview_entry_fill_delta(row, self.order, 0)["ready_for_posting"])

    def test_invalid_accounted_quantity_is_blocked(self):
        self.assertFalse(self.preview(-1)["ready_for_posting"])

    def test_missing_order_is_blocked(self):
        self.assertFalse(preview_entry_fill_delta(self.reservation, None, 0)["ready_for_posting"])


if __name__ == "__main__":
    unittest.main()
