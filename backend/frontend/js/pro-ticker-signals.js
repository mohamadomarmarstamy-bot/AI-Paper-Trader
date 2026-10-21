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
    function validateIntent(intent,proposal){
        if(!intent||intent.paper!==true||intent.execution_enabled!==false||intent.order_submitted!==false||intent.automatic_release_enabled!==false||!["prepared","canceled"].includes(intent.status)||intent.budget_reserved!==(intent.status==="prepared")||typeof intent.id!=="string"||typeof intent.reservation_id!=="string"||!intent.plan||intent.plan.order_submitted!==false||intent.plan.order_execution_enabled!==false||!Number.isSafeInteger(intent.plan.allocation_cents)||intent.plan.allocation_cents<1||!Number.isSafeInteger(intent.plan.shares)||intent.plan.shares<1||typeof intent.plan.symbol!=="string")throw Error("Invalid PAPER allocation evidence");
        if(proposal&&(intent.proposal_id!==proposal.request_id||intent.plan.symbol!==proposal.symbol||intent.plan.shares!==proposal.shares||intent.plan.allocation_cents>proposal.allocation_cents))throw Error("Allocation does not match saved draft");
    }
    async function refreshIntents(){
        const current=++intentVersion;intentStatus.textContent="Loading saved virtual allocations…";
        try{
            const response=await fetch("/auto-trader/pro-ticker/order-intents",{credentials:"same-origin",cache:"no-store"});if(!response.ok)throw Error("History request failed ("+response.status+").");
            const data=await response.json();if(data.paper!==true||data.execution_enabled!==false||data.automatic_release_enabled!==false||!Array.isArray(data.intents)||!Number.isSafeInteger(data.total_count)||data.total_count<data.intents.length||typeof data.has_more!=="boolean")throw Error("Invalid allocation history");
            data.intents.forEach(intent=>validateIntent(intent));if(current!==intentVersion)return;intentRows.replaceChildren();
            for(const intent of data.intents){const tr=document.createElement("tr");for(const value of [date(intent.created_at),intent.plan.symbol,intent.plan.shares,"$"+(intent.plan.allocation_cents/100).toFixed(2),intent.status==="prepared"?"Virtual funds reserved":"Canceled; virtual funds returned","Disabled — no order"]){const td=document.createElement("td");td.textContent=String(value);tr.appendChild(td);}intentRows.appendChild(tr);}
            intentStatus.textContent=data.total_count?"Saved "+data.total_count+" allocation intent(s). "+(data.has_more?"Latest 200 displayed. ":"")+"Virtual funds returned are not trading profits. Inspect the stock's saved review in Manual Signals to manage its allocation.":"No saved allocation intents. Inspect a saved BUY draft in Manual Signals to explicitly reserve virtual funds. Broker submission remains disabled.";
        }catch(error){if(current===intentVersion){intentRows.replaceChildren();intentStatus.textContent="Allocation history unavailable: "+error.message;}}
    }
    function allocationControls(review,node,selected){
        const proposal=review.proposal,intent=review.order_intent;
        if(intent)validateIntent(intent,proposal);
        const status=document.createElement("p");status.setAttribute("role","status");node.appendChild(status);
        if(intent?.status==="canceled"){status.textContent="Allocation canceled. Returned virtual funds are not trading profit. This intent cannot be submitted or reserved again.";return;}
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
            ["Execution","No order placed. Broker admission, execution and model training are not connected."]]){
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
        void refreshIntents();
        el("status").textContent="Loading saved signal reviews…";
        try{
            const response=await fetch(endpoint,{credentials:"same-origin",cache:"no-store"});if(!response.ok)throw Error("History request failed ("+response.status+").");
            const data=await response.json();flags(data);
            if(!Array.isArray(data.signals)||!Number.isSafeInteger(data.total_count)||data.total_count<data.signals.length||typeof data.has_more!=="boolean")throw Error("Invalid review history");
            data.signals.forEach(record);if(current!==version)return false;
            el("history").replaceChildren();
            for(const review of data.signals){
                const row=document.createElement("tr"),grade=review.entry_grade,snapshot=review.market_snapshot;
                for(const value of [date(review.submitted_at),review.symbol,grade?.grade??"Unavailable",grade?.score??"Unavailable",snapshot?.status==="captured"?(snapshot.fresh?"Fresh at capture":"Stale at capture"):"Unavailable",review.status+" · no order"]){const td=document.createElement("td");td.textContent=String(value);row.appendChild(td);}
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
