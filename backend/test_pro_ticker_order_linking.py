"""Test PAPER client order ID attribution and durable reservation linking."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pro_ticker_budget_ledger as ledger
from pro_ticker_order_linking import pro_ticker_client_order_id, verify_paper_entry_for_link


class PaperOrderLinkingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = os.path.join(self.tmp.name, "link.db")

        def connection():
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            self.addCleanup(conn.close)
            return conn

        p = patch.object(ledger, "get_connection", side_effect=connection)
        p.start()
        self.addCleanup(p.stop)
        self.reservation = ledger.reserve_pro_ticker_budget("AAPL", 100.00)
        self.reservation_id = self.reservation["reservation_id"]
        ledger.set_paper_reservation_expected_shares(self.reservation_id, 10)
        self.order = {
            "id": "paper-order-1",
            "client_order_id": pro_ticker_client_order_id(self.reservation_id),
            "symbol": "AAPL", "side": "buy", "status": "new", "qty": "10",
        }

    def test_verified_link_is_persisted(self):
        result = ledger.link_verified_paper_entry_order(self.reservation_id, self.order)
        self.assertTrue(result["linked"])
        self.assertFalse(result["duplicate"])
        saved = ledger.list_pro_ticker_reservations()["reservations"][0]
        self.assertEqual(saved["broker_order_id"], "paper-order-1")
        self.assertEqual(saved["status"], "submitted")

    def test_same_order_link_is_idempotent(self):
        ledger.link_verified_paper_entry_order(self.reservation_id, self.order)
        self.assertTrue(
            ledger.link_verified_paper_entry_order(self.reservation_id, self.order)["duplicate"]
        )

    def test_wrong_client_order_id_fails_closed(self):
        with self.assertRaises(ValueError):
            ledger.link_verified_paper_entry_order(
                self.reservation_id, {**self.order, "client_order_id": "other"}
            )

    def test_wrong_symbol_fails_closed(self):
        with self.assertRaises(ValueError):
            ledger.link_verified_paper_entry_order(
                self.reservation_id, {**self.order, "symbol": "MSFT"}
            )

    def test_sell_cannot_link_as_entry(self):
        with self.assertRaises(ValueError):
            ledger.link_verified_paper_entry_order(
                self.reservation_id, {**self.order, "side": "sell"}
            )

    def test_unknown_order_status_fails_closed(self):
        self.assertFalse(verify_paper_entry_for_link(
            {"id": self.reservation_id, "symbol": "AAPL", "status": "reserved"},
            {**self.order, "status": "mystery"},
        )["verified"])

    def test_different_order_cannot_replace_link(self):
        ledger.link_verified_paper_entry_order(self.reservation_id, self.order)
        with self.assertRaises(ValueError):
            ledger.link_verified_paper_entry_order(
                self.reservation_id, {**self.order, "id": "other"}
            )

    def test_broker_order_quantity_mismatch_fails_closed(self):
        with self.assertRaises(ValueError):
            ledger.link_verified_paper_entry_order(
                self.reservation_id, {**self.order, "qty": "11"},
            )

    def test_bound_quantity_is_immutable(self):
        with self.assertRaises(ValueError):
            ledger.set_paper_reservation_expected_shares(self.reservation_id, 11)

    def test_link_without_bound_quantity_is_rejected(self):
        second = ledger.reserve_pro_ticker_budget("MSFT", 100.00)
        order = {**self.order, "symbol": "MSFT", "id": "paper-order-2",
                 "client_order_id": pro_ticker_client_order_id(second["reservation_id"])}
        with self.assertRaises(ValueError):
            ledger.link_verified_paper_entry_order(second["reservation_id"], order)

    def test_invalid_reservation_id_rejected(self):
        with self.assertRaises(ValueError):
            pro_ticker_client_order_id("not-a-uuid")


if __name__ == "__main__":
    unittest.main()
