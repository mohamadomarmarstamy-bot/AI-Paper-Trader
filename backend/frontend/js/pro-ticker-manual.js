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
    let refreshVersion = 0;
    async function refresh() {
        const version = ++refreshVersion;
        try {
            const data = await request(endpoint + "?limit=100");
            if (version !== refreshVersion) return false;
            if (!Array.isArray(data.observations) ||
                !Number.isSafeInteger(data.count) ||
                data.count < 0) {
                throw new Error("Unexpected research response");
            }
            if (data.observations.some(record =>
                !record || typeof record !== "object" || Array.isArray(record) ||
                typeof record.symbol !== "string" ||
                !/^[A-Z][A-Z0-9.\-]{0,14}$/.test(record.symbol) ||
                (record.market_snapshot != null &&
                    (typeof record.market_snapshot !== "object" ||
                     Array.isArray(record.market_snapshot))) ||
                (record.order_status != null &&
                    typeof record.order_status !== "string")
            )) {
                throw new Error("Invalid research observation");
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
            if (!data.observations.length) {
                setStatus(data.count === 0
                    ? "No manual research submissions yet."
                    : "No research observations returned for this page.");
            } else {
                setStatus("Loaded " + data.observations.length + " research observation(s).");
            }
            return true;
        } catch (error) {
            if (version !== refreshVersion) return false;
            rows.replaceChildren();
            setStatus("Unable to load submissions: " + error.message);
            return false;
        }
    }
    let submitting = false;
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (submitting) return;
        submitting = true;
        const button = form.querySelector('button[type="submit"]');
        if (button) button.disabled = true;
        setStatus("Saving research observations...");
        // Any refresh started before this submission is no longer authoritative.
        refreshVersion += 1;
        try {
            const result = await request(endpoint, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ symbols: input.value })
            });
            if (!Number.isSafeInteger(result.count) || result.count < 0) {
                throw new Error("Unexpected save response");
            }
            setStatus("Saved " + result.count + " symbol(s). Research only; no PAPER orders placed.");
            input.value = "";
            const historyLoaded = await refresh();
            if (historyLoaded) {
                setStatus("Saved " + result.count + " symbol(s). Research only; no PAPER orders placed.");
            } else {
                setStatus("Saved " + result.count + " symbol(s), but history could not be refreshed.");
            }
        } catch (error) {
            setStatus("Could not save: " + error.message);
        } finally {
            submitting = false;
            if (button) button.disabled = false;
        }
    });
    window.loadProTickerManualLab = refresh;
})();
