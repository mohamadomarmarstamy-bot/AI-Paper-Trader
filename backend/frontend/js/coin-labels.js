/* Display names only. All orders and links continue to use the exact mint. */
(() => {
 const cache=new Map(),pending=new Set(),targets=new Map();let running=false,scheduled=false;
 const fallback=(mint,symbol)=>typeof symbol==='string'&&symbol.trim()&&symbol!==mint&&!mint.startsWith(symbol)?symbol.trim():`Unknown coin · ${mint.slice(0,5)}…${mint.slice(-4)}`;
 function paint(el,mint,label){el.textContent=label;el.title=mint;el.setAttribute('aria-label',`${label} · ${mint}`);}
 function schedule(){if(!scheduled&&!running){scheduled=true;queueMicrotask(flush);}}
 function attach(el,mint,symbol){
  if(typeof mint!=='string'||!mint)return el;
  el.dataset.coinMint=mint;const saved=cache.get(mint);paint(el,mint,saved?.label||fallback(mint,symbol));
  if(saved&&saved.until>Date.now())return el;
  if(!targets.has(mint))targets.set(mint,new Set());targets.get(mint).add(el);pending.add(mint);schedule();return el;
 }
 async function flush(){
  scheduled=false;if(running)return;running=true;
  try{while(pending.size){
   const batch=Array.from(pending).slice(0,30);batch.forEach(mint=>pending.delete(mint));
   const active=batch.filter(mint=>{const nodes=targets.get(mint);if(nodes)for(const el of nodes)if(!el.isConnected)nodes.delete(el);if(!nodes?.size){targets.delete(mint);return false;}return true;});
   if(!active.length)continue;
   const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),6000);let data={};
   try{const response=await fetch('/meme-coins/token-labels?mints='+encodeURIComponent(active.join(',')),{credentials:'same-origin',cache:'no-store',signal:controller.signal});if(response.ok)data=(await response.json()).labels||{};}catch{}finally{clearTimeout(timer);}
   for(const mint of active){
    const value=data[mint],name=typeof value?.name==='string'?value.name.trim():'',symbol=typeof value?.symbol==='string'?value.symbol.trim():'';
    const label=name?(symbol&&symbol.toLowerCase()!==name.toLowerCase()?`${name} (${symbol})`:name):symbol;
    cache.set(mint,{label,until:Date.now()+(label?600000:60000)});
    if(label)for(const el of targets.get(mint)||[])if(el.isConnected&&el.dataset.coinMint===mint)paint(el,mint,label);
    targets.delete(mint);
   }
   while(cache.size>2048)cache.delete(cache.keys().next().value);
  }}finally{running=false;if(pending.size)schedule();}
 }
 window.MemeCoinLabels={attach};
})();
