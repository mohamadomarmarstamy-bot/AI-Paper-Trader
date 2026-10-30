"use strict";
(()=>{
 const get=id=>document.getElementById(id);
 let busy=false,version=0,loadedWindow=null;
 const money=v=>Number.isFinite(v)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(v):'—';
 const pct=v=>Number.isFinite(v)?v.toFixed(2)+'%':'—';
 const text=(tag,value)=>{const node=document.createElement(tag);node.textContent=value;return node;};
 function metricsTable(rows){
  const wrap=document.createElement('div');wrap.style.overflowX='auto';const table=document.createElement('table');table.className='stock-research-table';
  const caption=text('caption','Actual closed automatic stock trades · estimated net results');table.append(caption);
  const head=document.createElement('thead'),tr=document.createElement('tr');
  for(const label of ['Cohort','Trades','Known cost basis','Net P/L','Avg net / trade','Win rate','Profit factor','Evidence'])tr.append(text('th',label));
  head.append(tr);table.append(head);const body=document.createElement('tbody');
  for(const row of rows){const line=document.createElement('tr');for(const value of [row.label,row.trades,row.costed_trades,money(row.estimated_net_pnl_usd),money(row.expectancy_usd),pct(row.win_rate_percent),Number.isFinite(row.profit_factor)?row.profit_factor.toFixed(2):row.no_net_losses?'No losses in sample':'—',row.sample_note])line.append(text('td',String(value)));body.append(line);}
  table.append(body);wrap.append(table);return wrap;
 }
 function render(data){
  const perf=data.performance,s=perf.summary,box=get('stock-performance-summary');box.replaceChildren();
  for(const [label,value] of [['Closed trades',s.trades],['Broker P/L',money(s.gross_pnl_usd)],['Estimated net P/L',money(s.estimated_net_pnl_usd)],['Average net / trade',money(s.expectancy_usd)],['Net win rate',pct(s.win_rate_percent)]]){
   const card=document.createElement('div');card.className='stock-research-stat';card.append(text('span',label),text('strong',String(value)));box.append(card);
  }
  get('stock-performance-note').textContent=`${perf.basis} Current extra costs per side: ${perf.cost_model.stock_slippage_bps_per_side} bps slippage + ${perf.cost_model.stock_fee_bps_per_side} bps fees. Evidence coverage: setup ${perf.coverage.known_setup}/${s.trades}, entry time ${perf.coverage.known_time}/${s.trades}, float ${perf.coverage.known_float}/${s.trades}. ${perf.coverage.missing_exit_dates_excluded} records with unknown closing dates excluded from this date window.`;
  for(const key of ['setup','session','float']){const target=get('stock-cohort-'+key);target.replaceChildren();target.append(perf.groups[key].length?metricsTable(perf.groups[key]):text('p','No completed trades in this window. New evidence builds as automatic paper trades close.'));}
  get('stock-parallel-basis').textContent=data.parallel.basis;
  const health=data.parallel.runtime;get('stock-parallel-status').textContent=`Prospective comparison ${data.enabled?'enabled':'paused'} · minimum ${data.parallel.minimum_pairs} matured pairs across ${data.parallel.minimum_sessions} sessions. ${health?.last_error||(!health?'Waiting for the research worker.':'Last worker check: '+new Date(health.last_tick*1000).toLocaleString())}`;
  const trials=get('stock-parallel-trials');trials.replaceChildren();
  if(!data.parallel.trials.length)trials.append(text('p','Comparisons will initialize from the current saved stock settings. No historical results are invented.'));
  for(const trial of data.parallel.trials){
   const card=document.createElement('article');card.className='panel stock-trial';
   card.append(text('h3',`${trial.origin==='learner'?'Learned proposal':'Research challenger'} · ${trial.state.replaceAll('_',' ')}`));
   card.append(text('p',Object.entries(trial.changes).map(([key,value])=>key.replaceAll('_',' ')+': '+trial.baseline[key]+' → '+value).join('; ')));
   const stats=trial.stats;card.append(text('p',`${stats.matured_pairs} matured pairs · ${stats.open} awaiting quotes · ${stats.unavailable} unavailable · ${stats.sessions} sessions · ${stats.symbols} symbols · ${stats.different_decisions} differing decisions`));
   const compare=document.createElement('div');compare.className='stock-research-stats';
   for(const [label,value] of [['Current filter · simulated P/L',money(stats.baseline_shadow_pnl_usd)],['Candidate · simulated P/L',money(stats.candidate_shadow_pnl_usd)],['Current mean / opportunity',pct(stats.baseline_mean_percent)],['Candidate mean / opportunity',pct(stats.candidate_mean_percent)],['Candidate improvement',pct(stats.improvement_percent)]]){
    const stat=document.createElement('div');stat.className='stock-research-stat';stat.append(text('span',label),text('strong',value));compare.append(stat);
   }
   card.append(compare,text('p',`${stats.baseline_selected} opportunities selected by the current filter; ${stats.candidate_selected} by the candidate. Equal $${data.parallel.notional_usd} hypothetical allocation per accepted opportunity; rejected opportunities earn zero.`));
   if(trial.decision.reason)card.append(text('p',trial.decision.reason));
   if(Number.isFinite(trial.decision.lower_95_percent))card.append(text('p','Session-bootstrap lower bound of improvement: '+trial.decision.lower_95_percent.toFixed(3)+' percentage points. Exploratory evidence.'));
   trials.append(card);
  }
 }
 async function load(){if(busy)return;busy=true;const own=++version;const button=get('stock-research-refresh');if(button)button.disabled=true;get('stock-research-window').disabled=true;get('stock-research-load-status').textContent='Loading saved stock results…';const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{const response=await fetch('/auto-trader/stock-research?days='+get('stock-research-window').value,{credentials:'same-origin',cache:'no-store',signal:controller.signal});const data=await response.json();if(!response.ok||!data.success)throw Error(data.detail||'Stock research unavailable.');if(own!==version)return;render(data);loadedWindow=data.performance.window_days;get('stock-research-load-status').textContent='Saved results loaded · '+new Date().toLocaleTimeString();}
  catch(error){if(loadedWindow!==null&&loadedWindow!==undefined)get('stock-research-window').value=String(loadedWindow);get('stock-research-load-status').textContent=error.message+' Existing displayed results are preserved.';}
  finally{clearTimeout(timeout);busy=false;if(button)button.disabled=false;get('stock-research-window').disabled=false;}
 }
 document.addEventListener('DOMContentLoaded',()=>{
  get('stock-research-refresh')?.addEventListener('click',load);get('stock-research-window')?.addEventListener('change',load);
  setInterval(()=>{if(!document.hidden&&!get('strategy-section')?.hidden)load();},30000);
 });
 window.loadStockResearch=load;
})();
