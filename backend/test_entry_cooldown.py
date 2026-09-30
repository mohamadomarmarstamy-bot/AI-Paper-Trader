"""Offline regression tests for automatic-entry cooldown behavior.

Run:
python -m unittest backend.test_entry_cooldown -v

These tests make no Alpaca requests and submit no orders.
"""
import unittest


def should_apply_entry_cooldown(entry_result):
    """Mirror the confirmed-fill condition used by run_auto_trader_cycle."""
    entry_trade = (
        entry_result.get("trade")
        if isinstance(entry_result, dict)
        else None
    )

    return (
        isinstance(entry_trade, dict)
        and str(
            entry_trade.get("status", "")
        ).strip().lower() == "filled"
    )


class EntryCooldownTests(unittest.TestCase):
    def test_confirmed_fill_gets_cooldown(self):
        entry_result = {
            "success": True,
            "trade": {
                "status": "filled",
                "symbol": "TEST",
            },
        }

        self.assertTrue(
            should_apply_entry_cooldown(entry_result)
        )

    def test_failed_entry_does_not_get_cooldown(self):
        entry_result = {
            "success": False,
            "error": "Order rejected",
        }

        self.assertFalse(
            should_apply_entry_cooldown(entry_result)
        )

    def test_rejected_trade_does_not_get_cooldown(self):
        entry_result = {
            "success": False,
            "trade": {
                "status": "rejected",
            },
        }

        self.assertFalse(
            should_apply_entry_cooldown(entry_result)
        )

    def test_accepted_but_unfilled_does_not_get_cooldown(self):
        for status in (
            "accepted",
            "new",
            "pending_new",
            "partially_filled",
        ):
            with self.subTest(status=status):
                entry_result = {
                    "success": True,
                    "trade": {
                        "status": status,
                    },
                }

                self.assertFalse(
                    should_apply_entry_cooldown(
                        entry_result
                    )
                )

    def test_missing_trade_does_not_get_cooldown(self):
        for entry_result in (
            None,
            {},
            {"success": True},
            {"success": False},
            {"trade": None},
            {"trade": "invalid"},
        ):
            with self.subTest(
                entry_result=entry_result
            ):
                self.assertFalse(
                    should_apply_entry_cooldown(
                        entry_result
                    )
                )

    def test_status_matching_is_normalized(self):
        entry_result = {
            "success": True,
            "trade": {
                "status": "  FILLED  ",
            },
        }

        self.assertTrue(
            should_apply_entry_cooldown(entry_result)
        )


if __name__ == "__main__":
    unittest.main()