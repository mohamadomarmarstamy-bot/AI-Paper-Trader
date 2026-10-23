"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const script = fs.readFileSync(path.join(__dirname, "pro-ticker-release.js"), "utf8");
const id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const exit = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const digest = "a".repeat(64);
function setup(fetcher) {
    const nodes = new Map();
    const element = () => ({textContent: "", value: "", checked: false, disabled: false, hidden: false,
        children: [], handlers: {}, addEventListener(name, fn) { this.handlers[name] = fn; },
        replaceChildren(...items) { this.children = items; }, appendChild(item) { this.children.push(item); }});
    for (const name of ["status", "form", "reservation", "prepare", "review", "attest", "confirm", "events"]) {
        nodes.set("pro-ticker-release-" + name, element());
    }
    const calls = [];
    const sandbox = { document: {getElementById: name => nodes.get(name), createElement: element}, window: {},
        fetch: async (url, options) => { calls.push({url, options}); return {ok: true, json: async () => fetcher(url, options)}; } };
    vm.runInNewContext(script, sandbox);
    return { get: name => nodes.get("pro-ticker-release-" + name), calls, load: sandbox.window.loadProTickerReleasePanel };
}
function settings(enabled = true) { return {paper: true, manual_release_enabled: enabled, automatic_release_enabled: false}; }
function history(events = []) { return {paper: true, events, total_count: events.length, counts_as_realized_profit: false}; }
function review() { return {paper: true, budget_released: false, reservation_id: id, review_id: id, evidence_digest: digest,
    expires_at: Date.now() / 1000 + 300, symbol: "AAPL", reserved_cents: 10000, filled_entry_shares: 5,
    filled_exit_shares: 5, entry_order_id: id, exit_order_ids: [exit]}; }
