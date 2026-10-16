"""HTTP-level tests for isolated Pro Ticker research routes.

Uses a real FastAPI ASGI request/response stack, without importing the production
app or accessing the database, market network, or broker. This is not staging.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException, Request

from test_pro_ticker_route_boundary import isolated_routes


class ProTickerResearchHTTPTests(unittest.TestCase):
    def setUp(self):
        self.routes = isolated_routes()
        self.routes["HTTPException"] = HTTPException
        self.routes["require_app_session"] = self.authenticate
        self.calls = []
        self.fake = ModuleType("pro_ticker_manual_intake")
        self.fake.list_manual_observations = self.history
        self.fake.save_manual_observations = self.save
        self.fake.parse_manual_symbols = lambda raw: [raw]
        self.app = FastAPI()

        @self.app.get("/auto-trader/pro-ticker-manual-submissions")
        def history(request: Request, limit: int = 100):
            return self.routes["list_auto_trader_pro_ticker_manual_submissions"](request, limit=limit)

        @self.app.post("/auto-trader/pro-ticker-paper-preview")
        async def preview(request: Request):
            return await self.routes["pro_ticker_paper_preview"](request)

        @self.app.post("/auto-trader/pro-ticker-manual-submissions")
        async def save(request: Request):
            return await self.routes["auto_trader_pro_ticker_manual_submissions"](request)

    @staticmethod
    def authenticate(request):
        if request.cookies.get("test_session") != "authorized":
            raise HTTPException(status_code=401, detail="Authentication required.")

    def history(self, limit):
        self.calls.append(("history", limit))
        return {"paper": True, "research_only": True, "count": 0, "observations": []}

    def save(self, symbols, quote_fetcher=None):
        self.calls.append(("save", symbols))
        return {"paper": True, "research_only": True, "orders_submitted": 0, "count": 1}

    def request(self, method, path, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.request(method, path, **kwargs)
        with patch.dict(sys.modules, {"pro_ticker_manual_intake": self.fake}):
            return asyncio.run(send())

    def test_anonymous_requests_get_http_401_without_side_effects(self):
        for method, path, payload in (
            ("GET", "/auto-trader/pro-ticker-manual-submissions", None),
            ("POST", "/auto-trader/pro-ticker-manual-submissions", {"symbols": "AAPL"}),
        ):
            with self.subTest(method=method):
                response = self.request(method, path, json=payload)
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.calls, [])

    def test_authorized_history_uses_requested_limit(self):
        response = self.request("GET", "/auto-trader/pro-ticker-manual-submissions?limit=25",
                                cookies={"test_session": "authorized"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["research_only"])
        self.assertEqual(self.calls, [("history", 25)])

    def test_authorized_save_remains_research_only(self):
        response = self.request("POST", "/auto-trader/pro-ticker-manual-submissions",
                                json={"symbols": "AAPL"}, cookies={"test_session": "authorized"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["orders_submitted"], 0)
        self.assertEqual(self.calls, [("save", "AAPL")])

    def test_bad_json_returns_http_400_without_saving(self):
        response = self.request("POST", "/auto-trader/pro-ticker-manual-submissions",
                                content="{bad", headers={"content-type": "application/json"},
                                cookies={"test_session": "authorized"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.calls, [])

    def test_invalid_payload_returns_http_422_without_saving(self):
        response = self.request("POST", "/auto-trader/pro-ticker-manual-submissions",
                                json={"symbols": ["AAPL"]}, cookies={"test_session": "authorized"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.calls, [])


    def test_anonymous_preview_is_http_401(self):
        response = self.request("POST", "/auto-trader/pro-ticker-paper-preview",
                                json={"symbol": "AAPL", "proposed_notional": 10})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.calls, [])

    def test_authenticated_preview_bad_json_is_http_400(self):
        response = self.request("POST", "/auto-trader/pro-ticker-paper-preview",
                                content="{bad", headers={"content-type": "application/json"},
                                cookies={"test_session": "authorized"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.calls, [])

    def test_authenticated_preview_invalid_payload_is_http_422(self):
        response = self.request("POST", "/auto-trader/pro-ticker-paper-preview",
                                json=["AAPL"], cookies={"test_session": "authorized"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.calls, [])


    def test_authenticated_preview_broker_unavailable_is_non_executing_http_200(self):
        fake_policy = ModuleType("pro_ticker_paper_policy")
        fake_policy.check_pro_ticker_paper_entry = lambda **kwargs: (
            _ for _ in ()
        ).throw(AssertionError("Broker unavailable must not reach eligibility"))
        fake_budget = ModuleType("pro_ticker_budget_ledger")
        fake_budget.get_pro_ticker_budget = lambda: {"committed": 0}
        self.routes["fetch_alpaca_paper_positions"] = lambda: (
            _ for _ in ()
        ).throw(ConnectionError("Offline broker test"))
        with patch.dict(sys.modules, {
            "pro_ticker_paper_policy": fake_policy,
            "pro_ticker_budget_ledger": fake_budget,
        }):
            response = self.request(
                "POST", "/auto-trader/pro-ticker-paper-preview",
                json={"symbol": "AAPL", "proposed_notional": 10},
                cookies={"test_session": "authorized"},
            )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["paper"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["allowed"])
        self.assertFalse(result["order_submitted"])
        self.assertEqual(self.calls, [])


    def test_eligible_http_preview_never_submits_order(self):
        broker_calls = []
        self.routes["fetch_alpaca_paper_positions"] = lambda: []
        self.routes["fetch_alpaca_market_clock"] = lambda: {"is_open": True}
        def paper_request(method, path, **kwargs):
            broker_calls.append((method, path))
            if method != "GET":
                raise AssertionError("PAPER preview attempted broker write")
            return [] if path == "/v2/orders" else {"buying_power": "100000"}
        self.routes["alpaca_paper_request"] = paper_request
        self.routes["alpaca_market_data_request"] = lambda method, path, **kwargs: {
            "trade": {"p": 100, "t": datetime.now(timezone.utc).isoformat()}
        }
        fake_policy = ModuleType("pro_ticker_paper_policy")
        fake_policy.check_pro_ticker_paper_entry = lambda **kwargs: SimpleNamespace(
            allowed=True, reason="eligible"
        )
        fake_budget = ModuleType("pro_ticker_budget_ledger")
        fake_budget.get_pro_ticker_budget = lambda: {"committed": 0}
        with patch.dict(sys.modules, {
            "pro_ticker_paper_policy": fake_policy,
            "pro_ticker_budget_ledger": fake_budget,
        }):
            response = self.request(
                "POST", "/auto-trader/pro-ticker-paper-preview",
                json={"symbol": "AAPL", "proposed_notional": 10},
                cookies={"test_session": "authorized"},
            )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["preflight_eligible"])
        self.assertFalse(result["allowed"])
        self.assertFalse(result["order_submitted"])
        self.assertFalse(result["virtual_budget_reconciliation_ready"])
        self.assertEqual(result["reason"], "execution_disabled_pending_reconciliation")
        self.assertTrue(broker_calls)
        self.assertTrue(all(method == "GET" for method, _ in broker_calls))

    def test_ineligible_http_preview_is_blocked(self):
        self.routes["fetch_alpaca_paper_positions"] = lambda: []
        self.routes["fetch_alpaca_market_clock"] = lambda: {"is_open": False}
        self.routes["alpaca_paper_request"] = lambda method, path, **kwargs: (
            [] if path == "/v2/orders" else {"buying_power": "100000"}
        )
        self.routes["alpaca_market_data_request"] = lambda method, path, **kwargs: {
            "trade": {"p": 100, "t": datetime.now(timezone.utc).isoformat()}
        }
        fake_policy = ModuleType("pro_ticker_paper_policy")
        fake_policy.check_pro_ticker_paper_entry = lambda **kwargs: SimpleNamespace(
            allowed=False, reason="market_closed"
        )
        fake_budget = ModuleType("pro_ticker_budget_ledger")
        fake_budget.get_pro_ticker_budget = lambda: {"committed": 0}
        with patch.dict(sys.modules, {
            "pro_ticker_paper_policy": fake_policy,
            "pro_ticker_budget_ledger": fake_budget,
        }):
            response = self.request(
                "POST", "/auto-trader/pro-ticker-paper-preview",
                json={"symbol": "AAPL", "proposed_notional": 10},
                cookies={"test_session": "authorized"},
            )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertFalse(result["preflight_eligible"])
        self.assertFalse(result["allowed"])
        self.assertFalse(result["order_submitted"])
        self.assertEqual(result["reason"], "market_closed")


if __name__ == "__main__":
    unittest.main()
