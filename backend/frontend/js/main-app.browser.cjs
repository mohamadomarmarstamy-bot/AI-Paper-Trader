const {test}=require('node:test');
const assert=require('node:assert/strict');
const http=require('node:http');
const fs=require('node:fs');
const path=require('node:path');
const {randomUUID}=require('node:crypto');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const defaults={position_size_percent:2,max_order_size_percent:2.5,max_position_size_percent:5,max_open_positions:50,stop_loss_percent:2,take_profit_percent:4,profit_lock_trigger_percent:.75,profit_lock_percent:.25,trailing_stop_percent:1,daily_profit_target_percent:5,daily_loss_limit_percent:1,stock_min_price_usd:2.0,stock_max_price_usd:20.0,stock_min_float_millions:0.0,stock_max_float_millions:20.0,stock_min_volume_ratio:2.0,stock_min_average_volume:250000.0,stock_max_spread_percent:0.5,stock_min_day_change_percent:2.0,stock_min_gap_percent:0.0,stock_max_atr_percent:8.0,stock_news_max_age_hours:24.0,stock_filters_enabled:true,stock_require_recent_news:false,stock_auto_adjust_enabled:true,stock_min_time_volume_ratio:1.5,stock_max_vwap_distance_percent:5,stock_slippage_bps_per_side:10,stock_fee_bps_per_side:0,stock_intraday_enabled:true,stock_require_entry_setup:true,stock_auto_rollback_enabled:true,stock_parallel_validation_enabled:true,pause_new_entries:true,notify_trade_events:true,notify_protection_events:true,notify_scanner_events:true};

