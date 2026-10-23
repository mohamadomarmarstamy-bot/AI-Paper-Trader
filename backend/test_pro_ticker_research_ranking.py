"""Research ranking never represents volume as a trading recommendation."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from pro_ticker_research_ranking import rank_research_candidates

class RankingTests(unittest.TestCase):
    def row(self, symbol, volume, *, seconds=0):
        return dict(symbol=symbol, price="1.25", volume=volume, average_volume=100,
                    observed_at=(datetime.now(timezone.utc)-timedelta(seconds=seconds)).isoformat(),
                    source="test_fixture")

    def test_deduplicates_and_ranks(self):
        result=rank_research_candidates([self.row("BBB",200),self.row("AAA",300),self.row("BBB",100,seconds=5)],limit=1)
        self.assertEqual(result["count"],2)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["candidates"][0]["symbol"],"AAA")
        self.assertFalse(result["order_execution_authorized"])
        self.assertEqual(result["evaluation"],"relative_volume_ranking_not_ai_score")

    def test_rejects_bad_batch_and_stale_evidence(self):
        for batch in (None,{},[self.row("AAA",1,seconds=200)]):
            with self.assertRaises(ValueError):rank_research_candidates(batch)
        with self.assertRaises(ValueError):rank_research_candidates([],limit=True)

    def test_ties_are_stable(self):
        result=rank_research_candidates([self.row("ZZZ",100),self.row("AAA",100)])
        self.assertEqual([r["symbol"] for r in result["candidates"]],["AAA","ZZZ"])
