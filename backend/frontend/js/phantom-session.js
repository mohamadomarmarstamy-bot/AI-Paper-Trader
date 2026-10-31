/* Restore only Phantom's existing trust. Never sign, spend, or persist a key. */
(() => {
 const key='phantom-auto-connect-blocked';let blocked=false,pending=null;
 const provider=()=>window.phantom?.solana?.isPhantom?window.phantom.solana:null;
 function isBlocked(){try{return blocked||localStorage.getItem(key)==='1';}catch{return blocked;}}
 function setBlocked(value){blocked=value;try{if(value)localStorage.setItem(key,'1');else localStorage.removeItem(key);}catch{/* Storage may be unavailable in a wallet browser. */}}
 async function reconnect(){
  const p=provider();if(!p||isBlocked())return null;
  if(p.isConnected&&p.publicKey)return p.publicKey;
  if(pending)return pending;
  pending=(async()=>{let timer;try{
   const connection=p.connect({onlyIfTrusted:true}).then(async result=>{if(isBlocked()){await p.disconnect?.();return null;}return result;});
   const result=await Promise.race([connection,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Phantom reconnect timed out.')),5000);})]);
   return result?.publicKey||null;
  }catch{return null;}finally{clearTimeout(timer);pending=null;}})();
  return pending;
 }
 async function connect(){const p=provider();if(!p)throw Error('Phantom is not available.');const result=await p.connect();setBlocked(false);return result;}
 async function disconnect(){setBlocked(true);await provider()?.disconnect?.();}
 window.PhantomSession={provider,reconnect,connect,disconnect,block:()=>setBlocked(true)};
})();
