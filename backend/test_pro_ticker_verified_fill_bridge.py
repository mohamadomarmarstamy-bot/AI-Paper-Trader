"""Tests for the verified PAPER order-to-position accounting bridge."""
import unittest
from unittest.mock import patch

import pro_ticker_verified_fill_bridge as bridge


class VerifiedFillBridgeTests(unittest.TestCase):
    def setUp(self):
        self.reservation_id = "12345678-1234-1234-1234-123456789abc"
        self.reservation = {
            "id": self.reservation_id, "symbol": "AAPL", "status": "submitted",
            "broker_order_id": "paper-order-1",
        }
        self.order = {
            "id": "paper-order-1",
            "client_order_id": "pt-" + self.reservation_id,
            "symbol": "AAPL", "side": "buy", "qty": "10",
            "filled_qty": "4", "status": "partially_filled",
        }
        p = patch.object(
            bridge, "list_pro_ticker_reservations",
            return_value={"reservations": [self.reservation]},
        )
        p.start()
        self.addCleanup(p.stop)

    def test_verified_partial_fill_posts_only_broker_cumulative_qty(self):
        with patch.object(bridge, "post_verified_cumulative_paper_fill", return_value={"posted": True}) as post:
            self.assertTrue(bridge.account_linked_paper_entry_snapshot(
                self.reservation_id, self.order,
            )["posted"])
            self.assertEqual(post.call_args.kwargs["cumulative_filled_shares"], 4)
            self.assertTrue(post.call_args.kwargs["ownership_verified"])

    def test_wrong_client_order_id_does_not_post(self):
        with patch.object(bridge, "post_verified_cumulative_paper_fill") as post:
            with self.assertRaises(ValueError):
                bridge.account_linked_paper_entry_snapshot(
                    self.reservation_id, {**self.order, "client_order_id": "scanner-order"},
                )
            post.assert_not_called()

    def test_wrong_broker_order_id_does_not_post(self):
        with self.assertRaises(ValueError):
            bridge.account_linked_paper_entry_snapshot(
                self.reservation_id, {**self.order, "id": "other"},
            )

    def test_sell_does_not_post(self):
        with self.assertRaises(ValueError):
            bridge.account_linked_paper_entry_snapshot(
                self.reservation_id, {**self.order, "side": "sell"},
            )

    def test_unknown_order_status_does_not_post(self):
        with self.assertRaises(ValueError):
            bridge.account_linked_paper_entry_snapshot(
                self.reservation_id, {**self.order, "status": "unknown"},
            )

    def test_unlinked_reservation_does_not_post(self):
        self.reservation["status"] = "reserved"
        with self.assertRaises(ValueError):
            bridge.account_linked_paper_entry_snapshot(self.reservation_id, self.order)

    def test_invalid_broker_quantity_does_not_post(self):
        with self.assertRaises(ValueError):
            bridge.account_linked_paper_entry_snapshot(
                self.reservation_id, {**self.order, "qty": "nan"},
            )

    def test_missing_reservation_does_not_post(self):
        with self.assertRaises(ValueError):
            bridge.account_linked_paper_entry_snapshot("another-id", self.order)


if __name__ == "__main__":
    unittest.main()
