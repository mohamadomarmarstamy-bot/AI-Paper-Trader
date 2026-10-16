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


if __name__ == "__main__":
    unittest.main()
