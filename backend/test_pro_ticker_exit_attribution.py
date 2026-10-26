"""Tests for PAPER strategy exit fill attribution."""
import unittest
from pro_ticker_exit_attribution import review_linked_paper_exit


class ExitAttributionTests(unittest.TestCase):
    def setUp(self):
        self.args = dict(
            symbol="AAPL", entry_filled_shares=5,
            expected_exit_order_id="exit-123", expected_exit_shares=5,
            broker_exit_order={
                "id": "exit-123", "symbol": "AAPL", "side": "sell",
                "qty": "5", "filled_qty": "5", "status": "filled",
            },
        )

    def check(self, **changes):
        return review_linked_paper_exit(**{**self.args, **changes})

    def test_linked_full_exit_verified_without_releasing_budget(self):
        result = self.check()
        self.assertTrue(result["fully_closed_verified"])
        self.assertEqual(result["remaining_strategy_shares"], 0)
        self.assertFalse(result["budget_released"])

    def test_partial_exit_does_not_prove_close(self):
        result = self.check(broker_exit_order={
            **self.args["broker_exit_order"],
            "filled_qty": "2", "status": "partially_filled",
        })
        self.assertFalse(result["fully_closed_verified"])
        self.assertEqual(result["remaining_strategy_shares"], 3)

    def test_unlinked_sell_order_rejected(self):
        self.assertEqual(
            self.check(expected_exit_order_id="another")["reason"],
            "order_id_mismatch",
        )

    def test_buy_order_cannot_be_used_as_exit(self):
        result = self.check(broker_exit_order={
            **self.args["broker_exit_order"], "side": "buy",
        })
        self.assertFalse(result["fully_closed_verified"])

    def test_cannot_exit_more_than_strategy_entry(self):
        self.assertEqual(
            self.check(expected_exit_shares=6)["reason"],
            "invalid_strategy_share_quantity",
        )

    def test_missing_order_fails_closed(self):
        self.assertFalse(self.check(broker_exit_order=None)["fully_closed_verified"])


if __name__ == "__main__":
    unittest.main()
