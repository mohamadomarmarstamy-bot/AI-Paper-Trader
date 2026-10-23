from datetime import datetime, timezone
import unittest
from pro_ticker_market_intake import collect_research_snapshots

NOW = datetime(2026,10,23,14,tzinfo=timezone.utc)

class MarketIntakeTests(unittest.TestCase):
    def quote(self, symbol):
        return dict(symbol=symbol,price="2.50",volume=200,average_volume=100,
                    observed_at=NOW.isoformat(),source="synthetic_fixture")

    def test_deduplicates_fetches_and_reports_provider_failure(self):
        calls=[]
        def fetch(symbol):
            calls.append(symbol)
            if symbol=="BAD":raise RuntimeError("private provider credential")
            return self.quote(symbol)
        result=collect_research_snapshots(["AAA","AAA","BAD"],fetch_snapshot=fetch,now=NOW)
        self.assertEqual(calls,["AAA","BAD"])
        self.assertEqual((result["accepted"],result["failed"]),(1,1))
        self.assertNotIn("credential",str(result))
        self.assertFalse(result["order_execution_authorized"])

    def test_symbol_mismatch_fails_closed(self):
        result=collect_research_snapshots(["AAA"],fetch_snapshot=lambda s:self.quote("BBB"),now=NOW)
        self.assertEqual(result["accepted"],0)
        self.assertEqual(result["failed"],1)

    def test_rejects_untrusted_symbol_before_fetch(self):
        called=[]
        with self.assertRaises(ValueError):
            collect_research_snapshots(["AAA","../B"],fetch_snapshot=lambda s:called.append(s),now=NOW)
        self.assertEqual(called,[])
