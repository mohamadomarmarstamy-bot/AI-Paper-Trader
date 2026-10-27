"use strict";
(() => {
    let pending = null;
    let snapshot = null;
    let loadedAt = 0;
    const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    const number = value => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)) ? Number(value) : null;
    const money = value => number(value) === null ? "Unavailable" : new Intl.NumberFormat("en-US", {style:"currency", currency:"USD", maximumFractionDigits: Number(value) > 0 && Number(value) < 1 ? 4 : 2}).format(Number(value));
    const shares = value => number(value) === null ? "Unavailable" : new Intl.NumberFormat("en-US", {maximumFractionDigits:6}).format(Number(value));
    const date = value => { const d = new Date(value); return !value || Number.isNaN(d.getTime()) ? "Unavailable" : d.toLocaleString(); };
    async function load(options = {}) {
        if (pending) return pending;
        if (!options.force && snapshot && Date.now() - loadedAt < 5000) return snapshot;
        pending = (async () => {
            const base = String(window.API_URL || "").replace(/\/+$/, "");
            const controller = new AbortController();
            const timeout = window.setTimeout(() => controller.abort(), 15000);
            try {
                const response = await fetch(base + "/account/trade-activity", {cache:"no-store", credentials:"same-origin", signal:controller.signal});
                if (!response.ok) throw Error("HTTP " + response.status);
                const data = await response.json();
                if (!Array.isArray(data.buys) || !Array.isArray(data.sells) || !Array.isArray(data.completed)) throw Error("Invalid fill response");
                snapshot = data; loadedAt = Date.now(); render(data); return data;
            } finally { window.clearTimeout(timeout); }
        })();
        try { return await pending; } finally { pending = null; }
    }
    function rows(id, html, columns, empty) {
        const body = document.getElementById(id);
        if (body) body.innerHTML = html || `<tr><td colspan="${columns}">${escape(empty)}</td></tr>`;
    }
    function render(data) {
        for (const side of ["buys", "sells"]) rows("activity-" + side, data[side].map(fill => `<tr><td>${escape(date(fill.filled_at))}</td><td>${escape(fill.symbol)}</td><td>${shares(fill.shares)}</td><td>${money(fill.price)}</td><td>${money(fill.total)}</td></tr>`).join(""), 5, "No confirmed " + side + " in the available history.");
        rows("activity-completed", data.completed.map(group => `<tr><td><details><summary>${escape(group.symbol)} · ${group.trades.length} completed ${group.trades.length === 1 ? "buy" : "buys"}</summary>${group.trades.map(lot => `<div class="activity-lot">Bought ${shares(lot.shares)} @ ${money(lot.buy_price)} · ${escape(date(lot.bought_at))}${lot.exits.map(exit => `<br>Sold ${shares(exit.shares)} @ ${money(exit.price)} · ${escape(date(exit.filled_at))}`).join("")}<br>Result: ${money(lot.profit_loss)}</div>`).join("")}</details></td><td>${shares(group.shares)}</td><td>${money(group.buy_price)}</td><td>${money(group.sell_price)}</td><td>${money(group.buy_total)}</td><td>${money(group.sell_total)}</td><td class="${Number(group.profit_loss) > 0 ? "positive" : Number(group.profit_loss) < 0 ? "negative" : ""}">${money(group.profit_loss)}</td></tr>`).join(""), 7, "No fully sold buy lots in the available history. Open or partly sold buys appear above.");
        const status = document.getElementById("activity-status");
        if (status) status.textContent = `${data.buys.length} buys · ${data.sells.length} sells · ${data.completed.length} completed stocks. ${data.broker_available === false ? "Broker refresh unavailable; showing saved fills. " : ""}${data.ledger_limit_reached ? "Saved history limit reached; older fills excluded. " : ""}${data.unmatched_sells?.length ? data.unmatched_sells.length + " sell fills have no matching buy in this history. " : ""}Updated ${date(data.generated_at * 1000)}.`;
    }
    async function refresh(options) {
        try { return await load(options); }
        catch (error) { const status = document.getElementById("activity-status"); if (status) status.textContent = "Activity refresh failed; displayed fills may be out of date. " + error.message; }
    }
    window.loadTradeActivity = load;
    document.addEventListener("DOMContentLoaded", () => {
        document.getElementById("refresh-activity-button")?.addEventListener("click", () => refresh({force:true}));
        refresh();
        window.setInterval(() => { if (!document.hidden) refresh(); }, 15000);
    });
})();
