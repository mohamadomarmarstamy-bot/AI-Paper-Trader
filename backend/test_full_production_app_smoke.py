"""Full production FastAPI import and route registration smoke test.

Run in a clean process with DATABASE_PATH pointing to a temporary SQLite file,
without ASGI lifespan startup. Never supply broker credentials.
"""
import importlib
import os
import tempfile
import unittest
from pathlib import Path


class ProductionAppImportSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="pro-ticker-app-smoke-")
        cls.original_db = os.environ.get("DATABASE_PATH")
        os.environ["DATABASE_PATH"] = str(Path(cls.temp_dir.name) / "smoke.sqlite3")
        cls.main = importlib.import_module("main")

    @classmethod
    def tearDownClass(cls):
        if cls.original_db is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = cls.original_db
        cls.temp_dir.cleanup()

    def test_real_production_app_has_research_routes(self):
        routes = {(method, route.path): route.endpoint.__name__
                  for route in self.main.app.routes
                  for method in (getattr(route, "methods", None) or set())}
        self.assertEqual(
            routes.get(("GET", "/auto-trader/pro-ticker-manual-submissions")),
            "list_auto_trader_pro_ticker_manual_submissions",
        )
        self.assertEqual(
            routes.get(("POST", "/auto-trader/pro-ticker-manual-submissions")),
            "auto_trader_pro_ticker_manual_submissions",
        )
        self.assertEqual(
            routes.get(("POST", "/auto-trader/pro-ticker-paper-preview")),
            "pro_ticker_paper_preview",
        )

    def test_production_import_initializes_only_temporary_database(self):
        database = importlib.import_module("database")
        self.assertEqual(Path(database.DATABASE_PATH),
                         Path(self.temp_dir.name) / "smoke.sqlite3")
        self.assertTrue(Path(database.DATABASE_PATH).is_file())


    def test_real_production_app_research_routes_reject_anonymous_http(self):
        import asyncio
        import httpx

        async def run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.main.app),
                base_url="http://testserver",
            ) as client:
                return [
                    await client.get("/auto-trader/pro-ticker-manual-submissions"),
                    await client.post("/auto-trader/pro-ticker-manual-submissions",
                                      json={"symbols": "AAPL"}),
                    await client.post("/auto-trader/pro-ticker-paper-preview",
                                      json={"symbol": "AAPL", "proposed_notional": 100}),
                ]

        responses = asyncio.run(run())
        self.assertEqual([response.status_code for response in responses],
                         [401, 401, 401])

    def test_real_production_app_preview_rejects_malformed_anonymous_request(self):
        import asyncio
        import httpx

        async def run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.main.app),
                base_url="http://testserver",
            ) as client:
                return await client.post("/auto-trader/pro-ticker-paper-preview",
                                         content=b"not-json",
                                         headers={"content-type": "application/json"})

        response = asyncio.run(run())
        self.assertEqual(response.status_code, 401)


    def test_real_app_authenticated_research_history_uses_temporary_database(self):
        import asyncio
        import httpx
        from unittest.mock import patch

        with patch.object(self.main, "require_app_session", return_value=None):
            async def run():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.main.app),
                    base_url="http://testserver",
                ) as client:
                    return await client.get(
                        "/auto-trader/pro-ticker-manual-submissions?limit=10"
                    )
            response = asyncio.run(run())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["research_only"])
        self.assertEqual(response.json()["count"], 0)

    def test_real_app_authenticated_preview_never_reaches_broker_on_bad_payload(self):
        import asyncio
        import httpx
        from unittest.mock import patch

        with patch.object(self.main, "require_app_session", return_value=None), \
             patch.object(self.main, "alpaca_paper_request",
                          side_effect=AssertionError("Broker must not be called")), \
             patch.object(self.main, "fetch_alpaca_paper_positions",
                          side_effect=AssertionError("Positions must not be called")):
            async def run():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.main.app),
                    base_url="http://testserver",
                ) as client:
                    return await client.post(
                        "/auto-trader/pro-ticker-paper-preview",
                        json={"symbol": "AAPL", "proposed_notional": "not-a-number"},
                    )
            response = asyncio.run(run())
        self.assertEqual(response.status_code, 422)


    def test_real_app_authenticated_research_save_persists_offline_observation(self):
        import asyncio
        import httpx
        from unittest.mock import patch

        trade = {"p": 125.5, "t": "2026-10-16T15:00:00Z", "s": 1}
        with patch.object(self.main, "require_app_session", return_value=None), \
             patch.object(self.main, "alpaca_market_data_request",
                          return_value={"trade": trade}) as market:
            async def run():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.main.app),
                    base_url="http://testserver",
                ) as client:
                    return await client.post(
                        "/auto-trader/pro-ticker-manual-submissions",
                        json={"symbols": "AAPL"},
                    )
            response = asyncio.run(run())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["research_only"])
        self.assertEqual(response.json()["orders_submitted"], 0)
        market.assert_called_once_with(
            "GET", "/v2/stocks/AAPL/trades/latest", timeout=3.0
        )

    def test_real_app_research_save_rejects_invalid_symbol_without_market_io(self):
        import asyncio
        import httpx
        from unittest.mock import patch

        with patch.object(self.main, "require_app_session", return_value=None), \
             patch.object(self.main, "alpaca_market_data_request",
                          side_effect=AssertionError("No market data for invalid symbol")):
            async def run():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.main.app),
                    base_url="http://testserver",
                ) as client:
                    return await client.post(
                        "/auto-trader/pro-ticker-manual-submissions",
                        json={"symbols": "INVALID!!"},
                    )
            response = asyncio.run(run())
        self.assertEqual(response.status_code, 422)


    def test_real_app_saved_research_observation_is_visible_in_history(self):
        import asyncio
        import httpx
        from unittest.mock import patch

        trade = {"p": 124.25, "t": "2026-10-16T15:00:00Z", "s": 1}
        with patch.object(self.main, "require_app_session", return_value=None), \
             patch.object(self.main, "alpaca_market_data_request",
                          return_value={"trade": trade}):
            async def run():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.main.app),
                    base_url="http://testserver",
                ) as client:
                    saved = await client.post(
                        "/auto-trader/pro-ticker-manual-submissions",
                        json={"symbols": "MSFT"},
                    )
                    history = await client.get(
                        "/auto-trader/pro-ticker-manual-submissions?limit=100"
                    )
                    return saved, history
            saved, history = asyncio.run(run())
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(history.status_code, 200)
        self.assertTrue(saved.json()["research_only"])
        self.assertEqual(saved.json()["orders_submitted"], 0)
        self.assertGreaterEqual(history.json()["count"], 1)
        self.assertTrue(any(
            observation.get("symbol") == "MSFT"
            for observation in history.json()["observations"]
        ))


    def test_research_observation_survives_fresh_database_connection(self):
        import asyncio
        import httpx
        import sqlite3
        from unittest.mock import patch

        trade = {"p": 83.75, "t": "2026-10-16T15:00:00Z", "s": 1}
        with patch.object(self.main, "require_app_session", return_value=None), \
             patch.object(self.main, "alpaca_market_data_request",
                          return_value={"trade": trade}):
            async def save():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.main.app),
                    base_url="http://testserver",
                ) as client:
                    return await client.post(
                        "/auto-trader/pro-ticker-manual-submissions",
                        json={"symbols": "NVDA"},
                    )
            response = asyncio.run(save())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["orders_submitted"], 0)

        # Re-open the SQLite file independently: data must not live only in
        # a process-local cache or in-memory connection.
        db_path = Path(self.temp_dir.name) / "smoke.sqlite3"
        with sqlite3.connect(db_path) as connection:
            saved_rows = connection.execute(
                "SELECT symbol, order_status, indicator_snapshot_json "
                "FROM pro_ticker_manual_observations WHERE symbol = ? "
                "ORDER BY id DESC LIMIT 1",
                ("NVDA",),
            ).fetchall()
        self.assertTrue(db_path.is_file())
        self.assertEqual(len(saved_rows), 1)
        self.assertEqual(saved_rows[0][0], "NVDA")
        self.assertEqual(saved_rows[0][1], "research_only")
        self.assertIn("alpaca_latest_trade", saved_rows[0][2])

        with patch.object(self.main, "require_app_session", return_value=None):
            async def read():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.main.app),
                    base_url="http://testserver",
                ) as client:
                    return await client.get(
                        "/auto-trader/pro-ticker-manual-submissions?limit=100"
                    )
            history = asyncio.run(read())
        self.assertEqual(history.status_code, 200)
        self.assertTrue(any(
            row.get("symbol") == "NVDA"
            for row in history.json()["observations"]
        ))


    def test_real_signed_session_cookie_authenticates_history_without_auth_mock(self):
        import asyncio
        import httpx
        from unittest.mock import patch

        # Exercise the actual signed-session verifier, not a patched auth guard.
        with patch.object(self.main, "APP_SESSION_SECRET", "isolated-ci-session-key"):
            signed_token = self.main.create_app_session_token()

            async def run():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.main.app),
                    base_url="http://testserver",
                    cookies={self.main.APP_SESSION_COOKIE: signed_token},
                ) as client:
                    return await client.get(
                        "/auto-trader/pro-ticker-manual-submissions?limit=10"
                    )

            response = asyncio.run(run())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["research_only"])

    def test_real_signed_session_cookie_rejects_tampering(self):
        import asyncio
        import httpx
        from unittest.mock import patch

        with patch.object(self.main, "APP_SESSION_SECRET", "isolated-ci-session-key"):
            signed_token = self.main.create_app_session_token()
            bad_token = signed_token[:-1] + (
                "0" if signed_token[-1] != "0" else "1"
            )

            async def run():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.main.app),
                    base_url="http://testserver",
                    cookies={self.main.APP_SESSION_COOKIE: bad_token},
                ) as client:
                    return await client.get(
                        "/auto-trader/pro-ticker-manual-submissions?limit=10"
                    )

            response = asyncio.run(run())
        self.assertEqual(response.status_code, 401)


    def test_real_signed_session_submits_research_without_broker_orders(self):
        import asyncio
        import httpx
        from unittest.mock import patch

        with patch.object(self.main, "APP_SESSION_SECRET", "isolated-ci-session-key"), \
             patch.object(self.main, "alpaca_market_data_request",
                          return_value={"trade": {
                              "p": 42.5, "t": "2026-10-16T15:00:00Z", "s": 1,
                          }}) as market, \
             patch.object(self.main, "alpaca_paper_request",
                          side_effect=AssertionError("No broker order access")):
            token = self.main.create_app_session_token()

            async def run():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.main.app),
                    base_url="http://testserver",
                    cookies={self.main.APP_SESSION_COOKIE: token},
                ) as client:
                    return await client.post(
                        "/auto-trader/pro-ticker-manual-submissions",
                        json={"symbols": "AMD"},
                    )

            response = asyncio.run(run())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["symbols"], ["AMD"])
        self.assertTrue(response.json()["research_only"])
        self.assertEqual(response.json()["orders_submitted"], 0)
        market.assert_called_once_with(
            "GET", "/v2/stocks/AMD/trades/latest", timeout=3.0
        )

    def test_real_signed_session_rejects_invalid_research_body_before_market_io(self):
        import asyncio
        import httpx
        from unittest.mock import patch

        with patch.object(self.main, "APP_SESSION_SECRET", "isolated-ci-session-key"), \
             patch.object(self.main, "alpaca_market_data_request",
                          side_effect=AssertionError("No market request expected")):
            token = self.main.create_app_session_token()

            async def run():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.main.app),
                    base_url="http://testserver",
                    cookies={self.main.APP_SESSION_COOKIE: token},
                ) as client:
                    return await client.post(
                        "/auto-trader/pro-ticker-manual-submissions",
                        json={"symbols": ["AMD"]},
                    )

            response = asyncio.run(run())
        self.assertEqual(response.status_code, 422)


    def test_research_history_survives_fresh_production_app_process(self):
        import json
        import subprocess
        import sys
        from pro_ticker_manual_intake import save_manual_observations

        saved = save_manual_observations("RSTRT")
        self.assertEqual(saved["orders_submitted"], 0)
        self.assertTrue(saved["research_only"])

        # A second Python interpreter imports the actual production app
        # against the same on-disk database and serves an authenticated
        # ASGI request. Lifespan startup and broker credentials are disabled.
        script = """
import asyncio
import json
import httpx
import main

async def check():
    token = main.create_app_session_token()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://testserver",
    ) as client:
        anonymous = await client.get(
            "/auto-trader/pro-ticker-manual-submissions?limit=500"
        )
        client.cookies.set(main.APP_SESSION_COOKIE, token)
        response = await client.get(
            "/auto-trader/pro-ticker-manual-submissions?limit=500"
        )
    return {
        "anonymous_status": anonymous.status_code,
        "status": response.status_code,
        "found": response.status_code == 200 and any(
            row.get("symbol") == "RSTRT"
            and row.get("order_status") == "research_only"
            for row in response.json().get("observations", [])
        ),
    }

print("RESTART_RESULT:" + json.dumps(asyncio.run(check())))
"""
        env = os.environ.copy()
        env.update({
            "DATABASE_PATH": str(Path(self.temp_dir.name) / "smoke.sqlite3"),
            "APP_SESSION_SECRET": "isolated-restart-ci-secret",
            "APP_ACCESS_PASSWORD": "",
            "ALPACA_API_KEY": "",
            "ALPACA_SECRET_KEY": "",
            "ALPACA_API_KEY_ID": "",
            "ALPACA_SECRET_KEY_ID": "",
        })
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(Path(__file__).resolve().parent),
            env=env,
            capture_output=True,
            text=True,
            timeout=35,
            check=True,
        )
        marker = "RESTART_RESULT:"
        result_line = next(
            line for line in completed.stdout.splitlines()
            if line.startswith(marker)
        )
        result = json.loads(result_line[len(marker):])
        self.assertEqual(result["anonymous_status"], 401)
        self.assertEqual(result["status"], 200)
        self.assertTrue(result["found"])


    def test_invalid_history_limit_is_rejected_before_database_read(self):
        import asyncio
        import httpx
        from unittest.mock import patch

        with patch.object(self.main, "APP_SESSION_SECRET", "isolated-ci-session-key"):
            token = self.main.create_app_session_token()

            async def run():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.main.app),
                    base_url="http://testserver",
                    cookies={self.main.APP_SESSION_COOKIE: token},
                ) as client:
                    return [
                        await client.get(
                            "/auto-trader/pro-ticker-manual-submissions?limit=0"
                        ),
                        await client.get(
                            "/auto-trader/pro-ticker-manual-submissions?limit=501"
                        ),
                        await client.get(
                            "/auto-trader/pro-ticker-manual-submissions?limit=invalid"
                        ),
                    ]

            responses = asyncio.run(run())
        self.assertEqual([response.status_code for response in responses],
                         [422, 422, 422])

    def test_missing_session_rejects_research_submission_before_market_io(self):
        import asyncio
        import httpx
        from unittest.mock import patch

        with patch.object(self.main, "alpaca_market_data_request",
                          side_effect=AssertionError("No market request expected")):
            async def run():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.main.app),
                    base_url="http://testserver",
                ) as client:
                    return await client.post(
                        "/auto-trader/pro-ticker-manual-submissions",
                        json={"symbols": "TSLA"},
                    )

            response = asyncio.run(run())
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
