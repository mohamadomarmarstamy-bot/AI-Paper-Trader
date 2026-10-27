const {test}=require('node:test'),assert=require('node:assert/strict'),http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
test('Meme workspace pages, queue timer, chart, scanner filters and persistent settings',{timeout:60000},async()=>{
 const root=path.resolve(__dirname,'..'),requests=[];let browser,revision=0,enabled=false,trade=50,liquidity=100000,volume=20000;
 const snapshot=()=>{const now=Date.now()/1000;return {server_time:now,revision,paper:true,live_execution_enabled:false,config:{minimum_liquidity_usd:liquidity,minimum_m5_volume_usd:volume,enabled,discover:true,watchlist:[],trade_usd:trade,max_positions:3,stop_percent:8,target_percent:20,trail_arm_percent:12,trail_percent:6,max_hold_minutes:30,daily_loss_usd:50},health:enabled?'running':'paused',last_cycle:now,cash_usd:950,paper_equity_usd:1003,realized_pnl_usd:1,unrealized_pnl_usd:2,discovery_cooldown_count:4,equity_history:[{at:now-120,equity_usd:1000},{at:now-60,equity_usd:null},{at:now,equity_usd:1003}],positions:[{mint:'held',symbol:'BONK',cost_usd:51,current_modeled_value_usd:53,mark_fresh:true}],closed:[{mint:'closed',symbol:'WIF',reason:'take_profit',entry_usd:50,proceeds_usd:51,pnl_usd:1,closed_at:now}],candidates:[{mint:'waiting',symbol:'PEPE',status:'blocked',reason:'Waiting for a second observation of the same pool, 45–180 seconds apart.',confirmation_started_at:now-10,confirmation_due_at:now+35,confirmation_expires_at:now+170,liquidity_usd:200000,volume_m5_usd:50000,change_m5_percent:5},{mint:'skip',symbol:'SKIP',status:'blocked',reason:'Liquidity below $100,000.'},{mint:'missing',symbol:'<img src=x onerror=alert(1)>',status:'unavailable',reason:'Liquidity data missing.'}],limitations:['Simulation only; source market timestamp unavailable.']};};
 const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://local');let body=null;if(req.method==='POST'){const chunks=[];for await(const c of req)chunks.push(c);body=JSON.parse(Buffer.concat(chunks));}requests.push({path:url.pathname,method:req.method,body});
  if(url.pathname==='/meme-coins/status'||url.pathname==='/meme-coins/scan'){res.setHeader('Content-Type','application/json');return res.end(JSON.stringify(snapshot()));}
  if(url.pathname==='/meme-coins/config'){
   res.setHeader('Content-Type','application/json');if(body.expected_revision!==revision){res.statusCode=409;return res.end(JSON.stringify({detail:'Settings changed. Refresh before saving.'}));}
   if('enabled'in body.changes)enabled=body.changes.enabled;if('trade_usd'in body.changes)trade=body.changes.trade_usd;if('minimum_liquidity_usd'in body.changes)liquidity=body.changes.minimum_liquidity_usd;if('minimum_m5_volume_usd'in body.changes)volume=body.changes.minimum_m5_volume_usd;revision++;return res.end(JSON.stringify(snapshot()));
  }
  const files={'/meme-coins':'meme-coins.html','/meme-coins/trades':'meme-coins.html','/meme-coins/scanner':'meme-coins.html','/meme-coins/settings':'meme-coins.html','/style.css':'style.css','/js/meme-coins.js':'js/meme-coins.js'};
  if(files[url.pathname]){res.setHeader('Content-Type',url.pathname.endsWith('.js')?'application/javascript':url.pathname.endsWith('.css')?'text/css':'text/html');return res.end(fs.readFileSync(path.join(root,files[url.pathname])));}
  res.statusCode=404;res.end();
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  for(const width of [1440,390]){
   enabled=false;trade=50;liquidity=100000;volume=20000;
   const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
   const base='http://127.0.0.1:'+server.address().port;
   await page.goto(base+'/meme-coins');await page.waitForFunction(()=>document.getElementById('meme-cash').textContent==='$950.00');
   fs.mkdirSync('.artifacts/meme-coins',{recursive:true});
   const shot=async name=>{assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);await page.screenshot({path:`.artifacts/meme-coins/${name}-${width}.png`,fullPage:true});};
   assert.equal(await page.locator('img').count(),0);assert.equal(await page.locator('[data-view=home]').isVisible(),true);assert.equal(await page.locator('[data-view=settings]').isVisible(),false);assert.match(await page.locator('#meme-home-holdings').innerText(),/BONK/);assert.equal(await page.locator('#meme-total-pnl').innerText(),'$3.00');assert.equal(await page.locator('#meme-equity-chart svg path').count(),2);await shot('home');
   await page.getByRole('button',{name:'Start paper bot',exact:true}).click();await page.waitForFunction(()=>document.getElementById('meme-status').textContent.startsWith('Running'));
   await page.getByRole('button',{name:'Pause new buys',exact:true}).click();await page.waitForFunction(()=>document.getElementById('meme-status').textContent.startsWith('Paused'));
   await page.locator('nav [data-page=trades]').click();assert.equal(new URL(page.url()).pathname,'/meme-coins/trades');assert.equal(await page.locator('#meme-waiting progress').count(),1);assert.match(await page.locator('#meme-waiting .timer-label').innerText(),/confirmation check in|Buys paused/);assert.match(await page.locator('#meme-positions').innerText(),/BONK/);await shot('trades');
   await page.locator('nav [data-page=scanner]').click();assert.equal(await page.locator('#scan-total').innerText(),'3');assert.equal(await page.locator('#scan-skipped').innerText(),'1');assert.equal(await page.locator('#scan-no-data').innerText(),'1');await page.locator('[data-filter="No data"]').click();assert.equal(await page.locator('#meme-candidates .meme-row').count(),1);assert.match(await page.locator('#meme-candidates').innerText(),/<img/);await page.locator('[data-filter=all]').click();await page.locator('#meme-candidates details > summary').first().click();await page.getByRole('button',{name:'Refresh',exact:true}).click();await page.waitForFunction(()=>!document.getElementById('meme-refresh').disabled);assert.equal(await page.locator('#meme-candidates details').first().evaluate(n=>n.open),true);await shot('scanner');
   await page.locator('nav [data-page=settings]').click();assert.equal(await page.locator('#meme-rules').evaluate(n=>n.open),false);await page.locator('#meme-trade_usd').fill('75');revision++;
   await page.getByRole('button',{name:'Save paper settings',exact:true}).click();await page.waitForFunction(()=>document.getElementById('meme-save-status').textContent.includes('Settings changed'));assert.equal(trade,50);
   await page.reload();await page.waitForFunction(()=>document.getElementById('meme-cash').textContent==='$950.00');assert.equal(await page.locator('[data-view=settings]').isVisible(),true);await page.locator('#meme-trade_usd').fill('75');await page.locator('#meme-minimum_liquidity_usd').fill('25000');await page.locator('#meme-minimum_m5_volume_usd').fill('5000');
   await page.getByRole('button',{name:'Save paper settings',exact:true}).click();await page.waitForFunction(()=>document.getElementById('meme-save-status').textContent.includes('Settings saved'));assert.equal(trade,75);await page.reload();await page.waitForFunction(()=>document.getElementById('meme-minimum_liquidity_usd').value==='25000');assert.equal(await page.locator('#meme-minimum_m5_volume_usd').inputValue(),'5000');await shot('settings');
   await page.locator('nav [data-page=scanner]').click();await page.getByRole('button',{name:'Scan now',exact:true}).click();await page.waitForFunction(()=>!document.getElementById('meme-scan').disabled);await page.goBack();assert.equal(await page.locator('[data-view=settings]').isVisible(),true);
   assert.deepEqual(errors,[]);await page.close();
  }
  assert.equal(requests.some(r=>r.method==='POST'&&!['/meme-coins/config','/meme-coins/scan'].includes(r.path)),false);
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
});
