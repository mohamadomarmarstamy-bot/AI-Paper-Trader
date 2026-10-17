"use strict";
// Real Chromium rendering and interactions against an isolated synthetic HTTP
// server. This never imports the trading app or contacts any broker.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const { chromium } = require(process.env.PAPER_PLAYWRIGHT_MODULE || "playwright");

const frontend = path.resolve(__dirname, "..");
const reservation = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const exit = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const digest = "a".repeat(64);

test("desktop and mobile require manual verification and render release history", {timeout: 60000}, async () => {
    const html = fs.readFileSync(path.join(frontend, "index.html"), "utf8");
    const start = html.indexOf('<section id="pro-ticker-manual-section"');
    assert.ok(start >= 0);
    const section = html.slice(start, html.indexOf("</section>", start) + 10)
        .replace('class="app-section hidden"', 'class="app-section"').replace(" hidden>", ">");
    const css = fs.readFileSync(path.join(frontend, "style.css"), "utf8");
    let enabled = false, released = false, writes = [], errors = [], origin;
    const server = http.createServer(async (req, res) => {
        try {
            const url = new URL(req.url, origin);
            if (url.pathname === "/") {
                res.setHeader("Content-Type", "text/html");
                res.end('<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>' + css + '</style></head><body><main style="padding:20px;max-width:1050px;margin:auto">' + section +
                    '</main><script src="/release.js"></script><script>window.loadProTickerReleasePanel();</script></body></html>');
                return;
            }
            if (url.pathname === "/release.js") {
                res.setHeader("Content-Type", "application/javascript");
                res.end(fs.readFileSync(path.join(__dirname, "pro-ticker-release.js")));
                return;
            }
            if (url.pathname === "/favicon.ico") { res.statusCode = 204; res.end(); return; }
            res.setHeader("Content-Type", "application/json");
            if (url.pathname.endsWith("/release-status")) {
                res.end(JSON.stringify({paper: true, manual_release_enabled: enabled, automatic_release_enabled: false})); return;
            }
            if (url.pathname.endsWith("/release-events")) {
                res.end(JSON.stringify({paper: true, total_count: released ? 1 : 0, counts_as_realized_profit: false,
                    events: released ? [{reservation_id: reservation, symbol: "AAPL", released_cents: 10000, released_at: "2026-10-16T23:00:00Z"}] : []})); return;
            }
            assert.equal(req.method, "POST");
            assert.equal(req.headers.origin, origin);
            assert.equal(req.headers["x-pro-ticker-operator"], "manual-release");
            assert.ok(req.headers.cookie.includes("synthetic-owner-session"));
            const chunks = [];
            for await (const chunk of req) chunks.push(chunk);
            const body = JSON.parse(Buffer.concat(chunks).toString());
            writes.push(url.pathname);
            assert.equal(enabled, true);
            if (url.pathname.endsWith("/release-review")) {
                assert.deepEqual(body, {reservation_id: reservation});
                res.end(JSON.stringify({paper: true, budget_released: false, reservation_id: reservation,
                    review_id: reservation, evidence_digest: digest, expires_at: Date.now() / 1000 + 300,
                    symbol: "AAPL", reserved_cents: 10000, filled_entry_shares: 5, filled_exit_shares: 5,
                    entry_order_id: reservation, exit_order_ids: [exit]})); return;
            }
            assert.ok(url.pathname.endsWith("/release-confirm"));
            assert.deepEqual(body, {review_id: reservation, evidence_digest: digest, paper_provenance_confirmed: true});
            released = true;
            res.end(JSON.stringify({paper: true, budget_released: true, automatic_release_enabled: false}));
        } catch (error) {
            errors.push(error.message);
            res.statusCode = 500;
            res.end(JSON.stringify({detail: "Synthetic test failed"}));
        }
    });
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    origin = "http://127.0.0.1:" + server.address().port;
    let browser;
    try {
        browser = await chromium.launch({headless: true});
        fs.mkdirSync(".artifacts/paper-release", {recursive: true});
        for (const width of [1280, 390]) {
            enabled = false; released = false; writes = []; errors = [];
            const context = await browser.newContext({viewport: {width, height: 1000}});
            await context.addCookies([{name: "ai_paper_trader_session", value: "synthetic-owner-session", url: origin}]);
            const page = await context.newPage();
            await page.goto(origin);
            await page.waitForFunction(() => document.getElementById("pro-ticker-release-status").textContent.includes("disabled"));
            assert.equal(await page.locator("#pro-ticker-release-form").isVisible(), false);
            assert.equal(writes.length, 0);
            enabled = true;
            await page.evaluate(() => window.loadProTickerReleasePanel());
            await page.locator("#pro-ticker-release-reservation").fill(reservation);
            await page.locator("#pro-ticker-release-prepare").click();
            await page.waitForFunction(() => !document.getElementById("pro-ticker-release-attest").disabled);
            assert.equal(await page.locator("#pro-ticker-release-confirm").isDisabled(), true);
            assert.equal(writes.length, 1);
            await page.screenshot({path: `.artifacts/paper-release/review-${width}.png`, fullPage: true});
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
            await page.locator("#pro-ticker-release-attest").check();
            await page.locator("#pro-ticker-release-confirm").click();
            await page.waitForFunction(() => document.getElementById("pro-ticker-release-status").textContent.startsWith("Virtual PAPER funds returned."));
            assert.equal(await page.locator("#pro-ticker-release-events tr").count(), 1);
            assert.ok((await page.locator("#pro-ticker-release-events").textContent()).includes("$100.00"));
            assert.equal(writes.length, 2);
            assert.equal(errors.length, 0, errors.join("; "));
            await page.screenshot({path: `.artifacts/paper-release/history-${width}.png`, fullPage: true});
            await context.close();
        }
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
});
