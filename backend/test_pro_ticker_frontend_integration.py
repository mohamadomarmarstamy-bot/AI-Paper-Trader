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
            self.js.index("if (!Array.isArray(data.observations) ||"),
            self.js.index("rows.replaceChildren()"),
        )

    def test_successful_save_reports_history_refresh_failure(self):
        self.assertIn("const historyLoaded = await refresh()", self.js)
        self.assertIn("if (historyLoaded)", self.js)
        self.assertIn("but history could not be refreshed", self.js)
        self.assertIn("return true;", self.js)
        self.assertIn("return false;", self.js)

    def test_save_requires_valid_count_before_success_message(self):
        self.assertIn("Number.isSafeInteger(result.count)", self.js)
        self.assertIn("result.count < 0", self.js)
        self.assertIn('throw new Error("Unexpected save response")', self.js)
        self.assertLess(
            self.js.index("Number.isSafeInteger(result.count)"),
            self.js.index('setStatus("Saved " + result.count'),
        )

    def test_form_rejects_overlapping_submissions(self):
        self.assertIn("let submitting = false;", self.js)
        self.assertIn("if (submitting) return;", self.js)
        self.assertIn("submitting = true;", self.js)
        self.assertIn("submitting = false;", self.js)
        self.assertIn("if (button) button.disabled = true;", self.js)
        self.assertIn("if (button) button.disabled = false;", self.js)

    def test_out_of_order_refresh_responses_are_ignored(self):
        self.assertIn("let refreshVersion = 0;", self.js)
        self.assertIn("const version = ++refreshVersion;", self.js)
        self.assertEqual(self.js.count("if (version !== refreshVersion) return false;"), 2)
        self.assertLess(
            self.js.index("if (version !== refreshVersion) return false;"),
            self.js.index("rows.replaceChildren()"),
        )

    def test_submitting_invalidates_inflight_history_refresh(self):
        submit = self.js.index('form.addEventListener("submit"')
        invalidate = self.js.index("refreshVersion += 1;", submit)
        save_request = self.js.index("const result = await request(endpoint,", submit)
        self.assertLess(invalidate, save_request)
        self.assertIn("if (version !== refreshVersion) return false;", self.js)

    def test_malformed_observations_are_rejected_before_rendering(self):
        self.assertIn("data.observations.some(record =>", self.js)
        self.assertIn('typeof record.symbol !== "string"', self.js)
        self.assertIn('throw new Error("Invalid research observation")', self.js)
        self.assertLess(
            self.js.index("data.observations.some(record =>"),
            self.js.index("rows.replaceChildren()"),
        )

    def test_research_history_count_must_be_nonnegative_integer(self):
        self.assertIn("!Number.isSafeInteger(data.count)", self.js)
        self.assertIn("data.count < 0", self.js)
        self.assertNotIn("data.count !== data.observations.length", self.js)

    def test_malformed_market_snapshot_is_rejected_before_rendering(self):
        self.assertIn("record.market_snapshot != null", self.js)
        self.assertIn('typeof record.market_snapshot !== "object"', self.js)
        self.assertIn("Array.isArray(record.market_snapshot)", self.js)
        self.assertLess(
            self.js.index("record.market_snapshot != null"),
            self.js.index("rows.replaceChildren()"),
        )

    def test_history_status_distinguishes_empty_and_populated_results(self):
        self.assertIn("if (!data.observations.length)", self.js)
        self.assertIn('data.count === 0', self.js)
        self.assertIn("No research observations returned for this page.", self.js)
        self.assertIn("Loaded ", self.js)
        self.assertIn("research observation(s).", self.js)

    def test_array_shaped_observation_records_are_rejected(self):
        self.assertIn("Array.isArray(record)", self.js)
        self.assertLess(
            self.js.index("Array.isArray(record)"),
            self.js.index("rows.replaceChildren()"),
        )

    def test_frontend_exposes_no_order_submission_controls(self):
        self.assertIn("Research only", self.html)
        self.assertIn("no PAPER orders placed", self.js)
        self.assertNotIn("/v2/orders", self.js)
        self.assertNotIn("/auto-trader/positions/close-all", self.js)


if __name__ == "__main__":
    unittest.main()
