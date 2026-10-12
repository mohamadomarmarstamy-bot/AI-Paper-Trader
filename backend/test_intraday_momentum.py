import unittest
from datetime import datetime, timedelta, timezone

from intraday_momentum import calculate_intraday_momentum


def make_bars(prices):
    start = datetime(2026, 10, 12, 14, 30, tzinfo=timezone.utc)
    return [
        {
            "t": (start + timedelta(minutes=i)).isoformat(),
            "c": price,
        }
        for i, price in enumerate(prices)
    ]


class TestIntradayMomentum(unittest.TestCase):

    def test_rising_prices(self):
        bars = make_bars([10 + i * 0.1 for i in range(16)])
        result = calculate_intraday_momentum(bars)

        self.assertTrue(result["available"])
        self.assertGreater(result["change_1m_percent"], 0)
        self.assertGreater(result["change_5m_percent"], 0)
        self.assertGreater(result["change_15m_percent"], 0)
        self.assertTrue(result["research_only"])

    def test_falling_prices(self):
        bars = make_bars([10 - i * 0.1 for i in range(16)])
        result = calculate_intraday_momentum(bars)

        self.assertTrue(result["available"])
        self.assertLess(result["change_1m_percent"], 0)
        self.assertLess(result["change_5m_percent"], 0)

    def test_insufficient_data(self):
        result = calculate_intraday_momentum(
            make_bars([10.0] * 5)
        )

        self.assertFalse(result["available"])
        self.assertEqual(
            result["reason"], "missing_timeframe_candles"
        )
        self.assertEqual(result["change_1m_percent"], 0.0)
        self.assertIsNone(result["change_5m_percent"])
        self.assertIsNone(result["change_15m_percent"])

    def test_invalid_data(self):
        bars = make_bars([10.0] * 16)
        bars[5]["c"] = None

        result = calculate_intraday_momentum(bars)

        self.assertFalse(result["available"])
        self.assertEqual(
            result["reason"], "invalid_minute_bar"
        )

    def test_missing_minute(self):
        bars = make_bars([10.0] * 16)
        bars[8]["t"] = (
            datetime.fromisoformat(bars[8]["t"])
            + timedelta(minutes=1)
        ).isoformat()

        result = calculate_intraday_momentum(bars)

        self.assertFalse(result["available"])
        self.assertEqual(
            result["reason"], "nonconsecutive_minute_bars"
        )

    def test_out_of_order_bars(self):
        bars = make_bars([10.0] * 16)
        bars[7], bars[8] = bars[8], bars[7]

        result = calculate_intraday_momentum(bars)

        self.assertFalse(result["available"])
        self.assertEqual(
            result["reason"], "nonconsecutive_minute_bars"
        )


    def test_missing_unrelated_minute(self):
        bars = make_bars([10 + i * 0.1 for i in range(16)])
        del bars[7]

        result = calculate_intraday_momentum(bars)

        self.assertTrue(result["available"])
        self.assertIsNotNone(result["change_1m_percent"])
        self.assertIsNotNone(result["change_5m_percent"])
        self.assertIsNotNone(result["change_15m_percent"])

    def test_missing_one_minute_reference(self):
        bars = make_bars([10 + i * 0.1 for i in range(16)])
        del bars[-2]

        result = calculate_intraday_momentum(bars)

        self.assertFalse(result["available"])
        self.assertIsNone(result["change_1m_percent"])
        self.assertIsNotNone(result["change_5m_percent"])
        self.assertIsNotNone(result["change_15m_percent"])

    def test_missing_five_minute_reference(self):
        bars = make_bars([10 + i * 0.1 for i in range(16)])
        del bars[-6]

        result = calculate_intraday_momentum(bars)

        self.assertFalse(result["available"])
        self.assertIsNotNone(result["change_1m_percent"])
        self.assertIsNone(result["change_5m_percent"])
        self.assertIsNotNone(result["change_15m_percent"])

    def test_missing_fifteen_minute_reference(self):
        bars = make_bars([10 + i * 0.1 for i in range(16)])
        del bars[0]

        result = calculate_intraday_momentum(bars)

        self.assertFalse(result["available"])
        self.assertIsNotNone(result["change_1m_percent"])
        self.assertIsNotNone(result["change_5m_percent"])
        self.assertIsNone(result["change_15m_percent"])

    def test_duplicate_timestamp_rejected(self):
        bars = make_bars([10.0] * 16)
        bars[8]["t"] = bars[7]["t"]

        result = calculate_intraday_momentum(bars)

        self.assertFalse(result["available"])
        self.assertEqual(
            result["reason"], "nonconsecutive_minute_bars"
        )


