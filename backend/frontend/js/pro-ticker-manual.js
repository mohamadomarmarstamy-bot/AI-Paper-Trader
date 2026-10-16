"use strict";
(() => {
    const endpoint = "/auto-trader/pro-ticker-manual-submissions";
    const form = document.getElementById("pro-ticker-manual-form");
    const input = document.getElementById("pro-ticker-manual-symbols");
    const status = document.getElementById("pro-ticker-manual-status");
    const rows = document.getElementById("pro-ticker-manual-history");
    if (!form || !input || !status || !rows) return;

    const setStatus = (message) => { status.textContent = message; };
    async function request(url, options = {}) {
        const response = await fetch(url, { credentials: "same-origin", ...options });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(typeof data.detail === "string" ? data.detail : "Request failed (" + response.status + ")");
        }
        return data;
    }
    async function refresh() {
        try {
            const data = await request(endpoint + "?limit=100");
            if (!Array.isArray(data.observations)) {
                throw new Error("Unexpected research response");
            }
            rows.replaceChildren();
            for (const record of data.observations) {
                const tr = document.createElement("tr");
                for (const value of [
                    record.symbol,
                    record.market_snapshot?.status === "captured" ? String(record.market_snapshot.price) : "Unavailable",
                    record.market_snapshot?.market_timestamp || "—",
                    record.observed_at ? new Date(record.observed_at).toLocaleString() : "—",
                    record.order_status || "research_only"
                ]) {
                    const td = document.createElement("td");
                    td.textContent = value;
                    tr.appendChild(td);
                }
                rows.appendChild(tr);
            }
            if (!data.count) setStatus("No manual research submissions yet.");
        } catch (error) {
            rows.replaceChildren();
            setStatus("Unable to load submissions: " + error.message);
        }
    }
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const button = form.querySelector('button[type="submit"]');
        button.disabled = true;
        setStatus("Saving research observations...");
        try {
            const result = await request(endpoint, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ symbols: input.value })
            });
            setStatus("Saved " + result.count + " symbol(s). Research only; no PAPER orders placed.");
            input.value = "";
            await refresh();
            setStatus("Saved " + result.count + " symbol(s). Research only; no PAPER orders placed.");
        } catch (error) {
            setStatus("Could not save: " + error.message);
        } finally {
            button.disabled = false;
        }
    });
    window.loadProTickerManualLab = refresh;
})();
