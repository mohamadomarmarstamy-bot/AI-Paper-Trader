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

    def test_same_symbol_reservations_remain_separately_accounted(self):
        first = self.entry("AAPL", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", 4)
        second = self.entry("AAPL", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", 3)
        exit_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        exit_order_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        with patch("pro_ticker_exit_links.get_pro_ticker_reservation", return_value={
            "id": first, "symbol": "AAPL", "status": "filled",
            "broker_order_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        }):
            link_verified_paper_exit(
                reservation_id=first, symbol="AAPL", expected_shares=4,
                exit_id=exit_id, broker_order={
                    "id": exit_order_id, "client_order_id": "ptx-" + exit_id,
                    "symbol": "AAPL", "side": "sell", "qty": "4",
                    "status": "filled",
                },
            )
        post_verified_cumulative_paper_fill(
            broker_order_id=exit_order_id, symbol="AAPL", side="sell",
            cumulative_filled_shares=4,
            ownership_verified=True, fill_verified=True,
        )
        first_result = review_linked_reservation_paper_shares(first)
        second_result = review_linked_reservation_paper_shares(second)
        self.assertEqual(first_result["remaining_attributed_shares"], 0)
        self.assertEqual(second_result["remaining_attributed_shares"], 3)
        self.assertFalse(first_result["release_authorized"])
        self.assertFalse(first_result["broker_reconciled"])
        self.assertFalse(second_result["budget_released"])
        self.assertEqual(first_result["exit_shares"], 4)
        self.assertEqual(second_result["exit_shares"], 0)
        self.assertFalse(first_result["verified"])
        self.assertFalse(second_result["verified"])

    def test_missing_reservation_fails_closed(self):
        result = review_linked_reservation_paper_shares(
            "99999999-9999-4999-8999-999999999999"
        )
        self.assertEqual(result["reason"], "reservation_missing")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])


if __name__ == "__main__":
    unittest.main()
