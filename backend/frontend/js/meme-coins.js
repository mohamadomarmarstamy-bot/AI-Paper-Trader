(() => {
 const $=id=>document.getElementById(id), note=$('meme-status');let revision=null,busy=false,dirty=false,initialized=false,editRevision=null;let latest=null,filter='all',receivedAt=0;
 const fields={confirmation_seconds:['Confirmation wait · seconds',10,180],confirmation_min_percent:['Minimum confirmation rise %',0,20],confirmation_max_percent:['Maximum confirmation rise %',.1,100],minimum_liquidity_usd:['Minimum pool liquidity $',0,1000000000],minimum_m5_volume_usd:['Minimum 5-minute volume $',0,1000000000],trade_usd:['Dollars per paper trade',5,200],max_positions:['Maximum open positions',1,1000],paper_capital_usd:['Virtual capital $ · increases add funds',1000,1000000000],stop_percent:['Stop loss %',3,20],target_percent:['Take profit %',5,100],trail_arm_percent:['Trail activates at %',5,50],trail_percent:['Trail giveback points',2,20],max_hold_minutes:['Maximum hold minutes',5,120],daily_loss_usd:['Daily loss brake $',10,100]};
 const text=(tag,value)=>{const n=document.createElement(tag);n.textContent=value;return n;};
 const short=v=>v?`${v.slice(0,5)}…${v.slice(-4)}`:'Token';
 const compact=v=>typeof v==='number'&&Number.isFinite(v)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',notation:'compact',maximumFractionDigits:1}).format(v):'—';
 const money=v=>typeof v==='number'&&Number.isFinite(v)?`$${v.toFixed(2)}`:'Unavailable';
 for(const [key,[label,min,max]]of Object.entries(fields)){const l=text('label',label),i=document.createElement('input');i.id='meme-'+key;i.type='number';i.min=min;i.max=max;i.step=['max_positions','max_hold_minutes','confirmation_seconds'].includes(key)?'1':'0.1';i.required=true;l.append(i);$('meme-setting-fields').append(l);}
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
  if(value.startsWith('PAPER buy simulated')||value.startsWith('PAPER exploration buy'))return 'Paper buy opened';
  return value;
 }

 function showPage(){const page=location.pathname.split('/')[2]||'home';const active=['home','trades','scanner','settings'].includes(page)?page:'home';for(const el of document.querySelectorAll('[data-view]'))el.hidden=el.dataset.view!==active;for(const a of document.querySelectorAll('nav [data-page]')){if(a.dataset.page===active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');}document.title=`${active[0].toUpperCase()+active.slice(1)} · Meme Coins`;if(latest&&active==='home')drawChart(latest);}
 for(const a of document.querySelectorAll('nav [data-page]'))a.onclick=e=>{if(e.ctrlKey||e.metaKey||e.shiftKey||e.altKey)return;e.preventDefault();history.pushState(null,'',a.getAttribute('href'));showPage();window.scrollTo(0,0);};
 addEventListener('popstate',showPage);showPage();
 for(const b of document.querySelectorAll('[data-filter]'))b.onclick=()=>{filter=b.dataset.filter;for(const c of document.querySelectorAll('[data-filter]'))c.setAttribute('aria-pressed',String(c===b));if(latest)render(latest,false);};
 function tone(el,value){el.classList.toggle('positive',Number.isFinite(value)&&value>=0);el.classList.toggle('negative',Number.isFinite(value)&&value<0);}
 let chartCache='',chartMetric='equity',chartWindow=86400;
 $('meme-chart-metric').onchange=e=>{chartMetric=e.target.value;chartCache='';if(latest)drawChart(latest);};
 $('meme-chart-window').onchange=e=>{chartWindow=Number(e.target.value);chartCache='';if(latest)drawChart(latest);};
 function drawChart(data){
  const box=$('meme-equity-chart'),labels=$('meme-chart-labels');
  const raw=(data.equity_history||[]).filter(p=>Number.isFinite(p.at)&&p.at>=(data.server_time||Date.now()/1000)-chartWindow);
  const points=raw.map(p=>({...p,value:Number.isFinite(p.equity_usd)?p.equity_usd-(chartMetric==='pnl'?(p.capital_usd||1000):0):null}));
  const width=Math.max(300,box.clientWidth||900);const key=JSON.stringify([chartMetric,chartWindow,width,points]);if(key===chartCache)return;chartCache=key;box.replaceChildren();labels.replaceChildren();
  const valid=points.filter(p=>Number.isFinite(p.value));
  if(valid.length<2){const empty=text('div',valid.length?'First snapshot saved. The chart builds as the bot runs.':'Your chart starts with the next bot scan.');empty.className='chart-empty';box.append(empty);return;}
  const node=(tag,attrs)=>{const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,v);return n;};
  const svg=node('svg',{viewBox:`0 0 ${width} 240`,preserveAspectRatio:'none',role:'img',tabindex:0,'aria-label':`Saved paper ${chartMetric==='pnl'?'P/L':'equity'}. Drag or use left and right arrows to inspect values. Missing observations leave gaps.`});
  const lo=Math.min(...valid.map(p=>p.value)),hi=Math.max(...valid.map(p=>p.value)),pad=Math.max((hi-lo)*.15,1),min=lo-pad,max=hi+pad,start=points[0].at,end=points.at(-1).at;
  const x=p=>55+(p.at-start)/Math.max(1,end-start)*(width-67),y=p=>210-(p.value-min)/(max-min)*185;
  for(const value of [max,(min+max)/2,min]){const yy=210-(value-min)/(max-min)*185;svg.append(node('line',{x1:55,x2:width-12,y1:yy,y2:yy,stroke:'#344260','stroke-dasharray':'4 6'}));const label=node('text',{x:0,y:yy+4,fill:'#9db0cc','font-size':11});label.textContent=compact(value);svg.append(label);}
  let path='',segments=[];for(const p of points){if(!Number.isFinite(p.value)){if(path)segments.push(path);path='';continue;}path+=(path?' L':'M')+x(p).toFixed(2)+' '+y(p).toFixed(2);}if(path)segments.push(path);
  for(const d of segments)svg.append(node('path',{d,fill:'none',stroke:'#7de4cc','stroke-width':3,'stroke-linejoin':'round','vector-effect':'non-scaling-stroke'}));
  for(const p of valid)svg.append(node('circle',{cx:x(p),cy:y(p),r:2,fill:'#9ef9df'}));
  const cursor=node('line',{x1:0,x2:0,y1:15,y2:220,stroke:'#b8a9ff','stroke-dasharray':'3 4',visibility:'hidden'}),dot=node('circle',{cx:0,cy:0,r:5,fill:'#c2aaff',stroke:'#131b2f','stroke-width':2,visibility:'hidden'});svg.append(cursor,dot);box.append(svg);
  const tip=text('div','');tip.className='chart-tooltip';tip.id='meme-chart-tooltip';tip.hidden=true;tip.setAttribute('role','status');box.append(tip);let selected=-1;
  function inspect(index){selected=Math.max(0,Math.min(points.length-1,index));const p=points[selected],xx=x(p);cursor.setAttribute('x1',xx);cursor.setAttribute('x2',xx);cursor.setAttribute('visibility','visible');dot.setAttribute('visibility',Number.isFinite(p.value)?'visible':'hidden');if(Number.isFinite(p.value)){dot.setAttribute('cx',xx);dot.setAttribute('cy',y(p));}tip.replaceChildren(text('strong',Number.isFinite(p.value)?money(p.value):'Value unavailable'),text('span',new Date(p.at*1000).toLocaleString()));const previous=points[selected-1];if(previous&&(p.capital_usd||1000)!==(previous.capital_usd||1000))tip.append(text('small','Includes a virtual funding adjustment'));tip.hidden=false;tip.style.left=`${Math.max(8,Math.min(box.clientWidth-210,xx/width*box.clientWidth-90))}px`;}
  function point(e){const rect=svg.getBoundingClientRect(),time=start+Math.max(0,Math.min(1,((e.clientX-rect.left)/rect.width*width-55)/(width-67)))*(end-start);let index=0;for(let i=1;i<points.length;i++)if(Math.abs(points[i].at-time)<Math.abs(points[index].at-time))index=i;inspect(index);}
  svg.addEventListener('pointerdown',e=>{svg.setPointerCapture(e.pointerId);point(e);});svg.addEventListener('pointermove',point);svg.addEventListener('pointerleave',e=>{if(!svg.hasPointerCapture(e.pointerId)){tip.hidden=true;cursor.setAttribute('visibility','hidden');dot.setAttribute('visibility','hidden');}});
  svg.addEventListener('keydown',e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();inspect(selected<0?points.length-1:selected+(e.key==='ArrowRight'?1:-1));}});
  labels.append(text('span',new Date(start*1000).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})),text('span',new Date(end*1000).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})));
 }
 function timers(){if(!latest)return;const now=(latest.server_time||0)+(performance.now()-receivedAt)/1000;for(const el of document.querySelectorAll('[data-confirmation-due]')){const start=Number(el.dataset.confirmationStart),due=Number(el.dataset.confirmationDue),expires=Number(el.dataset.confirmationExpires),fresh=!latest.error&&latest.config.enabled&&now-(latest.last_cycle||0)<=45&&now<=expires;const bar=el.querySelector('progress'),caption=el.querySelector('.timer-label');bar.value=fresh?Math.min(due-start,Math.max(0,now-start)):0;caption.textContent=!latest.config.enabled?'Buys paused':!fresh?'Waiting for a fresh scan':now<due?`Next confirmation check in ${Math.ceil(due-now)}s`:'Confirmation due · checking next scan';}}
 setInterval(timers,1000);
 function render(data,newResponse=true){revision=data.revision;if(newResponse){latest=data;receivedAt=performance.now();}
  const expanded=new Set([...document.querySelectorAll('details[data-row-key][open]')].map(n=>n.dataset.rowKey));
  function detail(el,key,address,extra){const d=document.createElement('details');d.dataset.rowKey=key;d.open=expanded.has(key);d.append(text('summary','Details'),text('p',address));if(extra)d.append(text('p',extra));el.append(d);}
  $('meme-cash').textContent=money(data.cash_usd);$('meme-equity').textContent=money(data.paper_equity_usd);$('meme-pnl').textContent=money(data.realized_pnl_usd);$('meme-count').textContent=data.positions.length;
  const total=Number.isFinite(data.paper_equity_usd)?data.paper_equity_usd-(data.config.paper_capital_usd||1000):null;$('meme-capital-label').textContent=`Net of ${money(data.config.paper_capital_usd||1000)} virtual funding`;$('meme-total-pnl').textContent=money(total);tone($('meme-total-pnl'),total);$('meme-open-pnl').textContent=money(data.unrealized_pnl_usd);tone($('meme-open-pnl'),data.unrealized_pnl_usd);tone($('meme-pnl'),data.realized_pnl_usd);drawChart(data);
  const health={running:'Running',paused:'Paused',starting:'Starting',paused_entries_monitoring_exits:'Buys paused · exits active',entry_brake:'Entry brake active',waiting_for_mints:'Waiting for tokens',data_unavailable:'Provider unavailable'};
  note.textContent=`${health[data.health]||data.health} · Last scan ${data.last_cycle?new Date(data.last_cycle*1000).toLocaleTimeString():'not run'}${data.error?' · '+data.error:''}`;
  if(!initialized||!dirty){for(const key of Object.keys(fields))$('meme-'+key).value=data.config[key];$('meme-watchlist').value=data.config.watchlist.join('\n');$('meme-discover').checked=data.config.discover;$('meme-confirmation-enabled').checked=data.config.confirmation_enabled??true;$('meme-entry-mode').value=data.config.entry_mode||'filtered';$('meme-auto-learning').checked=data.config.auto_learning||false;$('meme-learning-email').checked=data.config.learning_email_enabled??true;initialized=true;}
  const modes={filtered:'Filtered · full entry checks',explore:'PAPER exploration · no entry filters',learned:'Liquidity + volume only · token checks not performed'};
  $('meme-mode-status').textContent=modes[data.config.entry_mode||'filtered'];
  const learning=data.learning_summary||{total:0,wins:0,losses:0,notes:[]};
  $('meme-learning-status').textContent=data.learning?.status||'No learning outcomes yet.';
  $('meme-learning-email-status').textContent=`${data.learning?.review_status||'Review after every 10 settings changes'} · Email: ${!data.config.learning_email_enabled?'off':data.learning_email_configured?'configured':'configuration missing'} · ${learning.email_pending||0} queued`;
  list('meme-learning-evaluations',learning.evaluations||[],(el,r)=>{el.append(text('strong',`${r.type} · ${r.changed?'Settings updated':'No change'}`));if(r.changed)el.append(text('p',`Liquidity ${compact(r.before.minimum_liquidity_usd)} → ${compact(r.after.minimum_liquidity_usd)} · Volume ${compact(r.before.minimum_m5_volume_usd)} → ${compact(r.after.minimum_m5_volume_usd)}`));el.append(text('small',`${r.evidence?.reason||''}${r.email_status?' · Email '+r.email_status:''}`));},'First evaluation after 50 archived closes.');
  $('meme-learning-totals').textContent=`${learning.total} archived closes · ${learning.wins} wins · ${learning.losses} losses · modeled results, not real fills`;
  list('meme-learning-notes',learning.notes||[],(el,r)=>{el.append(text('strong',`${r.symbol} · ${money(r.pnl_usd)}`),text('p',r.note||r.reason));const f=r.entry_features||{};el.append(text('small',`Entry liquidity ${compact(f.liquidity_usd)} · 5m volume ${compact(f.volume_m5_usd)}`));detail(el,'learning-'+r.id,r.mint,null);},'Closed trades will appear here with notes.');
  const counts={};for(const row of data.candidates)counts[label(row)]=(counts[label(row)]||0)+1;
  $('meme-candidate-summary').textContent=Object.entries(counts).map(([name,count])=>`${count} ${name.toLowerCase()}`).join(' · ');
  $('scan-total').textContent=data.candidates.length;$('scan-waiting').textContent=counts.Watching||0;$('scan-skipped').textContent=(counts.Skipped||0)+(counts.Paused||0);$('scan-no-data').textContent=counts['No data']||0;
  $('meme-scan-health').textContent=`${data.discovery_cooldown_count||0} incomplete discovery tokens cooling down · counts reflect the latest scan`;
  const waiting=data.candidates.filter(r=>label(r)==='Watching');$('meme-waiting-count').textContent=waiting.length;
  list('meme-waiting',waiting,(el,r)=>{el.append(text('strong',r.symbol||short(r.mint)),text('p',`Liq ${compact(r.liquidity_usd)} · 5m vol ${compact(r.volume_m5_usd)}`));if(Number.isFinite(r.confirmation_due_at)){el.dataset.confirmationStart=r.confirmation_started_at;el.dataset.confirmationDue=r.confirmation_due_at;el.dataset.confirmationExpires=r.confirmation_expires_at;const bar=document.createElement('progress');bar.max=Math.max(1,r.confirmation_due_at-r.confirmation_started_at);bar.setAttribute('aria-label','Automatic confirmation progress');const caption=text('span','Waiting for next scan');caption.className='timer-label';el.append(bar,caption);}else el.append(text('p','Waiting for next scan'));detail(el,'waiting-'+r.mint,r.mint,null);},'No coins waiting. Qualifying coins appear here automatically.');timers();
  list('meme-home-holdings',data.positions,(el,r)=>{const pnl=typeof r.current_modeled_value_usd==='number'?r.current_modeled_value_usd-r.cost_usd:null;el.append(text('strong',r.symbol||short(r.mint)),text('p',`${money(r.current_modeled_value_usd)} · P/L ${money(pnl)}`));},'No open holdings yet.');
  const order={Open:0,Watching:1,Paused:2,Skipped:3,'No data':4};
  list('meme-candidates',[...data.candidates].filter(r=>filter==='all'||label(r)===filter).sort((a,b)=>order[label(a)]-order[label(b)]),(el,r)=>{
   const state=label(r),header=text('div','');header.className='meme-title-row';
   const badge=text('span',state);badge.className='meme-badge '+state.toLowerCase().replace(' ','-');
   header.append(text('strong',r.symbol||short(r.mint)),badge);el.append(header,text('p',reason(r)));
   if(typeof r.liquidity_usd==='number'){const metrics=text('p',`Liq ${compact(r.liquidity_usd)} · Vol ${compact(r.volume_m5_usd)} · 5m ${(typeof r.change_m5_percent==='number'?r.change_m5_percent.toFixed(1):'—')}%`);metrics.className='meme-metrics';el.append(metrics);}
   detail(el,'candidate-'+r.mint,r.mint,reason(r)!==r.reason?r.reason:null);
  },filter==='all'?'No candidates yet. Run a scan.':'No coins in this category.');
  list('meme-positions',data.positions,(el,r)=>{el.append(text('strong',r.symbol),text('p',`${money(r.cost_usd)} → ${money(r.current_modeled_value_usd)}${r.mark_fresh?'':' · mark unavailable'}`));detail(el,'position-'+r.mint,r.mint);},'None yet.');
  list('meme-closed',[...data.closed].reverse(),(el,r)=>{el.append(text('strong',`${r.symbol} · ${r.reason.replaceAll('_',' ')}`),text('p',`${money(r.entry_usd)} → ${money(r.proceeds_usd)} · P/L ${money(r.pnl_usd)}`));detail(el,'closed-'+r.mint+'-'+r.closed_at,r.mint);},'None yet.');
  list('meme-limitations',data.limitations,(el,r)=>el.append(text('p',r)),'');
 }
 async function request(path='',body){if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);const c=new AbortController(),timer=setTimeout(()=>c.abort(),60000);try{const response=await fetch('/meme-coins/'+(path||'status'),{method:body?'POST':'GET',cache:'no-store',signal:c.signal,...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:`Request failed (${response.status}).`);render(data);return true;}catch(e){note.textContent=e.name==='AbortError'?'Request timed out. Refresh before retrying; no blockchain transaction was sent.':e.message;return false;}finally{clearTimeout(timer);busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);}}
 async function config(changes,expected=revision){if(revision===null){note.textContent='Load current settings first.';return false;}return request('config',{changes,expected_revision:expected});}
 $('meme-start').onclick=()=>config({enabled:true});$('meme-pause').onclick=()=>config({enabled:false});$('meme-scan').onclick=()=>request('scan',{});$('meme-refresh').onclick=()=>request();
 $('meme-settings').addEventListener('input',()=>{$('meme-save-status').textContent='Unsaved changes';if(!dirty)editRevision=revision;dirty=true;});
 $('meme-settings').addEventListener('invalid',e=>{$('meme-save-status').textContent=`Check ${e.target.closest('label')?.firstChild?.textContent||'settings'}: ${e.target.validationMessage}`;},true);
 $('meme-settings').addEventListener('submit',async e=>{e.preventDefault();const changes={watchlist:$('meme-watchlist').value.split(/\s+/).filter(Boolean),discover:$('meme-discover').checked,confirmation_enabled:$('meme-confirmation-enabled').checked,entry_mode:$('meme-entry-mode').value,auto_learning:$('meme-auto-learning').checked,learning_email_enabled:$('meme-learning-email').checked};for(const key of Object.keys(fields))changes[key]=Number($('meme-'+key).value);$('meme-save-status').textContent='Saving…';if(await config(changes,editRevision??revision)){dirty=false;editRevision=null;$('meme-save-status').textContent='Settings saved · remembered after refresh';}else{$('meme-save-status').textContent=note.textContent;}});
 request();setInterval(()=>{if(!document.hidden)request();},10000);
})();
