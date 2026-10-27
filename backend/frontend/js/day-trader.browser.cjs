const {test}=require('node:test'),assert=require('node:assert/strict'),http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
test('Day trader experiments work on desktop/mobile without execution calls',{timeout:60000},async()=>{
 const root=path.resolve(__dirname,'..');const requests=[];let browser,fail=false;
 const frozen=Date.now()/1000;
 const fixture={summary_scope:'Latest experiments, not portfolio profit.',summary:[{strategy:'opening_range_breakout',modeled_samples:0,base_cost_modeled_pnl_usd:null,base_cost_matched_hold_pnl_usd:null}],limitations:['IEX covers one venue.'],results:[{symbol:'TEST',request_id:'saved',frozen_at:frozen,decision:{status:'evaluated',reason:'Fixed rules',feed:'iex',opening_rvol:2,vwap:10,setups:[{strategy:'opening_range_breakout',status:'candidate',reason:'Confirmed setup',shares:20,entry_ask:10,stop:9.5,target:11,planned_risk_usd:10.6,expires_at:frozen+3600}]},outcomes:null}]};
 const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://local');let body=null;
  if(req.method==='POST'){const chunks=[];for await(const c of req)chunks.push(c);body=JSON.parse(Buffer.concat(chunks));}
  requests.push({method:req.method,path:url.pathname,body});
  if(url.pathname.startsWith('/auto-trader/day-trader')){
   res.setHeader('Content-Type','application/json');if(fail){res.statusCode=503;return res.end(JSON.stringify({detail:'Synthetic data failure; no order sent.'}));}
   return res.end(JSON.stringify({...fixture,...(url.pathname.endsWith('/evaluate')?{evaluation:[{status:'pending',evaluate_after:frozen+3660}]}:{})}));
  }
  if(url.pathname==='/'){
   const html=fs.readFileSync(path.join(root,'index.html'),'utf8');const start=html.indexOf('<article class="panel" aria-labelledby="day-trader-title">');
   res.setHeader('Content-Type','text/html');return res.end('<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/style.css"></head><body><main style="max-width:1000px;padding:12px;margin:auto">'+html.slice(start,html.indexOf('<section class="scanner-page-grid">',start))+'</main><script src="/js/day-trader.js"></script></body></html>');
  }
  if(['/style.css','/js/day-trader.js'].includes(url.pathname)){res.setHeader('Content-Type',url.pathname.endsWith('.js')?'application/javascript':'text/css');return res.end(fs.readFileSync(path.join(root,url.pathname)));}
  res.statusCode=404;res.end();
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  for(const width of [1440,390]){
   const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto('http://127.0.0.1:'+server.address().port);
   await page.locator('#day-trader-symbol').fill('test');
   fail=true;await page.getByRole('button',{name:'Freeze intraday experiment'}).click();await page.waitForFunction(()=>document.getElementById('day-trader-note').textContent.includes('Synthetic'));
   const first=requests.at(-1).body.request_id;fail=false;
   await page.getByRole('button',{name:'Freeze intraday experiment'}).click();await page.getByRole('button',{name:'Evaluate saved forward outcome'}).waitFor();
   assert.equal(requests.filter(r=>r.path.endsWith('/observe')).at(-1).body.request_id,first);
   assert.match(await page.locator('#day-trader-results').innerText(),/Unavailable/);
   await page.getByRole('button',{name:'Evaluate saved forward outcome'}).click();await page.waitForFunction(()=>document.getElementById('day-trader-note').textContent.includes('Not due yet'));
   await page.getByRole('button',{name:'New observation'}).click();await page.getByRole('button',{name:'Freeze intraday experiment'}).click();await page.waitForFunction(()=>document.getElementById('day-trader-note').textContent.includes('Saved research'));
   assert.notEqual(requests.filter(r=>r.path.endsWith('/observe')).at(-1).body.request_id,first);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
   assert.deepEqual(errors,[]);fs.mkdirSync('.artifacts/day-trader',{recursive:true});await page.screenshot({path:`.artifacts/day-trader/${width}.png`,fullPage:true});await page.close();
  }
  assert.equal(requests.some(r=>r.method==='POST'&&!['/auto-trader/day-trader/observe','/auto-trader/day-trader/evaluate'].includes(r.path)),false);
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
});
