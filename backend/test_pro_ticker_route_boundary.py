"""Execute actual research route functions in isolation, without importing the live app.

The source functions are compiled from main.py; broker and database dependencies
are replaced with fail-fast doubles. These are route-function tests, not HTTP tests.
"""
import ast
import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).with_name("main.py")


class Denied(Exception):
    pass


class FakeRequest:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    async def json(self):
        if self.error:
            raise self.error
        return self.payload


def isolated_routes():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    names = {
        "auto_trader_pro_ticker_manual_submissions",
        "list_auto_trader_pro_ticker_manual_submissions",
        "pro_ticker_paper_preview",
    }
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    for node in nodes:
        node.decorator_list = []
        node.returns = None
        node.args.defaults = []
        for arg in node.args.args:
            arg.annotation = None
    namespace = {
        "Request": object,
        "Any": object,
        "require_app_session": lambda request: (_ for _ in ()).throw(Denied()),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(SOURCE), "exec"), namespace)
    return namespace


class ProTickerRouteBoundaryTests(unittest.TestCase):
    def test_all_three_routes_deny_unauthenticated_requests_before_work(self):
        routes = isolated_routes()
        with self.assertRaises(Denied):
            routes["list_auto_trader_pro_ticker_manual_submissions"](FakeRequest(), limit=100)
        with self.assertRaises(Denied):
            asyncio.run(routes["auto_trader_pro_ticker_manual_submissions"](FakeRequest({"symbols": "AAPL"})))
        with self.assertRaises(Denied):
            asyncio.run(routes["pro_ticker_paper_preview"](FakeRequest({"symbol": "AAPL", "proposed_notional": 10})))

    def test_unauthenticated_bad_json_is_denied_before_parsing(self):
        routes = isolated_routes()
        with self.assertRaises(Denied):
            asyncio.run(routes["auto_trader_pro_ticker_manual_submissions"](FakeRequest(error=RuntimeError("body should not be parsed"))))
        with self.assertRaises(Denied):
            asyncio.run(routes["pro_ticker_paper_preview"](FakeRequest(error=RuntimeError("body should not be parsed"))))


if __name__ == "__main__":
    unittest.main()
