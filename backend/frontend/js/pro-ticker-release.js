"use strict";
(() => {
    const prefix = "/auto-trader/pro-ticker-paper";
    const ids = ["pro-ticker-release-status", "pro-ticker-release-form", "pro-ticker-release-reservation",
        "pro-ticker-release-prepare", "pro-ticker-release-review", "pro-ticker-release-attest",
        "pro-ticker-release-confirm", "pro-ticker-release-events"];
    const elements = ids.map(id => document.getElementById(id));
    if (elements.some(element => !element)) return;
    const [status, form, reservation, prepare, summary, attest, confirm, rows] = elements;
    let enabled = false, busy = false, review = null, version = 0;
    const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
    function resetReview() {
        review = null;
        summary.textContent = "";
        attest.checked = false;
        attest.disabled = true;
        confirm.disabled = true;
    }
    function updateButtons() {
        prepare.disabled = busy || !enabled;
        confirm.disabled = busy || !enabled || !review || !attest.checked || review.expires_at * 1000 <= Date.now();
    }
    async function request(path, payload) {
        const options = { credentials: "same-origin" };
        if (payload !== undefined) {
            options.method = "POST";
            options.headers = { "Content-Type": "application/json", "X-Pro-Ticker-Operator": "manual-release" };
            options.body = JSON.stringify(payload);
        }
        const response = await fetch(prefix + path, options);
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request failed.");
        return data;
    }
    async function refresh() {
        const current = ++version;
        resetReview();
        enabled = false;
        updateButtons();
        try {
            const [settings, history] = await Promise.all([request("/release-status"), request("/release-events?limit=100")]);
            if (current !== version) return false;
            if (settings.paper !== true || typeof settings.manual_release_enabled !== "boolean" ||
                settings.automatic_release_enabled !== false || !Array.isArray(history.events) ||
                history.counts_as_realized_profit !== false || history.paper !== true ||
                !Number.isSafeInteger(history.total_count) || history.total_count < 0 ||
                history.events.some(event => !event || !uuid.test(event.reservation_id) ||
                    typeof event.symbol !== "string" || !/^[A-Z][A-Z0-9.\-]{0,9}$/.test(event.symbol) ||
                    !Number.isSafeInteger(event.released_cents) || event.released_cents <= 0 ||
                    typeof event.released_at !== "string" || !Number.isFinite(Date.parse(event.released_at)))) {
                throw new Error("Unexpected PAPER release history.");
            }
            rows.replaceChildren();
            for (const event of history.events) {
                const tr = document.createElement("tr");
                for (const value of [event.symbol, "$" + (event.released_cents / 100).toFixed(2),
                    event.released_at, event.reservation_id]) {
                    const td = document.createElement("td");
                    td.textContent = value;
                    tr.appendChild(td);
                }
                rows.appendChild(tr);
            }
            enabled = settings.manual_release_enabled;
            form.hidden = !enabled;
            status.textContent = enabled
                ? "Review closed PAPER trades or confirmed unfilled cancellations to return their reserved funds. Each release requires your verification."
                : "Manual release controls are disabled. Release history remains available.";
            if (!history.events.length) status.textContent += " No virtual budget releases yet.";
            updateButtons();
            return true;
        } catch (error) {
            if (current !== version) return false;
            enabled = false;
            form.hidden = true;
            rows.replaceChildren();
            status.textContent = "Unable to load PAPER releases: " + error.message;
            updateButtons();
            return false;
        }
        updateButtons();
    }
    reservation.addEventListener("input", () => { version += 1; resetReview(); updateButtons(); });
    attest.addEventListener("change", updateButtons);
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (!enabled || busy) return;
        resetReview();
        const id = reservation.value.trim();
        if (!uuid.test(id)) { status.textContent = "Enter a valid reservation UUID."; return; }
        busy = true;
        const current = ++version;
        updateButtons();
        status.textContent = "Checking linked PAPER orders and recorded fills...";
        try {
            const data = await request("/release-review", { reservation_id: id });
            const unfilled=data.release_kind==="unfilled_cancellation";
            if (current !== version) return false;
            if (data.paper !== true || data.reservation_id !== id || data.budget_released !== false || !uuid.test(data.review_id) ||
                typeof data.evidence_digest !== "string" || !/^[a-f0-9]{64}$/.test(data.evidence_digest) ||
                typeof data.expires_at !== "number" || data.expires_at * 1000 <= Date.now() ||
                typeof data.symbol !== "string" || !/^[A-Z][A-Z0-9.\-]{0,9}$/.test(data.symbol) ||
                !Number.isSafeInteger(data.reserved_cents) || data.reserved_cents <= 0 ||
                !Number.isSafeInteger(data.filled_entry_shares) || data.filled_entry_shares < 0 || (!unfilled && data.filled_entry_shares === 0) || (unfilled && data.filled_entry_shares !== 0) ||
                data.filled_exit_shares !== data.filled_entry_shares ||
                !uuid.test(data.entry_order_id) || !Array.isArray(data.exit_order_ids) ||
                (!unfilled && !data.exit_order_ids.length) || data.exit_order_ids.some(value => !uuid.test(value))) {
                throw new Error("Unexpected PAPER review. Prepare a fresh review.");
            }
            review = data;
            summary.textContent = data.symbol + ": return $" + (data.reserved_cents / 100).toFixed(2) +
                " of reserved funds; "+(unfilled?"confirmed unfilled cancellation (no trade or profit); ":"")+data.filled_entry_shares + " entry shares and " + data.filled_exit_shares +
                " exited shares. Entry order: " + data.entry_order_id + ". Exit orders: " + data.exit_order_ids.join(", ") +
                ". Evidence: " + data.evidence_digest + ". Review expires in five minutes.";
            attest.disabled = false;
            status.textContent = "Check these orders in your PAPER account before confirming. Returned funds are not trading profit.";
        } catch (error) {
            if (current === version) { resetReview(); status.textContent = "Review unavailable: " + error.message; }
        } finally { busy = false; updateButtons(); }
    });
    confirm.addEventListener("click", async () => {
        if (!enabled || busy || !review || !attest.checked) return;
        if (review.expires_at * 1000 <= Date.now()) {
            resetReview(); updateButtons(); status.textContent = "Review expired. Prepare a fresh review."; return;
        }
        const approved = review;
        busy = true;
        updateButtons();
        status.textContent = "Verifying PAPER evidence again and returning reserved funds...";
        try {
            const result = await request("/release-confirm", {
                review_id: approved.review_id, evidence_digest: approved.evidence_digest, paper_provenance_confirmed: true
            });
            if (result.paper !== true || (result.budget_released !== true && result.already_released !== true)) {
                throw new Error("Unexpected release response. Check release history before retrying.");
            }
            resetReview();
            const refreshed = await refresh();
            status.textContent = refreshed
                ? "Virtual PAPER funds returned. No broker orders were placed and no trading profit was added."
                : "Virtual PAPER funds returned, but history could not refresh. Reload release history.";
        } catch (error) {
            status.textContent = "Release not confirmed: " + error.message + " Check history or retry the same confirmation.";
        } finally { busy = false; updateButtons(); }
    });
    resetReview();
    updateButtons();
    window.loadProTickerReleasePanel = refresh;
})();
