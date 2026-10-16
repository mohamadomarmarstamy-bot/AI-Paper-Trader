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


if __name__ == "__main__":
    unittest.main()
