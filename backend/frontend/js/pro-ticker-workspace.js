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
    render(); window.loadProTickerWorkspace=() => { loadDiscovery(); loadSettings(); return refresh(); };
})();

// Bounded market research scanning shares the existing Scanner tab.
(() => {
    const panel=document.getElementById("pt-panel-scanner");if(!panel)return;
    const box=document.createElement("article"),title=document.createElement("h3"),note=document.createElement("p"),form=document.createElement("form"),label=document.createElement("label"),input=document.createElement("input"),run=document.createElement("button"),refresh=document.createElement("button"),status=document.createElement("p"),filters=document.createElement("div"),search=document.createElement("input"),sort=document.createElement("select"),body=document.createElement("tbody"),table=document.createElement("table"),scroller=document.createElement("div");
    box.id="pt-market-scanner";title.textContent="Fresh market research scan";
    note.textContent="Scan up to eight stocks, or leave blank to rotate through saved research/manual symbols. Uses Yahoo Finance minute closes; delayed or stale data is rejected. Relative volume is today's cumulative volume divided by 20 completed daily sessions, not time-of-day adjusted. Ranking is not an AI score and never places orders.";
    input.id="pt-market-symbols";input.maxLength=100;input.placeholder="AAPL, MSFT (optional)";label.append(document.createTextNode("Symbols to scan"),input);run.type="submit";run.textContent="Scan market candidates";refresh.type="button";refresh.textContent="Refresh saved market scan";form.append(label,run,refresh);status.id="pt-market-status";status.setAttribute("role","status");
    search.type="search";search.placeholder="Filter scanned symbols";search.setAttribute("aria-label","Filter scanned symbols");sort.setAttribute("aria-label","Sort market candidates");for(const [value,text]of [["relative_volume","Relative volume"],["price_change_percent","Price movement"],["symbol","Symbol"]]){const option=document.createElement("option");option.value=value;option.textContent=text;sort.appendChild(option);}filters.append(search,sort);
    const head=document.createElement("thead"),row=document.createElement("tr");for(const text of ["Symbol","Minute close","Movement","Volume / average","Relative volume","Research score","Market time","Source / evidence"]){const th=document.createElement("th");th.textContent=text;row.appendChild(th);}head.appendChild(row);table.append(head,body);scroller.className="pt-table";scroller.appendChild(table);box.append(title,note,form,status,filters,scroller);panel.prepend(box);
    let rows=[],busy=false,version=0,request=null;
    const endpoint="/auto-trader/pro-ticker/market-scan";
    function render(){
        body.replaceChildren();const filtered=rows.filter(r=>r.symbol.includes(search.value.trim().toUpperCase()));filtered.sort((a,b)=>sort.value==="symbol"?a.symbol.localeCompare(b.symbol):(Number(b[sort.value]??-Infinity)-Number(a[sort.value]??-Infinity))||a.symbol.localeCompare(b.symbol));
        for(const record of filtered){const tr=document.createElement("tr"),stamp=Date.parse(record.observed_at),stale=Date.now()-stamp>120000||stamp>Date.now();
            for(const value of [record.symbol,record.price,record.price_change_percent===null?"Unavailable":Number(record.price_change_percent).toFixed(2)+"%",record.volume+" / "+record.average_volume,record.relative_volume,"Unavailable (grade separately)",new Date(stamp).toLocaleString()+(stale?" · STALE":"")]){const td=document.createElement("td");td.textContent=String(value);tr.appendChild(td);}
            const evidence=document.createElement("td"),grade=document.createElement("button");grade.type="button";grade.textContent="Grade "+record.symbol;grade.addEventListener("click",()=>{const symbol=document.getElementById("pt-signal-symbol");document.getElementById("pt-tab-signals")?.click();if(symbol){symbol.value=record.symbol;symbol.focus();} });evidence.append(document.createTextNode(record.source+" · relative-volume ranking "),grade);tr.appendChild(evidence);body.appendChild(tr);
        }
    }
    function show(data){
        if(data.paper!==true||data.research_only!==true||data.order_execution_authorized!==false||data.automatic_scanning_enabled!==false||!["not_run","running","interrupted","completed"].includes(data.status))throw Error("Invalid scan safety flags");
        rows=[];
        if(data.run){const result=data.run;if(!Array.isArray(result.symbols)||result.symbols.length>8)throw Error("Invalid scan symbols");
            if(data.status==="completed"){
                if(![result.requested,result.accepted,result.failed].every(n=>Number.isSafeInteger(n)&&n>=0)||result.accepted+result.failed!==result.requested||result.requested!==result.symbols.length||!Array.isArray(result.candidates)||result.candidates.length!==result.accepted||!Array.isArray(result.failures)||result.failures.length!==result.failed||result.research_only!==true||result.order_execution_authorized!==false)throw Error("Invalid scan counts");
                for(const record of result.candidates){if(!record||!result.symbols.includes(record.symbol)||!Number.isFinite(Number(record.price))||Number(record.price)<=0||!Number.isSafeInteger(record.volume)||record.volume<0||!Number.isFinite(Number(record.average_volume))||Number(record.average_volume)<=0||!Number.isFinite(Number(record.relative_volume))||record.research_score!==null||record.market_data_verified!==false||record.order_execution_authorized!==false||record.research_only!==true||!Number.isFinite(Date.parse(record.observed_at))||typeof record.source!=="string"||(record.price_change_percent!==null&&!Number.isFinite(Number(record.price_change_percent))))throw Error("Invalid candidate evidence");}
                if(new Set(result.candidates.map(r=>r.symbol)).size!==result.candidates.length)throw Error("Duplicate scan symbols");rows=result.candidates;
                status.textContent="Saved scan: "+new Date(result.finished_at).toLocaleString()+" · "+result.accepted+" accepted / "+result.requested+" requested; "+result.failed+" unavailable. "+(result.symbols.length?"Symbols: "+result.symbols.join(", "):"No saved symbols yet; enter stocks or save research first.")+". "+result.failures.map(f=>f.symbol+": "+f.reason).join("; ");
            }else status.textContent="Saved scan is "+data.status+". Refresh status before retrying. No order was submitted.";
        }else status.textContent="No saved market scan. Enter stocks or scan saved research candidates.";
        render();
    }
    async function load(){const current=++version;try{const response=await fetch(endpoint,{credentials:"same-origin",cache:"no-store"});if(!response.ok)throw Error("History unavailable ("+response.status+")");const data=await response.json();if(current===version)show(data);}catch(error){if(current===version){rows=[];render();status.textContent="Market scan unavailable: "+error.message;}}}
    form.addEventListener("submit",async event=>{event.preventDefault();if(busy)return;const symbols=input.value.trim()?Array.from(new Set(input.value.trim().toUpperCase().split(/[\s,]+/))):[];
        if(symbols.length>8||symbols.some(s=>!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(s))){status.textContent="Enter at most eight valid stock symbols.";return;}
        const signature=JSON.stringify(symbols);request=request?.signature===signature?request:{signature,id:crypto.randomUUID()};busy=true;run.disabled=true;refresh.disabled=true;const current=++version;status.textContent="Scanning read-only market evidence…";
        try{const response=await fetch(endpoint,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify({request_id:request.id,symbols})});if(!response.ok)throw Error("Scan unconfirmed ("+response.status+"). Refresh saved status; unchanged retry retains the request ID.");const data=await response.json();if(current===version)show(data);if(data.status==="completed")request=null;}
        catch(error){status.textContent=error.message;}finally{busy=false;run.disabled=false;refresh.disabled=false;}
    });
    refresh.addEventListener("click",load);search.addEventListener("input",render);sort.addEventListener("change",render);document.getElementById("pt-refresh")?.addEventListener("click",load);window.loadProTickerMarketScan=load;load();
})();
