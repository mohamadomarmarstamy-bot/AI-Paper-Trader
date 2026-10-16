"""Tests for the verified PAPER order-to-position accounting bridge."""
import unittest
import tempfile
from pathlib import Path
import database
from pro_ticker_test_db_helper import tracked_test_connections
from pro_ticker_budget_ledger import reserve_pro_ticker_budget, set_paper_reservation_expected_shares, link_verified_paper_entry_order, get_pro_ticker_budget
from pro_ticker_position_ledger import list_pro_ticker_strategy_positions
from unittest.mock import patch

import pro_ticker_verified_fill_bridge as bridge


class VerifiedFillBridgeTests(unittest.TestCase):
    def setUp(self):
        self.reservation_id = "12345678-1234-1234-1234-123456789abc"
        self.reservation = {
            "id": self.reservation_id, "symbol": "AAPL", "status": "submitted",
            "broker_order_id": "paper-order-1", "expected_shares": 10,
        }
        self.order = {
            "id": "paper-order-1",
            "client_order_id": "pt-" + self.reservation_id,
            "symbol": "AAPL", "side": "buy", "qty": "10",
            "filled_qty": "4", "status": "partially_filled",
        }
        p = patch.object(
            bridge, "get_pro_ticker_reservation",
            return_value=self.reservation,
        )
        p.start()
        self.addCleanup(p.stop)

    def test_verified_partial_fill_posts_only_broker_cumulative_qty(self):
        with patch.object(bridge, "post_verified_cumulative_paper_fill", return_value={"posted": True}) as post:
            self.assertTrue(bridge.account_linked_paper_entry_snapshot(
                self.reservation_id, self.order,
            )["posted"])
            self.assertEqual(post.call_args.kwargs["cumulative_filled_shares"], 4)
            self.assertTrue(post.call_args.kwargs["ownership_verified"])

    def test_wrong_client_order_id_does_not_post(self):
        with patch.object(bridge, "post_verified_cumulative_paper_fill") as post:
            with self.assertRaises(ValueError):
                bridge.account_linked_paper_entry_snapshot(
                    self.reservation_id, {**self.order, "client_order_id": "scanner-order"},
                )
            post.assert_not_called()

    def test_wrong_broker_order_id_does_not_post(self):
        with self.assertRaises(ValueError):
            bridge.account_linked_paper_entry_snapshot(
                self.reservation_id, {**self.order, "id": "other"},
            )

    def test_sell_does_not_post(self):
        with self.assertRaises(ValueError):
            bridge.account_linked_paper_entry_snapshot(
                self.reservation_id, {**self.order, "side": "sell"},
            )

    def test_unknown_order_status_does_not_post(self):
        with self.assertRaises(ValueError):
            bridge.account_linked_paper_entry_snapshot(
                self.reservation_id, {**self.order, "status": "unknown"},
            )

    def test_unlinked_reservation_does_not_post(self):
        self.reservation["status"] = "reserved"
        with self.assertRaises(ValueError):
            bridge.account_linked_paper_entry_snapshot(self.reservation_id, self.order)

    def test_invalid_broker_quantity_does_not_post(self):
        with self.assertRaises(ValueError):
            bridge.account_linked_paper_entry_snapshot(
                self.reservation_id, {**self.order, "qty": "nan"},
            )

    def test_broker_qty_mismatch_with_reservation_does_not_post(self):
        with patch.object(bridge, "post_verified_cumulative_paper_fill") as post:
            with self.assertRaises(ValueError):
                bridge.account_linked_paper_entry_snapshot(
                    self.reservation_id, {**self.order, "qty": "11"},
                )
            post.assert_not_called()

    def test_unbound_expected_shares_do_not_post(self):
        self.reservation["expected_shares"] = None
        with self.assertRaises(ValueError):
            bridge.account_linked_paper_entry_snapshot(self.reservation_id, self.order)

    def test_missing_reservation_does_not_post(self):
        with self.assertRaises(ValueError):
            bridge.account_linked_paper_entry_snapshot("another-id", self.order)


class VerifiedFillLifecycleIntegrationTests(unittest.TestCase):
    """Real temporary SQLite ledger with synthetic PAPER broker snapshots."""

    def test_partial_fill_replay_and_completed_fill_preserve_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            with tracked_test_connections():
                with patch.object(database, "DATABASE_PATH", Path(directory) / "lifecycle.db"):
                    reservation = reserve_pro_ticker_budget("AAPL", 1000)
                    reservation_id = reservation["reservation_id"]
                    set_paper_reservation_expected_shares(reservation_id, 10)
                    order = {
                        "id": "paper-entry-lifecycle-1",
                        "client_order_id": "pt-" + reservation_id,
                        "symbol": "AAPL", "side": "buy", "qty": "10",
                        "filled_qty": "4", "status": "partially_filled",
                    }
                    link_verified_paper_entry_order(reservation_id, order)
                    first = bridge.account_linked_paper_entry_snapshot(reservation_id, order)
                    self.assertEqual(first["delta_shares"], 4)
                    replay = bridge.account_linked_paper_entry_snapshot(reservation_id, order)
                    self.assertEqual(replay["delta_shares"], 0)
                    completed = {**order, "filled_qty": "10", "status": "filled"}
                    second = bridge.account_linked_paper_entry_snapshot(reservation_id, completed)
                    self.assertEqual(second["delta_shares"], 6)
                    stale = bridge.account_linked_paper_entry_snapshot(reservation_id, order)
                    self.assertTrue(stale["stale_snapshot"])
                    self.assertEqual(list_pro_ticker_strategy_positions()["positions"][0]["shares"], 10)
                    self.assertEqual(get_pro_ticker_budget()["committed"], 1000)

    def test_wrong_client_id_never_posts_to_real_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            with tracked_test_connections():
                with patch.object(database, "DATABASE_PATH", Path(directory) / "lifecycle.db"):
                    reservation = reserve_pro_ticker_budget("AAPL", 1000)
                    reservation_id = reservation["reservation_id"]
                    set_paper_reservation_expected_shares(reservation_id, 10)
                    order = {
                        "id": "paper-entry-lifecycle-2",
                        "client_order_id": "pt-" + reservation_id,
                        "symbol": "AAPL", "side": "buy", "qty": "10",
                        "filled_qty": "4", "status": "partially_filled",
                    }
                    link_verified_paper_entry_order(reservation_id, order)
                    with self.assertRaises(ValueError):
                        bridge.account_linked_paper_entry_snapshot(
                            reservation_id, {**order, "client_order_id": "other-strategy"},
                        )
                    self.assertEqual(list_pro_ticker_strategy_positions()["positions"], [])
                    self.assertEqual(get_pro_ticker_budget()["committed"], 1000)


if __name__ == "__main__":
    unittest.main()
