"""Fail-closed research quote normalization tests."""
from datetime import datetime, timedelta, timezone
import unittest
from pro_ticker_market_snapshot import normalize_market_snapshot

NOW = datetime(2026, 10, 23, 14, 0, tzinfo=timezone.utc)

class MarketSnapshotTests(unittest.TestCase):
    def payload(self):
        return dict(symbol="TEST", price="2.50", volume=150000,
                    average_volume=100000, observed_at=NOW.isoformat(), source="fixture")

    def test_normalizes_without_authorizing_execution(self):
        result = normalize_market_snapshot(self.payload(), now=NOW)
        self.assertEqual(result["relative_volume"], "1.500")
        self.assertTrue(result["research_only"])
        self.assertFalse(result["market_data_verified"])
        self.assertFalse(result["order_execution_authorized"])

    def test_rejects_bad_inputs(self):
        changes = [
            {"symbol": "AAPL/../../"}, {"symbol": "é"},
            {"price": "NaN"}, {"price": 0}, {"price": True},
            {"volume": -1}, {"volume": 1.5}, {"average_volume": 0},
            {"source": "https://untrusted"}, {"observed_at": "2026-10-23T14:00:00"},
            {"observed_at": (NOW-timedelta(seconds=121)).isoformat()},
            {"observed_at": (NOW+timedelta(seconds=1)).isoformat()},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                normalize_market_snapshot({**self.payload(), **change}, now=NOW)

    def test_accepts_boundary_age(self):
        data = {**self.payload(), "observed_at": (NOW-timedelta(seconds=120)).isoformat()}
        self.assertEqual(normalize_market_snapshot(data, now=NOW)["symbol"], "TEST")
