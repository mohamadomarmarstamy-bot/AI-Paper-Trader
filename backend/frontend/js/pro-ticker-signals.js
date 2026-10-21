"use strict";
(() => {
    const form=document.getElementById("pt-signal-form"); if(!form)return;
    const el=id=>document.getElementById("pt-signal-"+id), endpoint="/auto-trader/pro-ticker/manual-signals";
    let requestId=null,saving=false,version=0,detailVersion=0;
    const date=value=>value&&Number.isFinite(Date.parse(value))?new Date(value).toLocaleString():"Unavailable";
    function flags(data){if(!data||data.paper!==true||data.orders_submitted!==0||data.automatic_execution_enabled!==false)throw Error("Unexpected PAPER safety flags");}
    function record(value){
        if(!value||typeof value.symbol!=="string"||!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(value.symbol)||typeof value.request_id!=="string"||
            !["pending","graded","unavailable","interrupted"].includes(value.status))throw Error("Invalid saved review");
        const grade=value.entry_grade;
        if(grade&& (grade.version!=="pro_ticker_daily_technical_grade_v1"||grade.training_eligible!==false||
            !["graded","unavailable"].includes(grade.status)||grade.status==="graded"&&(!["A","B","C","D"].includes(grade.grade)||!Number.isFinite(grade.score)||grade.score< -6||grade.score>6)))throw Error("Invalid grade evidence");
        const outcome=value.forward_outcome;
        if(outcome&&(outcome.kind!=="forward_quote_return_not_trade_pnl"||outcome.training_eligible!==false||!Number.isFinite(outcome.return_percent)||outcome.request_id!==value.request_id))throw Error("Invalid forward evidence");
        return value;
    }
    function detail(review){
        const selected=++detailVersion;
        const node=el("detail");node.replaceChildren();
        const grade=review.entry_grade,snapshot=review.market_snapshot;
        for(const [label,value]of [["Symbol",review.symbol],["Submitted",date(review.submitted_at)],["Analysis completed",date(review.completed_at)],
            ["Provisional grade",grade?.grade],["Technical score",grade?.score],["Version",grade?.version],["Signal",grade?.signal],
            ["Heuristic confidence (not win probability)",grade?.confidence],["Entry quote",snapshot?.price],
            ["Quote market time",snapshot?.market_timestamp],["Quote fresh at capture",snapshot?.fresh===true?"Yes":"No / unavailable"],
            ["Indicators",grade?.indicators?JSON.stringify(grade.indicators):null],["Reasons",grade?.reasons?.join(" ")],
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
        requestId=requestId||crypto.randomUUID();
        el("status").textContent="Capturing reference data and grading the stock. This does not submit a PAPER order…";
        try{
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
