"""Tests for conservative PAPER allocation consistency review."""
import unittest
from pro_ticker_virtual_allocation_audit import audit_virtual_allocations


class AllocationAuditTests(unittest.TestCase):
    def setUp(self):
        self.reservation = {"symbol": "AAPL", "status": "submitted",
                            "expected_shares": 10, "broker_order_id": "paper-id"}
        self.position = {"symbol": "AAPL", "shares": 4}

    def review(self, reservations=None, positions=None):
        return audit_virtual_allocations(
            [self.reservation] if reservations is None else reservations,
            [self.position] if positions is None else positions,
        )

    def test_matching_ledger_is_not_broker_reconciled(self):
        result = self.review()
        self.assertTrue(result["ledger_consistent"])
        self.assertFalse(result["reconciled"])
        self.assertFalse(result["budget_released"])

    def test_oversized_position_is_flagged(self):
        self.assertEqual(self.review(positions=[{"symbol": "AAPL", "shares": 11}])["issues"][0]["reason"],
                         "shares_exceed_reserved_order")

    def test_orphaned_position_is_flagged(self):
        self.assertEqual(self.review(reservations=[])["issues"][0]["reason"], "position_without_allocation")

    def test_missing_broker_link_is_flagged(self):
        row = {**self.reservation, "broker_order_id": None}
        self.assertEqual(self.review(reservations=[row])["issues"][0]["reason"], "missing_order_link")

    def test_missing_expected_quantity_is_flagged(self):
        row = {**self.reservation, "expected_shares": None}
        self.assertEqual(self.review(reservations=[row])["issues"][0]["reason"], "missing_expected_shares")

    def test_duplicate_allocations_are_flagged(self):
        self.assertIn("duplicate_active_allocation",
                      [issue["reason"] for issue in self.review(reservations=[self.reservation, self.reservation])["issues"]])

    def test_reserved_allocation_cannot_already_hold_shares(self):
        row = {**self.reservation, "status": "reserved", "broker_order_id": None}
        self.assertEqual(self.review(reservations=[row])["issues"][0]["reason"], "unsubmitted_position")

    def test_unknown_reservation_status_fails_closed(self):
        row = {**self.reservation, "status": "unexpected"}
        self.assertEqual(self.review(reservations=[row])["reason"], "invalid_reservation_status")

    def test_noncanonical_symbol_fails_closed(self):
        row = {**self.reservation, "symbol": "aapl"}
        self.assertEqual(self.review(reservations=[row])["reason"], "invalid_reservation")

    def test_invalid_snapshot_fails_closed(self):
        self.assertEqual(audit_virtual_allocations(None, [])["reason"], "invalid_snapshot")


if __name__ == "__main__":
    unittest.main()
