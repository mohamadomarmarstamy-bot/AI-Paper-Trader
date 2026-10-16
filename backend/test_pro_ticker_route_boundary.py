"""Execute actual research route functions in isolation, without importing the live app.

The source functions are compiled from main.py; broker and database dependencies
are replaced with fail-fast doubles. These are route-function tests, not HTTP tests.
"""
import ast
import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).with_name("main.py")


class Denied(Exception):
    pass


class RouteHTTPError(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail


class FakeRequest:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    async def json(self):
        if self.error:
            raise self.error
        return self.payload


def isolated_routes():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    names = {
        "auto_trader_pro_ticker_manual_submissions",
        "list_auto_trader_pro_ticker_manual_submissions",
        "pro_ticker_paper_preview",
    }
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    for node in nodes:
        node.decorator_list = []
        node.returns = None
        node.args.defaults = []
        for arg in node.args.args:
            arg.annotation = None
    namespace = {
        "Request": object,
        "HTTPException": RouteHTTPError,
        "Any": object,
        "require_app_session": lambda request: (_ for _ in ()).throw(Denied()),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(SOURCE), "exec"), namespace)
    return namespace


class ProTickerRouteBoundaryTests(unittest.TestCase):
    def test_all_three_routes_deny_unauthenticated_requests_before_work(self):
        routes = isolated_routes()
        with self.assertRaises(Denied):
            routes["list_auto_trader_pro_ticker_manual_submissions"](FakeRequest(), limit=100)
        with self.assertRaises(Denied):
            asyncio.run(routes["auto_trader_pro_ticker_manual_submissions"](FakeRequest({"symbols": "AAPL"})))
        with self.assertRaises(Denied):
            asyncio.run(routes["pro_ticker_paper_preview"](FakeRequest({"symbol": "AAPL", "proposed_notional": 10})))

    def test_unauthenticated_bad_json_is_denied_before_parsing(self):
        routes = isolated_routes()
        with self.assertRaises(Denied):
            asyncio.run(routes["auto_trader_pro_ticker_manual_submissions"](FakeRequest(error=RuntimeError("body should not be parsed"))))
        with self.assertRaises(Denied):
            asyncio.run(routes["pro_ticker_paper_preview"](FakeRequest(error=RuntimeError("body should not be parsed"))))


    def test_authenticated_submission_rejects_non_object_payload(self):
        routes = isolated_routes()
        routes["require_app_session"] = lambda request: None
        with self.assertRaises(RouteHTTPError) as caught:
            asyncio.run(routes["auto_trader_pro_ticker_manual_submissions"](FakeRequest(["AAPL"])))
        self.assertEqual(caught.exception.status_code, 422)

    def test_authenticated_preview_rejects_non_object_payload(self):
        routes = isolated_routes()
        routes["require_app_session"] = lambda request: None
        with self.assertRaises(RouteHTTPError) as caught:
            asyncio.run(routes["pro_ticker_paper_preview"](FakeRequest(["AAPL"])))
        self.assertEqual(caught.exception.status_code, 422)


    def test_authenticated_malformed_json_returns_400(self):
        for route_name in ("auto_trader_pro_ticker_manual_submissions", "pro_ticker_paper_preview"):
            with self.subTest(route=route_name):
                routes = isolated_routes()
                routes["require_app_session"] = lambda request: None
                with self.assertRaises(RouteHTTPError) as caught:
                    asyncio.run(routes[route_name](FakeRequest(error=ValueError("invalid JSON"))))
                self.assertEqual(caught.exception.status_code, 400)

    def test_authenticated_submission_requires_symbols_string(self):
        for payload in ({}, {"symbols": None}, {"symbols": ["AAPL"]}, {"symbols": 123}):
            with self.subTest(payload=payload):
                routes = isolated_routes()
                routes["require_app_session"] = lambda request: None
                with self.assertRaises(RouteHTTPError) as caught:
                    asyncio.run(routes["auto_trader_pro_ticker_manual_submissions"](FakeRequest(payload)))
                self.assertEqual(caught.exception.status_code, 422)

    def test_authenticated_preview_rejects_missing_or_invalid_notional(self):
        for payload in ({"symbol": "AAPL"}, {"symbol": "AAPL", "proposed_notional": "bad"}, {"symbol": "AAPL MSFT", "proposed_notional": 10}):
            with self.subTest(payload=payload):
                routes = isolated_routes()
                routes["require_app_session"] = lambda request: None
                with self.assertRaises(RouteHTTPError) as caught:
                    asyncio.run(routes["pro_ticker_paper_preview"](FakeRequest(payload)))
                self.assertEqual(caught.exception.status_code, 422)


    def test_authenticated_history_uses_read_only_intake(self):
        import sys
        from types import ModuleType
        routes = isolated_routes()
        routes["require_app_session"] = lambda request: None
        fake = ModuleType("pro_ticker_manual_intake")
        observed = []
        def list_observations(limit):
            observed.append(limit)
            return {"paper": True, "research_only": True, "count": 0, "observations": []}
        fake.list_manual_observations = list_observations
        with patch.dict(sys.modules, {"pro_ticker_manual_intake": fake}):
            result = routes["list_auto_trader_pro_ticker_manual_submissions"](FakeRequest(), limit=25)
        self.assertEqual(observed, [25])
        self.assertTrue(result["research_only"])
        self.assertEqual(result["observations"], [])

    def test_authenticated_manual_save_uses_research_intake_only(self):
        import sys
        from types import ModuleType
        routes = isolated_routes()
        routes["require_app_session"] = lambda request: None
        fake = ModuleType("pro_ticker_manual_intake")
        observed = []
        def save(symbols, quote_fetcher=None):
            observed.append((symbols, callable(quote_fetcher)))
            return {"paper": True, "research_only": True, "orders_submitted": 0, "count": 1}
        fake.save_manual_observations = save
        with patch.dict(sys.modules, {"pro_ticker_manual_intake": fake}):
            result = asyncio.run(routes["auto_trader_pro_ticker_manual_submissions"](FakeRequest({"symbols": "AAPL"})))
        self.assertEqual(observed, [("AAPL", True)])
        self.assertEqual(result["orders_submitted"], 0)
        self.assertTrue(result["research_only"])


    def test_manual_submission_rejects_invalid_symbols_without_broker_calls(self):
        import sys
        from types import ModuleType
        routes = isolated_routes()
        routes["require_app_session"] = lambda request: None
        fake = ModuleType("pro_ticker_manual_intake")
        def reject_symbols(symbols, quote_fetcher=None):
            raise ValueError("Invalid stock symbol")
        fake.save_manual_observations = reject_symbols
        with patch.dict(sys.modules, {"pro_ticker_manual_intake": fake}):
            with self.assertRaises(RouteHTTPError) as caught:
                asyncio.run(routes["auto_trader_pro_ticker_manual_submissions"](FakeRequest({"symbols": "BAD!"})))
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("Invalid stock symbol", caught.exception.detail)

    def test_manual_submission_quote_callback_is_read_only(self):
        import sys
        from types import ModuleType
        routes = isolated_routes()
        routes["require_app_session"] = lambda request: None
        calls = []
        routes["alpaca_market_data_request"] = lambda method, path, **kwargs: (
            calls.append((method, path)) or {"trade": {"p": 100.0, "t": "2026-10-16T14:00:00Z"}}
        )
        fake = ModuleType("pro_ticker_manual_intake")
        def capture(symbols, quote_fetcher=None):
            quote = quote_fetcher("AAPL")
            return {"paper": True, "research_only": True, "orders_submitted": 0, "price": quote["p"]}
        fake.save_manual_observations = capture
        with patch.dict(sys.modules, {"pro_ticker_manual_intake": fake}):
            result = asyncio.run(routes["auto_trader_pro_ticker_manual_submissions"](FakeRequest({"symbols": "AAPL"})))
        self.assertEqual(calls, [("GET", "/v2/stocks/AAPL/trades/latest")])
        self.assertEqual(result["orders_submitted"], 0)


    def test_preview_fails_closed_when_broker_snapshot_is_unavailable(self):
        import sys
        from types import ModuleType
        routes = isolated_routes()
        routes["require_app_session"] = lambda request: None
        routes["fetch_alpaca_paper_positions"] = lambda: (_ for _ in ()).throw(RuntimeError("offline"))
        fake_policy = ModuleType("pro_ticker_paper_policy")
        fake_policy.check_pro_ticker_paper_entry = lambda **kwargs: None
        fake_intake = ModuleType("pro_ticker_manual_intake")
        fake_intake.parse_manual_symbols = lambda raw: [raw]
        fake_budget = ModuleType("pro_ticker_budget_ledger")
        fake_budget.get_pro_ticker_budget = lambda: {"committed": 0}
        with patch.dict(sys.modules, {
            "pro_ticker_paper_policy": fake_policy,
            "pro_ticker_manual_intake": fake_intake,
            "pro_ticker_budget_ledger": fake_budget,
        }):
            result = asyncio.run(routes["pro_ticker_paper_preview"](
                FakeRequest({"symbol": "AAPL", "proposed_notional": 10})
            ))
        self.assertFalse(result["allowed"])
        self.assertFalse(result["order_submitted"])
        self.assertTrue(result["preview_only"])


    def test_eligible_preview_remains_non_executing(self):
        import sys
        from datetime import datetime, timezone
        from types import ModuleType, SimpleNamespace
        routes = isolated_routes()
        routes["require_app_session"] = lambda request: None
        calls = []
        routes["fetch_alpaca_paper_positions"] = lambda: []
        routes["fetch_alpaca_market_clock"] = lambda: {"is_open": True}
        def paper_request(method, path, **kwargs):
            calls.append((method, path))
            if method != "GET":
                raise AssertionError("Preview must never write broker orders")
            return [] if path == "/v2/orders" else {"buying_power": "100000"}
        routes["alpaca_paper_request"] = paper_request
        routes["alpaca_market_data_request"] = lambda method, path, **kwargs: (
            calls.append((method, path)) or {"trade": {"t": datetime.now(timezone.utc).isoformat(), "p": 100}}
        )
        fake_policy = ModuleType("pro_ticker_paper_policy")
        fake_policy.check_pro_ticker_paper_entry = lambda **kwargs: SimpleNamespace(allowed=True, reason="eligible")
        fake_intake = ModuleType("pro_ticker_manual_intake")
        fake_intake.parse_manual_symbols = lambda raw: [raw]
        fake_budget = ModuleType("pro_ticker_budget_ledger")
        fake_budget.get_pro_ticker_budget = lambda: {"committed": 0}
        with patch.dict(sys.modules, {
            "pro_ticker_paper_policy": fake_policy,
            "pro_ticker_manual_intake": fake_intake,
            "pro_ticker_budget_ledger": fake_budget,
        }):
            result = asyncio.run(routes["pro_ticker_paper_preview"](
                FakeRequest({"symbol": "AAPL", "proposed_notional": 10})
            ))
        self.assertTrue(result["preflight_eligible"])
        self.assertFalse(result["allowed"])
        self.assertFalse(result["order_submitted"])
        self.assertFalse(result["virtual_budget_reconciliation_ready"])
        self.assertEqual(result["reason"], "execution_disabled_pending_reconciliation")
        self.assertTrue(calls)
        self.assertTrue(all(method == "GET" for method, _ in calls))


    def test_preview_rejects_missing_market_trade_without_order(self):
        import sys
        from types import ModuleType
        routes = isolated_routes()
        routes["require_app_session"] = lambda request: None
        calls = []
        routes["fetch_alpaca_paper_positions"] = lambda: []
        routes["fetch_alpaca_market_clock"] = lambda: {"is_open": True}
        def paper_request(method, path, **kwargs):
            calls.append((method, path))
            return [] if path == "/v2/orders" else {"buying_power": "100000"}
        routes["alpaca_paper_request"] = paper_request
        routes["alpaca_market_data_request"] = lambda method, path, **kwargs: {"trade": None}
        fake_policy = ModuleType("pro_ticker_paper_policy")
        fake_policy.check_pro_ticker_paper_entry = lambda **kwargs: (_ for _ in ()).throw(AssertionError("No eligibility check with missing trade"))
        fake_intake = ModuleType("pro_ticker_manual_intake")
        fake_intake.parse_manual_symbols = lambda raw: [raw]
        fake_budget = ModuleType("pro_ticker_budget_ledger")
        fake_budget.get_pro_ticker_budget = lambda: {"committed": 0}
        with patch.dict(sys.modules, {
            "pro_ticker_paper_policy": fake_policy,
            "pro_ticker_manual_intake": fake_intake,
            "pro_ticker_budget_ledger": fake_budget,
        }):
            result = asyncio.run(routes["pro_ticker_paper_preview"](
                FakeRequest({"symbol": "AAPL", "proposed_notional": 10})
            ))
        self.assertFalse(result["allowed"])
        self.assertFalse(result["order_submitted"])
        self.assertTrue(result["preview_only"])
        self.assertTrue(all(method == "GET" for method, _ in calls))


if __name__ == "__main__":
    unittest.main()
