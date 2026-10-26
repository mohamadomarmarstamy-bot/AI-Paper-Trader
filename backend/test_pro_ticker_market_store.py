import sqlite3
import unittest
from pro_ticker_market_store import persist_market_research_run, latest_market_research_run

class ResearchStoreTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        self.result=dict(paper=True,research_only=True,order_execution_authorized=False,
                         requested=1,accepted=1,failed=0,candidates=[dict(symbol="AAA")])
    def tearDown(self):self.db.close()
    def test_empty_and_idempotent_save(self):
        self.assertIsNone(latest_market_research_run(self.db))
        persist_market_research_run(self.db,"run_1",self.result,recorded_at="2026-10-23T14:00:00Z")
        persist_market_research_run(self.db,"run_1",self.result,recorded_at="2026-10-23T14:00:00Z")
        self.assertEqual(latest_market_research_run(self.db)["result"],self.result)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM pro_ticker_market_research_runs").fetchone()[0],1)
    def test_conflicting_retry_rejected(self):
        persist_market_research_run(self.db,"run_1",self.result)
        with self.assertRaises(ValueError):
            persist_market_research_run(self.db,"run_1",{**self.result,"candidates":[]})
    def test_trading_permission_rejected(self):
        with self.assertRaises(ValueError):
            persist_market_research_run(self.db,"run_1",{**self.result,"order_execution_authorized":True})
