"""Offline regressions: no app startup, network, orders, or database writes.

Run: python -m unittest discover -s backend -p test_entry_quality.py -v
"""
import ast
import copy
import logging
from pathlib import Path
import threading
import types
import unittest

from entry_quality import evaluate_entry_quality, tighten_score_minimum

ROOT = Path(__file__).parent


def good_candidate(**changes):
    return dict({
        "symbol": "TEST", "signal": "BUY", "score": 80,
        "confidence": 85, "scanner_rank": 3, "atr_percent": 3,
        "scanner_stale": False, "momentum_30_candidate": False,
    }, **changes)


def evaluate(candidate, **settings):
    return evaluate_entry_quality(candidate, **dict({
        "score_min": 75, "confidence_min": 70,
        "max_scanner_rank": 20, "max_atr_percent": 8,
    }, **settings))


class EntryQualityTests(unittest.TestCase):
    def test_valid_normal_and_momentum(self):
        for momentum in (False, True):
            self.assertTrue(evaluate(good_candidate(momentum_30_candidate=momentum))["passed"])

    def test_momentum_cannot_bypass_each_requirement(self):
        for change, reason in [
            ({"signal": "SELL"}, "signal_not_buy"),
            ({"score": 74}, "score_below_adjusted_minimum"),
            ({"confidence": 69}, "confidence_below_minimum"),
            ({"scanner_rank": 21}, "scanner_rank_above_maximum"),
            ({"atr_percent": 9}, "atr_above_maximum"),
            ({"scanner_stale": True}, "scanner_stale_or_freshness_unknown"),
        ]:
            with self.subTest(change=change):
                result = evaluate(good_candidate(momentum_30_candidate=True, **change))
                self.assertFalse(result["passed"])
                self.assertIn(reason, result["failed_requirements"])

    def test_invalid_numbers_fail_closed(self):
        for field in ("score", "confidence", "scanner_rank", "atr_percent"):
            for value in (None, "bad", float("nan"), float("inf"), True, -1):
                with self.subTest(field=field, value=value):
                    self.assertFalse(evaluate(good_candidate(**{field: value}))["passed"])

    def test_missing_rank_or_freshness_is_not_permission_to_buy(self):
        for field in ("scanner_rank", "scanner_stale"):
            candidate = good_candidate()
            del candidate[field]
            self.assertFalse(evaluate(candidate)["passed"])

    def test_rank_alias_does_not_hide_invalid_primary_rank(self):
        self.assertTrue(evaluate(good_candidate(scanner_rank=None, rank=3))["passed"])
        for rank in (0, 1.5, float("nan")):
            self.assertFalse(evaluate(good_candidate(scanner_rank=rank, rank=3))["passed"])

    def test_boundaries_and_numeric_strings(self):
        self.assertTrue(evaluate(good_candidate(
            score="75", confidence="70", scanner_rank="20", atr_percent="8",
        ))["passed"])
        for field in ("score", "confidence"):
            self.assertFalse(evaluate(good_candidate(**{field: 101}))["passed"])
        self.assertFalse(evaluate(good_candidate(atr_percent=0))["passed"])

    def test_tightening_never_relaxes_existing_minimum(self):
        for current in (70, 75, 88, 90, 95, 100):
            for increase in (-5, 0, 2, 5):
                self.assertGreaterEqual(tighten_score_minimum(current, increase), current)
        self.assertEqual(tighten_score_minimum(88, 5), 90)
        self.assertEqual(tighten_score_minimum(95, 5), 95)


class ActualEntryFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Execute the actual admission section without importing main, whose
        # module-level initialization is unrelated to these offline tests.
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        cycle = source.index("def run_auto_trader_cycle()")
        start = source.index("            learning_score_min = (", cycle)
        end = source.index("            if symbol in existing_symbols:", start)
        section = source[start:end]
        wrapper = '''def admission(candidate, sentiment="NEUTRAL", regime="NEUTRAL", base=75, learning=None):
    calls = []
    def get_news(symbol, **kwargs):
        calls.append(symbol)
        return {"sentiment": sentiment}
    global get_symbol_news_context
    get_symbol_news_context = get_news
    global AUTO_TRADER_ENTRY_SCORE_MIN
    AUTO_TRADER_ENTRY_SCORE_MIN = base
    learning_summary = learning or {}
    market_regime = {"regime": regime}
    cycle_result = {"skipped_candidates": []}
    symbol = candidate["symbol"]
    signal = candidate["signal"]
    score = candidate.get("score")
    confidence = candidate.get("confidence")
    if True:
        for candidate in [candidate]:
'''
        namespace = {
            "evaluate_entry_quality": evaluate_entry_quality,
            "tighten_score_minimum": tighten_score_minimum,
            "safe_float": lambda value: float(value) if value is not None else None,
            "score_symbol_news_context": lambda context: context,
            "AUTO_TRADER_ENTRY_CONFIDENCE_MIN": 70,
            "AUTO_TRADER_MAX_ENTRY_SCANNER_RANK": 20,
            "AUTO_TRADER_MAX_ENTRY_ATR_PERCENT": 8,
            "AUTO_TRADER_STRATEGY_VERSION": "rank20_v1",
        }
        exec(compile(wrapper + section + '\n    return cycle_result, entry_quality, calls\n', str(ROOT / "main.py"), "exec"), namespace)
        cls.admission = staticmethod(namespace["admission"])

    def test_news_rechecks_momentum_candidate(self):
        skipped, result, calls = self.admission(good_candidate(score=78, momentum_30_candidate=True), sentiment="NEGATIVE")
        self.assertEqual(calls, ["TEST"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["required_score"], 80)
        self.assertIn("score_below_adjusted_minimum", skipped["skipped_candidates"][0]["failed_requirements"])

    def test_bad_candidate_never_fetches_news(self):
        skipped, result, calls = self.admission(good_candidate(score=60, momentum_30_candidate=True))
        self.assertFalse(result["passed"])
        self.assertEqual(calls, [])
        self.assertEqual(len(skipped["skipped_candidates"]), 1)

    def test_learning_regime_and_news_accumulate(self):
        _, result, calls = self.admission(good_candidate(score=84), regime="BEARISH", sentiment="NEGATIVE", learning={
            "enough_data": True, "win_rate_percent": 30, "average_return_percent": -2,
        })
        self.assertFalse(result["passed"])
        self.assertEqual(result["required_score"], 85)
        self.assertEqual(calls, [])
        _, result, calls = self.admission(good_candidate(score=87), regime="BEARISH", sentiment="NEGATIVE", learning={
            "enough_data": True, "win_rate_percent": 30, "average_return_percent": -2,
        })
        self.assertEqual(calls, ["TEST"])
        self.assertEqual(result["required_score"], 90)
        self.assertFalse(result["passed"])

    def test_valid_candidate_survives_and_strict_threshold_is_preserved(self):
        skipped, result, _ = self.admission(good_candidate())
        self.assertTrue(result["passed"])
        self.assertEqual(skipped["skipped_candidates"], [])
        _, result, _ = self.admission(good_candidate(score=94), base=95, regime="BEARISH")
        self.assertEqual(result["required_score"], 95)
        self.assertFalse(result["passed"])


class ScannerCacheTests(unittest.TestCase):
    def setUp(self):
        source = ast.parse((ROOT / "scanner.py").read_text(encoding="utf-8"))
        names = {"_copy_results", "_get_cached_results", "_set_cached_results", "scan_market"}
        functions = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
        future = ast.parse("from __future__ import annotations").body
        self.clock = types.SimpleNamespace(now=1000.0)
        self.ns = {
            "copy": copy, "time": types.SimpleNamespace(time=lambda: self.clock.now),
            "_cache_lock": threading.RLock(), "_scan_lock": threading.Lock(),
            "_scan_cache": {"results": [], "updated_at": 0},
            "SCAN_CACHE_SECONDS": 60, "logger": logging.getLogger("entry_quality_tests"),
        }
        exec(compile(ast.Module(body=future + functions, type_ignores=[]), "scanner.py", "exec"), self.ns)

    def test_cache_expiry_and_copies(self):
        candidate = good_candidate()
        self.ns["_set_cached_results"]([candidate])
        fresh = self.ns["_get_cached_results"](allow_stale=False)
        self.assertFalse(fresh[0]["scanner_stale"])
        fresh[0]["score"] = 0
        self.assertEqual(self.ns["_get_cached_results"](allow_stale=False)[0]["score"], 80)
        self.clock.now += 60
        self.assertIsNone(self.ns["_get_cached_results"](allow_stale=False))
        stale = self.ns["_get_cached_results"](allow_stale=True)
        self.assertTrue(stale[0]["scanner_stale"])
        self.assertFalse(evaluate(stale[0])["passed"])

    def test_failed_forced_refresh_marks_even_recent_cache_stale(self):
        self.ns["_set_cached_results"]([good_candidate()])
        def fail():
            raise RuntimeError("simulated universe outage")
        self.ns["load_market_universe"] = fail
        with self.assertLogs("entry_quality_tests", level="WARNING"):
            results = self.ns["scan_market"](force_refresh=True)
        self.assertTrue(results[0]["scanner_stale"])
        self.assertFalse(evaluate(results[0])["passed"])

    def test_other_refresh_failures_are_also_stale(self):
        for failure in ("empty_universe", "download_failed", "no_candidates"):
            with self.subTest(failure=failure):
                self.ns["_set_cached_results"]([good_candidate()])
                self.ns.update({
                    "load_market_universe": lambda: [] if failure == "empty_universe" else ["TEST"],
                    "clean_symbol": str,
                    "split_into_batches": lambda symbols, size: [symbols],
                    "DOWNLOAD_BATCH_SIZE": 25,
                    "download_batch": lambda batch: None if failure == "download_failed" else object(),
                    "extract_symbol_history": lambda *args: None,
                })
                with self.assertLogs("entry_quality_tests", level="WARNING"):
                    results = self.ns["scan_market"](force_refresh=True)
                self.assertTrue(results[0]["scanner_stale"])
                self.assertFalse(evaluate(results[0])["passed"])


if __name__ == "__main__":
    unittest.main()
