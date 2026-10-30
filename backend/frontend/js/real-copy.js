(() => {
 const $=id=>document.getElementById(id);let snapshot=null,busy=false,provider=null,generation=0;
 const money=n=>Number.isFinite(n)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(n):'—';
 const short=s=>s?`${s.slice(0,6)}…${s.slice(-5)}`:'No wallet linked';
 const node=(tag,value,cls)=>{const el=document.createElement(tag);el.textContent=value;if(cls)el.className=cls;return el;};
 async function request(path,payload){const response=await fetch('/real-copy/'+path,{method:payload?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:payload?{'Content-Type':'application/json'}:undefined,body:payload?JSON.stringify(payload):undefined});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Could not load this workspace.');return data;}
 function message(value){$('rc-message').textContent=value;}
 function controls(){$('rc-connect').disabled=busy||!snapshot;$('rc-unlink').disabled=busy||!snapshot?.wallet.address;$('rc-refresh').disabled=busy;$('rc-start').disabled=true;}
 function render(data){
  if(data.live_execution_enabled!==false||data.can_start!==false)throw Error('Unexpected execution state. Live workspace remains locked.');
  snapshot=data;const s=data.paper_sync,b=s.blueprint,c=b.settings,active=b.traders.filter(t=>t.active!==false);
  $('rc-wallet-address').textContent=short(data.wallet.address);$('rc-wallet-address').title=data.wallet.address||'';
  const localAddress=provider?.publicKey?.toString();
  $('rc-wallet-status').textContent=data.wallet.ownership_verified?(localAddress===data.wallet.address?'Phantom connected · ownership verified':'Wallet verified · reconnect Phantom on this device'):'Link Phantom with a message signature. No spending permission.';
  $('rc-connect').textContent=data.wallet.ownership_verified?'Reconnect Phantom':'Connect Phantom';
  $('rc-count').textContent=active.length;$('rc-size').textContent=money(c.trade_usd);$('rc-paper-return').textContent=Number.isFinite(s.performance.closed_return_percent)?`${s.performance.closed_return_percent>=0?'+':''}${s.performance.closed_return_percent.toFixed(2)}%`:'—';
  $('rc-paper-profit').textContent=`${money(s.performance.realized_pnl_usd)} net · ${s.performance.closed_count} completed paper trades`;
  $('rc-sync').textContent=`Synced ${new Date(s.read_at*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})} · revision ${s.settings_revision}`;
  $('rc-fingerprint').textContent=s.snapshot.slice(0,12);$('rc-guard').textContent=b.entry_protection.one_open_position_per_coin?'One position per coin':'Review required';
  $('rc-cooldown').textContent=`${Number((c.reentry_cooldown_seconds/60).toFixed(1))} min`;
  $('rc-discovery').textContent=c.auto_discover?'Automatic':'Paused';
  $('rc-feed').textContent=s.source_error?`Paper feed: ${s.source_error}`:`Paper feed · ${s.source_health.replaceAll('_',' ')}`;
  const roster=$('rc-roster');roster.replaceChildren();for(const [i,t]of active.entries()){const row=node('div','', 'rc-trader'),avatar=node('span',(t.name||t.wallet).slice(0,2).toUpperCase(),'rc-avatar');avatar.style.setProperty('--rc-hue',String((i*47+230)%360));const info=node('div','');info.append(node('strong',t.name||short(t.wallet)),node('small',short(t.wallet)));const badge=node('span',t.trade_provider==='rpc'?'Chain feed':'Provider feed','rc-pill');row.append(avatar,info,badge);roster.append(row);}if(!active.length)roster.append(node('p','Waiting for the paper roster.'));
  const signals=$('rc-signals');signals.replaceChildren();for(const e of s.signals){const row=node('div','', 'rc-signal');const side=node('span',e.side.toUpperCase(),e.side==='buy'?'rc-buy':'rc-sell');const info=node('div','');info.append(node('strong',short(e.mint)),node('small',e.reason));row.append(side,info);signals.append(row);}if(!s.signals.length)signals.append(node('p','New paper signals will appear here.'));
  controls();
 }
 async function load(){if(busy)return;busy=true;controls();try{render(await request('status'));}catch(e){message(e.message);}finally{busy=false;controls();}}
 function bindProvider(p){if(provider===p)return;provider=p;for(const event of ['accountChanged','disconnect'])p.on?.(event,()=>{generation++;message('Wallet session changed. Reconnect to verify the selected account.');if(snapshot)render(snapshot);});}
 async function connect(){
  if(busy||!snapshot)return;const p=window.phantom?.solana;
  if(!p?.isPhantom){message('Open this page with the Phantom browser extension, or inside Phantom’s mobile browser.');return;}
  busy=true;controls();bindProvider(p);generation++;
  try{
   const linked=await p.connect();const address=linked.publicKey?.toString();
   if(!address)throw Error('Phantom did not return a wallet address.');
   // connect may emit accountChanged; bind this signing attempt after it completes.
   const signingGeneration=generation;
   const challenge=await request('challenge',{wallet_address:address,expected_revision:snapshot.wallet.revision});
   message('Approve the wallet-link message in Phantom. Trading stays disabled.');
   const signed=await p.signMessage(new TextEncoder().encode(challenge.message),'utf8');
   if(generation!==signingGeneration||p.publicKey?.toString()!==address)throw Error('Wallet changed during linking. Connect again.');
   const signature=Array.from(new Uint8Array(signed.signature));if(signature.length!==64)throw Error('Phantom message signature unavailable.');
   render(await request('verify',{challenge_id:challenge.challenge_id,signature:btoa(String.fromCharCode(...signature))}));
   message('Phantom linked. Live copying stays locked.');
  }catch(e){message(e.code===4001?'Connection or signature declined. Live trading stays disabled.':e.message||'Could not link Phantom.');}
  finally{busy=false;controls();}
 }
 document.addEventListener('DOMContentLoaded',()=>{
  if(!$('real-copy-section'))return;
  $('rc-refresh').onclick=load;$('rc-connect').onclick=connect;
  $('rc-unlink').onclick=async()=>{if(busy||!snapshot)return;busy=true;controls();try{render(await request('unlink',{expected_revision:snapshot.wallet.revision}));await provider?.disconnect?.();message('Wallet link removed. Live copying remains locked.');}catch(e){message(e.message);}finally{busy=false;controls();}};
  setInterval(()=>{if(!$('real-copy-section').hidden)load();},15000);
  if(location.hash==='#real-copy')setTimeout(()=>document.querySelector('[data-section="real-copy-section"]').click(),0);
  controls();
 });
 window.loadRealCopyTrade=load;
})();
