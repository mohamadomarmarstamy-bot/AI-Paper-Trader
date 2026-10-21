"use strict";
(() => {
    const form=document.getElementById("pt-signal-form"); if(!form)return;
    const el=id=>document.getElementById("pt-signal-"+id), endpoint="/auto-trader/pro-ticker/manual-signals";
    let requestId=null,saving=false,version=0;
    const date=value=>value&&Number.isFinite(Date.parse(value))?new Date(value).toLocaleString():"Unavailable";
    function flags(data){if(!data||data.paper!==true||data.orders_submitted!==0||data.automatic_execution_enabled!==false)throw Error("Unexpected PAPER safety flags");}
    function record(value){
        if(!value||typeof value.symbol!=="string"||!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(value.symbol)||typeof value.request_id!=="string"||
            !["pending","graded","unavailable","interrupted"].includes(value.status))throw Error("Invalid saved review");
        const grade=value.entry_grade;
        if(grade&& (grade.version!=="pro_ticker_daily_technical_grade_v1"||grade.training_eligible!==false||
            !["graded","unavailable"].includes(grade.status)||grade.status==="graded"&&(!["A","B","C","D"].includes(grade.grade)||!Number.isFinite(grade.score)||grade.score< -6||grade.score>6)))throw Error("Invalid grade evidence");
        return value;
    }
    function detail(review){
        const node=el("detail");node.replaceChildren();
        const grade=review.entry_grade,snapshot=review.market_snapshot;
        for(const [label,value]of [["Symbol",review.symbol],["Submitted",date(review.submitted_at)],["Analysis completed",date(review.completed_at)],
            ["Provisional grade",grade?.grade],["Technical score",grade?.score],["Version",grade?.version],["Signal",grade?.signal],
            ["Heuristic confidence (not win probability)",grade?.confidence],["Entry quote",snapshot?.price],
            ["Quote market time",snapshot?.market_timestamp],["Quote fresh at capture",snapshot?.fresh===true?"Yes":"No / unavailable"],
            ["Indicators",grade?.indicators?JSON.stringify(grade.indicators):null],["Reasons",grade?.reasons?.join(" ")],
            ["Limitations",grade?.limitations?.join(" ")],["Execution","No order placed. Broker admission, execution and outcome learning are not connected."]]){
            const p=document.createElement("p"),b=document.createElement("strong");b.textContent=label+": ";p.append(b,document.createTextNode(value==null?"Unavailable":String(value)));node.appendChild(p);
        }
    }
    async function refresh(){
        const current=++version;
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
            return true;
        }catch(error){if(current===version){el("history").replaceChildren();el("detail").replaceChildren();el("status").textContent="Unable to load reviews: "+error.message;}return false;}
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