function fetcher(url) {
    if (url.endsWith("/release-status")) return settings();
    if (url.includes("/release-events")) return history();
    if (url.endsWith("/release-review")) return review();
    return {paper: true, budget_released: true};
}
async function prepare(ui) {
    await ui.load();
    ui.get("reservation").value = id;
    await ui.get("form").handlers.submit({preventDefault() {}});
}
test("disabled controls cannot prepare or confirm", async () => {
    const ui = setup(url => url.endsWith("/release-status") ? settings(false) : history());
    await ui.load();
    await ui.get("form").handlers.submit({preventDefault() {}});
    await ui.get("confirm").handlers.click();
    assert.equal(ui.get("form").hidden, true);
    assert.equal(ui.calls.length, 2);
});
test("review does not release and requires separate checked confirmation", async () => {
    const ui = setup(fetcher);
    await prepare(ui);
    assert.equal(ui.get("confirm").disabled, true);
    await ui.get("confirm").handlers.click();
    assert.equal(ui.calls.length, 3);
    assert.match(ui.get("review").textContent, /5 entry shares/);
    ui.get("attest").checked = true;
    ui.get("attest").handlers.change();
    await ui.get("confirm").handlers.click();
    const post = ui.calls.find(call => call.url.endsWith("/release-confirm"));
    assert.deepEqual(JSON.parse(post.options.body), {review_id: id, evidence_digest: digest, paper_provenance_confirmed: true});
    assert.equal(post.options.credentials, "same-origin");
    assert.equal(post.options.headers["X-Pro-Ticker-Operator"], "manual-release");
    assert.equal(ui.get("attest").checked, false);
});
test("changing reservation invalidates prepared approval", async () => {
    const ui = setup(fetcher);
    await prepare(ui);
    ui.get("attest").checked = true;
    ui.get("reservation").handlers.input();
    await ui.get("confirm").handlers.click();
    assert.equal(ui.calls.length, 3);
    assert.equal(ui.get("review").textContent, "");
});
test("malformed or expired review never enables confirmation", async () => {
    for (const update of [{expires_at: 1}, {filled_exit_shares: 4}, {evidence_digest: "bad"}, {budget_released: true}, {reserved_cents: -1}]) {
        const ui = setup(url => url.endsWith("/release-review") ? {...review(), ...update} : fetcher(url));
        await prepare(ui);
        assert.equal(ui.get("attest").disabled, true);
        assert.equal(ui.get("confirm").disabled, true);
    }
});
test("release history renders as virtual funds without adding trading profit", async () => {
    const ui = setup(url => url.includes("/release-events") ? history([{reservation_id: id, symbol: "AAPL", released_cents: 12345,
        released_at: "2026-10-16T23:00:00+00:00"}]) : settings(false));
    await ui.load();
    const row = ui.get("events").children[0];
    assert.equal(row.children[1].textContent, "$123.45");
    assert.equal(ui.get("form").hidden, true);
});
test("malformed history clears rows and disables release controls", async () => {
    const ui = setup(url => url.includes("/release-events") ? history([{reservation_id: id, symbol: "<script>", released_cents: 1}]) : settings());
    await ui.load();
    assert.equal(ui.get("events").children.length, 0);
    assert.equal(ui.get("form").hidden, true);
    assert.equal(ui.get("prepare").disabled, true);
});
test("stale review cannot approve a newly selected reservation", async () => {
    let resolve, started;
    const ready = new Promise(done => { started = done; });
    const ui = setup(url => url.endsWith("/release-review") ? new Promise(done => { resolve = done; started(); }) : fetcher(url));
    await ui.load();
    ui.get("reservation").value = id;
    const pending = ui.get("form").handlers.submit({preventDefault() {}});
    await ready;
    ui.get("reservation").handlers.input();
    resolve(review());
    await pending;
    assert.equal(ui.get("attest").disabled, true);
    assert.equal(ui.get("review").textContent, "");
});
test("network rejection retains exact review for safe retry", async () => {
    let fail = true;
    const ui = setup(url => {
        if (url.endsWith("/release-confirm") && fail) throw new Error("network failure");
        return fetcher(url);
    });
    await prepare(ui);
    ui.get("attest").checked = true;
    await ui.get("confirm").handlers.click();
    assert.match(ui.get("status").textContent, /Release not confirmed/);
    fail = false;
    await ui.get("confirm").handlers.click();
    const bodies = ui.calls.filter(call => call.url.endsWith("/release-confirm")).map(call => call.options.body);
    assert.equal(bodies.length, 2);
    assert.equal(bodies[0], bodies[1]);
});
test("unexpected automatic release flag blocks the panel", async () => {
    const ui = setup(url => url.endsWith("/release-status") ? {...settings(), automatic_release_enabled: true} : history());
    await ui.load();
    assert.equal(ui.get("prepare").disabled, true);
});

test("successful release with failed history refresh reports partial success", async () => {
    let released = false;
    const ui = setup(url => {
        if (url.endsWith("/release-confirm")) { released = true; return {paper: true, budget_released: true}; }
        if (released && url.includes("/release-events")) throw new Error("history unavailable");
        return fetcher(url);
    });
    await prepare(ui);
    ui.get("attest").checked = true;
    await ui.get("confirm").handlers.click();
    assert.match(ui.get("status").textContent, /funds returned, but history could not refresh/);
    assert.equal(ui.get("confirm").disabled, true);
});

test("confirmed unfilled cancellation still needs separate owner approval", async () => {
    const ui=setup(url=>url.endsWith('/release-review')?{...review(),release_kind:'unfilled_cancellation',filled_entry_shares:0,filled_exit_shares:0,exit_order_ids:[]}:fetcher(url));
    await prepare(ui);
    assert.match(ui.get('review').textContent,/no trade or profit/);
    assert.equal(ui.get('confirm').disabled,true);
    ui.get('attest').checked=true;ui.get('attest').handlers.change();
    assert.equal(ui.get('confirm').disabled,false);
});

test("zero-share release without an explicit cancellation classification fails closed",async()=>{
    const ui=setup(url=>url.endsWith('/release-review')?{...review(),filled_entry_shares:0,filled_exit_shares:0,exit_order_ids:[]}:fetcher(url));
    await prepare(ui);assert.equal(ui.get('confirm').disabled,true);assert.equal(ui.get('attest').disabled,true);
});
