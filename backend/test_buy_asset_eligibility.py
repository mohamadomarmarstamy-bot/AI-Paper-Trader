"""Regression tests for buy-side Alpaca asset eligibility."""

import ast
from pathlib import Path
import unittest
from typing import Any
from unittest.mock import Mock


def load_risk_validator(asset_response=None, asset_error=None):
    source = ast.parse(
        (Path(__file__).parent / "main.py").read_text(
            encoding="utf-8-sig"
        )
    )

    node = next(
        n for n in source.body
        if isinstance(n, ast.FunctionDef)
        and n.name == "validate_alpaca_paper_order_risk"
    )

    broker = Mock()

    if asset_error is not None:
        broker.side_effect = asset_error
    else:
        broker.return_value = asset_response

    account_fetch = Mock(
        side_effect=RuntimeError("STOP_AFTER_ASSET_CHECK")
    )

    ns = {
        "Any": Any,
        "clean_symbol": lambda s: str(s or "").strip().upper(),
        "safe_float": lambda x: float(x) if x is not None else None,
        "clean_error_message": str,
        "alpaca_paper_request": broker,
        "BrokerRateLimited": type(
            "BrokerRateLimited", (Exception,), {}
        ),
        "fetch_alpaca_paper_account": account_fetch,
    }

    module = ast.Module(
        body=ast.parse(
            "from __future__ import annotations"
        ).body + [node],
        type_ignores=[],
    )

    exec(compile(module, "main.py", "exec"), ns)

    return ns["validate_alpaca_paper_order_risk"], broker, account_fetch


class BuyAssetEligibilityTests(unittest.TestCase):

    def test_inactive_asset_blocks_purchase(self):
        validate, broker, account = load_risk_validator(
            {"status": "inactive", "tradable": False}
        )

        result = validate(
            symbol="DBRG",
            shares=10,
            side="buy",
        )

        self.assertFalse(result["approved"])
        self.assertTrue(result["inactive_asset"])
        self.assertTrue(result["requires_attention"])
        broker.assert_called_once_with(
            "GET", "/v2/assets/DBRG"
        )
        account.assert_not_called()

    def test_nontradable_asset_blocks_purchase(self):
        validate, broker, account = load_risk_validator(
            {"status": "active", "tradable": False}
        )

        result = validate(
            symbol="WBD",
            shares=10,
            side="buy",
        )

        self.assertFalse(result["approved"])
        self.assertTrue(result["inactive_asset"])
        broker.assert_called_once_with(
            "GET", "/v2/assets/WBD"
        )
        account.assert_not_called()

    def test_asset_lookup_failure_blocks_purchase(self):
        validate, broker, account = load_risk_validator(
            asset_error=RuntimeError("API unavailable")
        )

        result = validate(
            symbol="TEST",
            shares=2,
            side="buy",
        )

        self.assertFalse(result["approved"])
        self.assertTrue(
            result["asset_eligibility_unverified"]
        )
        broker.assert_called_once_with(
            "GET", "/v2/assets/TEST"
        )
        account.assert_not_called()

    def test_active_tradable_asset_continues_to_risk_checks(self):
        validate, broker, account = load_risk_validator(
            {"status": "active", "tradable": True}
        )

        with self.assertRaisesRegex(
            RuntimeError, "STOP_AFTER_ASSET_CHECK"
        ):
            validate(
                symbol="TEST",
                shares=2,
                side="buy",
            )

        broker.assert_called_once_with(
            "GET", "/v2/assets/TEST"
        )
        account.assert_called_once_with()

    def test_sell_does_not_require_buy_side_asset_check(self):
        validate, broker, account = load_risk_validator()

        with self.assertRaisesRegex(
            RuntimeError, "STOP_AFTER_ASSET_CHECK"
        ):
            validate(
                symbol="DBRG",
                shares=10,
                side="sell",
            )

        broker.assert_not_called()
        account.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
