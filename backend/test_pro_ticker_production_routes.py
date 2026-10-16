"""Static production route-registration contract checks.

Parses the production FastAPI decorators without importing main.py (which starts
database initialization and registers background tasks). These checks complement,
but do not replace, a full production-app startup smoke test.
"""
import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).with_name("main.py")
EXPECTED = {
    ("GET", "/auto-trader/pro-ticker-manual-submissions"):
        "list_auto_trader_pro_ticker_manual_submissions",
    ("POST", "/auto-trader/pro-ticker-manual-submissions"):
        "auto_trader_pro_ticker_manual_submissions",
    ("POST", "/auto-trader/pro-ticker-paper-preview"):
        "pro_ticker_paper_preview",
}


def production_routes():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    registered = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            method = decorator.func
            if not isinstance(method, ast.Attribute) or not isinstance(method.value, ast.Name):
                continue
            if method.value.id != "app" or method.attr not in {"get", "post"}:
                continue
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                continue
            registered.append((method.attr.upper(), decorator.args[0].value, node.name))
    return registered


class ProductionResearchRouteRegistrationTests(unittest.TestCase):
    def test_all_research_routes_registered_with_expected_http_methods(self):
        actual = {(method, path): name for method, path, name in production_routes()}
        for route, handler in EXPECTED.items():
            with self.subTest(route=route):
                self.assertEqual(actual.get(route), handler)

    def test_no_duplicate_production_research_route_registrations(self):
        matches = [(method, path, handler) for method, path, handler in production_routes()
                   if path in {path for _, path in EXPECTED}]
        self.assertEqual(len(matches), len(EXPECTED))
        self.assertEqual(len({(method, path) for method, path, _ in matches}), len(matches))

    def test_preview_is_post_only_in_production_route_registration(self):
        preview = [(method, name) for method, path, name in production_routes()
                   if path == "/auto-trader/pro-ticker-paper-preview"]
        self.assertEqual(preview, [("POST", "pro_ticker_paper_preview")])


    def test_production_routes_require_session_before_request_body_or_io(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
        handlers = set(EXPECTED.values())
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in handlers:
                continue
            with self.subTest(handler=node.name):
                self.assertTrue(node.body, "Handler must not be empty")
                first = node.body[1] if isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str) else node.body[0]
                self.assertIsInstance(first, ast.Expr)
                self.assertIsInstance(first.value, ast.Call)
                self.assertIsInstance(first.value.func, ast.Name)
                self.assertEqual(first.value.func.id, "require_app_session")

    def test_production_preview_contains_no_order_submission_calls(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
        handlers = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                    and node.name == "pro_ticker_paper_preview"]
        self.assertEqual(len(handlers), 1)
        calls = [node for node in ast.walk(handlers[0]) if isinstance(node, ast.Call)]
        forbidden = {
            "submit_alpaca_paper_market_order",
            "submit_alpaca_paper_order",
            "close_auto_trader_position",
            "close_all_auto_trader_positions",
        }
        invoked = {node.func.id for node in calls if isinstance(node.func, ast.Name)}
        self.assertFalse(invoked & forbidden, f"Order calls in preview: {invoked & forbidden}")
        broker_calls = [node for node in calls if isinstance(node.func, ast.Name)
                        and node.func.id == "alpaca_paper_request"]
        self.assertTrue(broker_calls)
        for node in broker_calls:
            with self.subTest(line=node.lineno):
                self.assertTrue(node.args)
                self.assertIsInstance(node.args[0], ast.Constant)
                self.assertEqual(node.args[0].value, "GET")


if __name__ == "__main__":
    unittest.main()
