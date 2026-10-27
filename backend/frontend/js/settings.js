"use strict";
(()=>{
 const get=id=>document.getElementById(id),keys=['position_size_percent','max_order_size_percent','max_position_size_percent','max_open_positions','stop_loss_percent','take_profit_percent','profit_lock_trigger_percent','profit_lock_percent','trailing_stop_percent','daily_profit_target_percent','daily_loss_limit_percent'],prefs=['notify_trade_events','notify_protection_events','notify_scanner_events'];
 const field=key=>get('setting-'+key.replaceAll('_','-'));
 let current=null,dirty=false,busy=false,loadVersion=0,pending=null;
 const status=text=>{if(get('settings-save-status'))get('settings-save-status').textContent=text;};
 async function request(path='',body){const response=await fetch('/auto-trader/settings'+path,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});const data=await response.json();if(!response.ok||data.success!==true)throw Error(data.detail||data.error||'Settings unavailable ('+response.status+').');return data;}
 function render(data){current=data;for(const key of keys)if(field(key))field(key).value=String(data.settings[key]);for(const key of prefs)if(get('setting-'+key))get('setting-'+key).checked=data.settings[key];if(get('settings-pause-new-entries'))get('settings-pause-new-entries').checked=data.settings.pause_new_entries;
  if(get('settings-pause-new-entries-description'))get('settings-pause-new-entries-description').textContent=data.settings.pause_new_entries?'New broad automatic buys are paused.':'Entry pause is off; server automation permissions still apply.';
  if(get('settings-runtime')){const r=data.runtime||{};get('settings-runtime').textContent='Automatic trader: '+(r.automatic_trader_enabled?'running':'paused')+'. Scanner health: '+(r.scanner_health||'unavailable')+'. Error logging and health monitoring stay on. AI model training and strategy experiments are disabled.';}
  if(get('settings-audit')){get('settings-audit').replaceChildren();for(const item of data.audit||[]){const row=document.createElement('p');row.textContent=new Date(item.created_at).toLocaleString()+' · '+item.actor+' · '+Object.entries(item.after).map(([k,v])=>k.replaceAll('_',' ')+': '+item.before[k]+' → '+v).join('; ');get('settings-audit').appendChild(row);}}
  window.appSettings=data.settings;document.dispatchEvent(new CustomEvent('app:preferences',{detail:data.settings}));dirty=false;if(get('settings-pause-new-entries'))get('settings-pause-new-entries').indeterminate=false;
 }
 function buttons(){for(const id of ['settings-save-button','settings-refresh','settings-restore','settings-emergency-pause','settings-undo'])if(get(id))get(id).disabled=busy||!current||(id==='settings-undo'&&!current.audit?.length);if(get('settings-pause-new-entries'))get('settings-pause-new-entries').disabled=busy||!current;}
 async function load(){const version=++loadVersion;try{const data=await request();if(version!==loadVersion)return;if(dirty){status('Settings changed or were refreshed. Your draft is preserved; reload saved values to discard it.');return;}render(data);status('Saved settings loaded. Changes affect subsequent decisions.');}catch(error){status(error.message);}buttons();}
 async function save(changes,path=''){if(busy||!current)return;const keepDraft=dirty&&!path&&Object.keys(changes).length===1&&Object.hasOwn(changes,'pause_new_entries'),draft=keepDraft?Object.fromEntries(keys.map(k=>[k,field(k)?.value])):null;busy=true;buttons();const identity=JSON.stringify([path,changes,current.revision]);if(pending?.identity!==identity)pending={identity,id:crypto.randomUUID()};status('Saving settings…');
  try{const data=await request(path,path?{expected_revision:current.revision,request_id:pending.id}:{changes,expected_revision:current.revision,request_id:pending.id});pending=null;dirty=false;render({...current,...data});await load();if(keepDraft){for(const [k,v] of Object.entries(draft))if(field(k))field(k).value=v;dirty=true;}status('Settings saved. '+(data.effect||''));document.dispatchEvent(new CustomEvent('app:settings-changed',{detail:{source:'settings'}}));}
  catch(error){if(Object.hasOwn(changes,'pause_new_entries')&&get('settings-pause-new-entries'))get('settings-pause-new-entries').indeterminate=true;status(error.message+' Your input was kept. Reload saved values before resolving a conflict.');}
  finally{busy=false;buttons();}
 }
 document.addEventListener('DOMContentLoaded',()=>{
  const form=get('auto-trader-settings-form');form?.addEventListener('input',()=>{dirty=true;});
  form?.addEventListener('submit',event=>{event.preventDefault();const changes={};for(const key of keys){const value=Number(field(key)?.value);if(field(key)?.value===''||!Number.isFinite(value)){status('Enter a finite number for '+key);return;}if(value!==current?.settings[key])changes[key]=value;}for(const key of prefs)if(get('setting-'+key)&&get('setting-'+key).checked!==current?.settings[key])changes[key]=get('setting-'+key).checked;if(!Object.keys(changes).length){status('No changed settings to save.');return;}save(changes);});
  get('settings-pause-new-entries')?.addEventListener('change',()=>save({pause_new_entries:get('settings-pause-new-entries').checked}));
  get('settings-refresh')?.addEventListener('click',()=>{dirty=false;load();});
  get('settings-emergency-pause')?.addEventListener('click',()=>save({pause_new_entries:true}));
  get('settings-restore')?.addEventListener('click',()=>save({position_size_percent:2,max_order_size_percent:2.5,max_position_size_percent:5,max_open_positions:50,stop_loss_percent:2,take_profit_percent:4,profit_lock_trigger_percent:.75,profit_lock_percent:.25,trailing_stop_percent:1,daily_profit_target_percent:5,daily_loss_limit_percent:1,pause_new_entries:true,notify_trade_events:true,notify_protection_events:true,notify_scanner_events:true}));
  get('settings-undo')?.addEventListener('click',()=>{const id=current?.audit?.[0]?.id;if(id)save({},'/undo/'+encodeURIComponent(id));});
  document.addEventListener('app:settings-changed',event=>{if(event.detail?.source!=='settings')load();});
  load();buttons();
 });
 window.loadMainSettings=load;
})();
