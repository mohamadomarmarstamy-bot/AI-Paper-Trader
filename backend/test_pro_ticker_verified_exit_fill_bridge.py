"""Verified PAPER exit bridge tests using persisted ownership links."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from pro_ticker_test_db_helper import tracked_test_connections
from pro_ticker_exit_links import link_verified_paper_exit
from pro_ticker_verified_exit_fill_bridge import account_linked_paper_exit_snapshot
from pro_ticker_position_ledger import (
    post_verified_cumulative_paper_fill, list_pro_ticker_strategy_positions,
)

RESERVATION_ID = "12345678-1234-1234-1234-123456789abc"
EXIT_ID = "abcdefab-1234-1234-1234-123456789abc"
ENTRY_ORDER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
EXIT_ORDER_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class VerifiedExitBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(database, "DATABASE_PATH", Path(self.temp.name) / "test.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        tracker = tracked_test_connections()
        tracker.__enter__()
        self.addCleanup(tracker.__exit__, None, None, None)
        self.reservation = {
            "id": RESERVATION_ID, "symbol": "AAPL", "status": "filled",
            "broker_order_id": ENTRY_ORDER_ID,
        }
        self.order = {
            "id": EXIT_ORDER_ID, "client_order_id": "ptx-" + EXIT_ID,
            "symbol": "AAPL", "side": "sell", "qty": "5",
            "filled_qty": "2", "status": "partially_filled",
        }
        owner = patch("pro_ticker_exit_links.get_pro_ticker_reservation", return_value=self.reservation)
        owner.start()
        self.addCleanup(owner.stop)
        reviewer = patch("pro_ticker_verified_exit_fill_bridge.get_pro_ticker_reservation", return_value=self.reservation)
        reviewer.start()
        self.addCleanup(reviewer.stop)
        link_verified_paper_exit(
            reservation_id=RESERVATION_ID, symbol="AAPL",
            expected_shares=5, broker_order=self.order, exit_id=EXIT_ID,
        )
        post_verified_cumulative_paper_fill(
            broker_order_id=ENTRY_ORDER_ID, symbol="AAPL", side="buy",
            cumulative_filled_shares=5, ownership_verified=True, fill_verified=True,
        )

    def test_partial_replay_complete_and_stale(self):
        first = account_linked_paper_exit_snapshot(EXIT_ORDER_ID, self.order)
        self.assertEqual(first["delta_shares"], 2)
        replay = account_linked_paper_exit_snapshot(EXIT_ORDER_ID, self.order)
        self.assertFalse(replay["posted"])
        complete = account_linked_paper_exit_snapshot(
            EXIT_ORDER_ID, {**self.order, "filled_qty": "5", "status": "filled"},
        )
        self.assertEqual(complete["delta_shares"], 3)
        self.assertEqual(list_pro_ticker_strategy_positions()["positions"], [])
        stale = account_linked_paper_exit_snapshot(EXIT_ORDER_ID, self.order)
        self.assertTrue(stale["stale_snapshot"])

    def test_canceled_zero_fill_exit_is_idempotent_and_leaves_position_open(self):
        canceled = {**self.order, "filled_qty": "0", "status": "canceled"}
        first = account_linked_paper_exit_snapshot(EXIT_ORDER_ID, canceled)
        replay = account_linked_paper_exit_snapshot(EXIT_ORDER_ID, canceled)
        self.assertFalse(first["posted"])
        self.assertFalse(replay["posted"])
        self.assertEqual(first["delta_shares"], 0)
        self.assertEqual(replay["delta_shares"], 0)
        self.assertEqual(list_pro_ticker_strategy_positions()["positions"][0]["shares"], 5)

    def test_exit_overfill_rejected_without_position_mutation(self):
        with self.assertRaises(ValueError):
            account_linked_paper_exit_snapshot(
                EXIT_ORDER_ID, {**self.order, "filled_qty": "6", "status": "filled"},
            )
        self.assertEqual(list_pro_ticker_strategy_positions()["positions"][0]["shares"], 5)

    def test_wrong_exit_symbol_rejected_without_position_mutation(self):
        with self.assertRaises(ValueError):
            account_linked_paper_exit_snapshot(
                EXIT_ORDER_ID, {**self.order, "symbol": "MSFT"},
            )
        self.assertEqual(list_pro_ticker_strategy_positions()["positions"][0]["shares"], 5)

    def test_wrong_client_order_rejected_without_post(self):
        with self.assertRaisesRegex(ValueError, "client_order_id_mismatch"):
            account_linked_paper_exit_snapshot(
                EXIT_ORDER_ID, {**self.order, "client_order_id": "unrelated"},
            )
        self.assertEqual(list_pro_ticker_strategy_positions()["positions"][0]["shares"], 5)

    def test_noncanonical_order_id_rejected(self):
        with self.assertRaisesRegex(ValueError, "Canonical"):
            account_linked_paper_exit_snapshot(" " + EXIT_ORDER_ID, self.order)
        self.assertEqual(list_pro_ticker_strategy_positions()["positions"][0]["shares"], 5)

    def test_unlinked_order_rejected(self):
        with self.assertRaisesRegex(ValueError, "ownership link missing"):
            account_linked_paper_exit_snapshot(
                "cccccccc-cccc-4ccc-8ccc-cccccccccccc", self.order,
            )


if __name__ == "__main__":
    unittest.main()
