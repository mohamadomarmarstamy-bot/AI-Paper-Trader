"""Temporary SQLite integration tests for persisted PAPER release evidence."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
import pro_ticker_position_ledger as positions
from pro_ticker_budget_ledger import (
    reserve_pro_ticker_budget, set_paper_reservation_expected_shares,
    link_verified_paper_entry_order, record_verified_paper_order_state,
    get_pro_ticker_budget,
)
from pro_ticker_exit_links import link_verified_paper_exit
from pro_ticker_persisted_release_review import review_persisted_paper_release
from pro_ticker_test_db_helper import tracked_test_connections


class PersistedPaperReleaseReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path_patch = patch.object(database, "DATABASE_PATH", Path(self.temp.name) / "release.db")
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        tracker = tracked_test_connections()
        tracker.__enter__()
        self.addCleanup(tracker.__exit__, None, None, None)

        reservation = reserve_pro_ticker_budget("AAPL", 1000)
        self.reservation_id = reservation["reservation_id"]
        set_paper_reservation_expected_shares(self.reservation_id, 5)
        self.entry_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        entry_order = {
            "id": self.entry_id, "client_order_id": "pt-" + self.reservation_id,
            "symbol": "AAPL", "side": "buy", "qty": "5",
            "filled_qty": "5", "status": "filled",
        }
        link_verified_paper_entry_order(self.reservation_id, entry_order)
        record_verified_paper_order_state(self.reservation_id, self.entry_id, "filled", filled_quantity=5)
        positions.post_verified_cumulative_paper_fill(
            broker_order_id=self.entry_id, symbol="AAPL", side="buy",
            cumulative_filled_shares=5, ownership_verified=True, fill_verified=True,
        )

    def link_exit(self):
        exit_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        order_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        link_verified_paper_exit(
            reservation_id=self.reservation_id, symbol="AAPL",
            expected_shares=5, exit_id=exit_id,
            broker_order={
                "id": order_id, "client_order_id": "ptx-" + exit_id,
                "symbol": "AAPL", "side": "sell", "qty": "5",
                "status": "filled",
            },
        )
        return order_id

    def test_entry_without_exit_does_not_release_budget(self):
        result = review_persisted_paper_release(self.reservation_id)
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        self.assertEqual(result["persisted_entry_shares"], 5)
        self.assertEqual(result["persisted_exit_orders"], 0)
        self.assertEqual(result["reason"], "persisted_strategy_position_still_open")
        self.assertEqual(get_pro_ticker_budget()["committed"], 1000)

    def test_partial_exit_is_read_only(self):
        exit_order_id = self.link_exit()
        positions.post_verified_cumulative_paper_fill(
            broker_order_id=exit_order_id, symbol="AAPL", side="sell",
            cumulative_filled_shares=2, ownership_verified=True, fill_verified=True,
        )
        result = review_persisted_paper_release(self.reservation_id)
        self.assertEqual(result["persisted_exit_orders"], 1)
        self.assertEqual(result["persisted_exit_shares"], 2)
        self.assertEqual(result["persisted_remaining_shares"], 3)
        self.assertEqual(result["reason"], "persisted_strategy_position_still_open")
        self.assertFalse(result["release_authorized"])
        self.assertEqual(get_pro_ticker_budget()["committed"], 1000)

    def test_complete_exit_still_needs_independent_broker_proof(self):
        exit_order_id = self.link_exit()
        positions.post_verified_cumulative_paper_fill(
            broker_order_id=exit_order_id, symbol="AAPL", side="sell",
            cumulative_filled_shares=5, ownership_verified=True, fill_verified=True,
        )
        result = review_persisted_paper_release(self.reservation_id)
        self.assertEqual(result["persisted_entry_shares"], 5)
        self.assertEqual(result["persisted_exit_orders"], 1)
        self.assertEqual(result["reason"], "persisted_records_not_independent_broker_proof")
        self.assertFalse(result["eligible_for_independent_review"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        self.assertEqual(positions.list_pro_ticker_strategy_positions()["positions"], [])
        self.assertEqual(get_pro_ticker_budget()["committed"], 1000)

    def test_historical_same_symbol_reservation_blocks_review(self):
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO pro_ticker_paper_reservations "
                "(id, symbol, notional_cents, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                 "AAPL", 100, "released",
                 "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            )
        result = review_persisted_paper_release(self.reservation_id)
        self.assertEqual(result["reason"], "other_reservations_share_symbol")
        self.assertFalse(result["release_authorized"])

    def test_unattributed_buy_blocks_release_review(self):
        positions.post_verified_cumulative_paper_fill(
            broker_order_id="ffffffff-ffff-4fff-8fff-ffffffffffff",
            symbol="AAPL", side="buy", cumulative_filled_shares=2,
            ownership_verified=True, fill_verified=True,
        )
        result = review_persisted_paper_release(self.reservation_id)
        self.assertEqual(result["reason"], "unattributed_strategy_buy_fills")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        self.assertEqual(get_pro_ticker_budget()["committed"], 1000)

    def test_unattributed_sell_blocks_release_review(self):
        exit_order_id = self.link_exit()
        positions.post_verified_cumulative_paper_fill(
            broker_order_id=exit_order_id, symbol="AAPL", side="sell",
            cumulative_filled_shares=3, ownership_verified=True, fill_verified=True,
        )
        positions.post_verified_cumulative_paper_fill(
            broker_order_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            symbol="AAPL", side="sell", cumulative_filled_shares=2,
            ownership_verified=True, fill_verified=True,
        )
        result = review_persisted_paper_release(self.reservation_id)
        self.assertEqual(result["reason"], "unattributed_strategy_sell_fills")
        self.assertFalse(result["release_authorized"])
        self.assertEqual(get_pro_ticker_budget()["committed"], 1000)

    def test_unknown_reservation_does_not_release(self):
        result = review_persisted_paper_release("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        self.assertEqual(result["reason"], "reservation_missing")
        self.assertFalse(result["budget_released"])


if __name__ == "__main__":
    unittest.main()
