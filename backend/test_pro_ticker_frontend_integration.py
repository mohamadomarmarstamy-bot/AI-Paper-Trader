"""Static integration checks for research-only Pro Ticker frontend wiring."""
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent / "frontend"


class ProTickerFrontendIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (FRONTEND / "index.html").read_text(encoding="utf-8-sig")
        cls.js = (FRONTEND / "js" / "pro-ticker-manual.js").read_text(encoding="utf-8-sig")

    def test_navigation_and_section_are_connected(self):
        self.assertIn('data-section="pro-ticker-manual-section"', self.html)
        self.assertIn('id="pro-ticker-manual-section"', self.html)
        self.assertIn('window.loadProTickerManualLab?.()', self.html)
        self.assertIn("window.loadProTickerManualLab = refresh", self.js)

    def test_form_ids_match_frontend_script(self):
        for element_id in ("pro-ticker-manual-form", "pro-ticker-manual-symbols",
                           "pro-ticker-manual-status", "pro-ticker-manual-history"):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', self.html)
                self.assertIn(f'getElementById("{element_id}")', self.js)

    def test_research_script_is_loaded_and_uses_expected_endpoint(self):
        self.assertIn('src="js/pro-ticker-manual.js"', self.html)
        self.assertIn('"/auto-trader/pro-ticker-manual-submissions"', self.js)
        self.assertIn('method: "POST"', self.js)
        self.assertIn('credentials: "same-origin"', self.js)

    def test_authenticated_backend_research_routes_are_wired(self):
        backend = (FRONTEND.parent / "main.py").read_text(encoding="utf-8-sig")
        for decorator in (
            '@app.get("/auto-trader/pro-ticker-manual-submissions")',
            '@app.post("/auto-trader/pro-ticker-manual-submissions")',
            '@app.post("/auto-trader/pro-ticker-paper-preview")',
        ):
            with self.subTest(decorator=decorator):
                self.assertIn(decorator, backend)
        start = backend.index('@app.get("/auto-trader/pro-ticker-manual-submissions")')
        end = backend.index('@app.get("/auto-trader/pro-ticker-paper-budget")', start)
        self.assertIn("require_app_session(request)", backend[start:end])
        start = backend.index('@app.post("/auto-trader/pro-ticker-manual-submissions")')
        end = backend.index('@app.get(', start)
        self.assertIn("require_app_session(request)", backend[start:end])
        self.assertIn("save_manual_observations", backend[start:end])

    def test_paper_preview_route_is_authenticated_and_non_executing(self):
        backend = (FRONTEND.parent / "main.py").read_text(encoding="utf-8-sig")
        start = backend.index('@app.post("/auto-trader/pro-ticker-paper-preview")')
        end = backend.index('@app.post("/auto-trader/pro-ticker-manual-submissions")', start)
        route = backend[start:end]
        self.assertIn("require_app_session(request)", route)
        self.assertIn('"allowed": False', route)
        self.assertIn('"order_submitted": False', route)
        self.assertIn('"virtual_budget_reconciliation_ready": False', route)
        self.assertIn("execution_disabled_pending_reconciliation", route)
        self.assertNotIn('alpaca_paper_request("POST"', route)
        self.assertNotIn('alpaca_paper_request("DELETE"', route)

    def test_research_history_fails_closed_on_malformed_response(self):
        self.assertIn("Array.isArray(data.observations)", self.js)
        self.assertIn('throw new Error("Unexpected research response")', self.js)
        self.assertIn("rows.replaceChildren()", self.js)
        self.assertIn('setStatus("Unable to load submissions: "', self.js)

    def test_failed_refresh_clears_stale_research_rows(self):
        catch_start = self.js.index('setStatus("Unable to load submissions: "')
        self.assertIn("rows.replaceChildren()", self.js[max(0, catch_start - 100):catch_start])
        self.assertLess(
            self.js.index("if (!Array.isArray(data.observations))"),
            self.js.index("rows.replaceChildren()"),
        )

    def test_frontend_exposes_no_order_submission_controls(self):
        self.assertIn("Research only", self.html)
        self.assertIn("no PAPER orders placed", self.js)
        self.assertNotIn("/v2/orders", self.js)
        self.assertNotIn("/auto-trader/positions/close-all", self.js)


if __name__ == "__main__":
    unittest.main()
