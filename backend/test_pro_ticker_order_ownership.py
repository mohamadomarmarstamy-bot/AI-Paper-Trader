"""Tests for conservative PAPER order ownership verification."""
import unittest

from pro_ticker_order_ownership import verify_paper_order_link


class OrderOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.reservation = {"broker_order_id": "order-123", "symbol": "AAPL"}
        self.order = {"id": "order-123", "symbol": "AAPL", "side": "buy"}

    def test_matching_entry(self):
        self.assertTrue(verify_paper_order_link(self.reservation, self.order)["verified"])

    def test_symbol_alone_does_not_establish_ownership(self):
        self.assertFalse(verify_paper_order_link(
            {**self.reservation, "broker_order_id": None}, self.order
        )["verified"])

    def test_different_order_rejected(self):
        self.assertFalse(verify_paper_order_link(
            self.reservation, {**self.order, "id": "order-456"}
        )["verified"])

    def test_sell_order_rejected(self):
        self.assertFalse(verify_paper_order_link(
            self.reservation, {**self.order, "side": "sell"}
        )["verified"])


if __name__ == "__main__":
    unittest.main()
