"""Durable Pro Ticker PAPER budget ledger tests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pro_ticker_test_db_helper import tracked_test_connections

import database
from pro_ticker_budget_ledger import (
    get_pro_ticker_budget,
    list_pro_ticker_reservations,
    reserve_pro_ticker_budget,
    record_verified_paper_order_state,
)


class BudgetLedgerTests(unittest.TestCase):
    def test_reserve_prevents_duplicate_and_overspend(self):
        with tempfile.TemporaryDirectory() as directory:
            with tracked_test_connections():
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

    def test_order_state_preserves_partial_fill_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            with tracked_test_connections():
                with patch.object(database, "DATABASE_PATH", Path(directory) / "ledger.db"):
                    reservation = reserve_pro_ticker_budget("AAPL", 1000)
                    reservation_id = reservation["reservation_id"]
                    with database.get_connection() as conn:
                        conn.execute(
                            "UPDATE pro_ticker_paper_reservations SET broker_order_id = ? WHERE id = ?",
                            ("paper-001", reservation_id),
                        )
                    state = record_verified_paper_order_state(
                        reservation_id, "paper-001", "canceled", filled_quantity=2,
                    )
                    self.assertEqual(state["status"], "filled")
                    self.assertEqual(get_pro_ticker_budget()["committed"], 1000)
                    with self.assertRaisesRegex(ValueError, "Conflicting"):
                        record_verified_paper_order_state(
                            reservation_id, "paper-002", "filled", filled_quantity=2,
                        )
                    with self.assertRaisesRegex(ValueError, "downgraded"):
                        record_verified_paper_order_state(
                            reservation_id, "paper-001", "canceled", filled_quantity=0,
                        )

    def test_rejected_unfilled_releases_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            with tracked_test_connections():
                with patch.object(database, "DATABASE_PATH", Path(directory) / "ledger.db"):
                    reservation = reserve_pro_ticker_budget("MSFT", 250)
                    with database.get_connection() as conn:
                        conn.execute(
                            "UPDATE pro_ticker_paper_reservations SET broker_order_id = ? WHERE id = ?",
                            ("paper-003", reservation["reservation_id"]),
                        )
                    record_verified_paper_order_state(
                        reservation["reservation_id"], "paper-003", "rejected",
                        filled_quantity=0,
                    )
                    self.assertEqual(get_pro_ticker_budget()["committed"], 0)

    def test_full_virtual_budget_cannot_be_reserved_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            with tracked_test_connections():
                with patch.object(database, "DATABASE_PATH", Path(directory) / "ledger.db"):
                    reserve_pro_ticker_budget("AAPL", 50000)
                    budget = get_pro_ticker_budget()
                    self.assertEqual(budget["committed"], 50000)
                    self.assertEqual(budget["available"], 0)
                    with self.assertRaisesRegex(ValueError, "budget exceeded"):
                        reserve_pro_ticker_budget("MSFT", 0.01)
                    self.assertEqual(get_pro_ticker_budget()["committed"], 50000)

    def test_repeated_rejected_state_cannot_restore_spent_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            with tracked_test_connections():
                with patch.object(database, "DATABASE_PATH", Path(directory) / "ledger.db"):
                    reservation = reserve_pro_ticker_budget("AAPL", 1000)
                    reservation_id = reservation["reservation_id"]
                    with database.get_connection() as conn:
                        conn.execute(
                            "UPDATE pro_ticker_paper_reservations SET broker_order_id = ? WHERE id = ?",
                            ("paper-rejected", reservation_id),
                        )
                    record_verified_paper_order_state(
                        reservation_id, "paper-rejected", "rejected", filled_quantity=0,
                    )
                    self.assertEqual(get_pro_ticker_budget()["committed"], 0)
                    with self.assertRaisesRegex(ValueError, "Terminal reservation"):
                        record_verified_paper_order_state(
                            reservation_id, "paper-rejected", "filled", filled_quantity=1,
                        )
                    self.assertEqual(get_pro_ticker_budget()["committed"], 0)

    def test_order_state_rejects_boolean_and_nonfinite_fill_quantities(self):
        with tempfile.TemporaryDirectory() as directory:
            with tracked_test_connections():
                with patch.object(database, "DATABASE_PATH", Path(directory) / "ledger.db"):
                    reservation = reserve_pro_ticker_budget("AAPL", 1000)
                    reservation_id = reservation["reservation_id"]
                    with database.get_connection() as conn:
                        conn.execute(
                            "UPDATE pro_ticker_paper_reservations SET broker_order_id = ? WHERE id = ?",
                            ("paper-validation", reservation_id),
                        )
                    for quantity in (True, False, float("nan"), float("inf"), -1):
                        with self.subTest(quantity=quantity), self.assertRaisesRegex(ValueError, "Invalid filled quantity"):
                            record_verified_paper_order_state(
                                reservation_id, "paper-validation", "filled",
                                filled_quantity=quantity,
                            )
                    self.assertEqual(get_pro_ticker_budget()["committed"], 1000)

    def test_unknown_broker_order_state_does_not_mutate_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            with tracked_test_connections():
                with patch.object(database, "DATABASE_PATH", Path(directory) / "ledger.db"):
                    reservation = reserve_pro_ticker_budget("MSFT", 125)
                    reservation_id = reservation["reservation_id"]
                    with database.get_connection() as conn:
                        conn.execute(
                            "UPDATE pro_ticker_paper_reservations SET broker_order_id = ? WHERE id = ?",
                            ("paper-unknown", reservation_id),
                        )
                    with self.assertRaisesRegex(ValueError, "Unverified order state"):
                        record_verified_paper_order_state(
                            reservation_id, "paper-unknown", "mystery", filled_quantity=0,
                        )
                    self.assertEqual(get_pro_ticker_budget()["committed"], 125)

    def test_rejects_invalid_amount(self):
        for amount in (0, -1, float("nan"), float("inf"), 0.001):
            with self.subTest(amount=amount):
                with self.assertRaises(ValueError):
                    reserve_pro_ticker_budget("AAPL", amount)


if __name__ == "__main__":
    unittest.main()
