"use strict";
(() => {
    const form=document.getElementById("pt-signal-form"); if(!form)return;
    const el=id=>document.getElementById("pt-signal-"+id), endpoint="/auto-trader/pro-ticker/manual-signals";
    let requestId=null,saving=false,version=0,detailVersion=0;
    const draftIntents=new Map();
    const allocationIds=new Map();
    let intentVersion=0;
    const intentHistory=document.createElement("article"),intentTitle=document.createElement("h3"),intentStatus=document.createElement("p"),intentRows=document.createElement("tbody");
    intentTitle.textContent="Saved PAPER allocation intents";intentStatus.setAttribute("role","status");
    const intentTable=document.createElement("table"),intentHead=document.createElement("thead"),intentHeader=document.createElement("tr"),intentScroller=document.createElement("div");
    for(const text of ["Saved","Symbol","Shares","Virtual allocation","State","Broker submission"]){const th=document.createElement("th");th.textContent=text;intentHeader.appendChild(th);}
    intentHead.appendChild(intentHeader);intentTable.append(intentHead,intentRows);intentScroller.style.overflowX="auto";intentScroller.appendChild(intentTable);intentHistory.id="pt-order-intents";intentHistory.append(intentTitle,intentStatus,intentScroller);document.getElementById("pt-panel-trading")?.appendChild(intentHistory);
    const researchPanel=document.createElement("article"),researchTitle=document.createElement("h3"),researchStatus=document.createElement("p"),exportButton=document.createElement("button");
    researchPanel.id="pt-research-export";researchTitle.textContent="Research evidence & training readiness";researchStatus.setAttribute("role","status");exportButton.type="button";exportButton.textContent="Export research evidence (CSV)";exportButton.disabled=true;researchPanel.append(researchTitle,researchStatus,exportButton);document.getElementById("pt-panel-performance")?.appendChild(researchPanel);
    let exporting=false;
    async function researchQuality(current){
        exportButton.disabled=true;researchStatus.textContent="Checking stored research evidence…";
        try{
            const response=await fetch(endpoint+"/dataset",{credentials:"same-origin",cache:"no-store"});if(!response.ok)throw Error("Dataset request failed ("+response.status+").");
            const data=await response.json();
            if(data.paper!==true||data.version!=="pro_ticker_research_evidence_v1"||data.kind!=="forward_quote_return_not_trade_pnl"||data.training_enabled!==false||data.model_updated!==false||data.training_ready_samples!==0||![data.total_count,data.window_count,data.graded_count,data.evaluated_count,data.unevaluated_count].every(n=>Number.isSafeInteger(n)&&n>=0)||data.window_count>data.total_count||data.graded_count>data.window_count||data.evaluated_count+data.unevaluated_count!==data.window_count||data.has_more!==(data.total_count>data.window_count))throw Error("Invalid research readiness evidence");
            if(current!==version)return;
            researchStatus.textContent="Export window: "+data.window_count+" of "+data.total_count+" reviews; "+data.graded_count+" graded, "+data.evaluated_count+" with a separate forward quote. Training-ready samples: 0. "+(data.has_more?"Latest 500 only; export is incomplete. ":"")+"Gains, losses and unevaluated reviews are retained. Entry features and future outcomes are separate. Indicator timing, actual executions, costs and out-of-sample evaluation remain unverified. Research returns are not trade P&L; no AI model is trained.";
            exportButton.disabled=exporting;
        }catch(error){if(current===version){researchStatus.textContent="Research export unavailable: "+error.message;exportButton.disabled=true;}}
    }
    exportButton.addEventListener("click",async()=>{
        if(exporting||exportButton.disabled)return;exporting=true;exportButton.disabled=true;
        try{
            const response=await fetch(endpoint+"/dataset?format=csv",{credentials:"same-origin",cache:"no-store"});
            if(!response.ok||!response.headers.get("content-type")?.startsWith("text/csv"))throw Error("CSV request failed");
            const number=name=>{const raw=response.headers.get(name);if(raw===null||!/^\d+$/.test(raw)||!Number.isSafeInteger(Number(raw)))throw Error("Missing export counts");return Number(raw);};
            const total=number("X-Research-Total-Count"),count=number("X-Research-Window-Count"),hasMore=response.headers.get("X-Research-Has-More");
            if(count>total||!["true","false"].includes(hasMore)||(hasMore==="true")!==(total>count))throw Error("Invalid export window");
            const url=URL.createObjectURL(await response.blob()),link=document.createElement("a");link.href=url;link.download="pro-ticker-research-evidence.csv";document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
            researchStatus.textContent="Downloaded "+count+" of "+total+" reviews. "+(hasMore==="true"?"Latest 500 only; incomplete export. ":"")+"Audit/research evidence only: training remains disabled and quote changes are not trade P&L.";
        }catch(error){researchStatus.textContent="Research download failed: "+error.message;}
        finally{exporting=false;exportButton.disabled=false;}
    });
    const executionPanel=document.createElement("article"),executionTitle=document.createElement("h3"),executionStatus=document.createElement("p");
    executionPanel.id="pt-execution-evidence";executionTitle.textContent="PAPER broker admission & recovery";executionStatus.setAttribute("role","status");executionPanel.append(executionTitle,executionStatus);document.getElementById("pt-panel-trading")?.appendChild(executionPanel);
    const monitorStatus=document.createElement("p");monitorStatus.id="pt-monitor-status";monitorStatus.setAttribute("role","status");executionPanel.appendChild(monitorStatus);
    async function loadMonitorStatus(){
        monitorStatus.textContent="Checking unattended PAPER monitoring…";
        try{
            const response=await fetch("/auto-trader/pro-ticker/monitor-status",{credentials:"same-origin",cache:"no-store"});
            if(!response.ok)throw Error("Unavailable");
            const data=await response.json();
            if(data.paper!==true||typeof data.enabled!=="boolean"||data.order_submitted!==false||data.budget_released!==false||!Array.isArray(data.observations)||data.observations.some(row=>typeof row.stale!=="boolean"||typeof row.protection_verified!=="boolean"||!Number.isFinite(row.checked_at)||!["pending","unavailable","needs_review","protected","closed"].includes(row.state)))throw Error("Invalid monitoring evidence");
            const attention=data.observations.filter(row=>row.stale||["unavailable","needs_review","pending"].includes(row.state)).length;
            monitorStatus.textContent="Unattended PAPER monitoring: "+(data.enabled?"enabled":"disabled")+". Latest observation window: "+data.observations.length+" intents; "+attention+" stale or requiring review. No automatic buying or budget release. Refresh to update observations.";
        }catch(error){monitorStatus.textContent="Unattended monitoring status unavailable. Refresh and inspect existing orders; protection is unverified.";}
    }
    let executionState=null,executionRequest=null;
    async function loadExecutionStatus(){
        if(executionRequest)return executionRequest;
        executionRequest=(async()=>{
            try{
                const response=await fetch("/auto-trader/pro-ticker/execution-status",{credentials:"same-origin",cache:"no-store"});if(!response.ok)throw Error("Status request failed ("+response.status+").");
                const data=await response.json();if(data.paper!==true||data.execution_enabled!==false||data.automatic_execution_enabled!==false||data.automatic_release_enabled!==false||data.account_verified!==false||typeof data.admission_review_enabled!=="boolean"||typeof data.recovery_enabled!=="boolean"||data.recovery_enabled!==data.admission_review_enabled)throw Error("Unexpected execution safety flags");
                executionState=data;executionStatus.textContent=data.owner_submission_enabled===true?"Owner-confirmed dedicated PAPER bracket submission is configured. Each order requires fresh server admission; automatic execution and automatic release are disabled.":data.admission_review_enabled?"Read-only dedicated PAPER evidence and existing-order lookup are configured. Account identity must still pass each server review. New order submission and automatic release remain disabled.":"Dedicated PAPER evidence gateway is not configured or approved. Existing trader credentials are not reused. Admission, recovery and new-order submission are unavailable here.";return data;
            }catch(error){executionState=null;executionStatus.textContent="PAPER evidence controls unavailable: "+error.message;return null;}
            finally{executionRequest=null;}
        })();return executionRequest;
    }
    function brokerEvidenceControls(review,node,selected){
        const intent=review.order_intent,box=document.createElement("article"),status=document.createElement("p"),admission=document.createElement("button"),recover=document.createElement("button");status.setAttribute("role","status");box.className="pt-broker-evidence";
        admission.type="button";admission.textContent="Review PAPER admission (no order)";admission.disabled=true;recover.type="button";recover.textContent="Find existing PAPER order (no new buy)";recover.disabled=true;
        box.append(status,admission,recover);node.appendChild(box);protectionEvidenceControls(intent,box,selected);submissionControls(review,box,selected);monitorControls(review,box,selected);managementControls(intent,box,selected);pricedExecutionControls(intent,box,selected);let busy=false,reviewId=null;
        const canReview=()=>intent.status==="prepared"&&!intent.attempt_started_at;
        const canRecover=()=>!!intent.attempt_started_at&&["submitting","uncertain","broker_linked"].includes(intent.status);
        const enabled=()=>{admission.disabled=busy||executionState?.admission_review_enabled!==true||!canReview();recover.disabled=busy||executionState?.recovery_enabled!==true||!canRecover();};
        loadExecutionStatus().then(state=>{if(detailVersion!==selected)return;status.textContent=state?.admission_review_enabled?"Review broker evidence for a prepared intent, or look up a recorded uncertain attempt. Neither action submits a new order or verifies protection.":"Broker evidence/recovery is unavailable until a dedicated PAPER gateway is approved and configured. No buy request can be sent.";enabled();});
        admission.addEventListener("click",async()=>{
            if(admission.disabled||busy)return;busy=true;enabled();
            try{
                reviewId=reviewId||crypto.randomUUID();status.textContent="Checking fresh dedicated PAPER evidence…";
                const response=await fetch("/auto-trader/pro-ticker/order-intents/"+encodeURIComponent(intent.id)+"/admission-review",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify({request_id:reviewId})});
                if(!response.ok)throw Error("Review could not be confirmed ("+response.status+"). Retry reuses its review ID.");
                const data=await response.json(),labels={dedicated_paper_account:"Dedicated PAPER account identity",account_active_and_unblocked:"Active, unblocked USD account",broker_buying_power:"Broker buying power",regular_market_open:"Fresh regular-market clock",active_tradable_equity:"Active tradable equity",broker_position_clear:"No conflicting broker position",broker_orders_clear:"No conflicting or truncated open-order snapshot",snapshot_fetch_fresh:"Broker snapshot fetch time",current_trade_fresh:"Fresh reference trade",entry_within_reserved_ceiling:"Entry within reserved amount",ledger_unchanged_during_review:"Unchanged allocation and settings",current_allocation_limit:"Current allocation limit",current_stop_risk_limit:"Current stop-risk limit",fresh_at_review_commit:"Evidence fresh when saved"};
                if(data.paper!==true||data.preview_only!==true||data.execution_enabled!==false||data.order_submitted!==false||data.automatic_release_enabled!==false||data.eligible_for_manual_submission!==false||data.intent_id!==intent.id||data.review_id!==reviewId||typeof data.expired!=="boolean"||typeof data.ledger_unchanged!=="boolean"||!Array.isArray(data.checks)||data.checks.length!==Object.keys(labels).length||new Set(data.checks.map(c=>c?.code)).size!==Object.keys(labels).length||data.checks.some(c=>!c||!labels[c.code]||typeof c.passed!=="boolean")||data.checks_passed!==data.checks.every(c=>c.passed)||!Array.isArray(data.submission_blockers)||!data.submission_blockers.includes("submission_not_enabled")||!data.submission_blockers.includes("protective_orders_not_verified"))throw Error("Invalid read-only admission evidence");
                if(detailVersion!==selected)return;const list=document.createElement("ul");for(const check of data.checks){const item=document.createElement("li");item.textContent=(check.passed?"Passed: ":"Blocked: ")+labels[check.code];list.appendChild(item);}status.replaceChildren(document.createTextNode((data.expired||!data.ledger_unchanged?"Review expired or allocation changed. ":data.checks_passed?"Read-only checks passed. ":"Read-only checks blocked. ")+"Submission remains disabled; protective orders are unverified."),list);reviewId=null;admission.textContent="Refresh PAPER admission review";
            }catch(error){status.textContent="PAPER admission unavailable: "+error.message;}
            finally{busy=false;enabled();}
        });
        recover.addEventListener("click",async()=>{
            if(recover.disabled||busy)return;busy=true;enabled();status.textContent="Looking up the saved client order ID. No new buy is sent…";
            try{
                const response=await fetch("/auto-trader/pro-ticker/order-intents/"+encodeURIComponent(intent.id)+"/recover",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:"{}"});
                if(!response.ok)throw Error("Lookup could not be confirmed ("+response.status+"). Refresh saved state before retrying.");
                const data=await response.json();if(data.paper!==true||typeof data.resolved!=="boolean"||data.order_resubmitted!==false||data.budget_released!==false||data.protection_verified!==false||data.intent?.id!==intent.id)throw Error("Invalid recovery evidence");validateIntent(data.intent,review.proposal);
                if(data.resolved?data.intent.status!=="broker_linked":data.intent.status!=="uncertain")throw Error("Inconsistent recovery result");
                await refresh();if(detailVersion===selected)detail({...review,order_intent:data.intent});await window.loadProTickerWorkspace?.();
                el("status").textContent=data.resolved?"Existing PAPER order recovered; no new buy sent. Protection still requires independent verification.":"No matching order found. Funds remain reserved; no new buy or budget release was authorized.";
            }catch(error){status.textContent="Recovery unavailable: "+error.message;}
            finally{busy=false;enabled();}
        });
    }
    function protectionEvidenceControls(intent,box,selected){
        const button=document.createElement("button"),status=document.createElement("p");
        button.type="button";button.textContent="Inspect existing PAPER protection";button.disabled=true;status.setAttribute("role","status");box.append(button,status);
        loadExecutionStatus().then(state=>{button.disabled=state?.protection_review_enabled!==true||intent.status!=="broker_linked";});
        let requestId=null,expiryTimer=null;
        button.addEventListener("click",async()=>{
            if(button.disabled)return;clearTimeout(expiryTimer);button.disabled=true;requestId=requestId||crypto.randomUUID();status.textContent="Independently checking existing exit orders…";
            try{
                const response=await fetch("/auto-trader/pro-ticker/order-intents/"+encodeURIComponent(intent.id)+"/protection-review",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify({request_id:requestId})});
                if(!response.ok)throw Error("Inspection unavailable ("+response.status+"). Retry retains the review ID.");
                const data=await response.json(),codes=["dedicated_paper_account","owned_bracket_entry","entry_fully_filled","entry_fills_accounted_without_exit","broker_position_matches","regular_market_open","two_independently_fetched_legs","leg_snapshots_match_parent","active_owned_sell_legs","stop_and_target_order_types","stop_and_target_within_plan","actual_entry_cost_and_stop_risk","fresh_trade_between_exit_levels","bounded_fetch_duration","ledger_unchanged_during_review","fresh_at_review_commit"];
                if(data.paper!==true||data.preview_only!==true||data.execution_enabled!==false||data.order_submitted!==false||data.budget_released!==false||data.loss_cap_guaranteed!==false||data.intent_id!==intent.id||data.review_id!==requestId||typeof data.expired!=="boolean"||typeof data.ledger_unchanged!=="boolean"||!Number.isFinite(data.expires_at)||!Array.isArray(data.checks)||data.checks.length!==codes.length||new Set(data.checks.map(c=>c?.code)).size!==codes.length||data.checks.some(c=>!codes.includes(c?.code)||typeof c.passed!=="boolean")||data.checks_passed!==data.checks.every(c=>c.passed)||data.protection_orders_verified!==(data.checks_passed&&!data.expired&&data.ledger_unchanged))throw Error("Invalid protection evidence");
                if(detailVersion!==selected)return;
                const list=document.createElement("ul");for(const check of data.checks){const item=document.createElement("li");item.textContent=(check.passed?"Passed: ":"Blocked: ")+check.code.replaceAll("_"," ");list.appendChild(item);}
                status.replaceChildren(document.createTextNode((data.protection_orders_verified?"Existing exit orders observed. ":"Protection could not be verified. ")+"No order was changed. Stops do not guarantee a loss cap; partial fills and after-hours protection are not certified."),list);
                const remaining=Math.max(0,data.expires_at*1000-Date.now());expiryTimer=setTimeout(()=>{if(detailVersion===selected)status.textContent="Protection observation expired. Inspect again for current evidence; no order was changed.";},Math.min(remaining,30000));requestId=null;
            }catch(error){if(detailVersion===selected)status.textContent=error.message;}
            finally{button.disabled=executionState?.protection_review_enabled!==true||intent.status!=="broker_linked";}
        });
    }
    function submissionControls(review,box,selected){
        const intent=review.order_intent,label=document.createElement("label"),confirmed=document.createElement("input"),button=document.createElement("button"),status=document.createElement("p");
        confirmed.type="checkbox";label.append(confirmed,document.createTextNode(" I authorize this dedicated PAPER limit bracket. Partial entry fills may be unprotected until the entire entry fills. Stops do not guarantee a loss cap; extended hours are excluded."));
        button.type="button";button.textContent="Submit approved PAPER bracket";button.disabled=true;status.setAttribute("role","status");box.append(label,button,status);
        let busy=false,attempted=false;
        const enabled=()=>{button.disabled=busy||attempted||!confirmed.checked||executionState?.owner_submission_enabled!==true||intent.status!=="prepared"||!!intent.attempt_started_at;};
        confirmed.addEventListener("change",enabled);loadExecutionStatus().then(enabled);
        button.addEventListener("click",async()=>{
            if(button.disabled)return;busy=true;attempted=true;enabled();status.textContent="Submitting the approved PAPER intent. Do not create another buy if confirmation is interrupted…";
            try{
                const response=await fetch("/auto-trader/pro-ticker/order-intents/"+encodeURIComponent(intent.id)+"/submit",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify({confirmed:true})});
                if(!response.ok)throw Error("Confirmation unavailable ("+response.status+"). Refresh saved state and use existing-order recovery.");
                const data=await response.json();if(data.paper!==true||typeof data.resolved!=="boolean"||typeof data.dispatch_attempted!=="boolean"||data.order_resubmitted!==false||data.budget_released!==false||data.protection_verified!==false||data.intent?.id!==intent.id)throw Error("Invalid PAPER confirmation; refresh saved state and recover the existing order.");validateIntent(data.intent,review.proposal);
                await refresh();if(detailVersion===selected)detail({...review,order_intent:data.intent});await window.loadProTickerWorkspace?.();
                el("status").textContent=data.resolved?"Owned PAPER order linked. Reconcile fills and independently inspect protection. No automatic budget release.":"PAPER submission is uncertain. Funds held; recover the saved order without another buy.";
            }catch(error){if(detailVersion===selected)status.textContent=error.message;await refreshIntents();}
            finally{busy=false;confirmed.checked=false;enabled();}
        });
    }
    function monitorControls(review,box,selected){
        const intent=review.order_intent,button=document.createElement("button"),status=document.createElement("p");button.type="button";button.textContent="Reconcile PAPER position & exits";button.disabled=true;status.setAttribute("role","status");box.append(button,status);
        let busy=false;const enabled=()=>{button.disabled=busy||executionState?.admission_review_enabled!==true||!intent.attempt_started_at||!["broker_linked","submitting","uncertain"].includes(intent.status);};loadExecutionStatus().then(enabled);
        button.addEventListener("click",async()=>{
            if(button.disabled)return;busy=true;enabled();status.textContent="Verifying owned entry and independently fetching bracket exits…";
            try{
                const response=await fetch("/auto-trader/pro-ticker/order-intents/"+encodeURIComponent(intent.id)+"/monitor",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:"{}"});if(!response.ok)throw Error("Reconciliation unavailable ("+response.status+"). Funds stay held; refresh and retry existing-order reads.");
                const data=await response.json();if(data.paper!==true||data.order_submitted!==false||data.budget_released!==false||data.automatic_release_enabled!==false||typeof data.resolved!=="boolean"||typeof data.closed!=="boolean"||typeof data.protection_verified!=="boolean"||data.intent?.id!==intent.id||!Array.isArray(data.legs)||data.legs.length>3||data.legs.some(leg=>!leg||typeof leg.id!=="string"||typeof leg.status!=="string"||!Number.isSafeInteger(leg.shares)||leg.shares<1||!Number.isSafeInteger(leg.filled_shares)||leg.filled_shares<0||leg.filled_shares>leg.shares))throw Error("Invalid reconciliation evidence");validateIntent(data.intent,review.proposal);
                await window.loadProTickerWorkspace?.();await refreshIntents();if(detailVersion!==selected)return;
                status.textContent=(data.closed?"Closure verified for this snapshot. Owner release approval is still required.":data.protection_verified?"Existing bracket protection observed for this regular-session snapshot.":"Position refreshed; protection is not certified for this snapshot.")+" No order placed or funds released. "+data.legs.map(leg=>leg.status+" "+leg.filled_shares+" / "+leg.shares+" shares").join("; ");
            }catch(error){if(detailVersion===selected)status.textContent=error.message;}
            finally{busy=false;enabled();}
        });
    }
    function managementControls(intent,box,selected){
        const panel=document.createElement("article"),title=document.createElement("h4"),note=document.createElement("p"),status=document.createElement("p"),cancelLabel=document.createElement("label"),cancelCheck=document.createElement("input"),closeLabel=document.createElement("label"),closeCheck=document.createElement("input"),cancel=document.createElement("button"),close=document.createElement("button"),inspect=document.createElement("button");
        title.textContent="Manage owned PAPER orders";
        note.textContent="Canceling an entry leaves already-filled shares open. Closing cancels existing exits before one regular-session market sell. Pending cancellation is not confirmation. Partial or rejected closes need review; virtual funds remain held.";
        cancelCheck.type=closeCheck.type="checkbox";cancelLabel.append(cancelCheck,document.createTextNode(" I approve canceling this intent's pending PAPER entry."));closeLabel.append(closeCheck,document.createTextNode(" I approve canceling this intent's pending orders and closing its remaining owned PAPER shares."));
        cancel.type=close.type=inspect.type="button";cancel.textContent="Cancel pending PAPER entry";close.textContent="Close owned PAPER position / resume";inspect.textContent="Inspect saved PAPER management";status.setAttribute("role","status");
        panel.append(title,note,cancelLabel,cancel,closeLabel,close,inspect,status);box.appendChild(panel);let busy=false;
        const enabled=()=>{const active=!!intent.attempt_started_at&&["broker_linked","submitting","uncertain"].includes(intent.status);cancel.disabled=busy||!active||executionState?.owner_management_enabled!==true||!cancelCheck.checked;close.disabled=busy||!active||executionState?.owner_management_enabled!==true||!closeCheck.checked;inspect.disabled=busy||!active||executionState?.recovery_enabled!==true;};
        cancelCheck.addEventListener("change",enabled);closeCheck.addEventListener("change",enabled);loadExecutionStatus().then(enabled);enabled();
        async function act(action){
            if(busy||(action==="close"?close.disabled:action==="cancel_entry"?cancel.disabled:inspect.disabled))return;
            busy=true;enabled();status.textContent=action?"Verifying owned orders and recording the approved PAPER action…":"Inspecting saved broker state without sending a new order or cancellation…";
            try{
                const path="/auto-trader/pro-ticker/order-intents/"+encodeURIComponent(intent.id)+(action?"/manage":"/manage-recover");
                const response=await fetch(path,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify(action?{action,confirmed:true}:{})});
                if(!response.ok)throw Error("Management unconfirmed ("+response.status+"). Inspect saved management before trying to continue.");
                const data=await response.json();
                if(data.paper!==true||data.budget_released!==false||data.automatic_release_enabled!==false||data.intent_id!==intent.id||typeof data.position_closed!=="boolean"||typeof data.entry_cancellation_confirmed!=="boolean"||typeof data.close_dispatch_attempted!=="boolean"||!Number.isSafeInteger(data.remaining_shares)||data.remaining_shares<0||typeof data.state!=="string")throw Error("Invalid management evidence; inspect existing broker orders.");
                if(detailVersion!==selected)return;
                status.textContent="Broker state: "+data.state+". Entry cancellation "+(data.entry_cancellation_confirmed?"confirmed":"not confirmed")+"; remaining owned shares: "+data.remaining_shares+". "+(data.position_closed?"Position closure verified; separate release approval is still required.":"Position is not confirmed closed. Inspect or explicitly resume the saved action.");
                await window.loadProTickerWorkspace?.();await refreshIntents();
            }catch(error){if(detailVersion===selected)status.textContent=error.message;}
            finally{busy=false;cancelCheck.checked=closeCheck.checked=false;enabled();}
        }
        cancel.addEventListener("click",()=>act("cancel_entry"));close.addEventListener("click",()=>act("close"));inspect.addEventListener("click",()=>act(null));
    }
    function pricedExecutionControls(intent,box,selected){
        const button=document.createElement("button"),status=document.createElement("p");button.type="button";button.textContent="Sync priced PAPER executions";button.disabled=true;status.setAttribute("role","status");box.append(button,status);let busy=false;
        const enabled=()=>{button.disabled=busy||executionState?.admission_review_enabled!==true||!["broker_linked","closed"].includes(intent.status);};loadExecutionStatus().then(enabled);
        button.addEventListener("click",async()=>{if(button.disabled)return;busy=true;enabled();status.textContent="Checking owned broker execution activities…";
            try{const response=await fetch("/auto-trader/pro-ticker/order-intents/"+encodeURIComponent(intent.id)+"/execution-sync",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:"{}"});if(!response.ok)throw Error("Execution sync unavailable ("+response.status+"). Reconcile fills and retry; no order was sent.");const data=await response.json();if(data.paper!==true||data.order_submitted!==false||data.budget_released!==false||data.coverage_complete!==true||data.net_pnl!==null||data.intent_id!==intent.id||!Number.isSafeInteger(data.execution_count)||data.execution_count<0)throw Error("Invalid execution sync evidence");await window.loadProTickerExecutions?.();if(detailVersion===selected)status.textContent="Saved "+data.execution_count+" owned broker FILL activities. View verified prices/times in Trade Journal. No order, share posting or release occurred.";
            }catch(error){if(detailVersion===selected)status.textContent=error.message;}finally{busy=false;enabled();}
        });
    }
    function validateIntent(intent,proposal){
        if(!intent||intent.paper!==true||intent.execution_enabled!==false||intent.automatic_release_enabled!==false||!["prepared","canceled","broker_linked","closed","broker_terminal_unfilled","submitting","uncertain"].includes(intent.status)||intent.budget_reserved!==(["prepared","broker_linked","submitting","uncertain"].includes(intent.status))||intent.order_submitted!==(["submitting","uncertain"].includes(intent.status)?null:["broker_linked","closed","broker_terminal_unfilled"].includes(intent.status))||typeof intent.id!=="string"||typeof intent.reservation_id!=="string"||!intent.plan||intent.plan.order_submitted!==false||intent.plan.order_execution_enabled!==false||!Number.isSafeInteger(intent.plan.allocation_cents)||intent.plan.allocation_cents<1||!Number.isSafeInteger(intent.plan.shares)||intent.plan.shares<1||typeof intent.plan.symbol!=="string")throw Error("Invalid PAPER allocation evidence");
        if(intent.audit_events!==undefined&&(!Array.isArray(intent.audit_events)||intent.audit_events.some(event=>!event||!["virtual_budget_reserved","owner_canceled_never_submitted","broker_lookup_unresolved","broker_order_recovered","owner_paper_submission_started"].includes(event.action)||typeof event.actor!=="string"||!Number.isFinite(Date.parse(event.recorded_at)))))throw Error("Invalid allocation audit history");
        if(proposal&&(intent.proposal_id!==proposal.request_id||intent.plan.symbol!==proposal.symbol||intent.plan.shares!==proposal.shares||intent.plan.allocation_cents>proposal.allocation_cents))throw Error("Allocation does not match saved draft");
    }
    async function refreshIntents(){
        const current=++intentVersion;intentStatus.textContent="Loading saved virtual allocations…";
        try{
            const response=await fetch("/auto-trader/pro-ticker/order-intents",{credentials:"same-origin",cache:"no-store"});if(!response.ok)throw Error("History request failed ("+response.status+").");
            const data=await response.json();if(data.paper!==true||data.execution_enabled!==false||data.automatic_release_enabled!==false||!Array.isArray(data.intents)||!Number.isSafeInteger(data.total_count)||data.total_count<data.intents.length||typeof data.has_more!=="boolean")throw Error("Invalid allocation history");
            data.intents.forEach(intent=>validateIntent(intent));if(current!==intentVersion)return;intentRows.replaceChildren();
            for(const intent of data.intents){const tr=document.createElement("tr");for(const value of [date(intent.created_at),intent.plan.symbol,intent.plan.shares,"$"+(intent.plan.allocation_cents/100).toFixed(2),({prepared:"Virtual funds reserved",canceled:"Canceled; virtual funds returned",broker_linked:"Broker order linked; reconcile fills",closed:"Closed; owner-approved funds returned",broker_terminal_unfilled:"Broker terminal; no entry fill",submitting:"Submission uncertain; funds held",uncertain:"Submission uncertain; funds held"})[intent.status],intent.order_submitted===null?"Uncertain — lookup only":intent.order_submitted?"Broker order linked; submission disabled here":"Disabled — no order"]){const td=document.createElement("td");td.textContent=String(value);tr.appendChild(td);}intentRows.appendChild(tr);}
            intentStatus.textContent=data.total_count?"Saved "+data.total_count+" allocation intent(s). "+(data.has_more?"Latest 200 displayed. ":"")+"Virtual funds returned are not trading profits. Inspect the stock's saved review in Manual Signals to manage its allocation.":"No saved allocation intents. Inspect a saved BUY draft in Manual Signals to explicitly reserve virtual funds. Broker submission remains disabled.";
        }catch(error){if(current===intentVersion){intentRows.replaceChildren();intentStatus.textContent="Allocation history unavailable: "+error.message;}}
    }
    function allocationControls(review,node,selected){
        const proposal=review.proposal,intent=review.order_intent;
        if(intent)validateIntent(intent,proposal);
        if(intent?.audit_events){
            const audit=document.createElement("details"),summary=document.createElement("summary"),list=document.createElement("ul");summary.textContent="Allocation audit history";audit.append(summary,list);
            if(!Array.isArray(intent.audit_events))throw Error("Invalid allocation audit history");
            for(const event of intent.audit_events){const labels={virtual_budget_reserved:"Owner reserved virtual PAPER funds",owner_canceled_never_submitted:"Owner canceled an unsubmitted allocation; virtual funds returned (not profit)",broker_lookup_unresolved:"Broker lookup unresolved; funds held, no order resubmitted",broker_order_recovered:"Existing broker order recovered by client ID; no new order",owner_paper_submission_started:"Owner approved PAPER dispatch; recover this intent if confirmation is interrupted"};if(!event||!labels[event.action]||typeof event.actor!=="string"||!Number.isFinite(Date.parse(event.recorded_at)))throw Error("Invalid allocation audit event");const item=document.createElement("li");item.textContent=date(event.recorded_at)+" · "+labels[event.action]+" · "+event.actor;list.appendChild(item);}
            node.appendChild(audit);
        }

        if(intent)brokerEvidenceControls(review,node,selected);
        const status=document.createElement("p");status.setAttribute("role","status");node.appendChild(status);
        if(intent?.status==="canceled"){status.textContent="Allocation canceled. Returned virtual funds are not trading profit. This intent cannot be submitted or reserved again.";return;}
        if(intent&&intent.status!=="prepared"){status.textContent="Allocation state: "+intent.status.replaceAll("_"," ")+". Broker order ownership is recorded in the ledger. Use independent reconciliation and the existing owner release review; this is not a never-submitted cancellation. Returned virtual funds are not trading profit.";return;}
        const cancel=intent?.status==="prepared",label=document.createElement("label"),confirm=document.createElement("input"),button=document.createElement("button");confirm.type="checkbox";confirm.style.width="auto";
        label.append(confirm,document.createTextNode(cancel?" I approve canceling this never-submitted allocation and returning its virtual funds.":" I approve reserving up to $"+(proposal.allocation_cents/100).toFixed(2)+" of virtual PAPER funds. This does not place an order."));
        button.type="button";button.disabled=true;button.textContent=cancel?"Cancel unsubmitted allocation":"Reserve virtual funds (no order)";
        let busy=false;confirm.addEventListener("change",()=>{button.disabled=busy||!confirm.checked;});
        status.textContent=cancel?"Virtual funds reserved: $"+(intent.plan.allocation_cents/100).toFixed(2)+". Broker submission remains disabled.":"No funds reserved. Allocation uses a fresh server quote and cannot exceed this draft's saved cost/risk ceilings.";
        button.addEventListener("click",async()=>{
            if(busy||!confirm.checked)return;busy=true;button.disabled=true;confirm.disabled=true;
            try{
                let payload,url="/auto-trader/pro-ticker/order-intents";
                if(cancel){url+="/"+encodeURIComponent(intent.id)+"/cancel";payload={confirmed:true};}
                else{let id=allocationIds.get(proposal.request_id);if(!id){id=crypto.randomUUID();allocationIds.set(proposal.request_id,id);}payload={request_id:id,proposal_id:proposal.request_id,confirmed:true};}
                status.textContent=cancel?"Verifying this allocation was never submitted…":"Reserving virtual funds atomically…";
                const response=await fetch(url,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
                if(!response.ok)throw Error("Request could not be confirmed ("+response.status+"). Refresh saved state before retrying.");
                const data=await response.json();if(data.paper!==true)throw Error("Invalid PAPER response");validateIntent(data.intent,proposal);
                const loaded=await refresh();if(detailVersion===selected)detail({...review,order_intent:data.intent});
                await window.loadProTickerWorkspace?.();
                if(!loaded)el("status").textContent="Allocation update confirmed, but review refresh failed. Refresh saved state before further actions.";
            }catch(error){status.textContent="Allocation update unconfirmed: "+error.message;busy=false;confirm.disabled=false;button.disabled=!confirm.checked;}
        });
        node.append(label,button);
    }

    const date=value=>value&&Number.isFinite(Date.parse(value))?new Date(value).toLocaleString():"Unavailable";
    function flags(data){if(!data||data.paper!==true||data.orders_submitted!==0||data.automatic_execution_enabled!==false)throw Error("Unexpected PAPER safety flags");}
    function validateDraft(proposal,review){
        if(proposal?.paper!==true||proposal.signal_id!==review.request_id||proposal.symbol!==review.symbol||proposal.budget_reserved!==false||proposal.order_submitted!==false||proposal.execution_authorized!==false||proposal.status!=="research_draft"||!Number.isSafeInteger(proposal.shares)||proposal.shares<1||!Number.isSafeInteger(proposal.allocation_cents)||proposal.allocation_cents<1)throw Error("Invalid saved draft safety flags");
    }
    function record(value){
        if(!value||typeof value.symbol!=="string"||!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(value.symbol)||typeof value.request_id!=="string"||
            !["pending","graded","unavailable","interrupted"].includes(value.status))throw Error("Invalid saved review");
        const grade=value.entry_grade;
        if(grade&& (grade.version!=="pro_ticker_daily_technical_grade_v1"||grade.training_eligible!==false||
            !["graded","unavailable"].includes(grade.status)||grade.status==="graded"&&(!["A","B","C","D"].includes(grade.grade)||!Number.isFinite(grade.score)||grade.score< -6||grade.score>6)))throw Error("Invalid grade evidence");
        const outcome=value.forward_outcome;
        if(outcome&&(outcome.kind!=="forward_quote_return_not_trade_pnl"||outcome.training_eligible!==false||!Number.isFinite(outcome.return_percent)||outcome.request_id!==value.request_id))throw Error("Invalid forward evidence");
        if(value.proposal)validateDraft(value.proposal,value);
        if(value.order_intent)validateIntent(value.order_intent,value.proposal);
        return value;
    }
    function detail(review){
        const selected=++detailVersion;
        const node=el("detail");node.replaceChildren();
        const grade=review.entry_grade,snapshot=review.market_snapshot;
        for(const [label,value]of [["Symbol",review.symbol],["Submitted",date(review.submitted_at)],["Analysis completed",date(review.completed_at)],
            ["Provisional grade",grade?.grade],["Technical score",grade?.score],["Version",grade?.version=== "pro_ticker_daily_technical_grade_v1"?"Daily technical grade v1":null],["Signal",grade?.signal],
            ["Heuristic confidence (not win probability)",grade?.confidence],["Submission reference quote",snapshot?.price],
            ["Quote market time",snapshot?.market_timestamp],["Quote fresh at capture",snapshot?.fresh===true?"Yes":"No / unavailable"],
            ["20-day moving average",grade?.indicators?.sma_20],["50-day moving average",grade?.indicators?.sma_50],
            ["RSI",grade?.indicators?.rsi],["Five-day momentum %",grade?.indicators?.five_day_momentum],
            ["Volume relative to 20-day average",grade?.indicators?.volume_ratio],["Reasons",grade?.reasons?.join(" ")],
            ["Limitations",grade?.limitations?.join(" ")],
            ["Saved forward quote change (not trade profit)",review.forward_outcome?String(review.forward_outcome.return_percent)+"%":"Not evaluated"],
            ["Execution",review.order_intent?.order_submitted===null?"Submission outcome is uncertain. Do not create another buy; reserved funds require independent recovery.":review.order_intent?.order_submitted?"A broker order is linked in the ledger. Inspect fills and protection; model training remains disabled.":"No order placed. Dedicated PAPER execution requires separate configuration and owner approval. Model training is disabled."]]){
            const p=document.createElement("p"),b=document.createElement("strong");b.textContent=label+": ";p.append(b,document.createTextNode(value==null?"Unavailable":String(value)));node.appendChild(p);
        }
        const check=document.createElement("button");check.type="button";check.textContent="Check 24+ hour forward quote";
        check.disabled=review.status!=="graded"||snapshot?.fresh!==true;
        check.addEventListener("click",async()=>{
            check.disabled=true;el("status").textContent="Checking a separate forward research quote for "+review.symbol+"…";
            try{
                const response=await fetch(endpoint+"/"+encodeURIComponent(review.request_id)+"/forward-review",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:"{}"});
                if(!response.ok)throw Error(response.status===409?"Wait until a fresh market quote is at least 24 hours after grading completed.":"Forward quote could not be verified ("+response.status+").");
                const data=await response.json(),outcome=data.outcome;
                if(data.paper!==true||data.model_updated!==false||outcome?.kind!=="forward_quote_return_not_trade_pnl"||outcome.training_eligible!==false||!Number.isFinite(outcome.return_percent))throw Error("Invalid forward research safety flags");
                const loaded=await refresh();if(detailVersion===selected)detail({...review,forward_outcome:outcome});
                el("status").textContent="Saved forward research for "+review.symbol+". This is not trade profit and did not update an AI model."+(loaded?"":" History refresh failed; the confirmed outcome is shown below.");
            }catch(error){el("status").textContent="Forward research unavailable: "+error.message;}
            finally{check.disabled=false;}
        });
        node.appendChild(check);
        const draftStatus=document.createElement("p");draftStatus.setAttribute("role","status");
        function showDraft(proposal){
            validateDraft(proposal,review);
            draftStatus.textContent="Saved PAPER research draft: "+proposal.shares+" shares; estimated allocation $"+(proposal.allocation_cents/100).toFixed(2)+" at creation. Stop: "+String(proposal.stop_price??"Unavailable")+"; target: "+String(proposal.take_profit_price??"Unavailable")+". Saved: "+date(proposal.created_at)+". The draft itself does not reserve funds or place an order. This saved quote and risk estimate must be revalidated before execution.";
        }
        if(review.proposal){showDraft(review.proposal);node.appendChild(draftStatus);allocationControls(review,node,selected);return;}
        if(review.status!=="graded"||grade?.signal!=="BUY")return;
        const draftForm=document.createElement("form"),inputs={};
        const heading=document.createElement("h4");heading.textContent="Save a PAPER trade draft";draftForm.appendChild(heading);
        const explanation=document.createElement("p");explanation.textContent="Choose your own size and risk assumptions. A fresh server quote is required. One immutable draft per review; no buying power is reserved and no order is placed.";draftForm.appendChild(explanation);
        for(const [key,label,maximum]of [["shares","Whole shares",100000],["stop_loss_percent","Stop-loss %",99],["take_profit_percent","Take-profit %",99]]){
            const wrapper=document.createElement("label"),input=document.createElement("input");wrapper.textContent=label+" ";input.name=key;input.type="number";input.min=key==="shares"?"1":"0.0001";input.max=String(maximum);input.step=key==="shares"?"1":"any";input.required=true;inputs[key]=input;wrapper.appendChild(input);draftForm.appendChild(wrapper);
        }
        const save=document.createElement("button");save.type="submit";save.textContent="Save PAPER draft (no order)";draftForm.appendChild(save);
        draftForm.addEventListener("submit",async event=>{
            event.preventDefault();if(save.disabled)return;save.disabled=true;Object.values(inputs).forEach(input=>input.disabled=true);
            try{
                const sizing={shares:Number(inputs.shares.value),stop_loss_percent:inputs.stop_loss_percent.value,take_profit_percent:inputs.take_profit_percent.value};
                const signature=JSON.stringify(sizing);let intent=draftIntents.get(review.request_id);
                if(!intent||intent.signature!==signature){intent={signature,request_id:crypto.randomUUID()};draftIntents.set(review.request_id,intent);}
                draftStatus.textContent="Saving a draft with a fresh server quote…";
                const response=await fetch(endpoint+"/proposal",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify({...sizing,request_id:intent.request_id,signal_id:review.request_id})});
                if(!response.ok)throw Error(response.status===409?"Draft already exists. Refresh saved reviews before retrying.":"Draft could not be verified ("+response.status+"). Refresh saved reviews before retrying.");
                const data=await response.json();if(data.paper!==true)throw Error("Invalid PAPER draft response");showDraft(data.proposal);
                save.textContent="PAPER draft saved";await refresh();if(detailVersion===selected)detail({...review,proposal:data.proposal});return;
            }catch(error){draftStatus.textContent="Unable to confirm draft: "+error.message;save.disabled=false;Object.values(inputs).forEach(input=>input.disabled=false);}
        });
        node.append(draftForm,draftStatus);
    }
    async function learning(current){
        const rows=el("learning-rows"),status=el("learning");if(!rows||!status)return;
        try{
            const response=await fetch(endpoint+"/learning",{credentials:"same-origin",cache:"no-store"});if(!response.ok)throw Error("Summary request failed");
            const data=await response.json();
            if(data.paper!==true||data.kind!=="forward_quote_return_not_trade_pnl"||data.model_updated!==false||data.training_enabled!==false||data.training_ready_samples!==0||!Array.isArray(data.groups)||
                ![data.review_count,data.window_count,data.evaluated_count].every(n=>Number.isSafeInteger(n)&&n>=0)||data.review_count<data.window_count||data.evaluated_count>data.window_count)throw Error("Invalid research summary");
            if(data.groups.some(g=>!g||!["A","B","C","D"].includes(g.grade)||typeof g.version!=="string"||![g.samples,g.positive,g.negative,g.flat].every(n=>Number.isSafeInteger(n)&&n>=0)||g.samples!==g.positive+g.negative+g.flat||!Number.isFinite(g.mean_forward_return_percent)))throw Error("Invalid summary groups");
            if(current!==version)return;rows.replaceChildren();
            for(const group of data.groups){const row=document.createElement("tr");for(const value of [group.grade,group.version,group.samples,group.positive,group.negative,group.mean_forward_return_percent.toFixed(2)]){const td=document.createElement("td");td.textContent=String(value);row.appendChild(td);}rows.appendChild(row);}
            status.textContent="Descriptive 24+ hour forward quote research. Evaluated "+data.evaluated_count+" of "+data.window_count+" reviews in this window. Gains and losses are retained; not realized P&L. No model training or automatic policy changes. "+(data.has_more?"Latest 500 reviews only. ":"")+String(data.reason||"");
        }catch(error){if(current===version){rows.replaceChildren();status.textContent="Forward research summary unavailable. No model training is enabled.";}}
    }
    async function refresh(){
        const current=++version;
        void loadExecutionStatus();
        void loadMonitorStatus();
        void refreshIntents();
        void researchQuality(current);
        el("status").textContent="Loading saved signal reviews…";
        try{
            const response=await fetch(endpoint,{credentials:"same-origin",cache:"no-store"});if(!response.ok)throw Error("History request failed ("+response.status+").");
            const data=await response.json();flags(data);
            if(!Array.isArray(data.signals)||!Number.isSafeInteger(data.total_count)||data.total_count<data.signals.length||typeof data.has_more!=="boolean")throw Error("Invalid review history");
            data.signals.forEach(record);if(current!==version)return false;
            el("history").replaceChildren();
            for(const review of data.signals){
                const row=document.createElement("tr"),grade=review.entry_grade,snapshot=review.market_snapshot;
                for(const value of [date(review.submitted_at),review.symbol,grade?.grade??"Unavailable",grade?.score??"Unavailable",snapshot?.status==="captured"?(snapshot.fresh?"Fresh at capture":"Stale at capture"):"Unavailable",review.status+(review.order_intent?.order_submitted===null?" · submission uncertain":review.order_intent?.order_submitted?" · broker order linked":" · no order")]){const td=document.createElement("td");td.textContent=String(value);row.appendChild(td);}
                const td=document.createElement("td"),button=document.createElement("button");button.type="button";button.textContent="Inspect "+review.symbol+" review";button.addEventListener("click",()=>detail(review));td.appendChild(button);row.appendChild(td);el("history").appendChild(row);
            }
            el("status").textContent=data.total_count?"Loaded "+data.signals.length+" saved review(s). "+(data.has_more?"Latest 100 displayed. ":"")+"No automatic PAPER orders or model updates.":"No saved signal reviews yet.";
            await learning(current);
            return true;
        }catch(error){if(current===version){el("history").replaceChildren();el("detail").replaceChildren();if(el("learning-rows"))el("learning-rows").replaceChildren();if(el("learning"))el("learning").textContent="Forward research unavailable while saved reviews cannot be loaded. No training is enabled.";el("status").textContent="Unable to load reviews: "+error.message;}return false;}
    }
    el("symbol").addEventListener("input",()=>{requestId=null;});
    form.addEventListener("submit",async event=>{
        event.preventDefault();if(saving)return;saving=true;version++;el("submit").disabled=true;el("symbol").disabled=true;
        el("status").textContent="Capturing reference data and grading the stock. This does not submit a PAPER order…";
        try{
            requestId=requestId||crypto.randomUUID();
            const response=await fetch(endpoint,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify({symbol:el("symbol").value.trim().toUpperCase(),request_id:requestId})});
            if(!response.ok)throw Error("Review request failed ("+response.status+"). Refresh saved status before retrying.");
            const data=await response.json();flags(data);const review=record(data.signal);
            const loaded=await refresh();detail(review);el("status").textContent="Review "+review.status+" for "+review.symbol+". No PAPER order placed; no model training performed."+(loaded?"":" History refresh failed; the confirmed review is shown below.");
        }catch(error){el("status").textContent="Unable to confirm review: "+error.message+" Retrying unchanged input reuses the same request ID.";}
        finally{saving=false;el("submit").disabled=false;el("symbol").disabled=false;}
    });
    el("refresh").addEventListener("click",refresh);
    el("new").addEventListener("click",()=>{if(saving)return;requestId=null;el("symbol").value="";el("symbol").focus();el("status").textContent="Enter a stock for a new timestamped review.";});
    window.loadProTickerSignals=refresh;
})();
