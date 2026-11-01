(() => {
 const $=id=>document.getElementById(id);let snapshot=null,busy=false,provider=null,generation=0,receivedAt=null,transportError=false,restoring=false,restoreProvider=null,checksKey='',buySettings=null,buyDirty=false,buyRevision=null,balanceData=null,balanceReceivedAt=null,balanceBusy=false,balanceError=false;
 const money=n=>Number.isFinite(n)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(n):'—';
 const short=s=>s?`${s.slice(0,6)}…${s.slice(-5)}`:'No wallet linked';
 const node=(tag,value,cls)=>{const el=document.createElement(tag);el.textContent=value;if(cls)el.className=cls;return el;};
 async function request(path,payload){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),path==='balance'?12000:path==='start'?35000:8000);try{const response=await fetch('/real-copy/'+path,{method:payload?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:payload?{'Content-Type':'application/json'}:undefined,body:payload?JSON.stringify(payload):undefined});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Could not load this workspace.');return data;}finally{clearTimeout(timer);}}
 function message(value){$('rc-message').textContent=value;}
 function controls(){$('rc-connect').disabled=busy||restoring||!snapshot;$('rc-unlink').disabled=busy||!snapshot?.wallet.address||snapshot?.live_execution_enabled||Boolean(snapshot?.live_positions?.length)||Boolean(snapshot?.live_runtime?.pending_count);$('rc-refresh').disabled=busy;$('rc-start').disabled=busy||!snapshot?.can_start||balanceData?.funding?.wallet_address!==snapshot?.wallet.address||balanceData?.settings?.revision!==buySettings?.revision||!balanceData?.funding?.funding_fits||balanceError||transportError||!balanceData?.funding?.valid_until||balanceData.server_time+(performance.now()-(balanceReceivedAt??0))/1000>balanceData.funding.valid_until;$('rc-pause').disabled=busy||!snapshot?.live_execution_enabled;$('rc-close-all').disabled=busy||!snapshot?.live_positions?.length;$('rc-buy-save').disabled=busy||!buySettings;$('rc-buy-reload').disabled=busy;for(const id of ['rc-buy-mode','rc-buy-usd','rc-buy-percent','rc-fee-reserve'])$(id).disabled=busy||!buySettings;}
 const sol=n=>Number.isFinite(n)?`${n.toLocaleString('en-US',{maximumFractionDigits:9})} SOL`:'Unavailable';
 function buyFields(){const fixed=$('rc-buy-mode').value==='fixed_usd';$('rc-buy-usd-field').hidden=!fixed;$('rc-buy-percent-field').hidden=fixed;$('rc-buy-usd').required=fixed;$('rc-buy-percent').required=!fixed;}
 function showSettings(settings){
  if(!settings)return;buySettings=settings;
  if(!buyDirty){const c=settings.config;$('rc-buy-mode').value=c.buy_mode;$('rc-buy-usd').value=c.buy_usd??'';$('rc-buy-percent').value=c.buy_percent??'';$('rc-fee-reserve').value=c.fee_reserve_sol;buyFields();}
 }
 function funding(){
  const b=balanceData?.funding,elapsed=balanceReceivedAt===null?Infinity:(performance.now()-balanceReceivedAt)/1000;
  const matches=b&&b.wallet_address===snapshot?.wallet.address;
  const fresh=matches&&!balanceError&&Number.isFinite(b.valid_until)&&balanceData.server_time+elapsed<=b.valid_until;
  const currentSettings=balanceData?.settings?.revision===buySettings?.revision;
  $('rc-balance-sol').textContent=fresh?sol(b.balance_sol):'Unavailable';
  $('rc-summary-balance').textContent=fresh?sol(b.balance_sol):'Unavailable';
  $('rc-spendable').textContent=fresh?`${sol(b.spendable_sol)}${Number.isFinite(b.spendable_usd)?` ≈ ${money(b.spendable_usd)}`:''}`:'Unavailable';
  $('rc-next-buy').textContent=fresh&&currentSettings&&Number.isFinite(b.next_buy_sol)?`${sol(b.next_buy_sol)}${Number.isFinite(b.next_buy_usd)?` ≈ ${money(b.next_buy_usd)}`:''}`:'Not available';
  $('rc-funding-status').textContent=balanceError?'Balance check failed. Retrying automatically.':!matches?'Checking wallet SOL balance…':!fresh?(b.error||'Wallet balance is unavailable or expired.'):!currentSettings?'Updating the buy preview for saved settings…':b.reason+(b.price_error?' '+b.price_error:'');
  if(b?.basis)$('rc-balance-detail').textContent=b.basis+(b.balance_observed_at?` Checked ${new Date(b.balance_observed_at*1000).toLocaleTimeString()}.`:'');
 }
 async function loadBalance(){
  if(balanceBusy)return;balanceBusy=true;
  try{const data=await request('balance');if(data.funding?.wallet_address!==snapshot?.wallet.address)return;balanceData=data;balanceReceivedAt=performance.now();balanceError=false;}catch{balanceError=true;}finally{balanceBusy=false;funding();controls();}
 }
 function health(){
  funding();
  const elapsed=receivedAt===null?Infinity:Math.max(0,(performance.now()-receivedAt)/1000),h=snapshot?.operational_health;
  let level=h?.level||'warning',label=h?.label||'Checking health…',detail=h?.detail||'Waiting for live readiness checks.';
  if(transportError||snapshot&&elapsed>60){level='error';label='Connection lost';detail='Live readiness is unavailable. Displayed values may be old.';}
  else if(h?.level==='ok'&&Number.isFinite(h.valid_until)&&snapshot.server_time+elapsed>h.valid_until){level='warning';label='Check delayed';detail='A live heartbeat or source check needs a fresh update.';}
  else if(snapshot&&elapsed>30){level='warning';label='Status delayed';detail='Waiting for fresh live readiness checks.';}
  else if(!snapshot){level='loading';detail='Connecting to the real-copy workspace.';}
  $('rc-health').dataset.level=level;
  for(const [id,value]of [['rc-health-label',label],['rc-health-detail',detail],['rc-health-age',receivedAt===null?'Waiting for status':`Status ${Math.floor(elapsed)}s ago`]])if($(id).textContent!==value)$(id).textContent=value;
  const address=provider?.isConnected===false?null:provider?.publicKey?.toString(),matched=address&&address===snapshot?.wallet.address&&snapshot?.wallet.ownership_verified;
  const feed=snapshot?.paper_sync.operational_health;
  const feedExpired=feed&&Number.isFinite(feed.valid_until)&&snapshot.server_time+elapsed>feed.valid_until;
  const checks=[{name:'Page connection',level:transportError||elapsed>60?'error':elapsed>30?'warning':'ok',detail:receivedAt===null?'Waiting for status':transportError||elapsed>60?'Updates unavailable':'Updates automatically'},
   {name:'Phantom session',level:matched?'ok':'warning',detail:matched?'Connected to verified wallet':address?'Selected wallet differs from verified wallet':restoring?'Restoring trusted connection':'Unlock or reconnect Phantom'},
   {name:'Wallet funding',level:!balanceError&&balanceData?.funding?.funding_fits&&balanceData.funding.wallet_address===snapshot?.wallet.address&&balanceReceivedAt!==null&&balanceData.server_time+(performance.now()-balanceReceivedAt)/1000<=balanceData.funding.valid_until?'ok':'warning',detail:$('rc-funding-status').textContent},
   ...(h?.checks||[]).map(c=>transportError||elapsed>60?{...c,level:'warning',detail:'Awaiting fresh status'}:c.name==='Source monitoring'&&feedExpired?{...c,level:'warning',detail:'Source checks need a fresh update'}:c)];
  const key=JSON.stringify(checks);if(key!==checksKey){checksKey=key;const box=$('rc-health-checks');box.replaceChildren();for(const c of checks){const el=node('div','', 'rc-health-check');el.dataset.level=c.level;el.append(node('strong',c.name),node('p',c.detail));box.append(el);}}
 }
 function accept(data){render(data);receivedAt=performance.now();transportError=false;health();}
 async function restore(force=false){
  const p=window.PhantomSession?.provider();if(!p||restoring||busy||!snapshot?.wallet.ownership_verified||!force&&restoreProvider===p)return;
  restoreProvider=p;restoring=true;bindProvider(p);controls();health();
  try{await window.PhantomSession.reconnect();if(snapshot)render(snapshot);}finally{restoring=false;controls();health();}
 }
 function render(data){
  if(typeof data.live_execution_enabled!=='boolean'||typeof data.can_start!=='boolean')throw Error('Execution state unavailable.');
  snapshot=data;showSettings(data.buy_settings);
  $('rc-live-badge').textContent=data.live_execution_enabled?(data.operational_health?.level==='error'?'● REAL MONEY · needs attention':'● REAL MONEY · copying'):'● Live trading off';
  $('rc-live-summary').textContent=data.live_execution_enabled?'Real trades are active. Fresh admitted source buys use your saved live size.':'Real trading is off. Configure the server signer, then activate copying yourself.';
  $('rc-live-count').textContent=String(data.live_trade_count??0);
  $('rc-summary-open').textContent=String(data.live_positions?.length??0);
  $('rc-live-pnl').dataset.sign=Number.isFinite(data.live_pnl_sol)?(data.live_pnl_sol<0?'negative':'positive'):'unknown';
  $('rc-live-pnl').textContent=Number.isFinite(data.live_pnl_sol)?`${data.live_pnl_sol>=0?'+':''}${data.live_pnl_sol.toFixed(6)} SOL`:'Unpriced';
  const setup=data.execution_setup;$('rc-setup-status').textContent=setup?`Engine installed · ${setup.signer_matches?'signer matches wallet':'matching signer needed'} · ${setup.jupiter_configured?'Jupiter configured':'Jupiter key needed'} · server switch ${setup.release_allowed?'on':'off'}`:'Execution setup unavailable.';
  const holdings=$('rc-live-holdings');holdings.replaceChildren();for(const p of data.live_positions||[]){const row=node('div',''),head=node('div','', 'rc-row'),button=node('button',p.pending_reason?'Exit queued':'Sell');button.type='button';button.disabled=busy||Boolean(p.pending_reason);button.onclick=()=>closeLive([p.id],false);head.append(node('strong',short(p.mint)),button);row.append(head,node('small',`${(Number(p.quantity_raw)/10**p.decimals).toLocaleString('en-US',{maximumFractionDigits:6})} tokens · ${sol(p.cost_lamports/1e9)} remaining cost`),node('small',Number.isFinite(p.exit_value_lamports)&&Date.now()/1000-(p.marked_at||0)<=60?`${sol(p.exit_value_lamports/1e9)} minimum exit estimate`:'Waiting for a current exit quote'));holdings.append(row);}if(!data.live_positions?.length)holdings.append(node('p','No real holdings.'));
  const activity=$('rc-live-orders');activity.replaceChildren();for(const o of data.live_runtime?.orders||[]){const row=node('div','');row.append(node('strong',`${o.side.toUpperCase()} · ${o.status.toUpperCase()}`),node('small',short(o.mint)),node('small',o.error||o.reason));if(o.signature){const a=node('a','View transaction ↗','rc-link');a.href='https://solscan.io/tx/'+encodeURIComponent(o.signature);a.target='_blank';a.rel='noopener';row.append(a);}activity.append(row);}if(!data.live_runtime?.orders?.length)activity.append(node('p','No real transactions.'));
  const s=data.paper_sync,b=s.blueprint,c=b.settings,active=b.traders.filter(t=>t.active!==false);
  $('rc-wallet-address').textContent=short(data.wallet.address);$('rc-wallet-address').title=data.wallet.address||'';
  const localAddress=provider?.isConnected===false?null:provider?.publicKey?.toString();
  $('rc-wallet-status').textContent=data.wallet.ownership_verified?(localAddress===data.wallet.address?'Phantom connected · ownership verified':localAddress?'Different Phantom account selected · saved wallet unchanged':'Wallet link saved · unlock or reconnect Phantom'):'Link Phantom with a message signature. No spending permission.';
  $('rc-connect').textContent=localAddress===data.wallet.address&&data.wallet.ownership_verified?'Phantom connected':data.wallet.ownership_verified?'Reconnect Phantom':'Connect Phantom';
  $('rc-summary-following').textContent=`${active.length} wallets followed`;
  $('rc-count').textContent=active.length;$('rc-size').textContent=money(c.trade_usd);$('rc-paper-return').textContent=Number.isFinite(s.performance.closed_return_percent)?`${s.performance.closed_return_percent>=0?'+':''}${s.performance.closed_return_percent.toFixed(2)}%`:'—';
  $('rc-paper-profit').textContent=`${money(s.performance.realized_pnl_usd)} net · ${s.performance.closed_count} realized paper exits, including partial sales`;
  $('rc-sync').textContent=`Synced ${new Date(s.read_at*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})} · revision ${s.settings_revision}`;
  $('rc-fingerprint').textContent=s.snapshot.slice(0,12);$('rc-guard').textContent=b.entry_protection.one_open_position_per_coin?'One position per coin':'Every fresh buy + add-ons';
  $('rc-cooldown').textContent=c.stop_loss_percent===0?'Off':`${c.stop_loss_percent??20}% per lot`;
  $('rc-discovery').textContent=c.auto_discover?'Automatic':'Paused';
  $('rc-feed').textContent=s.source_error?`Paper feed: ${s.source_error}`:`Paper feed · ${s.source_health.replaceAll('_',' ')}`;
  const roster=$('rc-roster');roster.replaceChildren();for(const [i,t]of active.entries()){const row=node('div','', 'rc-trader'),avatar=node('span',(t.name||t.wallet).slice(0,2).toUpperCase(),'rc-avatar');avatar.style.setProperty('--rc-hue',String((i*47+230)%360));const info=node('div','');info.append(node('strong',t.name||short(t.wallet)),node('small',short(t.wallet)));const badge=node('span',t.trade_provider==='rpc'?'Chain feed':'Provider feed','rc-pill');row.append(avatar,info,badge);roster.append(row);}if(!active.length)roster.append(node('p','Waiting for the paper roster.'));
  const signals=$('rc-signals');signals.replaceChildren();for(const e of s.signals){const row=node('div','', 'rc-signal');const side=node('span',e.side.toUpperCase(),e.side==='buy'?'rc-buy':'rc-sell');const info=node('div','');info.append(node('strong',short(e.mint)),node('small',e.reason));row.append(side,info);signals.append(row);}if(!s.signals.length)signals.append(node('p','New paper signals will appear here.'));
  controls();health();
 }
 async function load(){if(busy)return;busy=true;controls();try{accept(await request('status'));message('');}catch(e){transportError=true;health();message(e.name==='AbortError'?'Status request timed out. Retrying automatically.':e.message);}finally{busy=false;controls();void restore();void loadBalance();}}
 async function liveAction(path,payload){
  if(busy||!snapshot)return;busy=true;controls();
  try{accept(await request(path,payload));message(path==='start'?'REAL copying activated.':path==='pause'?'New real buys paused. Existing exits remain monitored.':'Live exit queued. Waiting for a supported swap and on-chain confirmation.');}
  catch(e){transportError=true;health();message(e.name==='AbortError'?'Action reply timed out. Refreshing the live state before another action.':e.message);}finally{busy=false;controls();if(transportError)void load();}
 }
 async function closeLive(ids,pause){if(!confirm(`Sell ${ids.length} REAL holding(s) at the available swap price${pause?' and pause new buys':''}? Network costs and slippage apply.`))return;await liveAction('close',{positions:ids,pause_new_buys:pause});}
 function bindProvider(p){if(provider===p)return;provider=p;for(const event of ['accountChanged','disconnect'])p.on?.(event,()=>{generation++;message('Wallet session changed. Reconnect to verify the selected account.');if(snapshot)render(snapshot);});}
 async function connect(){
  if(busy||restoring||!snapshot)return;const p=window.PhantomSession?.provider();
  if(!p?.isPhantom){message('Open this page with the Phantom browser extension, or inside Phantom’s mobile browser.');return;}
  busy=true;controls();bindProvider(p);generation++;
  try{
   const linked=await window.PhantomSession.connect();const address=linked.publicKey?.toString();
   if(!address)throw Error('Phantom did not return a wallet address.');
   if(snapshot.wallet.ownership_verified&&address===snapshot.wallet.address){render(snapshot);message('Phantom reconnected. Your verified wallet link is unchanged.');return;}
   // connect may emit accountChanged; bind this signing attempt after it completes.
   const signingGeneration=generation;
   const challenge=await request('challenge',{wallet_address:address,expected_revision:snapshot.wallet.revision});
   message('Approve the wallet-link message in Phantom. Trading stays disabled.');
   const signed=await p.signMessage(new TextEncoder().encode(challenge.message),'utf8');
   if(generation!==signingGeneration||p.publicKey?.toString()!==address)throw Error('Wallet changed during linking. Connect again.');
   const signature=Array.from(new Uint8Array(signed.signature));if(signature.length!==64)throw Error('Phantom message signature unavailable.');
   accept(await request('verify',{challenge_id:challenge.challenge_id,signature:btoa(String.fromCharCode(...signature))}));
   message('Phantom linked. Live copying stays locked.');void loadBalance();
  }catch(e){message(e.code===4001?'Connection or signature declined. Live trading stays disabled.':e.message||'Could not link Phantom.');}
  finally{busy=false;controls();}
 }
 document.addEventListener('DOMContentLoaded',()=>{
  if(!$('real-copy-section'))return;
  $('rc-buy-settings').addEventListener('input',()=>{if(!buyDirty)buyRevision=buySettings?.revision;buyDirty=true;buyFields();});
  $('rc-buy-reload').onclick=async()=>{buyDirty=false;buyRevision=null;await load();if(!transportError)$('rc-buy-message').textContent='Saved buy settings reloaded.';};
  $('rc-buy-settings').onsubmit=async event=>{
   event.preventDefault();if(busy||!buySettings)return;busy=true;controls();
   const val=id=>$(id).value===''?null:Number($(id).value);
   try{const settings=await request('settings',{changes:{buy_mode:$('rc-buy-mode').value,buy_usd:val('rc-buy-usd'),buy_percent:val('rc-buy-percent'),fee_reserve_sol:val('rc-fee-reserve')},expected_revision:buyRevision??buySettings.revision});buyDirty=false;buyRevision=null;showSettings(settings);$('rc-buy-message').textContent=snapshot?.live_execution_enabled?'Buy settings saved. Restart copying to apply them; existing exits remain monitored.':'Buy settings saved. Real trading remains off.';void loadBalance();}
   catch(e){$('rc-buy-message').textContent=e.message;}
   finally{busy=false;controls();}
  };
  $('rc-start').onclick=async()=>{
   if(busy||!snapshot?.can_start)return;
   const c=buySettings.config,size=c.buy_mode==='fixed_usd'?`${money(c.buy_usd)} per coin`:`${c.buy_percent}% of available SOL per coin`;
   if(!confirm(`Enable REAL Solana copy trading with ${size}? The server key can spend this wallet's funds. Slippage limit: 1%; maximum price impact: 5%; maximum priority fee: 0.0001 SOL plus the base fee and account rent. Loss stops can fill beyond their trigger. New settings require a restart; pausing buys keeps exits running.`))return;
   await liveAction('start',{expected_revision:snapshot.live_runtime.revision,wallet_revision:snapshot.wallet.revision,budget_revision:buySettings.revision,acknowledgement:'ENABLE REAL SOLANA COPY TRADING'});
  };
  $('rc-pause').onclick=()=>snapshot&&liveAction('pause',{expected_revision:snapshot.live_runtime.revision});
  $('rc-close-all').onclick=()=>snapshot?.live_positions?.length&&closeLive(snapshot.live_positions.map(p=>p.id),true);
  $('rc-refresh').onclick=load;$('rc-connect').onclick=connect;
  $('rc-unlink').onclick=async()=>{if(busy||!snapshot)return;busy=true;controls();try{accept(await request('unlink',{expected_revision:snapshot.wallet.revision}));await window.PhantomSession?.disconnect();balanceData=null;balanceReceivedAt=null;funding();message('Wallet link removed. Live copying remains locked.');}catch(e){message(e.message);}finally{busy=false;controls();}};
  setInterval(()=>{health();controls();},1000);
  window.addEventListener('focus',()=>{if(!$('real-copy-section').hidden)void restore(true);});
  window.addEventListener('load',()=>void restore());
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!$('real-copy-section').hidden)void restore(true);});
  setInterval(()=>{if(!$('real-copy-section').hidden)load();},15000);
  if(location.hash==='#real-copy')setTimeout(()=>document.querySelector('[data-section="real-copy-section"]').click(),0);
  controls();health();
 });
 window.loadRealCopyTrade=load;
})();
