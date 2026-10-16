"""Durable PAPER exit ownership link tests; never submit orders."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import database
import pro_ticker_exit_links as exits


class PaperExitLinkTests(unittest.TestCase):
    def setUp(self):
        self.reservation_id = "12345678-1234-1234-1234-123456789abc"
        self.exit_id = "abcdefab-1234-1234-1234-123456789abc"
        self.order = {
            "id": "paper-exit-1", "client_order_id": "ptx-" + self.exit_id,
            "symbol": "AAPL", "side": "sell", "qty": "3",
            "status": "partially_filled",
        }
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp.name) / "test.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
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

    def test_link_and_repeat_idempotently(self):
        self.assertFalse(self.link()["duplicate"])
        self.assertTrue(self.link()["duplicate"])

    def test_wrong_client_order_id_rejected(self):
        with self.assertRaisesRegex(ValueError, "client order ID"):
            self.link({**self.order, "client_order_id": "scanner-exit"})

    def test_buy_cannot_be_linked_as_exit(self):
        with self.assertRaisesRegex(ValueError, "side mismatch"):
            self.link({**self.order, "side": "buy"})

    def test_wrong_exit_quantity_rejected(self):
        with self.assertRaisesRegex(ValueError, "quantity mismatch"):
            self.link({**self.order, "qty": "4"})

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
