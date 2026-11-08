(() => {
 const $=id=>document.getElementById(id),node=(tag,text,cls)=>{const el=document.createElement(tag);el.textContent=text??'';if(cls)el.className=cls;return el;};
 let offset=0,receivedAt=0,busy=false,generation=0,total=0;
 const sol=n=>Number.isFinite(n)?`${(n/1e9).toLocaleString('en-US',{maximumFractionDigits:6})} SOL`:'Unavailable';
 const seconds=n=>Number.isFinite(n)?`${(n/1000).toFixed(3)}s`:'—';
 const percent=n=>Number.isFinite(n)?`${n>=0?'+':''}${n.toFixed(2)}%`:'—';
 const price=n=>Number.isFinite(n)&&n>0?n.toPrecision(7):'—';
 const short=s=>s?`${s.slice(0,6)}…${s.slice(-5)}`:'Unattributed';
 const signed=(cell,value)=>{if(Number.isFinite(value))cell.dataset.sign=value<0?'negative':value>0?'positive':'neutral';return cell;};
 function table(id,rows,empty){const target=$(id);target.replaceChildren();if(!rows.length){const tr=node('tr',''),td=node('td',empty);td.colSpan=12;tr.append(td);target.append(tr);return;}for(const cells of rows){const tr=node('tr','');for(const value of cells){const td=node('td','');if(value instanceof Node)td.append(value);else td.textContent=value;tr.append(td);}target.append(tr);}}
 function timing(rows,side,key){return rows.find(r=>r.side===side&&r.key===key)||{count:0,median:null,p90:null};}
 function sample(value,total){return `${value.count}/${total} recorded`;}
 function render(data){
  total=data.entry_count;
  $('rc-report-time').textContent=`${data.wallet?short(data.wallet):'All recorded wallets'} · updated ${new Date(data.checked_at*1000).toLocaleTimeString()} · ${data.order_count} orders · ${data.pending_orders} pending`;
  const pnl=$('rc-report-pnl');pnl.textContent=data.accounting_complete?sol(data.ledger_realized_lamports):'Needs review';pnl.dataset.sign=data.accounting_complete?(data.ledger_realized_lamports<0?'negative':'positive'):'unknown';
  $('rc-report-ledger').textContent=data.accounting_complete?'Realized only · excludes open holdings':`Ledger: ${sol(data.ledger_realized_lamports)} · allocated: ${sol(data.allocated_realized_lamports)}`;
  $('rc-report-fees').textContent=sol(data.failed_fees_lamports);
  $('rc-report-fee-count').textContent=data.unknown_fee_records?`${data.unknown_fee_records} failed orders have unknown fees`:'Known paid fees · rejected orders excluded';
  $('rc-report-premium').textContent=percent(data.entry_premium.median);
  $('rc-report-premium-count').textContent=`Median · ${data.entry_premium.count}/${data.entry_count} buys comparable`;
  const speed=timing(data.timings,'buy','source_to_broadcast_ms');$('rc-report-speed').textContent=seconds(speed.median);$('rc-report-speed-count').textContent=`Median · ${speed.count} settled buys with timing`;
  $('rc-report-warning').textContent=data.accounting_complete?'':`Accounting incomplete. ${Number.isFinite(data.ledger_difference_lamports)?`Ledger minus allocated results: ${sol(data.ledger_difference_lamports)}. `:''}Unallocated sales, unknown fees or external wallet changes need review.`;
  $('rc-report-warning').hidden=data.accounting_complete;
  $('rc-report-basis').textContent=data.basis;$('rc-report-price-basis').textContent=data.price_basis;$('rc-report-comparison-basis').textContent=data.comparison_basis;
  table('rc-report-wallets',data.wallets.map(w=>[
   node('span',w.name||short(w.wallet),'rc-report-name'),w.confirmed_buys,w.completed_positions,
   Number.isFinite(w.win_rate_percent)?`${w.win_rate_percent.toFixed(1)}%`:'—',w.open_positions,
   signed(node('span',sol(w.net_realized_lamports)),w.net_realized_lamports),sol(w.failed_fees_lamports),
   `${w.failed_orders} / ${w.rejected_orders}`,percent(w.entry_premium.median),
   w.unallocated_sales||w.unknown_fee_records?`${w.unallocated_sales} sales / ${w.unknown_fee_records} fees unknown`:'Allocated'
  ]),'No copied real trades recorded.');
  table('rc-report-cohorts',data.cohorts.map(c=>{
   const buy=timing(c.timings,'buy','source_to_broadcast_ms'),sell=timing(c.timings,'sell','trigger_to_broadcast_ms');
   return [c.label,c.confirmed_buys,`${seconds(buy.median)} · n=${buy.count}`,`${seconds(sell.median)} · n=${sell.count}`,`${percent(c.entry_premium.median)} · n=${c.entry_premium.count}`];
  }),'Waiting for measured trades.');
  table('rc-report-timings',data.timings.filter(r=>r.count).map(r=>[r.side.toUpperCase(),r.label,seconds(r.median),seconds(r.p90),sample(r,r.eligible_orders)]),'No settled orders have timing data yet.');
  table('rc-report-entries',data.entries.map(e=>{
   const coin=node('span',short(e.mint),'rc-report-name');if(e.mint){coin.title=e.mint;window.MemeCoinLabels?.attach(coin,e.mint);}
   const link=node('a','Receipt ↗','rc-link');if(e.signature){link.href='https://solscan.io/tx/'+encodeURIComponent(e.signature);link.target='_blank';link.rel='noopener';}else link.textContent='—';
   return [Number.isFinite(e.created_at)?new Date(e.created_at*1000).toLocaleString():'—',coin,e.source_name,price(e.source_sol_per_token),price(e.our_sol_per_token),percent(e.premium_percent),seconds(e.source_to_broadcast_ms),link];
  }),'No confirmed real buys.');
  $('rc-report-page').textContent=data.entry_count?`${data.offset+1}–${Math.min(data.offset+data.limit,data.entry_count)} of ${data.entry_count} buys`:'0 buys';
 }
 function controls(){if(!$('rc-performance'))return;$('rc-report-refresh').disabled=busy;$('rc-report-prev').disabled=busy||offset===0;$('rc-report-next').disabled=busy||offset+20>=total;}
 async function load(force=false){
  const panel=$('rc-performance');if(!panel?.open||busy||!force&&performance.now()-receivedAt<30000)return;
  busy=true;controls();$('rc-report-status').textContent='Loading trade report…';const ticket=++generation,controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);
  try{
   const response=await fetch(`/real-copy/performance?offset=${offset}&limit=20`,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
   if(!response.ok)throw Error('Trade report unavailable');const data=await response.json();if(ticket!==generation)return;
   render(data);receivedAt=performance.now();$('rc-report-status').textContent='';
  }catch{if(ticket===generation){receivedAt=0;$('rc-report-status').textContent='Report unavailable. Displayed results may be old. Retry to refresh.';}}
  finally{clearTimeout(timer);busy=false;controls();}
 }
 document.addEventListener('DOMContentLoaded',()=>{
  if(!$('rc-performance'))return;
  $('rc-performance').addEventListener('toggle',()=>{if($('rc-performance').open)void load(true);});
  $('rc-report-refresh').onclick=()=>void load(true);
  $('rc-report-prev').onclick=()=>{offset=Math.max(0,offset-20);void load(true);};
  $('rc-report-next').onclick=()=>{if(offset+20<total){offset+=20;void load(true);}};
  controls();
 });
 window.RealCopyPerformance={refresh:()=>void load(false)};
})();
