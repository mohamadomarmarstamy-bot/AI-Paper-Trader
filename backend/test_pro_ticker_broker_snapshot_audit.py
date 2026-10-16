"""Integration tests for read-only PAPER broker snapshot cross-checks."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from pro_ticker_test_db_helper import tracked_test_connections
from pro_ticker_budget_ledger import (
    reserve_pro_ticker_budget, set_paper_reservation_expected_shares,
    link_verified_paper_entry_order, record_verified_paper_order_state,
)
from pro_ticker_exit_links import link_verified_paper_exit
from pro_ticker_position_ledger import post_verified_cumulative_paper_fill
from pro_ticker_broker_snapshot_audit import audit_linked_paper_broker_snapshots


ENTRY_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
EXIT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
EXIT_CLIENT_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


class BrokerSnapshotAuditTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_patch = patch.object(database, "DATABASE_PATH", Path(temp.name) / "test.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        tracker = tracked_test_connections()
        tracker.__enter__()
        self.addCleanup(tracker.__exit__, None, None, None)
        self.reservation_id = reserve_pro_ticker_budget("AAPL", 100)["reservation_id"]
        set_paper_reservation_expected_shares(self.reservation_id, 5)
        self.entry_order = {
            "id": ENTRY_ID, "client_order_id": "pt-" + self.reservation_id,
            "symbol": "AAPL", "side": "buy", "qty": "5",
            "filled_qty": "5", "status": "filled",
        }
        link_verified_paper_entry_order(self.reservation_id, self.entry_order)
        record_verified_paper_order_state(
            self.reservation_id, ENTRY_ID, "filled", filled_quantity=5,
        )
        post_verified_cumulative_paper_fill(
            broker_order_id=ENTRY_ID, symbol="AAPL", side="buy",
            cumulative_filled_shares=5, ownership_verified=True, fill_verified=True,
        )
        self.exit_order = {
            "id": EXIT_ID, "client_order_id": "ptx-" + EXIT_CLIENT_ID,
            "symbol": "AAPL", "side": "sell", "qty": "5",
            "filled_qty": "5", "status": "filled",
        }
        with patch("pro_ticker_exit_links.get_pro_ticker_reservation", return_value={
            "id": self.reservation_id, "symbol": "AAPL", "status": "filled",
            "broker_order_id": ENTRY_ID,
        }):
            link_verified_paper_exit(
                reservation_id=self.reservation_id, symbol="AAPL",
                expected_shares=5, exit_id=EXIT_CLIENT_ID,
                broker_order=self.exit_order,
            )
        post_verified_cumulative_paper_fill(
            broker_order_id=EXIT_ID, symbol="AAPL", side="sell",
            cumulative_filled_shares=5, ownership_verified=True, fill_verified=True,
        )

    def audit(self, **updates):
        values = dict(
            reservation_id=self.reservation_id,
            entry_order=self.entry_order,
            exit_orders={EXIT_ID: self.exit_order},
        )
        values.update(updates)
        return audit_linked_paper_broker_snapshots(**values)

    def test_complete_linked_broker_snapshots_match(self):
        result = self.audit()
        self.assertTrue(result["verified"])
        self.assertEqual(result["entry_shares"], 5)
        self.assertEqual(result["exit_shares"], 5)
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])

    def test_missing_exit_snapshot_fails_closed(self):
        result = self.audit(exit_orders={})
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "exit_snapshots_incomplete_or_extra")

    def test_wrong_exit_client_order_fails_closed(self):
        bad = {**self.exit_order, "client_order_id": "wrong"}
        result = self.audit(exit_orders={EXIT_ID: bad})
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "paper_exit_client_order_id_mismatch")

    def test_partial_exit_snapshot_fails_closed(self):
        partial = {**self.exit_order, "status": "partially_filled", "filled_qty": "2"}
        result = self.audit(exit_orders={EXIT_ID: partial})
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "exit_broker_order_not_terminal")


if __name__ == "__main__":
    unittest.main()
