const {test}=require('node:test'),assert=require('node:assert/strict'),http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
test('Solana paper page controls, accounting labels, revision conflicts and mobile layout',{timeout:60000},async()=>{
 const root=path.resolve(__dirname,'..'),requests=[];let browser,revision=0,enabled=false,trade=50,liquidity=100000,volume=20000;
 const snapshot=()=>({revision,paper:true,live_execution_enabled:false,config:{minimum_liquidity_usd:liquidity,minimum_m5_volume_usd:volume,enabled,discover:true,watchlist:[],trade_usd:trade,max_positions:3,stop_percent:8,target_percent:20,trail_arm_percent:12,trail_percent:6,max_hold_minutes:30,daily_loss_usd:50},health:enabled?'running':'paused',cash_usd:1000,paper_equity_usd:1000,realized_pnl_usd:0,positions:[],closed:[],candidates:[{mint:'1'.repeat(31)+'2',symbol:'<img src=x onerror=alert(1)>',status:'blocked',reason:'Waiting for a second observation of the same pool, 45–180 seconds apart.'}],limitations:['Simulation only; source market timestamp unavailable.']});
 const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://local');let body=null;if(req.method==='POST'){const chunks=[];for await(const c of req)chunks.push(c);body=JSON.parse(Buffer.concat(chunks));}requests.push({path:url.pathname,method:req.method,body});
  if(url.pathname==='/meme-coins/status'||url.pathname==='/meme-coins/scan'){res.setHeader('Content-Type','application/json');return res.end(JSON.stringify(snapshot()));}
  if(url.pathname==='/meme-coins/config'){
   res.setHeader('Content-Type','application/json');if(body.expected_revision!==revision){res.statusCode=409;return res.end(JSON.stringify({detail:'Settings changed. Refresh before saving.'}));}
   if('enabled'in body.changes)enabled=body.changes.enabled;if('trade_usd'in body.changes)trade=body.changes.trade_usd;if('minimum_liquidity_usd'in body.changes)liquidity=body.changes.minimum_liquidity_usd;if('minimum_m5_volume_usd'in body.changes)volume=body.changes.minimum_m5_volume_usd;revision++;return res.end(JSON.stringify(snapshot()));
  }
  const files={'/':'meme-coins.html','/style.css':'style.css','/js/meme-coins.js':'js/meme-coins.js'};
  if(files[url.pathname]){res.setHeader('Content-Type',url.pathname.endsWith('.js')?'application/javascript':url.pathname.endsWith('.css')?'text/css':'text/html');return res.end(fs.readFileSync(path.join(root,files[url.pathname])));}
  res.statusCode=404;res.end();
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  for(const width of [1440,390]){
   enabled=false;trade=50;
   const page=await browser.newPage({viewport:{width,height:1100}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForFunction(()=>document.getElementById('meme-cash').textContent==='$1000.00');
   assert.equal(await page.locator('img').count(),0);
   assert.equal(await page.locator('#meme-rules').evaluate(n=>n.open),false);
   assert.equal(await page.locator('#meme-data-notes').evaluate(n=>n.open),false);
   assert.match(await page.locator('#meme-candidates').innerText(),/Watching/);
   await page.locator('#meme-candidates details > summary').click();
   await page.getByRole('button',{name:'Refresh',exact:true}).click();await page.waitForFunction(()=>!document.getElementById('meme-refresh').disabled);
   assert.equal(await page.locator('#meme-candidates details').evaluate(n=>n.open),true);
   await page.getByRole('button',{name:'Start paper bot',exact:true}).click();await page.waitForFunction(()=>document.getElementById('meme-status').textContent.startsWith('Running'));
   await page.getByRole('button',{name:'Pause new buys',exact:true}).click();await page.waitForFunction(()=>document.getElementById('meme-status').textContent.startsWith('Paused'));
   await page.locator('#meme-settings-panel > summary').click();
   await page.locator('#meme-trade_usd').fill('75');
   // Another operator changes configuration while an unsaved form is open.
   revision++;await page.getByRole('button',{name:'Refresh',exact:true}).click();await page.waitForFunction(()=>!document.getElementById('meme-refresh').disabled);
   await page.getByRole('button',{name:'Save paper settings',exact:true}).click();await page.waitForFunction(()=>document.getElementById('meme-status').textContent.includes('Settings changed'));
   assert.equal(trade,50);
   await page.reload();await page.waitForFunction(()=>document.getElementById('meme-cash').textContent==='$1000.00');
   await page.locator('#meme-settings-panel > summary').click();
   await page.locator('#meme-minimum_liquidity_usd').fill('25000');await page.locator('#meme-minimum_m5_volume_usd').fill('5000');await page.locator('#meme-trade_usd').fill('75');await page.getByRole('button',{name:'Save paper settings',exact:true}).click();await page.waitForFunction(()=>!document.getElementById('meme-start').disabled);assert.equal(trade,75);assert.equal(liquidity,25000);assert.equal(volume,5000);assert.match(await page.locator('#meme-save-status').innerText(),/Settings saved/);await page.reload();await page.waitForFunction(()=>document.getElementById('meme-cash').textContent==='$1000.00');await page.locator('#meme-settings-panel > summary').click();assert.equal(await page.locator('#meme-minimum_liquidity_usd').inputValue(),'25000');assert.equal(await page.locator('#meme-minimum_m5_volume_usd').inputValue(),'5000');
   await page.getByRole('button',{name:'Scan now',exact:true}).click();await page.waitForFunction(()=>!document.getElementById('meme-scan').disabled);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
   assert.deepEqual(errors,[]);fs.mkdirSync('.artifacts/meme-coins',{recursive:true});await page.screenshot({path:`.artifacts/meme-coins/${width}.png`,fullPage:true});await page.close();
  }
  assert.equal(requests.some(r=>r.method==='POST'&&!['/meme-coins/config','/meme-coins/scan'].includes(r.path)),false);
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
});
