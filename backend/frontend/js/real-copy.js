(() => {
 const $=id=>document.getElementById(id);let snapshot=null,busy=false,provider=null,generation=0,receivedAt=null,transportError=false,restoring=false,restoreProvider=null,checksKey='';
 const money=n=>Number.isFinite(n)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(n):'—';
 const short=s=>s?`${s.slice(0,6)}…${s.slice(-5)}`:'No wallet linked';
 const node=(tag,value,cls)=>{const el=document.createElement(tag);el.textContent=value;if(cls)el.className=cls;return el;};
 async function request(path,payload){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);try{const response=await fetch('/real-copy/'+path,{method:payload?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:payload?{'Content-Type':'application/json'}:undefined,body:payload?JSON.stringify(payload):undefined});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Could not load this workspace.');return data;}finally{clearTimeout(timer);}}
 function message(value){$('rc-message').textContent=value;}
 function controls(){$('rc-connect').disabled=busy||restoring||!snapshot;$('rc-unlink').disabled=busy||!snapshot?.wallet.address;$('rc-refresh').disabled=busy;$('rc-start').disabled=true;}
 function health(){
  const elapsed=receivedAt===null?Infinity:Math.max(0,(performance.now()-receivedAt)/1000),h=snapshot?.operational_health;
  let level=h?.level||'warning',label=h?.label||'Checking health…',detail=h?.detail||'Waiting for live readiness checks.';
  if(transportError||snapshot&&elapsed>60){level='error';label='Connection lost';detail='Live readiness is unavailable. Displayed values may be old.';}
  else if(snapshot&&elapsed>30){level='warning';label='Status delayed';detail='Waiting for fresh live readiness checks.';}
  else if(!snapshot){level='loading';detail='Connecting to the real-copy workspace.';}
  $('rc-health').dataset.level=level;
  for(const [id,value]of [['rc-health-label',label],['rc-health-detail',detail],['rc-health-age',receivedAt===null?'Waiting for status':`Status ${Math.floor(elapsed)}s ago`]])if($(id).textContent!==value)$(id).textContent=value;
  const address=provider?.isConnected===false?null:provider?.publicKey?.toString(),matched=address&&address===snapshot?.wallet.address&&snapshot?.wallet.ownership_verified;
  const feed=snapshot?.paper_sync.operational_health;
  const feedExpired=feed&&Number.isFinite(feed.valid_until)&&snapshot.server_time+elapsed>feed.valid_until;
  const checks=[{name:'Page connection',level:transportError||elapsed>60?'error':elapsed>30?'warning':'ok',detail:receivedAt===null?'Waiting for status':transportError||elapsed>60?'Updates unavailable':'Updates automatically'},
   {name:'Phantom session',level:matched?'ok':'warning',detail:matched?'Connected to verified wallet':address?'Selected wallet differs from verified wallet':restoring?'Restoring trusted connection':'Unlock or reconnect Phantom'},
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
  if(data.live_execution_enabled!==false||data.can_start!==false)throw Error('Unexpected execution state. Live workspace remains locked.');
  snapshot=data;const s=data.paper_sync,b=s.blueprint,c=b.settings,active=b.traders.filter(t=>t.active!==false);
  $('rc-wallet-address').textContent=short(data.wallet.address);$('rc-wallet-address').title=data.wallet.address||'';
  const localAddress=provider?.isConnected===false?null:provider?.publicKey?.toString();
  $('rc-wallet-status').textContent=data.wallet.ownership_verified?(localAddress===data.wallet.address?'Phantom connected · ownership verified':localAddress?'Different Phantom account selected · saved wallet unchanged':'Wallet link saved · unlock or reconnect Phantom'):'Link Phantom with a message signature. No spending permission.';
  $('rc-connect').textContent=localAddress===data.wallet.address&&data.wallet.ownership_verified?'Phantom connected':data.wallet.ownership_verified?'Reconnect Phantom':'Connect Phantom';
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
 async function load(){if(busy)return;busy=true;controls();try{accept(await request('status'));message('');}catch(e){transportError=true;health();message(e.name==='AbortError'?'Status request timed out. Retrying automatically.':e.message);}finally{busy=false;controls();void restore();}}
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
   message('Phantom linked. Live copying stays locked.');
  }catch(e){message(e.code===4001?'Connection or signature declined. Live trading stays disabled.':e.message||'Could not link Phantom.');}
  finally{busy=false;controls();}
 }
 document.addEventListener('DOMContentLoaded',()=>{
  if(!$('real-copy-section'))return;
  $('rc-refresh').onclick=load;$('rc-connect').onclick=connect;
  $('rc-unlink').onclick=async()=>{if(busy||!snapshot)return;busy=true;controls();try{accept(await request('unlink',{expected_revision:snapshot.wallet.revision}));await window.PhantomSession?.disconnect();message('Wallet link removed. Live copying remains locked.');}catch(e){message(e.message);}finally{busy=false;controls();}};
  setInterval(health,1000);
  window.addEventListener('focus',()=>{if(!$('real-copy-section').hidden)void restore(true);});
  window.addEventListener('load',()=>void restore());
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!$('real-copy-section').hidden)void restore(true);});
  setInterval(()=>{if(!$('real-copy-section').hidden)load();},15000);
  if(location.hash==='#real-copy')setTimeout(()=>document.querySelector('[data-section="real-copy-section"]').click(),0);
  controls();health();
 });
 window.loadRealCopyTrade=load;
})();
