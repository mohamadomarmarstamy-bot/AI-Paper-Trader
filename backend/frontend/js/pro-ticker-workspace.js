"use strict";
(() => {
    const root = document.getElementById("pro-ticker-manual-section");
    if (!root || !document.getElementById("pt-status")) return;
    const el = id => document.getElementById("pt-" + id);
    const closedPanel=document.createElement("article"),closedTitle=document.createElement("h3"),closedStatus=document.createElement("p"),closedMetrics=document.createElement("p"),closedRows=document.createElement("tbody"),closedTable=document.createElement("table"),closedHead=document.createElement("thead"),closedHeader=document.createElement("tr"),closedScroller=document.createElement("div");
    closedPanel.id="pt-closed-performance";closedTitle.textContent="Closed PAPER outcomes — gross estimates";closedStatus.setAttribute("role","status");
    for(const label of ["Recorded at (not execution time)","Symbol","Shares","Gross estimate","Price coverage"]){const th=document.createElement("th");th.textContent=label;closedHeader.appendChild(th);}
    closedHead.appendChild(closedHeader);closedTable.append(closedHead,closedRows);closedScroller.style.overflowX="auto";closedScroller.appendChild(closedTable);closedPanel.append(closedTitle,closedStatus,closedMetrics,closedScroller);el("panel-performance")?.appendChild(closedPanel);
    let closedVersion=0;
    async function loadClosedPerformance(){
        const current=++closedVersion;closedStatus.textContent="Loading closed PAPER outcome evidence…";
        try{
            const response=await fetch("/auto-trader/pro-ticker/closed-performance",{credentials:"same-origin",cache:"no-store"});if(!response.ok)throw Error("Report request failed ("+response.status+").");
            const data=await response.json(),counts=[data.closed_reservations_count,data.recorded_count,data.priced_count,data.unpriced_count,data.gross_wins,data.gross_losses,data.gross_flat];
            if(data.paper!==true||data.kind!=="closed_gross_estimate_from_terminal_broker_averages"||data.model_updated!==false||data.net_pnl!==null||data.unrealized_pnl!==null||data.drawdown!==null||!counts.every(n=>Number.isSafeInteger(n)&&n>=0)||data.recorded_count>data.closed_reservations_count||data.priced_count+data.unpriced_count!==data.closed_reservations_count||data.gross_wins+data.gross_losses+data.gross_flat!==data.priced_count||!Array.isArray(data.rows)||data.rows.length>data.recorded_count||data.has_more!==(data.recorded_count>data.rows.length))throw Error("Invalid closed PAPER report");
            if(data.priced_count){
                if(![data.gross_pnl_cents,data.gross_profit_cents,data.gross_loss_cents].every(Number.isSafeInteger)||data.gross_profit_cents<0||data.gross_loss_cents<0||data.gross_pnl_cents!==data.gross_profit_cents-data.gross_loss_cents||!Number.isFinite(data.gross_win_rate_percent)||data.gross_win_rate_percent<0||data.gross_win_rate_percent>100||(data.gross_profit_factor!==null&&(!Number.isFinite(data.gross_profit_factor)||data.gross_profit_factor<0)))throw Error("Invalid gross metrics");
            }else if([data.gross_pnl_cents,data.gross_profit_cents,data.gross_loss_cents,data.gross_win_rate_percent,data.gross_profit_factor].some(v=>v!==null))throw Error("Missing prices cannot imply performance");
            for(const [field,count]of [["average_gross_gain_cents",data.gross_wins],["average_gross_loss_cents",data.gross_losses]])if(count?(!Number.isSafeInteger(data[field])||(field==="average_gross_gain_cents"?data[field]<=0:data[field]>=0)):data[field]!==null)throw Error("Invalid average gross result");
            if(data.rows.some(row=>!row||row.paper!==true||row.kind!==data.kind||typeof row.symbol!=="string"||!Number.isFinite(Date.parse(row.recorded_at))||!["priced","unavailable"].includes(row.status)||row.training_eligible!==false||row.net_pnl!==null||row.fees!==null||(row.status==="priced"?(!Number.isSafeInteger(row.gross_pnl_cents)||!Number.isSafeInteger(row.shares)||row.shares<1):row.gross_pnl_cents!==null)))throw Error("Invalid closed outcome rows");
            if(current!==closedVersion)return;closedRows.replaceChildren();
            for(const row of data.rows){const tr=document.createElement("tr");for(const value of [date(row.recorded_at),row.symbol,row.shares??"Unavailable",row.status==="priced"?money(row.gross_pnl_cents):"Unavailable",row.status==="priced"?"Terminal average prices; fees unknown":"Average prices unavailable"]){const td=document.createElement("td");td.textContent=String(value);tr.appendChild(td);}closedRows.appendChild(tr);}
            closedStatus.textContent="Price coverage: "+data.priced_count+" of "+data.closed_reservations_count+" owner-reconciled closed reservations. "+data.unpriced_count+" remain unpriced. "+(data.has_more?"Displayed rows are limited; summary includes all recorded outcomes. ":"")+"Gross estimates use terminal broker average fill prices. Net P&L, fees, exact execution prices and equity drawdown remain unavailable. Returned virtual funds are not profit.";
            closedMetrics.textContent=data.priced_count?"Gross closed estimate: "+money(data.gross_pnl_cents)+" · Gross wins/losses/flat: "+data.gross_wins+" / "+data.gross_losses+" / "+data.gross_flat+" · Gross win rate: "+data.gross_win_rate_percent.toFixed(1)+"% · Gross profit factor: "+(data.gross_profit_factor===null?"Unavailable (no gross losses)":data.gross_profit_factor.toFixed(2))+" · Average gross gain / loss: "+(data.average_gross_gain_cents===null?"Unavailable":money(data.average_gross_gain_cents))+" / "+(data.average_gross_loss_cents===null?"Unavailable":money(data.average_gross_loss_cents)):"No priced closed outcomes. No zero profit or win-rate result is inferred.";
        }catch(error){if(current===closedVersion){closedRows.replaceChildren();closedMetrics.textContent="";closedStatus.textContent="Closed PAPER performance unavailable: "+error.message;}}
    }

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
            const offsets = {ArrowRight: 1, ArrowLeft: -1, ArrowDown: 1, ArrowUp: -1};
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
        const draft = document.createElement("button");
        draft.type = "button"; draft.textContent = "Draft an estimate for " + record.symbol;
        draft.addEventListener("click", () => {
            el("proposal-symbol").value = record.symbol;
            // Research levels are historical, not verified current prices. Also
            // clear the previous symbol's quantity and protection assumptions.
            for (const id of ["price","shares","stop","target"]) el("proposal-" + id).value = "";
            el("proposal-form").dispatchEvent(new Event("input", {bubbles:true}));
            el("proposal-result").textContent = "Selected " + record.symbol + " from saved research. Enter a reference price and new risk assumptions; historical research is not current broker evidence.";
            activate("trading"); el("proposal-price").focus();
        });
        node.appendChild(draft);
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
        const discovery = snapshot?.scanner?.public_discovery;
        el("scanner-note").textContent=snapshot ? snapshot.scanner.reason +
            (snapshot.scanner.unattributed_research_records ? " Research records without an identified symbol: " + snapshot.scanner.unattributed_research_records + ". They are excluded from candidates." : "") +
            (discovery && typeof discovery.status === "string" ? " Last public discovery: " + discovery.status + (discovery.run?.finished_at ? " (" + date(discovery.run.finished_at) + ")" : "") + ". This is not a live market scan." : "") : "Scanner data unavailable.";
        el("performance-note").textContent=snapshot ? snapshot.performance.reason : "Performance data unavailable.";
        el("health").textContent=snapshot ? "Database: readable. Broker positions and orders: not reconciled by this ledger snapshot. Automatic discovery: disabled. Order submission requires separate server configuration and owner approval. Automatic release: disabled. No background jobs are started by this workspace." : "System health unavailable.";
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
        const requestVersion=++version; void loadClosedPerformance(); el("status").textContent="Loading persisted Pro Ticker data…";
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
    let discovering = false, discoveryVersion = 0;
    function showDiscovery(data) {
        if (data.paper !== true || data.automatic_discovery_enabled !== false || data.order_execution_enabled !== false ||
            !["not_run","running","completed","partial","failed","no_results","interrupted"].includes(data.status)) throw Error("Invalid discovery status");
        const run = data.run;
        let text = "Public research discovery: " + data.status + ". ";
        if (run) {
            text += "Started: " + date(run.started_at) + ". ";
            if (run.finished_at) text += "Finished: " + date(run.finished_at) + ". ";
            if (Number.isSafeInteger(run.saved)) text += "Saved articles: " + run.saved + "; skipped: " + run.skipped + "; failures: " + run.failed + ". ";
            if (typeof run.reason === "string") text += run.reason;
        }
        el("discovery-status").textContent = text;
    }
    async function loadDiscovery() {
        const current = ++discoveryVersion;
        try {
            const response = await fetch("/auto-trader/pro-ticker/discovery", {credentials:"same-origin",cache:"no-store"});
            if (!response.ok) throw Error("Discovery status request failed (" + response.status + ").");
            const data = await response.json();
            if (current === discoveryVersion) showDiscovery(data);
        } catch (error) {
            if (current === discoveryVersion) el("discovery-status").textContent = "Discovery status unavailable: " + error.message;
        }
    }
    el("discover").addEventListener("click", async () => {
        if (discovering) return;
        discovering = true; discoveryVersion += 1; el("discover").disabled = true;
        el("discovery-status").textContent = "Discovering public research. One page and at most five new articles; this may take up to a few minutes.";
        try {
            const response = await fetch("/auto-trader/pro-ticker/discovery", {method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:"{}"});
            if (!response.ok) throw Error("Discovery request failed (" + response.status + ").");
            showDiscovery(await response.json());
            await refresh();
        } catch (error) {
            el("discovery-status").textContent = "Discovery response unavailable: " + error.message + " Check persisted status before retrying; the server may still be working.";
        } finally { discovering = false; el("discover").disabled = false; }
    });
    let settings = null, settingsVersion = 0, settingsDirty = false, savingSettings = false;
    const settingsInputs = ["settings-allocation", "settings-risk", "settings-articles"];
    function settingsControls(disabled) {
        settingsInputs.forEach(id => { el(id).disabled = disabled; });
        el("settings-save").disabled = disabled;
    }
    function validateSettings(data) {
        if (data.paper !== true || data.scope !== "pro_ticker_proposals_and_manual_discovery" ||
            data.order_execution_enabled !== false || data.automatic_release_enabled !== false ||
            !Number.isSafeInteger(data.revision) || data.revision < 0) throw Error("Invalid settings safety flags or revision");
        for (const [key, max] of [["max_allocation_cents",5000000],["max_stop_risk_cents",5000000],["discovery_article_limit",5]]) {
            if (!Number.isSafeInteger(data.settings?.[key]) || data.settings[key]<1 || data.settings[key]>max) throw Error("Invalid setting value");
        }
        return data;
    }
    function showSettings(data) {
        settings = validateSettings(data); settingsDirty = false;
        el("settings-allocation").value = (data.settings.max_allocation_cents/100).toFixed(2);
        el("settings-risk").value = (data.settings.max_stop_risk_cents/100).toFixed(2);
        el("settings-articles").value = data.settings.discovery_article_limit;
        settingsControls(false);
        el("settings-status").textContent = "Loaded Pro Ticker settings revision " + data.revision +
            (data.revision === 0 ? " (unmodified defaults; not strategy recommendations)." : ". Saved at " + date(data.updated_at) + ".") +
            " Trading and automatic releases remain disabled.";
    }
    async function loadSettings(force = false) {
        if (savingSettings || settingsDirty && !force) return;
        const current = ++settingsVersion; settingsControls(true);
        el("settings-status").textContent = "Loading Pro Ticker settings…";
        try {
            const response = await fetch("/auto-trader/pro-ticker/settings", {credentials:"same-origin",cache:"no-store"});
            if (!response.ok) throw Error("Settings request failed (" + response.status + ").");
            const data = await response.json();
            if (current === settingsVersion) showSettings(data);
        } catch (error) {
            if (current === settingsVersion) { settings = null; settingsControls(true); el("settings-status").textContent = "Settings unavailable: " + error.message; }
        }
    }
    el("settings-form").addEventListener("input", () => {
        settingsDirty = true; el("settings-status").textContent = "Unsaved changes. Other sessions may have changed the saved limits.";
    });
    el("settings-reload").addEventListener("click", () => loadSettings(true));
    el("settings-form").addEventListener("submit", async event => {
        event.preventDefault(); if (!settings || savingSettings) return;
        savingSettings = true; settingsVersion += 1; settingsControls(true); el("settings-reload").disabled = true;
        el("settings-status").textContent = "Saving Pro Ticker settings…";
        try {
            const response = await fetch("/auto-trader/pro-ticker/settings", {method:"PUT",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify({
                expected_revision:settings.revision, settings:{
                    max_allocation_cents:Math.round(Number(el("settings-allocation").value)*100),
                    max_stop_risk_cents:Math.round(Number(el("settings-risk").value)*100),
                    discovery_article_limit:Number(el("settings-articles").value)}})});
            if (!response.ok) throw Error(response.status === 409 ? "Saved settings changed in another session. Reload before saving again." : "Settings save failed (" + response.status + "). Reload to verify the saved state before retrying.");
            showSettings(await response.json());
            proposalVersion += 1;
            el("proposal-result").textContent = "Settings changed. Recalculate any hypothetical proposal against the saved limits.";
        } catch (error) {
            settings = null; settingsControls(true); el("settings-status").textContent = "Unable to save: " + error.message;
        } finally { savingSettings = false; el("settings-reload").disabled = false; }
    });
    el("refresh").addEventListener("click",()=> { refresh(); loadDiscovery(); loadSettings(); window.loadProTickerManualLab?.(); window.loadProTickerReleasePanel?.(); window.loadProTickerSignals?.(); });
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
                typeof result.risk_limits_satisfied !== "boolean" || !Array.isArray(result.risk_limit_reasons) ||
                result.risk_limit_reasons.some(reason => !["allocation_exceeds_pro_ticker_limit","estimated_stop_risk_exceeds_pro_ticker_limit"].includes(reason)) ||
                result.risk_limits_satisfied !== (result.risk_limit_reasons.length === 0) ||
                !Number.isSafeInteger(result.settings_revision) || result.settings_revision < 0 ||
                typeof result.within_virtual_budget !== "boolean" || typeof result.reason !== "string" ||
                ![result.allocation_cents,result.estimated_stop_risk_cents,result.available_virtual_cents].every(n=>Number.isSafeInteger(n)&&n>=0)) throw Error("Invalid proposal safety or amount data");
            el("proposal-result").textContent = "Hypothetical allocation: " + money(result.allocation_cents) +
                ". Stop: " + result.stop_price + ". Target: " + result.take_profit_price +
                ". Estimated stop risk: " + money(result.estimated_stop_risk_cents) +
                ". Available virtual funds: " + money(result.available_virtual_cents) +
                (result.within_virtual_budget ? ". Within virtual allocation limit. " : ". BLOCKED: exceeds virtual allocation limit. ") +
                (result.risk_limits_satisfied ? "Within Pro Ticker proposal limits. " : "BLOCKED by Pro Ticker proposal limits: " + result.risk_limit_reasons.map(reason => ({
                    allocation_exceeds_pro_ticker_limit:"allocation exceeds your saved maximum",
                    estimated_stop_risk_exceeds_pro_ticker_limit:"estimated stop risk exceeds your saved maximum"
                }[reason])).join("; ") + ". ") +
                "Settings revision: " + result.settings_revision + ". " +
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
    render(); window.loadProTickerWorkspace=() => { loadDiscovery(); loadSettings(); window.loadProTickerMarketScan?.(); window.loadProTickerExecutions?.(); return refresh(); };
})();

