(()=>{'use strict';const $=id=>document.getElementById(id),money=x=>Number.isFinite(x)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(x):'Unavailable';let busy=false;
const node=(tag,text)=>{const e=document.createElement(tag);e.textContent=text;return e;};
function render(d){$('start').hidden=d.started;$('start').disabled=busy||d.started;$('status').textContent=d.started?`${d.operational_health?.label||'Checking'} · ${d.error||d.operational_health?.detail||''} · last cycle ${d.last_cycle?new Date(d.last_cycle*1000).toLocaleString():'waiting'}`:'Ready to begin Jijo, Tech and Kadenox prospectively.';
$('basis').textContent=d.started?`Started ${new Date(d.trial_started_at*1000).toLocaleString()} · ${money(d.config.trade_usd)} per entry · ${d.config.stop_loss_percent}% stop trigger · equity ${money(d.equity_usd)}`:'Only trades observed after the trial begins are eligible.';
$('results').replaceChildren();for(const r of d.wallet_results||d.candidates||[]){const row=document.createElement('tr');for(const value of [r.name,r.active===false?'Paused · exits only':'Testing',money(r.realized_pnl_usd),r.completed_positions??'—',r.winning_completed_positions??'—',r.open_positions??'—'])row.append(node('td',value));$('results').append(row);}
for(const [id,rows]of [['holdings',d.positions||[]],['events',(d.events||[]).slice(0,20)]]){
 $(id).replaceChildren();if(!rows.length)$(id).append(node('li','No records yet.'));
 for(const r of rows){const item=node('li',''),label=node('span',r.symbol||r.mint);label.title=r.mint;window.MemeCoinLabels?.attach(label,r.mint,r.symbol);
  if(id==='holdings')item.append(label,node('span',` · ${money(r.mark_usd)} marked · ${money(r.cost_usd)} remaining cost`));
  else item.append(node('span',`${r.side} · ${r.status} · `),label,node('span',` · ${r.reason} · ${Number.isFinite(r.latency_seconds)?r.latency_seconds.toFixed(1)+'s delay':''}`));
  $(id).append(item);
 }
}
$('coverage').replaceChildren();const coverage=Object.entries(d.rpc_coverage||{});if(!coverage.length)$('coverage').append(node('li','No records yet.'));
for(const [w,c]of coverage)$('coverage').append(node('li',`${(d.traders||[]).find(t=>t.wallet===w)?.name||w}: ${c?.checked_at?new Date(c.checked_at*1000).toLocaleString():'awaiting first check'}${c?.error?' · '+c.error:''}`));}

async function request(start=false){if(busy)return;busy=true;$('start').disabled=true;const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);try{const r=await fetch('/meme-coins/copy/trials/'+(start?'start':'status'),{method:start?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:controller.signal,...(start?{headers:{'Content-Type':'application/json'},body:'{}'}:{})});const d=await r.json();if(!r.ok)throw Error(d.detail||'Trial unavailable');render(d);}catch(e){$('status').textContent='Trial status unavailable: '+e.message;}finally{clearTimeout(timer);busy=false;$('start').disabled=false;}}
$('start').onclick=()=>request(true);request();setInterval(()=>request(),10000);})();
