"""Unit tests for the research-only Pro Ticker manual intake."""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pro_ticker_manual_intake import parse_manual_symbols, save_manual_observations


class ManualProTickerIntakeTests(unittest.TestCase):
    def test_parse_deduplicates_and_normalizes(self):
        self.assertEqual(parse_manual_symbols("aapl, MSFT aapl\nBRK.B"), ["AAPL", "MSFT", "BRK.B"])

    def test_rejects_invalid_and_empty(self):
        for raw in ("", "AAPL! DROP", "$BAD", "AAPL/" ):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parse_manual_symbols(raw)

    def test_history_is_read_only_and_newest_first(self):
        import database
        from pro_ticker_manual_intake import list_manual_observations
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(database, "DATABASE_PATH", Path(folder) / "test.db"):
                save_manual_observations("AAPL")
                save_manual_observations("MSFT")
                history = list_manual_observations()
                self.assertEqual(history["count"], 2)
                self.assertEqual(
                    [row["symbol"] for row in history["observations"]],
                    ["MSFT", "AAPL"],
                )
                self.assertTrue(history["research_only"])
                with self.assertRaises(ValueError):
                    list_manual_observations(limit=0)

    def test_saves_research_only_without_orders(self):
        with tempfile.TemporaryDirectory() as folder:
            import database
            with patch.object(database, "DATABASE_PATH", Path(folder) / "test.db"):
                result = save_manual_observations("aapl, msft, aapl")
                self.assertEqual(result["count"], 2)
                self.assertEqual(result["orders_submitted"], 0)
                self.assertTrue(result["research_only"])
                with database.get_connection() as connection:
                    rows = connection.execute(
                        "SELECT symbol, order_status, observed_at FROM pro_ticker_manual_observations ORDER BY id"
                    ).fetchall()
                self.assertEqual([row["symbol"] for row in rows], ["AAPL", "MSFT"])
                self.assertTrue(all(row["order_status"] == "research_only" for row in rows))
                self.assertTrue(all(row["observed_at"] for row in rows))


if __name__ == "__main__":
    unittest.main()
