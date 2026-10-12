import unittest
from unittest.mock import patch

from automated_momentum_research import evaluate_momentum_candidates


class TestAutomatedMomentumResearch(unittest.TestCase):

    def setUp(self):
        self.candidates = [
            {"symbol": "AMD", "scanner_rank": 1, "signal": "BUY", "score": 90},
            {"symbol": "PLTR", "scanner_rank": 2, "signal": "BUY", "score": 85},
            {"symbol": "AMD", "scanner_rank": 3, "signal": "HOLD", "score": 70},
        ]

    @patch("automated_momentum_research.fetch_intraday_momentum")
    def test_evaluates_unique_stocks(self, mock_fetch):
        mock_fetch.return_value = {
            "available": True,
            "change_5m_percent": 1.5,
        }

        result = evaluate_momentum_candidates(
            self.candidates,
            lambda *args, **kwargs: {},
        )

        self.assertEqual(result["evaluated_count"], 2)
        self.assertEqual(
            [item["symbol"] for item in result["results"]],
            ["AMD", "PLTR"],
        )
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("automated_momentum_research.fetch_intraday_momentum")
    def test_respects_limit(self, mock_fetch):
        mock_fetch.return_value = {"available": True}

        result = evaluate_momentum_candidates(
            self.candidates,
            lambda *args, **kwargs: {},
            limit=1,
        )

        self.assertEqual(result["evaluated_count"], 1)
        self.assertEqual(mock_fetch.call_count, 1)

    @patch("automated_momentum_research.fetch_intraday_momentum")
    def test_handles_unavailable_data(self, mock_fetch):
        mock_fetch.return_value = {
            "available": False,
            "reason": "missing_timeframe_candles",
        }

        result = evaluate_momentum_candidates(
            self.candidates,
            lambda *args, **kwargs: {},
        )

        self.assertEqual(result["evaluated_count"], 2)
        self.assertFalse(
            result["results"][0]["momentum"]["available"]
        )

    @patch("automated_momentum_research.fetch_intraday_momentum")
    def test_continues_after_exception(self, mock_fetch):
        mock_fetch.side_effect = [
            RuntimeError("Temporary failure"),
            {"available": True},
        ]

        result = evaluate_momentum_candidates(
            self.candidates,
            lambda *args, **kwargs: {},
        )

        self.assertEqual(result["evaluated_count"], 2)
        self.assertEqual(
            result["results"][0]["momentum"]["reason"],
            "momentum_evaluation_failed",
        )
        self.assertTrue(
            result["results"][1]["momentum"]["available"]
        )

    def test_rejects_invalid_limit(self):
        for limit in (0, 31, -1, True, 1.5):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    evaluate_momentum_candidates(
                        self.candidates,
                        lambda *args, **kwargs: {},
                        limit=limit,
                    )


if __name__ == "__main__":
    unittest.main()
