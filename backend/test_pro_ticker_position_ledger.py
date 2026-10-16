"""SQLite integration tests for idempotent simulated PAPER strategy accounting."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pro_ticker_position_ledger as ledger


class PaperPositionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "strategy.db")
        def connection():
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            self.addCleanup(conn.close)
            return conn
        self.patch = patch.object(ledger, "get_connection", side_effect=connection)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def post(self, event_id="execution-1", **updates):
        values = dict(
            event_id=event_id, broker_order_id="paper-order-1",
            symbol="AAPL", side="buy", filled_shares=4,
            ownership_verified=True, fill_verified=True,
        )
        values.update(updates)
        return ledger.post_verified_paper_fill(**values)

    def test_buy_persists_across_connections(self):
        self.assertEqual(self.post()["current_shares"], 4)
        self.assertEqual(ledger.list_pro_ticker_strategy_positions()["positions"][0]["shares"], 4)

    def test_same_execution_is_idempotent(self):
        self.post()
        result = self.post()
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["current_shares"], 4)

    def test_conflicting_event_id_is_rejected(self):
        self.post()
        with self.assertRaises(ValueError):
            self.post(filled_shares=5)

    def test_duplicate_event_rejects_bool_share_quantity(self):
        self.post(filled_shares=1)
        with self.assertRaises(ValueError):
            self.post(filled_shares=True)
        self.assertEqual(ledger.list_pro_ticker_strategy_positions()["positions"][0]["shares"], 1)

    def test_duplicate_event_rejects_invalid_side_and_quantity(self):
        self.post(filled_shares=2)
        for updates in ({"filled_shares": 0}, {"filled_shares": 2.0}, {"side": None}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.post(**updates)

    def test_one_broker_order_cannot_change_symbol_or_side_across_events(self):
        self.post(filled_shares=2)
        for updates in ({"symbol": "MSFT"}, {"side": "sell"}):
            with self.subTest(updates=updates), self.assertRaisesRegex(ValueError, "another symbol or side"):
                self.post("execution-2", filled_shares=1, **updates)
        self.assertEqual(ledger.get_accounted_paper_order_shares("paper-order-1"), 2)
        self.assertEqual(ledger.list_pro_ticker_strategy_positions()["positions"][0]["shares"], 2)

    def test_whitespace_order_and_event_ids_cannot_create_distinct_attribution(self):
        for updates in ({"event_id": " execution-1"}, {"broker_order_id": "paper-order-1 "}):
            with self.subTest(updates=updates), self.assertRaisesRegex(ValueError, "Canonical"):
                self.post(**updates)
        for broker_order_id in (" paper-order-cumulative", "paper-order-cumulative "):
            with self.subTest(broker_order_id=broker_order_id), self.assertRaisesRegex(ValueError, "Canonical"):
                self.cumulative(1, broker_order_id=broker_order_id)
        self.assertEqual(ledger.list_pro_ticker_strategy_positions()["positions"], [])

    def test_reservation_attribution_uses_order_ids_not_symbol_balance(self):
        self.post(filled_shares=4)
        self.post("execution-2", broker_order_id="another-entry",
                  filled_shares=3)
        self.post("execution-3", broker_order_id="linked-exit",
                  side="sell", filled_shares=4)
        result = ledger.review_reservation_attributed_shares(
            reservation_id="reservation-1", entry_order_id="paper-order-1",
            exit_order_ids=["linked-exit"],
        )
        self.assertEqual(result["entry_shares"], 4)
        self.assertEqual(result["exit_shares"], 4)
        self.assertEqual(result["remaining_attributed_shares"], 0)
        self.assertEqual(ledger.list_pro_ticker_strategy_positions()["positions"][0]["shares"], 3)
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["verified"])

    def test_reservation_attribution_rejects_noncanonical_identifiers(self):
        for values in (
            {"reservation_id": " reservation-1", "entry_order_id": "entry", "exit_order_ids": []},
            {"reservation_id": "reservation-1", "entry_order_id": "entry ", "exit_order_ids": []},
            {"reservation_id": "reservation-1", "entry_order_id": "entry", "exit_order_ids": [" exit"]},
        ):
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, "Canonical"):
                ledger.review_reservation_attributed_shares(**values)

    def test_reservation_attribution_rejects_duplicate_exit_ids(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            ledger.review_reservation_attributed_shares(
                reservation_id="reservation-1", entry_order_id="entry",
                exit_order_ids=["exit", "exit"],
            )

    def test_sell_reduces_strategy_position(self):
        self.post()
        result = self.post(
            "execution-2", broker_order_id="paper-sell-1",
            side="sell", filled_shares=3,
        )
        self.assertEqual(result["current_shares"], 1)

    def test_oversell_does_not_write_event(self):
        self.post()
        with self.assertRaises(ValueError):
            self.post("execution-2", side="sell", filled_shares=5)
        self.assertEqual(ledger.list_pro_ticker_strategy_positions()["positions"][0]["shares"], 4)

    def test_unverified_fill_is_rejected(self):
        with self.assertRaises(ValueError):
            self.post(fill_verified=False)
        self.assertEqual(ledger.list_pro_ticker_strategy_positions()["positions"], [])

    def test_unverified_ownership_is_rejected(self):
        with self.assertRaises(ValueError):
            self.post(ownership_verified=False)

    def test_two_distinct_partial_fills_add(self):
        self.post(filled_shares=2)
        self.post("execution-2", filled_shares=2)
        self.assertEqual(ledger.list_pro_ticker_strategy_positions()["positions"][0]["shares"], 4)


    def cumulative(self, quantity, **updates):
        values = dict(
            broker_order_id="paper-order-cumulative", symbol="AAPL",
            side="buy", cumulative_filled_shares=quantity,
            ownership_verified=True, fill_verified=True,
        )
        values.update(updates)
        return ledger.post_verified_cumulative_paper_fill(**values)

    def test_cumulative_partial_fill_increments_once(self):
        self.assertEqual(self.cumulative(2)["delta_shares"], 2)
        self.assertEqual(self.cumulative(5)["delta_shares"], 3)
        self.assertEqual(self.cumulative(5)["delta_shares"], 0)
        self.assertEqual(
            ledger.list_pro_ticker_strategy_positions()["positions"][0]["shares"], 5,
        )

    def test_stale_cumulative_snapshot_does_not_reverse_shares(self):
        self.cumulative(5)
        result = self.cumulative(2)
        self.assertTrue(result["stale_snapshot"])
        self.assertEqual(result["delta_shares"], 0)

    def test_cumulative_order_identity_cannot_change(self):
        self.cumulative(2)
        with self.assertRaises(ValueError):
            self.cumulative(3, symbol="MSFT")
        with self.assertRaises(ValueError):
            self.cumulative(3, side="sell")

    def test_cumulative_exit_partial_replay_and_full_close(self):
        self.cumulative(5)
        first = self.cumulative(
            2, broker_order_id="paper-sell-cumulative", side="sell",
        )
        self.assertEqual(first["delta_shares"], 2)
        self.assertEqual(first["current_shares"], 3)
        replay = self.cumulative(
            2, broker_order_id="paper-sell-cumulative", side="sell",
        )
        self.assertEqual(replay["delta_shares"], 0)
        completed = self.cumulative(
            5, broker_order_id="paper-sell-cumulative", side="sell",
        )
        self.assertEqual(completed["delta_shares"], 3)
        self.assertEqual(completed["current_shares"], 0)
        stale = self.cumulative(
            2, broker_order_id="paper-sell-cumulative", side="sell",
        )
        self.assertTrue(stale["stale_snapshot"])
        self.assertEqual(ledger.list_pro_ticker_strategy_positions()["positions"], [])

    def test_cumulative_sell_cannot_oversell(self):
        self.cumulative(3)
        with self.assertRaises(ValueError):
            self.cumulative(
                4, broker_order_id="paper-sell-cumulative", side="sell",
            )
        self.assertEqual(
            ledger.list_pro_ticker_strategy_positions()["positions"][0]["shares"], 3,
        )

    def test_unverified_cumulative_snapshot_cannot_post(self):
        with self.assertRaises(ValueError):
            self.cumulative(2, fill_verified=False)
        self.assertEqual(ledger.list_pro_ticker_strategy_positions()["positions"], [])



if __name__ == "__main__":
    unittest.main()
