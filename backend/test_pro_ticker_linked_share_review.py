"""Integration tests for read-only linked reservation share review."""
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
from pro_ticker_linked_share_review import review_linked_reservation_paper_shares


class LinkedReservationShareReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_patch = patch.object(database, "DATABASE_PATH", Path(temp.name) / "test.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        tracker = tracked_test_connections()
        tracker.__enter__()
        self.addCleanup(tracker.__exit__, None, None, None)

    def entry(self, symbol, order_id, shares):
        reservation = reserve_pro_ticker_budget(symbol, 100)
        reservation_id = reservation["reservation_id"]
        set_paper_reservation_expected_shares(reservation_id, shares)
        link_verified_paper_entry_order(reservation_id, {
            "id": order_id, "client_order_id": "pt-" + reservation_id,
            "symbol": symbol, "side": "buy", "qty": str(shares),
            "status": "filled",
        })
        record_verified_paper_order_state(reservation_id, order_id, "filled",
                                          filled_quantity=shares)
        post_verified_cumulative_paper_fill(
            broker_order_id=order_id, symbol=symbol, side="buy",
            cumulative_filled_shares=shares,
            ownership_verified=True, fill_verified=True,
        )
        return reservation_id

    def test_duplicate_active_symbol_cannot_change_existing_attribution(self):
        first = self.entry("AAPL", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", 4)
        before = review_linked_reservation_paper_shares(first)
        with self.assertRaisesRegex(ValueError, "active reservation already exists"):
            self.entry("AAPL", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", 3)
        after = review_linked_reservation_paper_shares(first)
        self.assertEqual(after["entry_shares"], before["entry_shares"])
        self.assertEqual(after["remaining_attributed_shares"], 4)
        self.assertEqual(after["exit_shares"], 0)
        self.assertFalse(after["release_authorized"])
        self.assertFalse(after["budget_released"])

    def test_missing_reservation_fails_closed(self):
        result = review_linked_reservation_paper_shares(
            "99999999-9999-4999-8999-999999999999"
        )
        self.assertEqual(result["reason"], "reservation_missing")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])


if __name__ == "__main__":
    unittest.main()
