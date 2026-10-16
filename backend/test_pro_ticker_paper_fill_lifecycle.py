"""End-to-end PAPER reservation -> verified order -> incremental fill ledger tests.

Uses temporary SQLite and fabricated PAPER broker snapshots. No network or orders.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pro_ticker_budget_ledger as budget
import pro_ticker_position_ledger as positions
from pro_ticker_order_linking import pro_ticker_client_order_id
from pro_ticker_verified_fill_bridge import account_linked_paper_entry_snapshot


class PaperFillLifecycleIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "paper-lifecycle.db"

        def connect():
            conn = sqlite3.connect(path, timeout=30)
            conn.row_factory = sqlite3.Row
            self.addCleanup(conn.close)
            return conn

        for module in (budget, positions):
            patcher = patch.object(module, "get_connection", side_effect=connect)
            patcher.start()
            self.addCleanup(patcher.stop)

        allocation = budget.reserve_pro_ticker_budget("AAPL", 1000)
        self.reservation_id = allocation["reservation_id"]
        budget.set_paper_reservation_expected_shares(self.reservation_id, 10)
        self.order = {
            "id": "paper-order-lifecycle",
            "client_order_id": pro_ticker_client_order_id(self.reservation_id),
            "symbol": "AAPL", "side": "buy", "qty": "10",
            "filled_qty": "0", "status": "new",
        }
        budget.link_verified_paper_entry_order(self.reservation_id, self.order)

    def snapshot(self, filled, status="partially_filled", **changes):
        order = {**self.order, "filled_qty": str(filled), "status": status, **changes}
        return account_linked_paper_entry_snapshot(self.reservation_id, order)

    def test_partial_fills_and_replay_do_not_double_count(self):
        self.assertFalse(self.snapshot(0, status="new")["posted"])
        self.assertEqual(self.snapshot(2)["delta_shares"], 2)
        self.assertEqual(self.snapshot(5)["delta_shares"], 3)
        self.assertEqual(self.snapshot(5)["delta_shares"], 0)
        self.assertEqual(self.snapshot(10, status="filled")["delta_shares"], 5)
        self.assertEqual(
            positions.list_pro_ticker_strategy_positions()["positions"][0]["shares"], 10,
        )
        self.assertEqual(positions.get_accounted_paper_order_shares(self.order["id"]), 10)
        self.assertEqual(budget.get_pro_ticker_budget()["committed"], 1000)

    def test_stale_snapshot_does_not_reverse_accounting(self):
        self.snapshot(6)
        result = self.snapshot(3)
        self.assertTrue(result["stale_snapshot"])
        self.assertEqual(positions.get_accounted_paper_order_shares(self.order["id"]), 6)

    def test_delayed_out_of_order_snapshots_converge_without_double_count(self):
        self.assertEqual(self.snapshot(7)["delta_shares"], 7)
        stale = self.snapshot(2)
        self.assertTrue(stale["stale_snapshot"])
        self.assertEqual(stale["delta_shares"], 0)
        self.assertEqual(self.snapshot(9)["delta_shares"], 2)
        self.assertEqual(self.snapshot(7)["delta_shares"], 0)
        self.assertEqual(self.snapshot(10, status="filled")["delta_shares"], 1)
        replay = self.snapshot(10, status="filled")
        self.assertFalse(replay["posted"])
        self.assertEqual(replay["delta_shares"], 0)
        self.assertEqual(positions.get_accounted_paper_order_shares(self.order["id"]), 10)
        self.assertEqual(
            positions.list_pro_ticker_strategy_positions()["positions"][0]["shares"], 10,
        )
        self.assertEqual(budget.get_pro_ticker_budget()["committed"], 1000)

    def test_terminal_canceled_partial_fill_replay_is_not_reversed(self):
        self.assertEqual(self.snapshot(4, status="canceled")["delta_shares"], 4)
        self.assertEqual(self.snapshot(4, status="canceled")["delta_shares"], 0)
        stale = self.snapshot(0, status="new")
        self.assertTrue(stale["stale_snapshot"])
        self.assertEqual(positions.get_accounted_paper_order_shares(self.order["id"]), 4)
        self.assertEqual(
            positions.list_pro_ticker_strategy_positions()["positions"][0]["shares"], 4,
        )
        self.assertEqual(budget.get_pro_ticker_budget()["committed"], 1000)

    def test_wrong_client_id_cannot_mutate_ledger(self):
        with self.assertRaises(ValueError):
            self.snapshot(4, client_order_id="other")
        self.assertEqual(positions.list_pro_ticker_strategy_positions()["positions"], [])

    def test_wrong_quantity_cannot_mutate_ledger(self):
        with self.assertRaises(ValueError):
            self.snapshot(4, qty="11")
        self.assertEqual(positions.list_pro_ticker_strategy_positions()["positions"], [])

    def test_wrong_side_cannot_mutate_ledger(self):
        with self.assertRaises(ValueError):
            self.snapshot(4, side="sell")
        self.assertEqual(positions.list_pro_ticker_strategy_positions()["positions"], [])

    def test_unknown_reservation_cannot_mutate_ledger(self):
        with self.assertRaises(ValueError):
            account_linked_paper_entry_snapshot("not-a-reservation", self.order)
        self.assertEqual(positions.list_pro_ticker_strategy_positions()["positions"], [])


if __name__ == "__main__":
    unittest.main()