class TestIntradayFreshness(unittest.TestCase):

    def test_stale_bars_rejected(self):
        from intraday_momentum import fetch_intraday_momentum

        bars = make_bars([10.0] * 16)

        def fake_request(method, path, *, params):
            return {"bars": bars}

        result = fetch_intraday_momentum(
            "TEST",
            fake_request,
            now=datetime(
                2026, 10, 12, 15, 30,
                tzinfo=timezone.utc,
            ),
        )

        self.assertFalse(result["available"])
        self.assertEqual(
            result["reason"],
            "stale_or_incomplete_minute_bars",
        )

    def test_stale_partial_bars_rejected(self):
        from intraday_momentum import fetch_intraday_momentum

        # Five consecutive candles cannot provide all 15-minute
        # measurements, and these candles are also outdated.
        bars = make_bars([10.0] * 5)

        def fake_request(method, path, *, params):
            return {"bars": bars}

        result = fetch_intraday_momentum(
            "TEST",
            fake_request,
            now=datetime(
                2026, 10, 12, 15, 30,
                tzinfo=timezone.utc,
            ),
        )

        self.assertFalse(result["available"])
        self.assertEqual(
            result["reason"],
            "stale_or_incomplete_minute_bars",
        )
        self.assertTrue(result["research_only"])

    def test_incomplete_bar_rejected(self):
        from intraday_momentum import fetch_intraday_momentum

        bars = make_bars([10.0] * 16)

        # Shift all candles forward so the final one
        # starts during the current, incomplete minute.
        for bar in bars:
            timestamp = datetime.fromisoformat(bar["t"])
            bar["t"] = (
                timestamp + timedelta(minutes=1)
            ).isoformat()

        def fake_request(method, path, *, params):
            return {"bars": bars}

        result = fetch_intraday_momentum(
            "TEST",
            fake_request,
            now=datetime(
                2026, 10, 12, 14, 46, 30,
                tzinfo=timezone.utc,
            ),
        )

        self.assertFalse(result["available"])
        self.assertEqual(
            result["reason"],
            "stale_or_incomplete_minute_bars",
        )


if __name__ == "__main__":
    unittest.main()

class TestAlpacaIntradayFetcher(unittest.TestCase):

    def test_fetch_completed_minute_bars(self):
        from intraday_momentum import fetch_intraday_momentum

        bars = make_bars([
            10.0 + i * 0.1 for i in range(16)
        ])

        requests = []

        def fake_alpaca_request(method, path, *, params):
            requests.append({
                "method": method,
                "path": path,
                "params": params,
            })
            return {"bars": bars}

        now = datetime(
            2026, 10, 12, 14, 46, 30,
            tzinfo=timezone.utc,
        )

        result = fetch_intraday_momentum(
            "TEST",
            fake_alpaca_request,
            now=now,
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["symbol"], "TEST")
        self.assertEqual(
            result["data_source"], "alpaca_iex"
        )
        self.assertTrue(result["research_only"])

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["method"], "GET")
        self.assertEqual(
            requests[0]["path"],
            "/v2/stocks/TEST/bars",
        )
        self.assertEqual(
            requests[0]["params"]["timeframe"],
            "1Min",
        )
        self.assertEqual(
            requests[0]["params"]["feed"],
            "iex",
        )

    def test_alpaca_failure(self):
        from intraday_momentum import fetch_intraday_momentum

        def failing_request(method, path, *, params):
            raise RuntimeError("Temporary API failure")

        result = fetch_intraday_momentum(
            "TEST",
            failing_request,
        )

        self.assertFalse(result["available"])
        self.assertEqual(
            result["reason"],
            "market_data_request_failed",
        )


if __name__ == "__main__":
    unittest.main()