// Bounded market research scanning shares the existing Scanner tab.
(() => {
    const panel=document.getElementById("pt-panel-scanner");if(!panel)return;
    const box=document.createElement("article"),title=document.createElement("h3"),note=document.createElement("p"),form=document.createElement("form"),label=document.createElement("label"),input=document.createElement("input"),run=document.createElement("button"),discover=document.createElement("button"),refresh=document.createElement("button"),status=document.createElement("p"),filters=document.createElement("div"),search=document.createElement("input"),sort=document.createElement("select"),body=document.createElement("tbody"),table=document.createElement("table"),scroller=document.createElement("div");
    box.id="pt-market-scanner";title.textContent="Fresh market research scan";
    form.className="pt-market-form";filters.className="pt-market-filters";discover.className="pt-market-primary";
    const intro=document.createElement("p"),methodology=document.createElement("details"),methodologyTitle=document.createElement("summary");
    intro.textContent="Find US equity movers, compare volume and momentum, then send a candidate for grading. Research only; no orders are placed.";
    methodology.className="pt-methodology";methodologyTitle.textContent="Data sources, filters & limitations";methodology.append(methodologyTitle,note);
    note.textContent="Scan up to eight stocks, or leave blank to rotate through saved research/manual symbols. Uses Yahoo Finance minute closes; delayed or stale data is rejected. Relative volume is today's cumulative volume divided by 20 completed daily sessions, not time-of-day adjusted. Discover US market movers queries up to 250 Yahoo equities, filters price ≥ $0.25, average volume ≥ 50,000, movement ≥ 5% and volume ratio ≥ 1.5, then rotates eight fresh detailed checks. This is a bounded provider window, not every listed stock. Ranking is not an AI score and never places orders.";
    input.id="pt-market-symbols";input.maxLength=100;input.placeholder="AAPL, MSFT (optional)";label.append(document.createTextNode("Symbols to scan"),input);run.type="submit";run.textContent="Scan market candidates";refresh.type="button";refresh.textContent="Refresh saved market scan";discover.type="button";discover.textContent="Discover US market movers";form.append(label,run,discover,refresh);status.id="pt-market-status";status.setAttribute("role","status");
    search.type="search";search.placeholder="Filter scanned symbols";search.setAttribute("aria-label","Filter scanned symbols");sort.setAttribute("aria-label","Sort market candidates");for(const [value,text]of [["relative_volume","Relative volume"],["price_change_percent","Price movement"],["symbol","Symbol"]]){const option=document.createElement("option");option.value=value;option.textContent=text;sort.appendChild(option);}filters.append(search,sort);
    const head=document.createElement("thead"),row=document.createElement("tr");for(const text of ["Symbol","Minute close","Movement","Volume / average","Relative volume","Saved daily grade","Market time","Source / evidence"]){const th=document.createElement("th");th.textContent=text;row.appendChild(th);}head.appendChild(row);table.append(head,body);scroller.className="pt-table";scroller.appendChild(table);box.append(title,intro,form,status,filters,scroller,methodology);panel.prepend(box);
    let rows=[],grades={},busy=false,version=0,request=null;
    const endpoint="/auto-trader/pro-ticker/market-scan";
    function render(){
        body.replaceChildren();const filtered=rows.filter(r=>r.symbol.includes(search.value.trim().toUpperCase()));filtered.sort((a,b)=>sort.value==="symbol"?a.symbol.localeCompare(b.symbol):(Number(b[sort.value]??-Infinity)-Number(a[sort.value]??-Infinity))||a.symbol.localeCompare(b.symbol));
        for(const record of filtered){const tr=document.createElement("tr"),stamp=Date.parse(record.observed_at),stale=Date.now()-stamp>120000||stamp>Date.now();
            const price=new Intl.NumberFormat(undefined,{style:"currency",currency:"USD",minimumFractionDigits:Number(record.price)<1?4:2,maximumFractionDigits:4}).format(Number(record.price));
            const volume=value=>new Intl.NumberFormat(undefined,{maximumFractionDigits:0}).format(Number(value));
            const values=[record.symbol,price,record.price_change_percent===null?"Unavailable":(Number(record.price_change_percent)>0?"+":"")+Number(record.price_change_percent).toFixed(2)+"%",volume(record.volume)+" / "+volume(record.average_volume),Number(record.relative_volume).toFixed(2)+"×",grades[record.symbol]?grades[record.symbol].grade+" ("+grades[record.symbol].score+"/6), saved "+new Date(grades[record.symbol].recorded_at).toLocaleString()+"; not current intraday":"Unavailable (grade separately)",new Date(stamp).toLocaleString()+(stale?" · STALE":"")];
            for(const [index,value]of values.entries()){const td=document.createElement("td");td.textContent=String(value);if(index===0)td.className="pt-symbol";if(index===2&&record.price_change_percent!==null)td.className=Number(record.price_change_percent)>=0?"pt-positive":"pt-negative";if(index===6&&stale)td.className="pt-stale";tr.appendChild(td);}
            const evidence=document.createElement("td"),grade=document.createElement("button");grade.type="button";grade.textContent="Grade "+record.symbol;grade.addEventListener("click",()=>{const symbol=document.getElementById("pt-signal-symbol");document.getElementById("pt-tab-signals")?.click();if(symbol){symbol.value=record.symbol;symbol.focus();} });evidence.append(document.createTextNode((record.source==="yahoo_finance_minute_close"?"Yahoo Finance · minute close":record.source)+" · relative-volume ranking "),grade);tr.appendChild(evidence);body.appendChild(tr);
        }
    }
    function show(data){
        if(data.paper!==true||data.research_only!==true||data.order_execution_authorized!==false||data.automatic_scanning_enabled!==false||!["not_run","running","interrupted","completed","failed"].includes(data.status))throw Error("Invalid scan safety flags");
        rows=[];grades={};
        if(data.saved_daily_grades&&typeof data.saved_daily_grades==="object"){for(const [symbol,grade] of Object.entries(data.saved_daily_grades)){if(grade&&grade.kind==="saved_daily_technical_not_intraday"&&["A","B","C","D"].includes(grade.grade)&&Number.isInteger(grade.score)&&grade.score>=-6&&grade.score<=6&&Number.isFinite(Date.parse(grade.recorded_at)))grades[symbol]=grade;}}
        if(data.run){const result=data.run;if(!Array.isArray(result.symbols)||result.symbols.length>8)throw Error("Invalid scan symbols");
            if(data.status==="completed"){
                if(![result.requested,result.accepted,result.failed].every(n=>Number.isSafeInteger(n)&&n>=0)||result.accepted+result.failed!==result.requested||result.requested!==result.symbols.length||!Array.isArray(result.candidates)||result.candidates.length!==result.accepted||!Array.isArray(result.failures)||result.failures.length!==result.failed||result.research_only!==true||result.order_execution_authorized!==false)throw Error("Invalid scan counts");
                for(const record of result.candidates){if(!record||!result.symbols.includes(record.symbol)||!Number.isFinite(Number(record.price))||Number(record.price)<=0||!Number.isSafeInteger(record.volume)||record.volume<0||!Number.isFinite(Number(record.average_volume))||Number(record.average_volume)<=0||!Number.isFinite(Number(record.relative_volume))||record.research_score!==null||record.market_data_verified!==false||record.order_execution_authorized!==false||record.research_only!==true||!Number.isFinite(Date.parse(record.observed_at))||typeof record.source!=="string"||(record.price_change_percent!==null&&!Number.isFinite(Number(record.price_change_percent))))throw Error("Invalid candidate evidence");}
                if(new Set(result.candidates.map(r=>r.symbol)).size!==result.candidates.length)throw Error("Duplicate scan symbols");rows=result.candidates;
                status.textContent="Saved scan: "+new Date(result.finished_at).toLocaleString()+" · "+result.accepted+" accepted / "+result.requested+" requested; "+result.failed+" unavailable. "+(result.symbols.length?"Symbols: "+result.symbols.join(", "):result.universe_source==="yahoo_us_equity_movers"?"No eligible fresh movers in the returned provider window.":"No saved symbols yet; enter stocks or save research first.")+". "+(result.universe_source==="yahoo_us_equity_movers"?"Yahoo discovery: "+result.universe_count+" eligible symbols in this bounded screen; eight detailed checks per scan. ":"")+result.failures.map(f=>f.symbol+": "+f.reason).join("; ");
            }else status.textContent="Saved scan is "+data.status+". "+(typeof result.error==="string"?result.error+" ":"")+"Refresh status before retrying. No order was submitted.";
        }else status.textContent="No saved market scan. Enter stocks or scan saved research candidates.";
        render();
    }
    async function load(){const current=++version;try{const response=await fetch(endpoint,{credentials:"same-origin",cache:"no-store"});if(!response.ok)throw Error("History unavailable ("+response.status+")");const data=await response.json();if(current===version)show(data);}catch(error){if(current===version){rows=[];render();status.textContent="Market scan unavailable: "+error.message;}}}
    async function submitScan(discovery=false){if(busy)return;const symbols=!discovery&&input.value.trim()?Array.from(new Set(input.value.trim().toUpperCase().split(/[\s,]+/))):[];
        if(symbols.length>8||symbols.some(s=>!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(s))){status.textContent="Enter at most eight valid stock symbols.";return;}
        const signature=JSON.stringify({symbols,discovery});request=request?.signature===signature?request:{signature,id:crypto.randomUUID()};busy=true;run.disabled=true;refresh.disabled=true;discover.disabled=true;const current=++version;status.textContent="Scanning read-only market evidence…";
        try{const response=await fetch(endpoint,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify(discovery?{request_id:request.id,symbols:[],mode:"discover"}:{request_id:request.id,symbols})});if(!response.ok)throw Error("Scan unconfirmed ("+response.status+"). Refresh saved status; unchanged retry retains the request ID.");const data=await response.json();if(current===version)show(data);if(["completed","failed"].includes(data.status))request=null;}
        catch(error){status.textContent=error.message;}finally{busy=false;run.disabled=false;refresh.disabled=false;discover.disabled=false;}
    }
    form.addEventListener("submit",event=>{event.preventDefault();void submitScan(false);});discover.addEventListener("click",()=>submitScan(true));
    refresh.addEventListener("click",load);search.addEventListener("input",render);sort.addEventListener("change",render);document.getElementById("pt-refresh")?.addEventListener("click",load);window.loadProTickerMarketScan=load;load();
})();

