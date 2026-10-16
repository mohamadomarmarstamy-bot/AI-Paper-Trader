"""HTTP checks with the real production session-verification functions.

Extract authentication helpers from main.py without importing the production app
or starting its trading background jobs. Broker and persistence are mocked.
"""
import ast
import asyncio
import hashlib
import hmac
import sys
import time
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException, Request

from test_pro_ticker_route_boundary import isolated_routes


SOURCE = Path(__file__).with_name("main.py")


def production_auth():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    wanted = {"verify_app_session_token", "request_has_valid_app_session", "require_app_session"}
    nodes = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted]
    if {node.name for node in nodes} != wanted:
        raise AssertionError("Production authentication helpers not found")
    for node in nodes:
        node.decorator_list = []
        node.returns = None
        for arg in node.args.args:
            arg.annotation = None
    namespace = {
        "Request": Request,
        "HTTPException": HTTPException,
        "APP_SESSION_SECRET": "offline-http-test-secret",
        "APP_SESSION_COOKIE": "ai_paper_trader_session",
        "APP_SESSION_MAX_AGE_SECONDS": 604800,
        "time": time,
        "hashlib": hashlib,
        "hmac": hmac,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 str(SOURCE), "exec"), namespace)
    return namespace


class ProductionAuthenticationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.auth = production_auth()
        self.routes = isolated_routes()
        self.routes["HTTPException"] = HTTPException
        self.routes["require_app_session"] = self.auth["require_app_session"]
        self.fake = ModuleType("pro_ticker_manual_intake")
        self.fake.list_manual_observations = lambda limit: {
            "paper": True, "research_only": True, "count": 0, "observations": []
        }
        self.fake.save_manual_observations = lambda symbols, quote_fetcher=None: {
            "paper": True, "research_only": True, "orders_submitted": 0
        }
        self.app = FastAPI()

        @self.app.get("/research")
        def history(request: Request):
            return self.routes["list_auto_trader_pro_ticker_manual_submissions"](request, limit=10)

        @self.app.post("/research")
        async def save(request: Request):
            return await self.routes["auto_trader_pro_ticker_manual_submissions"](request)

    def signed_cookie(self, timestamp=None):
        timestamp_text = str(int(time.time()) if timestamp is None else timestamp)
        signature = hmac.new(b"offline-http-test-secret", timestamp_text.encode(),
                             hashlib.sha256).hexdigest()
        return {"ai_paper_trader_session": f"{timestamp_text}.{signature}"}

    def request(self, method, **kwargs):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                return await client.request(method, "/research", **kwargs)
        with patch.dict(sys.modules, {"pro_ticker_manual_intake": self.fake}):
            return asyncio.run(send())

    def test_production_auth_accepts_signed_cookie_for_research_read(self):
        response = self.request("GET", cookies=self.signed_cookie())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["research_only"])

    def test_production_auth_accepts_signed_cookie_for_research_save(self):
        response = self.request("POST", cookies=self.signed_cookie(),
                                json={"symbols": "AAPL"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["orders_submitted"], 0)

    def test_production_auth_rejects_tampered_cookie(self):
        cookies = self.signed_cookie()
        cookies["ai_paper_trader_session"] += "tampered"
        response = self.request("GET", cookies=cookies)
        self.assertEqual(response.status_code, 401)

    def test_production_auth_rejects_expired_cookie(self):
        cookies = self.signed_cookie(int(time.time()) - 604801)
        response = self.request("GET", cookies=cookies)
        self.assertEqual(response.status_code, 401)

    def test_production_auth_rejects_missing_cookie(self):
        response = self.request("GET")
        self.assertEqual(response.status_code, 401)


    def test_production_auth_rejects_wrong_signature(self):
        cookies = self.signed_cookie()
        token = cookies["ai_paper_trader_session"]
        timestamp, _ = token.split(".", 1)
        cookies["ai_paper_trader_session"] = timestamp + "." + ("0" * 64)
        response = self.request("GET", cookies=cookies)
        self.assertEqual(response.status_code, 401)

    def test_production_auth_rejects_malformed_timestamp(self):
        response = self.request(
            "GET", cookies={"ai_paper_trader_session": "not-a-time.signature"}
        )
        self.assertEqual(response.status_code, 401)

    def test_production_auth_rejects_missing_secret_even_with_signed_cookie(self):
        self.auth["APP_SESSION_SECRET"] = ""
        response = self.request("GET", cookies=self.signed_cookie())
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
