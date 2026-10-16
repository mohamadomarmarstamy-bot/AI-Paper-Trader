"""Durable PAPER exit ownership link tests; never submit orders."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pro_ticker_test_db_helper import tracked_test_connections
import database
import pro_ticker_exit_links as exits


class PaperExitLinkTests(unittest.TestCase):
    def setUp(self):
        self.reservation_id = "12345678-1234-1234-1234-123456789abc"
        self.exit_id = "abcdefab-1234-1234-1234-123456789abc"
        self.order = {
            "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "client_order_id": "ptx-" + self.exit_id,
            "symbol": "AAPL", "side": "sell", "qty": "3",
            "status": "partially_filled",
        }
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        tracker = tracked_test_connections()
        tracker.__enter__()
        self.addCleanup(tracker.__exit__, None, None, None)
        db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp.name) / "test.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        from pro_ticker_budget_ledger import (
            reserve_pro_ticker_budget, set_paper_reservation_expected_shares,
            link_verified_paper_entry_order,
        )
        self.reservation_id = reserve_pro_ticker_budget("AAPL", 100)["reservation_id"]
        set_paper_reservation_expected_shares(self.reservation_id, 3)
        link_verified_paper_entry_order(self.reservation_id, {
            "id": "paper-entry-1", "client_order_id": "pt-" + self.reservation_id,
            "symbol": "AAPL", "side": "buy", "qty": "3", "status": "new",
        })
        owner = patch.object(exits, "get_pro_ticker_reservation", return_value={
            "id": self.reservation_id, "symbol": "AAPL", "status": "filled",
            "broker_order_id": "paper-entry-1",
        })
        owner.start()
        self.addCleanup(owner.stop)

    def link(self, order=None):
        return exits.link_verified_paper_exit(
            reservation_id=self.reservation_id, symbol="AAPL",
            expected_shares=3, broker_order=self.order if order is None else order,
            exit_id=self.exit_id,
        )

    def test_stale_active_lookup_cannot_link_exit_after_release(self):
        with database.get_connection() as conn:
            conn.execute("UPDATE pro_ticker_paper_reservations SET status = 'released' WHERE id = ?",
                         (self.reservation_id,))
        # The mocked pre-lock lookup still claims the reservation is active.
        with self.assertRaisesRegex(ValueError, "changed before exit linking"):
            self.link()
        with database.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM pro_ticker_paper_exit_links").fetchone()[0], 0)

    def test_link_and_repeat_idempotently(self):
        self.assertFalse(self.link()["duplicate"])
        self.assertTrue(self.link()["duplicate"])

    def test_linked_exit_can_be_retrieved_for_evidence_review(self):
        self.link()
        linked = exits.get_pro_ticker_exit_link(self.order["id"])
        self.assertEqual(linked["reservation_id"], self.reservation_id)
        self.assertEqual(linked["broker_order_id"], self.order["id"])
        self.assertEqual(linked["expected_shares"], 3)

    def test_noncanonical_broker_order_id_rejected(self):
        with self.assertRaisesRegex(ValueError, "Canonical PAPER broker order UUID"):
            self.link({**self.order, "id": "paper-exit-1"})

    def test_wrong_client_order_id_rejected(self):
        with self.assertRaisesRegex(ValueError, "client order ID"):
            self.link({**self.order, "client_order_id": "scanner-exit"})

    def test_buy_cannot_be_linked_as_exit(self):
        with self.assertRaisesRegex(ValueError, "side mismatch"):
            self.link({**self.order, "side": "buy"})

    def test_noncanonical_broker_side_rejected(self):
        for side in ("SELL", "Sell", " sell ", 1):
            with self.subTest(side=side):
                with self.assertRaisesRegex(ValueError, "side mismatch"):
                    self.link({**self.order, "side": side})

    def test_noncanonical_broker_symbol_rejected(self):
        for symbol in ("aapl", "AAPL ", " AAPL", 123):
            with self.subTest(symbol=symbol):
                with self.assertRaisesRegex(ValueError, "symbol or side mismatch"):
                    self.link({**self.order, "symbol": symbol})

    def test_wrong_exit_quantity_rejected(self):
        with self.assertRaisesRegex(ValueError, "quantity mismatch"):
            self.link({**self.order, "qty": "4"})

    def test_nonfinite_exit_quantity_rejected(self):
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "quantity mismatch"):
                    self.link({**self.order, "qty": value})

    def test_symbol_whitespace_rejected(self):
        with self.assertRaisesRegex(ValueError, "Canonical symbol"):
            exits.link_verified_paper_exit(
                reservation_id=self.reservation_id, symbol="AAPL ",
                expected_shares=3, broker_order={**self.order, "symbol": "AAPL "},
                exit_id=self.exit_id,
            )

    def test_unlinked_reservation_rejected(self):
        with patch.object(exits, "get_pro_ticker_reservation", return_value=None):
            with self.assertRaisesRegex(ValueError, "active PAPER"):
                self.link()

    def test_conflicting_reuse_of_broker_order_rejected(self):
        self.link()
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            exits.link_verified_paper_exit(
                reservation_id=self.reservation_id, symbol="AAPL",
                expected_shares=3, broker_order={
                    **self.order, "client_order_id": "ptx-00000000-0000-0000-0000-000000000001",
                }, exit_id="00000000-0000-0000-0000-000000000001",
            )


if __name__ == "__main__":
    unittest.main()