(() => {
    const panel=document.getElementById("pt-panel-journal");if(!panel)return;
    const article=document.createElement("article"),title=document.createElement("h3"),status=document.createElement("p"),summary=document.createElement("p"),refresh=document.createElement("button"),download=document.createElement("button"),filter=document.createElement("input"),table=document.createElement("table"),body=document.createElement("tbody"),scroller=document.createElement("div");
    article.id="pt-priced-executions";title.textContent="Verified broker executions";status.setAttribute("role","status");refresh.type="button";refresh.textContent="Refresh priced execution journal";download.type="button";download.textContent="Export full execution history (CSV)";download.disabled=true;filter.type="search";filter.placeholder="Filter execution symbol";filter.setAttribute("aria-label","Filter execution symbol");
    const head=document.createElement("thead"),row=document.createElement("tr");for(const text of ["Executed at (broker)","Recorded at","Symbol","Side","Shares","Execution price","Broker order"]){const th=document.createElement("th");th.textContent=text;row.appendChild(th);}head.appendChild(row);table.append(head,body);scroller.className="pt-table";scroller.appendChild(table);article.append(title,status,summary,refresh,download,filter,scroller);panel.prepend(article);
    let rows=[],version=0,exporting=false;
    function render(){body.replaceChildren();for(const record of rows.filter(r=>r.symbol.includes(filter.value.trim().toUpperCase()))){const tr=document.createElement("tr");for(const value of [new Date(record.executed_at).toLocaleString(),new Date(record.recorded_at).toLocaleString(),record.symbol,record.side,record.shares,record.price,record.broker_order_id]){const td=document.createElement("td");td.textContent=String(value);tr.appendChild(td);}body.appendChild(tr);}}
    async function load(){const current=++version;download.disabled=true;try{
        const response=await fetch("/auto-trader/pro-ticker/execution-journal",{credentials:"same-origin",cache:"no-store"});if(!response.ok)throw Error("History unavailable ("+response.status+")");const data=await response.json();
        if(data.paper!==true||data.kind!=="owned_broker_fill_activities"||data.net_pnl!==null||data.fees!==null||data.model_updated!==false||!Number.isSafeInteger(data.total_count)||data.total_count<0||!Number.isSafeInteger(data.closed_priced_count)||data.closed_priced_count<0||!Array.isArray(data.rows)||data.rows.length>500||data.rows.length>data.total_count||data.has_more!==(data.total_count>data.rows.length)||(data.closed_priced_count?typeof data.gross_realized_usd!=="string"||!/^\-?\d+(\.\d+)?$/.test(data.gross_realized_usd):data.gross_realized_usd!==null))throw Error("Invalid execution coverage");
        if(![data.gross_wins,data.gross_losses,data.gross_flat].every(n=>Number.isSafeInteger(n)&&n>=0)||data.gross_wins+data.gross_losses+data.gross_flat!==data.closed_priced_count||(data.closed_priced_count?typeof data.gross_win_rate_percent!=="number"||!Number.isFinite(data.gross_win_rate_percent)||data.gross_win_rate_percent<0||data.gross_win_rate_percent>100:data.gross_win_rate_percent!==null))throw Error("Invalid gross outcome coverage");
        const numericString=value=>typeof value==='string'&&/^\-?\d+(\.\d+)?$/.test(value)&&Number.isFinite(Number(value));
        if(data.closed_priced_count){
            if(![data.gross_profit_usd,data.gross_loss_usd,data.average_gross_result_usd].every(numericString)||Number(data.gross_profit_usd)<0||Number(data.gross_loss_usd)<0||
               (Number(data.gross_loss_usd)>0? !numericString(data.gross_profit_factor)||Number(data.gross_profit_factor)<0:data.gross_profit_factor!==null))throw Error("Invalid execution gross metrics");
        }else if([data.gross_profit_usd,data.gross_loss_usd,data.gross_profit_factor,data.average_gross_result_usd].some(value=>value!==null))throw Error("Missing closed executions cannot imply metrics");
        if(data.rows.some(record=>!record||typeof record.activity_id!=="string"||typeof record.intent_id!=="string"||typeof record.broker_order_id!=="string"||typeof record.symbol!=="string"||!["buy","sell"].includes(record.side)||!Number.isSafeInteger(record.shares)||record.shares<=0||typeof record.price!=="string"||!Number.isFinite(Number(record.price))||Number(record.price)<=0||!Number.isFinite(Date.parse(record.executed_at))||!Number.isFinite(Date.parse(record.recorded_at))))throw Error("Invalid priced execution evidence");
        if(current!==version)return;rows=data.rows;render();download.disabled=exporting||!rows.length;
        status.textContent="Showing "+rows.length+" of "+data.total_count+" broker FILL activities. "+(data.has_more?"Latest 500 shown; full CSV exports all stored activities. ":"")+"Sync an owned intent from Manual Signals to retrieve execution evidence. Prices/times come from individual broker activities, separate from share-posting timestamps and average-price estimates.";
        summary.textContent=data.closed_priced_count?"Gross realized from complete owner-reconciled executions: $"+data.gross_realized_usd+" across "+data.closed_priced_count+" closed trades. Gross wins / losses / flat: "+data.gross_wins+" / "+data.gross_losses+" / "+data.gross_flat+"; gross win rate "+data.gross_win_rate_percent+"%. Gross profit / loss: $"+data.gross_profit_usd+" / $"+data.gross_loss_usd+"; gross profit factor: "+(data.gross_profit_factor===null?"Unavailable (no gross losses)":data.gross_profit_factor)+"; average gross result per closed trade: $"+data.average_gross_result_usd+". Budget return is separate. Fees, adjustments and net P&L remain unavailable without verified cost evidence.":"No complete owner-reconciled execution-priced outcomes. No zero-profit result is inferred. Fees and net P&L are unavailable.";
    }catch(error){if(current===version){rows=[];render();summary.textContent="";status.textContent="Priced execution journal unavailable: "+error.message;}}}
    download.addEventListener("click",async()=>{
        if(exporting||download.disabled)return;exporting=true;download.disabled=true;
        try{
            const response=await fetch("/auto-trader/pro-ticker/execution-journal.csv",{credentials:"same-origin",cache:"no-store"});
            const count=response.headers.get("X-Execution-Total-Count");
            if(!response.ok||!response.headers.get("content-type")?.startsWith("text/csv")||count===null||!/^\d+$/.test(count)||!Number.isSafeInteger(Number(count)))throw Error("Full export unavailable");
            const blob=await response.blob();if(!blob.size)throw Error("Empty export response");
            const url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download="pro-ticker-execution-history.csv";document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
            status.textContent="Downloaded full history: "+count+" stored broker FILL activities from one database snapshot. Fees, adjustments and net P&L remain unavailable.";
        }catch(error){status.textContent="Execution history export failed: "+error.message;}
        finally{exporting=false;download.disabled=!rows.length;}
    });refresh.addEventListener("click",load);filter.addEventListener("input",render);document.getElementById("pt-refresh")?.addEventListener("click",load);window.loadProTickerExecutions=load;load();
})();
(() => {
    const button = document.getElementById('pt-account-check');
    const status = document.getElementById('pt-account-status');
    if (!button || !status) return;
    button.addEventListener('click', async () => {
        button.disabled = true;
        status.textContent = 'Checking dedicated PAPER account…';
        try {
            const response = await fetch('/auto-trader/pro-ticker/account-status', {credentials: 'same-origin', cache: 'no-store'});
            if (!response.ok) throw Error('Connection could not be verified (' + response.status + ').');
            const data = await response.json();
            if (data.paper !== true || data.account_verified !== true || data.order_execution_authorized !== false) throw Error('Unexpected verification response.');
            status.textContent = 'Dedicated PAPER account identity verified. ' +
                (data.account_active && data.currency_usd && data.trading_unblocked ? 'Account is active, USD, and unblocked. ' : 'Account readiness needs review. ') +
                'No order was submitted. Checked ' + new Date().toLocaleTimeString() + '.';
        } catch (error) {
            status.textContent = error.message + ' No order was submitted.';
        } finally { button.disabled = false; }
    });
})();

