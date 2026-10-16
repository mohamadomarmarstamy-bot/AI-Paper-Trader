"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const script = fs.readFileSync(path.join(__dirname, "pro-ticker-manual.js"), "utf8");

function setup(fetchImpl) {
    const elements = new Map();
    const makeElement = () => ({
        textContent: "", value: "", disabled: false, children: [],
        appendChild(child) { this.children.push(child); },
        replaceChildren(...children) { this.children = children; },
        addEventListener(name, handler) { this.handlers[name] = handler; },
        handlers: {},
        querySelector() { return elements.get("button"); }
    });
    for (const id of ["pro-ticker-manual-form", "pro-ticker-manual-symbols", "pro-ticker-manual-status", "pro-ticker-manual-history", "button"]) {
        elements.set(id, makeElement());
    }
    const calls = [];
    const sandbox = {
        document: {
            getElementById(id) { return elements.get(id); },
            createElement() { return makeElement(); }
        },
        window: {},
        fetch(url, options) { calls.push({ url, options }); return fetchImpl(url, options); }
    };
    vm.runInNewContext(script, sandbox, { filename: "pro-ticker-manual.js" });
    return { elements, calls, refresh: sandbox.window.loadProTickerManualLab,
        submit: () => elements.get("pro-ticker-manual-form").handlers.submit({ preventDefault() {} }) };
}
function reply(data, ok = true) {
    return { ok, status: ok ? 200 : 400, async json() { return data; } };
}
test("valid history renders research-only cells without order requests", async () => {
    const app = setup(async () => reply({ count: 1, observations: [
        { symbol: "AAPL", order_status: "research_only", market_snapshot: { status: "captured", price: 123 } }
    ] }));
    assert.equal(await app.refresh(), true);
    const rows = app.elements.get("pro-ticker-manual-history").children;
    assert.equal(rows.length, 1);
    assert.equal(rows[0].children[0].textContent, "AAPL");
    assert.equal(rows[0].children[1].textContent, "123");
    assert.match(app.elements.get("pro-ticker-manual-status").textContent, /Loaded 1/);
    assert.deepEqual(app.calls.map(x => x.url), ["/auto-trader/pro-ticker-manual-submissions?limit=100"]);
});
test("unexpected execution status fails closed and clears stale rows", async () => {
    let count = 0;
    const app = setup(async () => reply(count++ === 0
        ? { count: 1, observations: [{ symbol: "AAPL", order_status: "research_only" }] }
        : { count: 1, observations: [{ symbol: "AAPL", order_status: "filled" }] }));
    assert.equal(await app.refresh(), true);
    assert.equal(await app.refresh(), false);
    assert.equal(app.elements.get("pro-ticker-manual-history").children.length, 0);
    assert.match(app.elements.get("pro-ticker-manual-status").textContent, /Invalid research observation/);
});
test("stale refresh cannot overwrite newer history", async () => {
    let release;
    let first = true;
    const app = setup(async () => {
        if (first) {
            first = false;
            return new Promise(resolve => { release = resolve; });
        }
        return reply({ count: 1, observations: [{ symbol: "MSFT" }] });
    });
    const stale = app.refresh();
    assert.equal(await app.refresh(), true);
    release(reply({ count: 1, observations: [{ symbol: "AAPL" }] }));
    assert.equal(await stale, false);
    assert.equal(app.elements.get("pro-ticker-manual-history").children[0].children[0].textContent, "MSFT");
});
test("submission only posts to research endpoint and refreshes history", async () => {
    const app = setup(async (url, options) => options.method === "POST"
        ? reply({ count: 1 })
        : reply({ count: 1, observations: [{ symbol: "AAPL" }] }));
    app.elements.get("pro-ticker-manual-symbols").value = "AAPL";
    await app.submit();
    assert.equal(app.calls.length, 2);
    assert.equal(app.calls[0].url, "/auto-trader/pro-ticker-manual-submissions");
    assert.equal(app.calls[0].options.method, "POST");
    assert.equal(JSON.parse(app.calls[0].options.body).symbols, "AAPL");
    assert.equal(app.elements.get("pro-ticker-manual-symbols").value, "");
    assert.match(app.elements.get("pro-ticker-manual-status").textContent, /no PAPER orders placed/);
});
