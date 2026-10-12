"""Offline tests for the automated momentum research endpoint."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


def load_endpoint(*, authenticated=True):
    path = Path(__file__).parent / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "auto_trader_automated_momentum_research"
    ]

    if len(nodes) != 1:
        raise AssertionError("Expected exactly one research endpoint")

    nodes[0].decorator_list = []

    def require_session(request):
        if not authenticated:
            raise PermissionError("Authentication required")

    namespace = {
        "require_app_session": require_session,
        "alpaca_market_data_request": Mock(),
        "Query": lambda default=None, **kwargs: default,
    }

    future = ast.parse(
        "from __future__ import annotations"
    ).body

    exec(
        compile(
            ast.Module(
                body=future + nodes,
                type_ignores=[],
            ),
            str(path),
            "exec",
        ),
        namespace,
    )

    return namespace["auto_trader_automated_momentum_research"]


class AutomatedMomentumEndpointTests(unittest.TestCase):

    def test_authentication_required(self):
        endpoint = load_endpoint(authenticated=False)

        with self.assertRaises(PermissionError):
            endpoint(request=object(), limit=10)

    def test_empty_cache_does_not_evaluate(self):
        endpoint = load_endpoint()

        with patch(
            "scanner.get_fresh_scanner_candidates",
            return_value=[],
        ), patch(
            "automated_momentum_research.evaluate_momentum_candidates"
        ) as evaluate:

            result = endpoint(request=object(), limit=10)

        self.assertFalse(result["available"])
        self.assertEqual(
            result["reason"],
            "no_fresh_scanner_candidates",
        )
        self.assertEqual(result["evaluated_count"], 0)
        evaluate.assert_not_called()

    def test_fresh_candidates_are_evaluated(self):
        endpoint = load_endpoint()

        candidates = [
            {"symbol": "AAPL", "scanner_rank": 1},
            {"symbol": "NVDA", "scanner_rank": 2},
        ]

        expected = {
            "research_only": True,
            "evaluated_count": 2,
            "results": [
                {"symbol": "AAPL"},
                {"symbol": "NVDA"},
            ],
        }

        with patch(
            "scanner.get_fresh_scanner_candidates",
            return_value=candidates,
        ), patch(
            "automated_momentum_research.evaluate_momentum_candidates",
            return_value=expected,
        ) as evaluate:

            result = endpoint(request=object(), limit=10)

        self.assertTrue(result["available"])
        self.assertTrue(result["research_only"])
        self.assertEqual(result["evaluated_count"], 2)
        self.assertEqual(
            result["candidate_source"],
            "fresh_scanner_cache",
        )

        evaluate.assert_called_once()
        self.assertIs(
            evaluate.call_args.args[0],
            candidates,
        )
        self.assertEqual(
            evaluate.call_args.kwargs["limit"],
            10,
        )


if __name__ == "__main__":
    unittest.main()