(() => {
    const panel=document.getElementById('pt-panel-performance');if(!panel)return;
    const article=document.createElement('article'),title=document.createElement('h3'),note=document.createElement('p'),status=document.createElement('p'),refresh=document.createElement('button'),scroller=document.createElement('div'),table=document.createElement('table'),head=document.createElement('thead'),body=document.createElement('tbody');
    article.id='pt-execution-grade-outcomes';title.textContent='Execution outcomes by saved entry grade';
    note.textContent='Descriptive gross results from completely covered closed PAPER trades, grouped by the grade and version saved at allocation. This selected sample is not a prediction, an AI win probability or evidence of strategy improvement. Fees, regime and indicator timing remain unverified; model training stays disabled.';
    status.setAttribute('role','status');refresh.type='button';refresh.textContent='Refresh execution grade outcomes';scroller.className='pt-table';
    const header=document.createElement('tr');for(const label of ['Saved grade','Grading version','Closed trades','Gross wins / losses / flat','Gross result','Gross profit factor','Average gross result']){const th=document.createElement('th');th.textContent=label;header.appendChild(th);}head.appendChild(header);table.append(head,body);scroller.appendChild(table);article.append(title,note,status,refresh,scroller);panel.appendChild(article);
    let version=0;
    const count=value=>Number.isSafeInteger(value)&&value>=0;
    const numeric=value=>typeof value==='string'&&/^-?\d+(\.\d+)?$/.test(value)&&Number.isFinite(Number(value));
    async function load(){const current=++version;try{
        const response=await fetch('/auto-trader/pro-ticker/execution-journal',{credentials:'same-origin',cache:'no-store'});if(!response.ok)throw Error('Execution report unavailable ('+response.status+').');const data=await response.json();
        if(data.paper!==true||data.kind!=='owned_broker_fill_activities'||data.training_eligible!==false||data.model_updated!==false||data.net_pnl!==null||data.grade_source!=='saved_allocation_entry_grade'||!count(data.closed_priced_count)||!count(data.unattributed_closed_count)||!Array.isArray(data.entry_grade_groups))throw Error('Invalid grade outcome evidence.');
        const keys=new Set();let grouped=0;
        for(const row of data.entry_grade_groups){
            if(!row||!['A','B','C','D'].includes(row.grade)||typeof row.grade_version!=='string'||!/^[A-Za-z0-9_.-]{1,120}$/.test(row.grade_version)||![row.closed_count,row.gross_wins,row.gross_losses,row.gross_flat].every(count)||row.closed_count===0||row.gross_wins+row.gross_losses+row.gross_flat!==row.closed_count||![row.gross_realized_usd,row.gross_profit_usd,row.gross_loss_usd,row.average_gross_result_usd].every(numeric)||Number(row.gross_profit_usd)<0||Number(row.gross_loss_usd)<0||(Number(row.gross_loss_usd)>0?!numeric(row.gross_profit_factor)||Number(row.gross_profit_factor)<0:row.gross_profit_factor!==null))throw Error('Invalid grouped execution metrics.');
            const key=row.grade_version+':'+row.grade;if(keys.has(key))throw Error('Duplicate grade outcome group.');keys.add(key);grouped+=row.closed_count;
        }
        if(!Number.isSafeInteger(grouped)||grouped+data.unattributed_closed_count!==data.closed_priced_count)throw Error('Grade coverage denominator differs.');
        if(current!==version)return;body.replaceChildren();
        for(const row of data.entry_grade_groups){const tr=document.createElement('tr');for(const value of [row.grade,row.grade_version,row.closed_count,row.gross_wins+' / '+row.gross_losses+' / '+row.gross_flat,'$'+row.gross_realized_usd,row.gross_profit_factor===null?'Unavailable (no gross losses)':Number(row.gross_profit_factor).toFixed(2),'$'+Number(row.average_gross_result_usd).toFixed(2)]){const td=document.createElement('td');td.textContent=String(value);tr.appendChild(td);}body.appendChild(tr);}
        status.textContent=data.closed_priced_count?grouped+' of '+data.closed_priced_count+' completely covered closed trades attributed to a saved grade; '+data.unattributed_closed_count+' lack valid grade provenance. Open or incomplete trades are excluded.':'No completely covered closed execution outcomes. Grade performance is unavailable; no zero result is inferred.';
    }catch(error){if(current===version){body.replaceChildren();status.textContent='Execution grade outcomes unavailable: '+error.message;}}}
    refresh.addEventListener('click',load);document.getElementById('pt-refresh')?.addEventListener('click',load);load();
})();

