"use strict";
(() => {
    const root = document.getElementById("pro-ticker-manual-section");
    if (!root || !document.getElementById("pt-status")) return;
    const el = id => document.getElementById("pt-" + id);
    let snapshot = null, version = 0, journalRows = [];
    const tabs = Array.from(root.querySelectorAll("[data-pt-tab]"));
    function activate(key, focus = false) {
        for (const tab of tabs) {
            const selected = tab.dataset.ptTab === key;
            tab.setAttribute("aria-selected", String(selected));
            tab.tabIndex = selected ? 0 : -1;
            el("panel-" + tab.dataset.ptTab).hidden = !selected;
            if (selected && focus) tab.focus();
        }
    }
    tabs.forEach((tab, i) => {
        tab.addEventListener("click", () => activate(tab.dataset.ptTab));
        tab.addEventListener("keydown", event => {
            const offsets = {ArrowRight: 1, ArrowLeft: -1};
            let n;
            if (event.key in offsets) n = (i + offsets[event.key] + tabs.length) % tabs.length;
            else if (event.key === "Home") n = 0;
            else if (event.key === "End") n = tabs.length - 1;
            else return;
            event.preventDefault(); activate(tabs[n].dataset.ptTab, true);
        });
    });
    activate("overview");
    const money = cents => new Intl.NumberFormat(undefined, {style: "currency", currency: "USD"}).format(cents / 100);
    const date = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : "Unavailable";
    function table(id, records, columns) {
        const body = el(id); body.replaceChildren();
        if (!records.length) {
            const tr = document.createElement("tr"), td = document.createElement("td");
            td.colSpan = columns.length; td.textContent = snapshot ? "No persisted Pro Ticker records in this view." : "Data unavailable.";
            tr.appendChild(td); body.appendChild(tr); return;
        }
        for (const record of records) {
            const tr = document.createElement("tr");
            for (const column of columns) {
                const td = document.createElement("td"), value = column(record);
                if (value && value.nodeType) td.appendChild(value);
                else td.textContent = value == null ? "Unavailable" : String(value);
                tr.appendChild(td);
            }
            body.appendChild(tr);
        }
    }
    function researchDetail(record) {
        const node = el("research-detail"); node.replaceChildren();
        for (const [label, value] of [
            ["Symbol", record.symbol], ["Research title", record.article_title], ["Summary", record.article_summary],
            ["Published date", record.published_date], ["Direction", record.direction], ["Setup", record.setup_type],
            ["Long reference level", record.long_level], ["Short reference level", record.short_level],
            ["Relative volume", record.relative_volume], ["RSI context", record.rsi_context], ["Volume context", record.volume_context],
            ["Recorded scanner score", record.our_scanner_score], ["Recorded confidence", record.our_scanner_confidence],
            ["Recorded skip reason", record.our_skip_reason], ["Updated", date(record.updated_at)],
            ["Limitations", "Historical saved evidence; not a current price, fresh AI recommendation, or verified catalyst. Market regime and forward outcomes are not available here."]
        ]) {
            const p = document.createElement("p"), strong = document.createElement("strong");
            strong.textContent = label + ": "; p.appendChild(strong); p.appendChild(document.createTextNode(value == null || value === "" ? "Unavailable" : String(value))); node.appendChild(p);
        }
        activate("research");
    }
    function candidates() {
        const search = el("search").value.trim().toLowerCase(), direction = el("direction").value;
        const seen = new Set();
        let records = (snapshot?.research.rows || []).filter(r => {
            if (seen.has(r.symbol)) return false;
            seen.add(r.symbol); return true;
        }).filter(r => (!search || [r.symbol, r.article_title, r.article_summary].some(v => String(v || "").toLowerCase().includes(search))) &&
            (!direction || (direction === "long" ? /long|bull|buy/i : /short|bear|sell/i).test(r.direction || "")));
        if (el("sort").value === "symbol") records.sort((a,b) => a.symbol.localeCompare(b.symbol));
        if (el("sort").value === "score") records.sort((a,b) => (b.our_scanner_score ?? -Infinity) - (a.our_scanner_score ?? -Infinity));
        el("candidate-count").textContent = records.length + " distinct symbols in the loaded research window. " + (snapshot?.research.has_more ? "More records exist; this is a limited snapshot." : "");
        table("candidates", records, [r=>r.symbol,r=>r.our_scanner_score,r=>r.our_scanner_confidence,r=>r.direction,r=>date(r.updated_at),r=> {
            const button = document.createElement("button"); button.type="button"; button.textContent="Inspect " + r.symbol; button.addEventListener("click",()=>researchDetail(r)); return button;
        }]);
    }
    function journal() {
        const symbol = el("journal-symbol").value.trim().toUpperCase(), from=el("journal-from").value, to=el("journal-to").value;
        journalRows = (snapshot?.journal.rows || []).filter(r => {
            const d = new Date(r.recorded_at);
            const day = Number.isFinite(d.getTime()) ? [d.getFullYear(),String(d.getMonth()+1).padStart(2,"0"),String(d.getDate()).padStart(2,"0")].join("-") : "";
            return (!symbol || r.symbol.includes(symbol)) && (!from || day && day >= from) && (!to || day && day <= to);
        });
        table("journal",journalRows,[r=>date(r.recorded_at),r=>r.symbol,r=>r.side,r=>r.shares,r=>r.broker_order_id,r=>r.event_id]);
        el("export").disabled = !snapshot || !journalRows.length;
    }
    function render() {
        el("metrics").replaceChildren();
        if (snapshot) {
            for (const [label,value] of [["Configured virtual budget",money(snapshot.budget.configured_cents)],["Committed allocations",money(snapshot.budget.committed_cents)],["Available virtual funds",money(snapshot.budget.available_cents)],["Open ledger positions",snapshot.positions.total]]) {
                const card=document.createElement("div"),strong=document.createElement("strong"),caption=document.createElement("span");card.className="pt-metric";caption.textContent=label;strong.textContent=String(value);card.append(caption,strong);el("metrics").appendChild(card);
            }
        }
        table("positions", snapshot?.positions.rows || [], [r=>r.symbol,r=>r.shares,r=>date(r.updated_at)]);
        table("orders", snapshot?.reservations.rows || [], [r=>r.symbol,r=>r.status,r=>money(r.notional_cents),r=>r.broker_order_id]);
        table("reservations", snapshot?.reservations.rows || [], [r=>r.id,r=>r.symbol,r=>money(r.notional_cents),r=>r.status,r=>date(r.updated_at)]);
        el("scanner-note").textContent=snapshot ? snapshot.scanner.reason : "Scanner data unavailable.";
        el("performance-note").textContent=snapshot ? snapshot.performance.reason : "Performance data unavailable.";
        el("health").textContent=snapshot ? "Database: readable. Broker: not checked. Automatic discovery: disabled. Order submission: disabled. Automatic release: disabled. No background jobs are started by this workspace." : "System health unavailable.";
        el("research-detail").replaceChildren();
        el("research-detail").textContent="Select a saved candidate in Scanner to inspect its evidence.";
        candidates(); journal();
    }
    function validate(data) {
        if (!data || data.paper !== true || data.scope !== "pro_ticker_strategy_ledger" || data.order_execution_enabled !== false || data.automatic_release_enabled !== false || data.broker_reconciled !== false) throw Error("Unexpected workspace safety flags");
        for (const key of ["configured_cents","committed_cents","available_cents","overallocated_cents"]) if (!Number.isSafeInteger(data.budget?.[key]) || data.budget[key]<0) throw Error("Invalid budget data");
        if (data.budget.available_cents !== Math.max(0,data.budget.configured_cents-data.budget.committed_cents)) throw Error("Inconsistent available budget");
        for (const key of ["positions","reservations","journal","research"]) {
            const group=data[key];
            if (!Array.isArray(group?.rows) || !Number.isSafeInteger(group.total) || group.total<group.rows.length || typeof group.has_more!=="boolean" || group.rows.some(r=>!r || typeof r!=="object" || typeof r.symbol!=="string")) throw Error("Invalid " + key + " records");
        }
        for (const r of data.reservations.rows) if (!Number.isSafeInteger(r.notional_cents) || r.notional_cents<=0) throw Error("Invalid allocation");
        for (const r of [...data.positions.rows,...data.journal.rows]) if (!Number.isSafeInteger(r.shares) || r.shares<=0) throw Error("Invalid shares");
        if (typeof data.performance?.reason!=="string" || typeof data.scanner?.reason!=="string" || !Number.isFinite(Date.parse(data.generated_at))) throw Error("Invalid snapshot metadata");
        return data;
    }
    async function refresh() {
        const requestVersion=++version; el("status").textContent="Loading persisted Pro Ticker data…";
        try {
            const response=await fetch("/auto-trader/pro-ticker/workspace",{credentials:"same-origin",cache:"no-store"});
            if (!response.ok) throw Error(response.status===401 ? "Sign in again to load your workspace." : "Workspace request failed ("+response.status+").");
            const data=validate(await response.json());
            if (requestVersion!==version) return;
            snapshot=data; render();
            const limited=["positions","reservations","journal","research"].filter(k=>data[k].has_more);
            el("status").textContent="Ledger snapshot: "+date(data.generated_at)+". Broker not reconciled. "+(limited.length ? "Latest 200 records per view; truncated: "+limited.join(", ")+". " : "")+(data.budget.overallocated_cents ? "WARNING: allocations exceed virtual budget." : "");
        } catch(error) {
            if (requestVersion!==version) return;
            snapshot=null;render();el("status").textContent="Unable to load workspace: "+error.message;
        }
    }
    el("refresh").addEventListener("click",()=> { refresh(); window.loadProTickerManualLab?.(); window.loadProTickerReleasePanel?.(); });
    let calculating = false, proposalVersion = 0;
    el("proposal-form").addEventListener("input", () => {
        proposalVersion += 1;
        el("proposal-result").textContent = "Inputs changed. Recalculate this hypothetical proposal.";
    });
    el("proposal-form").addEventListener("submit", async event => {
        event.preventDefault();
        if (calculating) return;
        const current = ++proposalVersion;
        calculating = true; el("proposal-button").disabled = true;
        el("proposal-result").textContent = "Calculating against current virtual allocations…";
        try {
            const response = await fetch("/auto-trader/pro-ticker/proposal", {
                method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({symbol: el("proposal-symbol").value.trim().toUpperCase(),
                    reference_price: el("proposal-price").value, shares: Number(el("proposal-shares").value),
                    stop_loss_percent: el("proposal-stop").value, take_profit_percent: el("proposal-target").value})
            });
            if (!response.ok) throw Error("Proposal request failed (" + response.status + ").");
            const result = await response.json();
            if (current !== proposalVersion) return;
            if (result.paper !== true || result.hypothetical !== true || result.order_submitted !== false ||
                result.order_execution_enabled !== false || result.budget_reserved !== false ||
                typeof result.within_virtual_budget !== "boolean" || typeof result.reason !== "string" ||
                ![result.allocation_cents,result.estimated_stop_risk_cents,result.available_virtual_cents].every(n=>Number.isSafeInteger(n)&&n>=0)) throw Error("Invalid proposal safety or amount data");
            el("proposal-result").textContent = "Hypothetical allocation: " + money(result.allocation_cents) +
                ". Stop: " + result.stop_price + ". Target: " + result.take_profit_price +
                ". Estimated stop risk: " + money(result.estimated_stop_risk_cents) +
                ". Available virtual funds: " + money(result.available_virtual_cents) +
                (result.within_virtual_budget ? ". Within virtual allocation limit. " : ". BLOCKED: exceeds virtual allocation limit. ") +
                result.reason + " No funds reserved; no order placed.";
        } catch (error) {
            if (current === proposalVersion) el("proposal-result").textContent = "Unable to calculate: " + error.message;
        } finally { calculating = false; el("proposal-button").disabled = false; }
    });
    for (const id of ["search","direction","sort"]) el(id).addEventListener("input",candidates);
    for (const id of ["journal-symbol","journal-from","journal-to"]) el(id).addEventListener("input",journal);
    el("export").addEventListener("click",()=> {
        if (!snapshot || !journalRows.length) return;
        const cell=value=>'"'+String(value??"").replace(/^[=+@\-\t\r]/,"'$&").replaceAll('"','""')+'"';
        const fields=["recorded_at","symbol","side","shares","broker_order_id","event_id"];
        const csv=[fields,...journalRows.map(r=>fields.map(f=>r[f]))].map(row=>row.map(cell).join(",")).join("\r\n");
        const url=URL.createObjectURL(new Blob([csv],{type:"text/csv;charset=utf-8"}));
        const link=document.createElement("a");link.href=url;link.download="pro-ticker-ledger-fills.csv";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
    render(); window.loadProTickerWorkspace=refresh;
})();
