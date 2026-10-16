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
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )]
        self.assertTrue(db_path.is_file())
        self.assertTrue(any("pro_ticker" in table for table in tables))

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


if __name__ == "__main__":
    unittest.main()