(() => {
    const panel=document.getElementById('pt-panel-performance');if(!panel)return;
    const article=document.createElement('article'),title=document.createElement('h3'),form=document.createElement('form'),status=document.createElement('p'),summary=document.createElement('p'),results=document.createElement('div');
    article.id='pt-performance-analytics';title.textContent='Execution analytics & chronological evaluation';status.setAttribute('role','status');
    const fields={};
    for(const [key,label] of [['start','From (UTC)'],['end','Until, exclusive (UTC)'],['split_at','Evaluation split (UTC)']]){
        const wrapper=document.createElement('label'),input=document.createElement('input');wrapper.textContent=label+' ';input.type='datetime-local';input.name=key;input.step='1';fields[key]=input;wrapper.appendChild(input);form.appendChild(wrapper);
    }
    const refresh=document.createElement('button'),capture=document.createElement('button');refresh.type='submit';refresh.textContent='Refresh execution analytics';capture.type='button';capture.textContent='Save read-only PAPER equity observation';
    form.appendChild(refresh);article.append(title,form,capture,status,summary,results);panel.appendChild(article);
    let version=0,busy=false;
    const number=value=>typeof value==='string'&&/^-?\d+(\.\d+)?$/.test(value)&&Number.isFinite(Number(value));
    const count=value=>Number.isSafeInteger(value)&&value>=0;
    function table(headers,rows){const scroller=document.createElement('div'),table=document.createElement('table'),head=document.createElement('thead'),body=document.createElement('tbody'),header=document.createElement('tr');scroller.className='pt-table';
        for(const value of headers){const th=document.createElement('th');th.textContent=value;header.appendChild(th);}head.appendChild(header);
        for(const values of rows){const tr=document.createElement('tr');for(const value of values){const td=document.createElement('td');td.textContent=value==null?'Unavailable':String(value);tr.appendChild(td);}body.appendChild(tr);}table.append(head,body);scroller.appendChild(table);return scroller;
    }
    async function load(){const current=++version;status.textContent='Loading execution analytics…';summary.textContent='';results.replaceChildren();try{
        const query=new URLSearchParams();for(const [key,input] of Object.entries(fields)){if(input.value)query.set(key,input.value+'Z');}
        const response=await fetch('/auto-trader/pro-ticker/performance-analytics'+(query.size?'?'+query.toString():''),{credentials:'same-origin',cache:'no-store'});
        if(!response.ok)throw Error('Report unavailable ('+response.status+').');const data=await response.json(),evaluation=data.strategy_evaluation;
        if(data.version!=='pro_ticker_performance_v1'||data.paper!==true||data.order_execution_authorized!==false||data.model_updated!==false||data.automatic_strategy_changes!==false||data.net_pnl_usd!==null||data.fees_usd!==null||data.adjustments_usd!==null||!count(data.closed_count)||!count(data.incomplete_sync_count)||!Array.isArray(data.daily)||!Array.isArray(data.groups)||!Array.isArray(data.equity_observations)||!evaluation||evaluation.training_enabled!==false||evaluation.automatic_strategy_changes!==false||evaluation.model_updated!==false||evaluation.risk_limits_changed!==false||!count(evaluation.development?.samples)||!count(evaluation.holdout?.samples))throw Error('Invalid analytics evidence.');
        if((data.closed_count?!number(data.gross_realized_usd):data.gross_realized_usd!==null)||(data.observed_equity_drawdown_usd!==null&&!number(data.observed_equity_drawdown_usd)))throw Error('Invalid analytics totals.');
        if(data.daily.some(row=>!/^\d{4}-\d{2}-\d{2}$/.test(row.date)||![row.gross_realized_usd,row.cumulative_gross_realized_usd,row.gross_realized_drawdown_usd].every(number))||data.groups.some(row=>!count(row.samples)||typeof row.dimension!=='string'||typeof row.label!=='string'||!number(row.gross_realized_usd))||data.equity_observations.some(row=>!Number.isFinite(Date.parse(row.observed_at))||!number(row.equity_usd)||!number(row.cash_usd)||!Array.isArray(row.positions)||row.positions.some(p=>typeof p.symbol!=='string'||!count(p.shares)||![p.market_value_usd,p.cost_basis_usd,p.gross_unrealized_usd].every(number)||p.net_unrealized_usd!==null)))throw Error('Invalid analytics rows.');
        if(current!==version)return;
        summary.textContent=data.closed_count+' covered closed trades; '+data.incomplete_sync_count+' incomplete synced intents excluded. Gross realized: '+(data.gross_realized_usd===null?'Unavailable':'$'+data.gross_realized_usd)+'. Net P/L, fees and adjustments: unavailable without verified cost evidence. Returned allocations are not profits.';
        results.appendChild(table(['Date (New York)','Gross realized USD','Cumulative gross USD','Daily realized drawdown USD'],data.daily.map(r=>[r.date,r.gross_realized_usd,r.cumulative_gross_realized_usd,r.gross_realized_drawdown_usd])));
        results.appendChild(table(['Comparison','Saved label','Samples','Gross result USD'],data.groups.map(r=>[r.dimension,r.label,r.samples,r.gross_realized_usd])));
        results.appendChild(table(['Observation','Dedicated account equity USD','Cash USD','Owned open marks'],data.equity_observations.map(r=>[new Date(r.observed_at).toLocaleString(),r.equity_usd,r.cash_usd,r.positions.map(p=>p.symbol+': gross unrealized $'+p.gross_unrealized_usd).join('; ')||'No open holdings'])));
        const cohortRows=[];
        for(const [name,cohort] of [['Development',evaluation.development],['Holdout',evaluation.holdout]]){
            if(!Array.isArray(cohort.groups)||cohort.groups.some(g=>typeof g.dimension!=='string'||typeof g.label!=='string'||!count(g.samples)||!number(g.gross_realized_usd)))throw Error('Invalid chronological cohort evidence.');
            for(const group of cohort.groups)cohortRows.push([name,group.dimension,group.label,group.samples,group.gross_realized_usd]);
        }
        results.appendChild(table(['Cohort','Comparison','Saved label','Samples','Gross result USD'],cohortRows));
        status.textContent='Daily drawdown excludes open excursions. Broker equity observations are not adjusted for external cash flows. '+(evaluation.status==='unavailable_split_required'?'Choose a chronological split to inspect separate development and holdout cohorts.':'Retrospective split: '+evaluation.development.samples+' development / '+evaluation.holdout.samples+' holdout; '+evaluation.split_spanning_excluded+' spanning trades excluded. This is not prospective validation.')+' Strategy changes require separate approval; no retraining or risk changes occur.';
    }catch(error){if(current===version){summary.textContent='';results.replaceChildren();status.textContent='Execution analytics unavailable: '+error.message;}}}
    form.addEventListener('submit',event=>{event.preventDefault();void load();});
    capture.addEventListener('click',async()=>{if(busy)return;busy=true;capture.disabled=true;try{
        status.textContent='Reading the dedicated PAPER account and owned execution evidence…';
        const response=await fetch('/auto-trader/pro-ticker/performance-analytics/capture',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}'});
        if(!response.ok)throw Error('Observation unavailable ('+response.status+').');const data=await response.json();if(data.paper!==true||data.order_submitted!==false||data.budget_released!==false)throw Error('Invalid read-only observation.');await load();
    }catch(error){status.textContent='Equity observation not saved: '+error.message;}finally{busy=false;capture.disabled=false;}});
    const cashPanel=document.createElement('article'),cashTitle=document.createElement('h3'),cashStatus=document.createElement('p'),cashRows=document.createElement('div'),cashRead=document.createElement('button'),cashSync=document.createElement('button');
    cashPanel.id='pt-cash-evidence';cashTitle.textContent='Fees, rebates & cash adjustments';cashStatus.setAttribute('role','status');
    cashStatus.textContent='Not checked. Cash activity history is separate from trade profit. Final net P/L remains unavailable without complete cost evidence.';
    cashRead.type='button';cashRead.textContent='Refresh saved cash evidence';cashSync.type='button';cashSync.textContent='Sync read-only PAPER cash evidence';
    cashPanel.append(cashTitle,cashRead,cashSync,cashStatus,cashRows);panel.appendChild(cashPanel);let cashVersion=0,cashBusy=false;
    async function loadCash(){const current=++cashVersion;cashRows.replaceChildren();cashStatus.textContent='Reading saved cash evidence…';try{
        const response=await fetch('/auto-trader/pro-ticker/cash-evidence',{credentials:'same-origin',cache:'no-store'});if(!response.ok)throw Error('Cash report unavailable ('+response.status+').');const data=await response.json();
        if(data.version!=='pro_ticker_cash_evidence_v1'||data.scope!=='dedicated_account_cash_history_not_trade_pnl'||data.paper!==true||data.order_execution_authorized!==false||data.model_updated!==false||data.cost_coverage_final!==false||data.net_pnl_usd!==null||!count(data.total_count)||!count(data.unattributed_count)||!Array.isArray(data.rows)||data.rows.length!==data.total_count||data.unattributed_count>data.total_count||!Array.isArray(data.closed_trade_known_costs))throw Error('Invalid cash evidence.');
        if(data.rows.some(r=>typeof r.activity_id!=='string'||typeof r.activity_type!=='string'||!number(r.net_amount_usd)||!Number.isFinite(Date.parse(r.effective_date))||!['reported_order_cost','external_cash_flow','income','other_adjustment'].includes(r.category)||!['owned_order','unattributed'].includes(r.attribution_status))||data.closed_trade_known_costs.some(r=>typeof r.intent_id!=='string'||!number(r.gross_pnl_usd)||r.net_pnl_usd!==null||r.cost_coverage_final!==false||(r.known_linked_cost_cash_usd===null?r.gross_after_known_costs_usd!==null:!number(r.known_linked_cost_cash_usd)||!number(r.gross_after_known_costs_usd))))throw Error('Invalid cash activity rows.');
        if(current!==cashVersion)return;
        cashRows.appendChild(table(['Reported date','Type','Cash amount USD','Symbol','Classification','Attribution'],data.rows.map(r=>[r.effective_date,r.activity_type,r.net_amount_usd,r.symbol,r.category,r.attribution_status])));
        cashRows.appendChild(table(['Closed intent','Gross result USD','Known linked cost cash USD','Gross after known costs USD'],data.closed_trade_known_costs.map(r=>[r.intent_id,r.gross_pnl_usd,r.known_linked_cost_cash_usd,r.gross_after_known_costs_usd])));
        cashStatus.textContent=data.total_count+' saved account cash activities; '+data.unattributed_count+' have no verified owned-order link. Last observation: '+(data.last_observed_at?new Date(data.last_observed_at).toLocaleString():'Unavailable')+'. All-history cash evidence is separate from date-filtered trade results. Deposits, withdrawals, income and unlinked adjustments are not trade profit. Known linked costs are partial evidence; final net P/L remains unavailable.';
    }catch(error){if(current===cashVersion){cashRows.replaceChildren();cashStatus.textContent='Cash evidence unavailable: '+error.message;}}}
    cashRead.addEventListener('click',()=>{if(!cashBusy)return loadCash();});
    cashSync.addEventListener('click',async()=>{if(cashBusy)return;cashBusy=true;cashSync.disabled=true;cashRead.disabled=true;++cashVersion;cashRows.replaceChildren();try{
        cashStatus.textContent='Reading dedicated PAPER cash activity evidence; no orders or releases…';
        const response=await fetch('/auto-trader/pro-ticker/cash-evidence/sync',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}'});
        if(!response.ok)throw Error('Cash sync unavailable ('+response.status+').');const data=await response.json();
        if(data.version!=='pro_ticker_cash_evidence_v1'||data.paper!==true||data.order_submitted!==false||data.budget_released!==false||data.net_pnl_usd!==null||!count(data.observed_activity_count)||!count(data.new_activity_count)||data.new_activity_count>data.observed_activity_count)throw Error('Invalid cash sync response.');await loadCash();
    }catch(error){cashStatus.textContent='Cash sync could not be confirmed: '+error.message;}finally{cashBusy=false;cashSync.disabled=false;cashRead.disabled=false;}});
    document.getElementById('pt-refresh')?.addEventListener('click',load);void load();
})();
