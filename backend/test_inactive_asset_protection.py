"""Offline tests for inactive-asset protection recovery."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


def load_recovery_function(asset_response=None, asset_error=None):
    source = ast.parse(
        (Path(__file__).parent / "main.py").read_text(encoding="utf-8")
    )

    node = next(
        n for n in source.body
        if isinstance(n, ast.FunctionDef)
        and n.name == "submit_alpaca_recovery_oco"
    )

    broker = Mock()

    if asset_error is not None:
        broker.side_effect = asset_error
    else:
        broker.return_value = asset_response

    fetch_orders = Mock(return_value=[])

    ns = {
        "clean_symbol": lambda s: str(s or "").strip().upper(),
        "safe_float": lambda x: float(x) if x is not None else None,
        "clean_error_message": str,
        "alpaca_paper_request": broker,
        "fetch_alpaca_open_orders_for_symbol": fetch_orders,
        "BrokerRateLimited": type("BrokerRateLimited", (Exception,), {}),
    }

    module = ast.Module(body=ast.parse("from __future__ import annotations").body + [node], type_ignores=[])
    exec(compile(module, "main.py", "exec"), ns)

    return ns["submit_alpaca_recovery_oco"], broker, fetch_orders


class InactiveAssetProtectionTests(unittest.TestCase):

    def test_inactive_asset_blocks_recovery(self):
        recovery, broker, fetch_orders = load_recovery_function(
            {"status": "inactive", "tradable": False}
        )

        result = recovery(
            symbol="DBRG",
            shares=120,
            current_price=16.00,
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["inactive_asset"])
        self.assertTrue(result["requires_attention"])
        broker.assert_called_once_with("GET", "/v2/assets/DBRG")
        fetch_orders.assert_not_called()

    def test_nontradable_asset_blocks_recovery(self):
        recovery, broker, fetch_orders = load_recovery_function(
            {"status": "active", "tradable": False}
        )

        result = recovery(
            symbol="WBD",
            shares=62,
            current_price=30.95,
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["inactive_asset"])
        fetch_orders.assert_not_called()

    def test_lookup_failure_does_not_change_orders(self):
        recovery, broker, fetch_orders = load_recovery_function(
            asset_error=RuntimeError("API unavailable")
        )

        result = recovery(
            symbol="DBRG",
            shares=120,
            current_price=16.00,
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["deferred"])
        fetch_orders.assert_not_called()

    def test_active_asset_continues_to_order_check(self):
        recovery, broker, fetch_orders = load_recovery_function(
            {"status": "active", "tradable": True}
        )

        # Stop execution immediately after confirming the function
        # reaches the existing open-order lookup.
        fetch_orders.side_effect = RuntimeError("TEST STOP")

        result = recovery(
            symbol="TEST",
            shares=2,
            current_price=10.00,
        )

        self.assertFalse(result["success"])
        broker.assert_called_once_with("GET", "/v2/assets/TEST")
        fetch_orders.assert_called_once_with("TEST")


if __name__ == "__main__":
    unittest.main()
