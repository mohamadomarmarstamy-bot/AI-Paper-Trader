"""Exact PAPER reservation lookup regression tests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pro_ticker_test_db_helper import tracked_test_connections

import database
from pro_ticker_budget_ledger import (
    get_pro_ticker_reservation, reserve_pro_ticker_budget,
)


class ExactReservationLookupTests(unittest.TestCase):
    def test_lookup_finds_older_reservation_beyond_history_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            with tracked_test_connections():
                with patch.object(database, "DATABASE_PATH", Path(directory) / "ledger.db"):
                    first = reserve_pro_ticker_budget("AAPL", 100)
                    for index in range(501):
                        reserve_pro_ticker_budget("S" + str(index), 1)
                    row = get_pro_ticker_reservation(first["reservation_id"])
                    self.assertEqual(row["symbol"], "AAPL")
                    self.assertEqual(row["notional"], 100.0)

    def test_missing_valid_uuid_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            with tracked_test_connections():
                with patch.object(database, "DATABASE_PATH", Path(directory) / "ledger.db"):
                    self.assertIsNone(get_pro_ticker_reservation(
                        "12345678-1234-1234-1234-123456789abc",
                    ))

    def test_malformed_uuid_fails_closed(self):
        with self.assertRaises(ValueError):
            get_pro_ticker_reservation("../orders")
        with self.assertRaises(ValueError):
            get_pro_ticker_reservation("12345678-1234-1234-1234-123456789ABC")


if __name__ == "__main__":
    unittest.main()
