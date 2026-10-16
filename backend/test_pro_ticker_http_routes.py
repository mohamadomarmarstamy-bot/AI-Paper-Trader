"""HTTP-level tests for isolated Pro Ticker research routes.

Uses a real FastAPI ASGI request/response stack, without importing the production
app or accessing the database, market network, or broker. This is not staging.
"""
import asyncio
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
        self.app = FastAPI()

        @self.app.get("/auto-trader/pro-ticker-manual-submissions")
        def history(request: Request, limit: int = 100):
            return self.routes["list_auto_trader_pro_ticker_manual_submissions"](request, limit=limit)

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


if __name__ == "__main__":
    unittest.main()
