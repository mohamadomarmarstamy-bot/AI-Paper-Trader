(() => {
 const $=id=>document.getElementById(id), note=$('meme-status');let revision=null,busy=false,dirty=false,initialized=false,editRevision=null;
 const fields={minimum_liquidity_usd:['Minimum pool liquidity $',0,1000000000],minimum_m5_volume_usd:['Minimum 5-minute volume $',0,1000000000],trade_usd:['Dollars per paper trade',5,200],max_positions:['Maximum open positions',1,5],stop_percent:['Stop loss %',3,20],target_percent:['Take profit %',5,100],trail_arm_percent:['Trail activates at %',5,50],trail_percent:['Trail giveback points',2,20],max_hold_minutes:['Maximum hold minutes',5,120],daily_loss_usd:['Daily loss brake $',10,100]};
 const text=(tag,value)=>{const n=document.createElement(tag);n.textContent=value;return n;};
 const short=v=>v?`${v.slice(0,5)}…${v.slice(-4)}`:'Token';
 const compact=v=>typeof v==='number'&&Number.isFinite(v)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',notation:'compact',maximumFractionDigits:1}).format(v):'—';
 const money=v=>typeof v==='number'&&Number.isFinite(v)?`$${v.toFixed(2)}`:'Unavailable';
 for(const [key,[label,min,max]]of Object.entries(fields)){const l=text('label',label),i=document.createElement('input');i.id='meme-'+key;i.type='number';i.min=min;i.max=max;i.step=['max_positions','max_hold_minutes'].includes(key)?'1':'0.1';i.required=true;l.append(i);$('meme-setting-fields').append(l);}
 function list(id,rows,render,empty){const box=$(id);box.replaceChildren();if(!rows.length)box.append(text('p',empty));for(const row of rows){const div=text('div','');div.className='meme-row';render(div,row);box.append(div);}}
 function label(row){
  if(row.status==='paper_open')return 'Open';
  if(row.status==='unavailable')return 'No data';
  if(row.reason?.startsWith('Waiting for a second'))return 'Watching';
  if(row.reason?.includes('paused'))return 'Paused';
  return 'Skipped';
 }
 function reason(row){
  const value=row.reason||'';
  if(value.startsWith('Liquidity below'))return `Low liquidity · ${compact(row.liquidity_usd)} · ${value}`;
  if(value.startsWith('Five-minute volume below'))return `Low 5m volume · ${compact(row.volume_m5_usd)} · ${value}`;
  if(value.startsWith('Waiting for a second'))return 'Waiting for confirmation';
  if(value.startsWith('Five-minute change'))return 'Momentum outside +2–20%';
  if(value.startsWith('Pair age'))return 'Pool too new or too old';
  if(value.startsWith('Insufficient recent'))return 'Low recent activity';
  if(value.startsWith('No confirmed continuation'))return 'No continuation / too extended';
  if(value.startsWith('Existing paper position'))return 'Monitoring exits';
  if(value.startsWith('PAPER buy simulated'))return 'Paper buy opened';
  return value;
 }
 function render(data){revision=data.revision;
  const expanded=new Set([...document.querySelectorAll('details[data-row-key][open]')].map(n=>n.dataset.rowKey));
  function detail(el,key,address,extra){const d=document.createElement('details');d.dataset.rowKey=key;d.open=expanded.has(key);d.append(text('summary','Details'),text('p',address));if(extra)d.append(text('p',extra));el.append(d);}
  $('meme-cash').textContent=money(data.cash_usd);$('meme-equity').textContent=money(data.paper_equity_usd);$('meme-pnl').textContent=money(data.realized_pnl_usd);$('meme-count').textContent=data.positions.length;
  const health={running:'Running',paused:'Paused',starting:'Starting',paused_entries_monitoring_exits:'Buys paused · exits active',entry_brake:'Entry brake active',waiting_for_mints:'Waiting for tokens',data_unavailable:'Provider unavailable'};
  note.textContent=`${health[data.health]||data.health} · Last scan ${data.last_cycle?new Date(data.last_cycle*1000).toLocaleTimeString():'not run'}${data.error?' · '+data.error:''}`;
  if(!initialized||!dirty){for(const key of Object.keys(fields))$('meme-'+key).value=data.config[key];$('meme-watchlist').value=data.config.watchlist.join('\n');$('meme-discover').checked=data.config.discover;initialized=true;}
  const counts={};for(const row of data.candidates)counts[label(row)]=(counts[label(row)]||0)+1;
  $('meme-candidate-summary').textContent=Object.entries(counts).map(([name,count])=>`${count} ${name.toLowerCase()}`).join(' · ');
  const order={Open:0,Watching:1,Paused:2,Skipped:3,'No data':4};
  list('meme-candidates',[...data.candidates].sort((a,b)=>order[label(a)]-order[label(b)]),(el,r)=>{
   const state=label(r),header=text('div','');header.className='meme-title-row';
   const badge=text('span',state);badge.className='meme-badge '+state.toLowerCase().replace(' ','-');
   header.append(text('strong',r.symbol||short(r.mint)),badge);el.append(header,text('p',reason(r)));
   if(typeof r.liquidity_usd==='number'){const metrics=text('p',`Liq ${compact(r.liquidity_usd)} · Vol ${compact(r.volume_m5_usd)} · 5m ${r.change_m5_percent.toFixed(1)}%`);metrics.className='meme-metrics';el.append(metrics);}
   detail(el,'candidate-'+r.mint,r.mint,reason(r)!==r.reason?r.reason:null);
  },'No candidates yet. Run a scan.');
  list('meme-positions',data.positions,(el,r)=>{el.append(text('strong',r.symbol),text('p',`${money(r.cost_usd)} → ${money(r.current_modeled_value_usd)}${r.mark_fresh?'':' · mark unavailable'}`));detail(el,'position-'+r.mint,r.mint);},'None yet.');
  list('meme-closed',[...data.closed].reverse(),(el,r)=>{el.append(text('strong',`${r.symbol} · ${r.reason.replaceAll('_',' ')}`),text('p',`${money(r.entry_usd)} → ${money(r.proceeds_usd)} · P/L ${money(r.pnl_usd)}`));detail(el,'closed-'+r.mint+'-'+r.closed_at,r.mint);},'None yet.');
  list('meme-limitations',data.limitations,(el,r)=>el.append(text('p',r)),'');
 }
 async function request(path='',body){if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);const c=new AbortController(),timer=setTimeout(()=>c.abort(),60000);try{const response=await fetch('/meme-coins/'+(path||'status'),{method:body?'POST':'GET',cache:'no-store',signal:c.signal,...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:`Request failed (${response.status}).`);render(data);return true;}catch(e){note.textContent=e.name==='AbortError'?'Request timed out. Refresh before retrying; no blockchain transaction was sent.':e.message;return false;}finally{clearTimeout(timer);busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);}}
 async function config(changes,expected=revision){if(revision===null){note.textContent='Load current settings first.';return false;}return request('config',{changes,expected_revision:expected});}
 $('meme-start').onclick=()=>config({enabled:true});$('meme-pause').onclick=()=>config({enabled:false});$('meme-scan').onclick=()=>request('scan',{});$('meme-refresh').onclick=()=>request();
 $('meme-settings').addEventListener('input',()=>{if(!dirty)editRevision=revision;dirty=true;});
 $('meme-settings').addEventListener('submit',async e=>{e.preventDefault();const changes={watchlist:$('meme-watchlist').value.split(/\s+/).filter(Boolean),discover:$('meme-discover').checked};for(const key of Object.keys(fields))changes[key]=Number($('meme-'+key).value);if(await config(changes,editRevision??revision)){dirty=false;editRevision=null;}});
 request();setInterval(()=>{if(!document.hidden)request();},10000);
})();
