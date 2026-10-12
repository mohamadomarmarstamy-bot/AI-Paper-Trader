"""Tests for read-only market-session classification."""

import unittest
from datetime import datetime, timezone

from market_session_research import classify_market_session


class TestMarketSessionResearch(unittest.TestCase):

    def setUp(self):
        self.calendar = {
            "date": "2026-10-12",
            "open": "09:30",
            "close": "16:00",
        }

    def classify(self, hour, minute=0, calendar=None):
        observed_at = datetime(
            2026, 10, 12, hour, minute,
            tzinfo=timezone.utc,
        )
        return classify_market_session(
            observed_at,
            self.calendar if calendar is None else calendar,
        )

    def test_regular_session(self):
        result = self.classify(14)
        self.assertEqual(
            result["market_session"],
            "regular_session",
        )
        self.assertTrue(result["regular_market_open"])

    def test_pre_market(self):
        result = self.classify(12)
        self.assertEqual(
            result["market_session"],
            "pre_market",
        )

    def test_after_hours(self):
        result = self.classify(21)
        self.assertEqual(
            result["market_session"],
            "after_hours",
        )

    def test_closed_overnight(self):
        # 07:00 UTC = 03:00 Eastern on October 12.
        result = self.classify(7)
        self.assertEqual(
            result["market_session"],
            "closed",
        )

    def test_holiday(self):
        result = classify_market_session(
            datetime(
                2026, 12, 25, 15,
                tzinfo=timezone.utc,
            ),
            None,
        )
        self.assertEqual(
            result["market_session"],
            "closed",
        )

    def test_weekend(self):
        result = classify_market_session(
            datetime(
                2026, 10, 17, 15,
                tzinfo=timezone.utc,
            ),
            None,
        )
        self.assertEqual(
            result["market_session"],
            "closed",
        )

    def test_early_close(self):
        early_calendar = {
            "date": "2026-10-12",
            "open": "09:30",
            "close": "13:00",
        }

        result = self.classify(
            18,
            calendar=early_calendar,
        )

        self.assertEqual(
            result["market_session"],
            "after_hours",
        )
        self.assertFalse(result["regular_market_open"])

    def test_calendar_unavailable(self):
        result = classify_market_session(
            datetime(
                2026, 10, 12, 14,
                tzinfo=timezone.utc,
            ),
            None,
            calendar_verified=False,
        )

        self.assertEqual(
            result["market_session"],
            "unknown",
        )
        self.assertFalse(result["market_calendar_verified"])

    def test_daylight_saving_time(self):
        calendar = {
            "date": "2026-12-14",
            "open": "09:30",
            "close": "16:00",
        }

        result = classify_market_session(
            datetime(
                2026, 12, 14, 15,
                tzinfo=timezone.utc,
            ),
            calendar,
        )

        self.assertEqual(
            result["market_session"],
            "regular_session",
        )


if __name__ == "__main__":
    unittest.main()
