"""Durable Pro Ticker PAPER budget ledger tests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from pro_ticker_budget_ledger import (
    get_pro_ticker_budget,
    list_pro_ticker_reservations,
    reserve_pro_ticker_budget,
)


class BudgetLedgerTests(unittest.TestCase):
    def test_reserve_prevents_duplicate_and_overspend(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(database, "DATABASE_PATH", Path(directory) / "ledger.db"):
                self.assertEqual(get_pro_ticker_budget()["available"], 50000.0)
                first = reserve_pro_ticker_budget("aapl", 1200.50)
                self.assertFalse(first["order_submitted"])
                self.assertEqual(get_pro_ticker_budget()["committed"], 1200.50)
                with self.assertRaisesRegex(ValueError, "active reservation"):
                    reserve_pro_ticker_budget("AAPL", 100)
                with self.assertRaisesRegex(ValueError, "budget exceeded"):
                    reserve_pro_ticker_budget("MSFT", 49000)
                self.assertEqual(len(list_pro_ticker_reservations()["reservations"]), 1)

    def test_rejects_invalid_amount(self):
        for amount in (0, -1, float("nan"), float("inf"), 0.001):
            with self.subTest(amount=amount):
                with self.assertRaises(ValueError):
                    reserve_pro_ticker_budget("AAPL", amount)


if __name__ == "__main__":
    unittest.main()