test('main app desktop/mobile settings, Jarvis actions, drawer and voice fallback',{timeout:120000},async()=>{
 let settings,revision,audit,profile,proposals,chatPosts,speechPosts,delaySpeech=false,releaseSpeech=null;
 const reset=()=>{settings={...defaults};revision='0';audit=[];profile={preferred_name:'Omar',address_as:'Sir',language:'en-US',voice:'cedar',timezone:'America/New_York',wake_phrase:'Hey Jarvis',voice_activation:false,automatic_greeting:false};proposals=new Map();chatPosts=[];speechPosts=0;};reset();
 const runtime={automatic_trader_enabled:false,scanner_health:'disabled'};
 const snapshot=()=>({success:true,paper:true,settings:{...settings},defaults:{...defaults},stock_learning:{pending_closed_count:12,email:{configured:true,pending:1,accepted:2,attention:0},experiments:[{state:'pending',followup_count:7,before:{stock_min_time_volume_ratio:1.5},after:{stock_min_time_volume_ratio:1.65},result:{}}],reviews:[{created_at:new Date().toISOString(),changed:true,wins:8,losses:17,realized_pnl_usd:-25,estimated_net_pnl_usd:-30,email_status:'pending',exit_analysis:{observed_trades:10,total_trades:25,median_giveback_percent:1.2,basis:'Sampled marks only.',actual_net_mean_return_percent:-.5,shadow_exits:[{policy:'1% stop / 2% target',trades:10,estimated_net_mean_return_percent:.2}]},reason:"Losing batch risk reduction",before:{position_size_percent:2},after:{position_size_percent:1.8},comparisons:[]}]},revision,audit:[...audit],runtime});
 const mutate=(changes,actor='settings')=>{const before=Object.fromEntries(Object.keys(changes).map(k=>[k,settings[k]]));Object.assign(settings,changes);revision=String(Number(revision)+1);const receipt={id:randomUUID(),before,after:changes,actor,created_at:new Date().toISOString(),revision_after:revision};audit.unshift(receipt);return {...snapshot(),receipt,effect:'Subsequent decisions only; existing orders are unchanged.'};};
 let origin;
 const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost');const send=(data,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
  if(req.method==='POST')assert.equal(req.headers.origin,origin);
  let body={};if(req.method==='POST'){const parts=[];for await(const chunk of req)parts.push(chunk);body=JSON.parse(Buffer.concat(parts).toString());}
  if(url.pathname==='/auto-trader/stock-research')return send({success:true,paper:true,enabled:true,performance:{summary:{trades:2,gross_pnl_usd:10,estimated_net_pnl_usd:8,expectancy_usd:4,win_rate_percent:50},groups:{setup:[{label:'Breakout',trades:2,costed_trades:2,estimated_net_pnl_usd:8,expectancy_usd:4,win_rate_percent:50,profit_factor:1.5,sample_note:'Small sample'}],session:[],float:[]},basis:'Actual closed paper trades.',cost_model:{stock_slippage_bps_per_side:10,stock_fee_bps_per_side:0},coverage:{known_setup:2,known_time:2,known_float:1,missing_exit_dates_excluded:0}},parallel:{minimum_pairs:50,minimum_sessions:5,notional_usd:1000,basis:'Prospective paired quote markouts. No orders.',runtime:{last_tick:Date.now()/1000,last_error:null},trials:[{id:'trial',origin:'learner',state:'collecting',changes:{stock_min_time_volume_ratio:1.65},baseline:{stock_min_time_volume_ratio:1.5},stats:{matured_pairs:12,open:2,unavailable:1,sessions:2,symbols:5,different_decisions:4,baseline_shadow_pnl_usd:-10,candidate_shadow_pnl_usd:5,baseline_mean_percent:-.1,candidate_mean_percent:.05,improvement_percent:.15,baseline_selected:12,candidate_selected:8},decision:{reason:'More prospective evidence needed.'}}]}});
  if(url.pathname==='/auto-trader/settings'&&req.method==='GET')return send(snapshot());
  if(url.pathname==='/auto-trader/settings'&&req.method==='POST'){
   if(body.expected_revision!==revision)return send({detail:'Settings changed elsewhere. Refresh before saving.'},409);
   return send(mutate(body.changes));
  }
  if(url.pathname.startsWith('/auto-trader/settings/undo/'))return send(mutate(audit[0].before,'undo'));
  if(url.pathname==='/auto-trader/status')return send({health:'disabled',enabled:false});
  if(url.pathname==='/account/trade-activity')return send({buys:[],sells:[],completed:[],generated_at:Date.now()/1000,broker_available:true});
  if(url.pathname==='/auto-trader/logs')return send({logs:[]});
  if(url.pathname==='/jarvis/status')return send({success:true,chat_ready:true,settings_ready:true,trading_access:false});
  if(url.pathname==='/jarvis/profile'){if(req.method==='POST')Object.assign(profile,body);return send({success:true,profile});}
  if(url.pathname==='/jarvis/chat'){
   chatPosts.push(body);assert.ok(body.request_id);assert.ok(body.ui_context);
   if(body.message.toLowerCase().includes('set stop loss to 2')){const result=mutate({stop_loss_percent:2},'jarvis');return send({success:true,reply:'Saved stop loss at 2%. Existing orders are unchanged.',receipt:result.receipt,settings:snapshot(),proposals:[]});}
   const proposal={id:randomUUID(),before:{take_profit_percent:settings.take_profit_percent},after:{take_profit_percent:6},effect:'Applies to subsequent decisions only.'};proposals.set(proposal.id,proposal);
   return send({success:true,reply:'Review the proposed setting below.',proposals:[proposal]});
  }
  if(url.pathname.startsWith('/jarvis/proposals/')&&url.pathname.endsWith('/apply')){const id=url.pathname.split('/')[3];return send(mutate(proposals.get(id).after,'jarvis_preview'));}
  if(url.pathname==='/jarvis/speech'){speechPosts++;if(delaySpeech)await new Promise(r=>{releaseSpeech=r;});return send({detail:'Synthetic speech failure'},503);}
  if(url.pathname==='/'){
   let html=fs.readFileSync(path.join(root,'index.html'),'utf8');
   const tested=new Set(['app.js','jarvis.js','profile.js','settings.js','trade-activity.js','notifications.js','stock-research.js']);
   html=html.replace(/<script\b[^>]*src="([^"]+)"[^>]*><\/script>/g,(tag,src)=>tested.has(src.split('/').pop().split('?')[0])?tag:'');
   res.setHeader('Content-Type','text/html');res.end(html);return;
  }
  const filename=path.resolve(root,'.'+url.pathname);
  if(filename.startsWith(root+path.sep)&&fs.existsSync(filename)&&fs.statSync(filename).isFile()){res.setHeader('Content-Type',filename.endsWith('.js')?'application/javascript':'text/css');res.end(fs.readFileSync(filename));return;}
  return send({});
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));origin='http://127.0.0.1:'+server.address().port;
 let browser;
 try{
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  for(const width of [1440,390]){
   reset();const context=await browser.newContext({viewport:{width,height:1000}});const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',route=>route.request().url().startsWith(origin)?route.continue():route.abort());
   await page.addInitScript(()=>{
    window.loadAccount=async()=>{};window.watchlist=['AAPL','TEST'];window.__browserSpeaks=0;window.__micStarts=0;
    class Recognition {constructor(){window.__recognition=this;}start(){window.__micStarts++;this.onstart?.();}stop(){this.onend?.();}abort(){this.onend?.();}}
    window.SpeechRecognition=Recognition;
    window.SpeechSynthesisUtterance=class{constructor(text){this.text=text;}};
    Object.defineProperty(window,'speechSynthesis',{value:{getVoices:()=>[{lang:'en-GB'}],cancel(){},speak(utterance){window.__browserSpeaks++;utterance.onstart?.();setTimeout(()=>utterance.onend?.(),10);}}});
   });
   await page.goto(origin);await page.waitForFunction(()=>document.getElementById('jarvis-status-text').textContent==='Online · app tools connected');
   assert.equal(await page.evaluate(()=>window.API_URL),origin);
   await page.locator('.nav-button[data-section="strategy-section"]').click();await page.getByText(/Saved results loaded/).waitFor();assert.match(await page.locator('#stock-cohort-setup').innerText(),/Breakout/);assert.match(await page.locator('#stock-parallel-trials').innerText(),/12 matured pairs/);await page.locator('#stock-research-window').selectOption('30');await page.getByText(/Saved results loaded/).waitFor();
   await page.locator('.nav-button[data-section="settings-section"]').click();
   await page.getByText('Saved settings loaded. Changes affect subsequent decisions.',{exact:true}).waitFor();
   assert.equal(await page.locator('#settings-section input[type=checkbox][disabled]').count(),0);
   assert.equal(await page.locator('#settings-section').getByText('Not connected yet',{exact:true}).count(),0);
   assert.equal(await page.locator('#setting-stock-max-float-millions').inputValue(),'20');assert.equal(await page.locator('#setting-stock-min-price-usd').inputValue(),'2');assert.match(await page.locator('#stock-learning-status').innerText(),/12\/25/);assert.match(await page.locator('#stock-learning-reviews').innerText(),/2 → 1.8/);assert.match(await page.locator('#stock-experiment-status').innerText(),/7\/25/);assert.match(await page.locator('#stock-review-email-status').innerText(),/1 queued.*2 accepted/);assert.match(await page.locator('#stock-learning-reviews').innerText(),/Review email: queued/);assert.match(await page.locator('#stock-learning-reviews').innerText(),/estimated net \$-30/);assert.equal(await page.locator('#setting-stock-min-time-volume-ratio').inputValue(),'1.5');await page.locator('#setting-stock-min-time-volume-ratio').fill('1.8');await page.locator('#setting-stock-max-float-millions').fill('18');
   await page.locator('#setting-stop-loss-percent').fill('3');await page.getByRole('button',{name:'Save Trading Settings',exact:true}).click();
   await page.getByText(/Settings saved\./).waitFor();assert.equal(settings.stop_loss_percent,3);assert.equal(settings.stock_max_float_millions,18);assert.equal(settings.stock_min_time_volume_ratio,1.8);
   await page.getByRole('button',{name:'Undo last settings change',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('setting-stop-loss-percent').value==='2');assert.equal(settings.stop_loss_percent,2);
   await page.locator('#setting-stop-loss-percent').fill('4');mutate({take_profit_percent:5},'other_tab');
   await page.getByRole('button',{name:'Save Trading Settings',exact:true}).click();await page.getByText(/Settings changed elsewhere/).waitFor();assert.equal(settings.stop_loss_percent,2);assert.equal(await page.locator('#setting-stop-loss-percent').inputValue(),'4');
   await page.getByRole('button',{name:'Reload saved values & status',exact:true}).click();await page.waitForFunction(()=>document.getElementById('setting-stop-loss-percent').value==='2');
   await page.getByRole('button',{name:'Restore defaults & pause new entries',exact:true}).click();await page.getByText(/Settings saved\./).waitFor();assert.equal(settings.take_profit_percent,4);assert.equal(settings.pause_new_entries,true);
   await page.locator('#setting-notify_trade_events').uncheck();await page.getByRole('button',{name:'Save Trading Settings',exact:true}).click();await page.getByText(/Settings saved\./).waitFor();assert.equal(settings.notify_trade_events,false);
   await page.locator('.nav-button[data-section="jarvis-section"]').click();await page.locator('#jarvis-drawer.open').waitFor();await page.locator('#jarvis-input').fill('Set stop loss to 2%');await page.locator('#jarvis-send-button').click();await page.getByText('Saved change: stop loss percent: 2 → 2',{exact:true}).waitFor();assert.equal(chatPosts.length,1);
   await page.locator('#jarvis-input').fill('Suggest a take profit adjustment');await page.locator('#jarvis-send-button').click();await page.getByRole('button',{name:'Apply settings preview',exact:true}).click();await page.getByText(/Saved: take profit percent: 4 → 6/).waitFor();assert.equal(settings.take_profit_percent,6);
   await page.getByRole('button',{name:'Close Jarvis',exact:true}).click();await page.locator('#jarvis-drawer.open').waitFor({state:'hidden'});
   await page.locator('.nav-button[data-section="settings-section"]').click();await page.waitForFunction(()=>document.getElementById('setting-take-profit-percent').value==='6');
   await page.locator('.nav-button[data-section="profile-section"]').click();await page.getByRole('button',{name:'Use British assistant voice',exact:true}).click();await page.getByText('Profile saved.',{exact:true}).waitFor();assert.equal(profile.language,'en-GB');assert.equal(profile.voice,'cedar');
   await page.getByRole('button',{name:'Preview AI voice',exact:true}).click();await page.waitForFunction(()=>window.__browserSpeaks===1&&!document.body.classList.contains('jarvis-speaking'));assert.equal(speechPosts,1);
   delaySpeech=true;const pendingSpeech=page.waitForRequest(req=>req.url()===origin+'/jarvis/speech');
   await page.getByRole('button',{name:'Preview AI voice',exact:true}).click();await pendingSpeech;
   await page.locator('#jarvis-orb').click();await page.locator('#jarvis-stop-button').click();releaseSpeech?.();releaseSpeech=null;
   await page.waitForTimeout(100);assert.equal(await page.evaluate(()=>window.__browserSpeaks),1);
   delaySpeech=false;await page.getByRole('button',{name:'Close Jarvis',exact:true}).click();
   await page.locator('#jarvis-profile-voice-activation').check();await page.locator('#jarvis-profile-save').click();await page.getByText('Profile saved.',{exact:true}).waitFor();
   await page.locator('#jarvis-orb').click();await page.locator('#jarvis-drawer.open').waitFor();await page.locator('#jarvis-listen-button').click();
   await page.evaluate(()=>window.__recognition.onresult({results:[[{transcript:'set stop loss to 2%'}]]}));assert.equal(chatPosts.length,2);
   await page.evaluate(()=>window.__recognition.onresult({results:[[{transcript:'Hey Jarvis, set stop loss to 2%'}]]}));await page.waitForFunction(()=>document.getElementById('jarvis-actions').textContent.includes('Saved change'));assert.equal(chatPosts.length,3);
   await page.locator('#jarvis-voice-end').click();const micStarts=await page.evaluate(()=>window.__micStarts);await page.getByRole('button',{name:'Close Jarvis',exact:true}).click();
   await page.waitForTimeout(450);assert.equal(await page.evaluate(()=>window.__micStarts),micStarts,'closed voice sessions must not restart the microphone');
   assert.deepEqual(errors,[]);await context.close();
  }
 }finally{releaseSpeech?.();await browser?.close();await new Promise(r=>server.close(r));}
});
