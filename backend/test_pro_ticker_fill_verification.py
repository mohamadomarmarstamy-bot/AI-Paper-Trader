"""Unit tests for read-only PAPER broker fill verification."""
import unittest
from pro_ticker_fill_verification import assess_paper_order_fill


class FillVerificationTests(unittest.TestCase):
    def setUp(self):
        self.base = dict(
            expected_order_id="paper-order-1", expected_symbol="AAPL",
            expected_side="buy", expected_shares=5,
            broker_order={
                "id": "paper-order-1", "symbol": "AAPL",
                "side": "buy", "qty": "5", "filled_qty": "2",
                "status": "partially_filled",
            },
        )

    def check(self, **changes):
        return assess_paper_order_fill(**{**self.base, **changes})

    def test_partial_fill_verified_without_releasing_remainder(self):
        result = self.check()
        self.assertTrue(result["verified"])
        self.assertEqual(result["filled_shares"], 2)
        self.assertEqual(result["remaining_shares"], 3)
        self.assertFalse(result["terminal"])

    def test_completed_fill(self):
        result = self.check(broker_order={
            **self.base["broker_order"], "filled_qty": "5", "status": "filled",
        })
        self.assertTrue(result["verified"])
        self.assertTrue(result["terminal"])

    def test_canceled_partial_fill_preserves_filled_shares(self):
        result = self.check(broker_order={
            **self.base["broker_order"], "status": "canceled",
        })
        self.assertTrue(result["verified"])
        self.assertEqual(result["filled_shares"], 2)

    def test_wrong_order_rejected(self):
        self.assertFalse(self.check(expected_order_id="other")["verified"])

    def test_wrong_side_rejected(self):
        self.assertFalse(self.check(expected_side="sell")["verified"])

    def test_wrong_quantity_rejected(self):
        self.assertFalse(self.check(expected_shares=6)["verified"])

    def test_impossible_fill_rejected(self):
        self.assertFalse(self.check(broker_order={
            **self.base["broker_order"], "filled_qty": "6",
        })["verified"])

    def test_inconsistent_filled_status_rejected(self):
        self.assertFalse(self.check(broker_order={
            **self.base["broker_order"], "status": "filled",
        })["verified"])

    def test_boolean_broker_quantities_rejected(self):
        for field in ("qty", "filled_qty"):
            for value in (True, False):
                with self.subTest(field=field, value=value):
                    order = {**self.base["broker_order"], field: value}
                    result = self.check(broker_order=order)
                    self.assertFalse(result["verified"])
                    self.assertEqual(result["reason"], "invalid_broker_quantities")

    def test_missing_snapshot_rejected(self):
        self.assertFalse(self.check(broker_order=None)["verified"])


if __name__ == "__main__":
    unittest.main()
