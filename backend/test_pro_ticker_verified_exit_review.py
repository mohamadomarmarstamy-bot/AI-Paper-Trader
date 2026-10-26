"""Tests for read-only linked PAPER exit fill preview."""
import unittest
from pro_ticker_verified_exit_review import review_verified_paper_exit_link


class VerifiedExitReviewTests(unittest.TestCase):
    def setUp(self):
        self.link = {
            "reservation_id": "reservation", "broker_order_id": "paper-exit",
            "client_order_id": "ptx-exit", "symbol": "AAPL",
            "expected_shares": 5,
        }
        self.reservation = {
            "id": "reservation", "symbol": "AAPL",
            "status": "filled", "broker_order_id": "paper-entry",
        }
        self.order = {
            "id": "paper-exit", "client_order_id": "ptx-exit",
            "symbol": "AAPL", "side": "sell", "qty": "5",
            "filled_qty": "2", "status": "partially_filled",
        }

    def review(self, link=None, reservation=None, order=None):
        return review_verified_paper_exit_link(
            self.link if link is None else link,
            self.reservation if reservation is None else reservation,
            self.order if order is None else order,
        )

    def test_partial_fill_verified_without_mutations(self):
        result = self.review()
        self.assertTrue(result["verified"])
        self.assertEqual(result["filled_shares"], 2)
        self.assertFalse(result["database_updated"])
        self.assertFalse(result["budget_released"])

    def test_wrong_client_order_id_rejected(self):
        result = self.review(order={**self.order, "client_order_id": "scanner"})
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "paper_exit_client_order_id_mismatch")

    def test_wrong_order_id_rejected(self):
        self.assertEqual(self.review(order={**self.order, "id": "other"})["reason"], "order_id_mismatch")

    def test_buy_order_rejected(self):
        self.assertEqual(self.review(order={**self.order, "side": "buy"})["reason"], "side_mismatch")

    def test_missing_reservation_link_rejected(self):
        self.assertEqual(self.review(reservation={**self.reservation, "broker_order_id": None})["reason"],
                         "strategy_reservation_ownership_mismatch")

    def test_filled_order_still_does_not_release_budget(self):
        result = self.review(order={**self.order, "filled_qty": "5", "status": "filled"})
        self.assertTrue(result["verified"])
        self.assertTrue(result["terminal"])
        self.assertFalse(result["budget_released"])
        self.assertFalse(result["reconciled"])

    def test_malformed_quantity_rejected(self):
        self.assertFalse(self.review(order={**self.order, "filled_qty": "NaN"})["verified"])


if __name__ == "__main__":
    unittest.main()
