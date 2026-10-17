"use strict";
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require(process.env.PAPER_PLAYWRIGHT_MODULE||'playwright');
const frontend=path.resolve(__dirname,'..');
const stamp='2026-10-17T00:00:00Z';
const group=rows=>({rows,total:rows.length,has_more:false});
const fixture=()=>({paper:true,scope:'pro_ticker_strategy_ledger',generated_at:stamp,order_execution_enabled:false,automatic_release_enabled:false,broker_reconciled:false,
 budget:{configured_cents:5000000,committed_cents:10000,available_cents:4990000,overallocated_cents:0},
 positions:group([{symbol:'AAPL',shares:3,updated_at:stamp}]),reservations:group([{id:'test-reservation',symbol:'AAPL',notional_cents:10000,status:'filled',updated_at:stamp,broker_order_id:'test-entry'}]),
 journal:group([{event_id:'entry',broker_order_id:'test-entry',symbol:'AAPL',side:'buy',shares:5,recorded_at:stamp},{event_id:'exit',broker_order_id:'test-exit',symbol:'AAPL',side:'sell',shares:2,recorded_at:stamp}]),
 research:group([{symbol:'MSFT',article_title:'Lower score',our_scanner_score:70,updated_at:stamp,direction:'short'}, {symbol:'AAPL',article_title:'<img src=x onerror=alert(1)>',article_summary:'Stored evidence',our_scanner_score:90,our_scanner_confidence:75,updated_at:stamp,direction:'long'}, {symbol:'AAPL',article_title:'Older duplicate',our_scanner_score:20,updated_at:stamp}]),
 scanner:{reason:'Saved research is not a live scan.'},performance:{reason:'Verified execution prices are unavailable.'}});

test('Pro Ticker desktop/mobile tabs, data, filtering, export and fail-closed refresh', {timeout:90000},async()=>{
 let data=fixture(),fail=false,requests=[],browser;
 const raw=fs.readFileSync(path.join(frontend,'index.html'),'utf8');const start=raw.indexOf('<section id="pro-ticker-manual-section"');
 const section=raw.slice(start,raw.indexOf('</section>',start)+10).replace('class="app-section hidden"','class="app-section"').replace(' hidden>','>');
 const server=http.createServer(async(req,res)=>{
  requests.push([req.method,req.url]);
  if(req.url==='/'){res.setHeader('Content-Type','text/html; charset=utf-8');res.end('<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/style.css"></head><body><main style="padding:16px;max-width:1100px;margin:auto">'+section+'</main><script src="/js/pro-ticker-workspace.js"></script><script>window.loadProTickerWorkspace()</script></body></html>');return;}
  if(req.url==='/style.css'){res.setHeader('Content-Type','text/css');res.end(fs.readFileSync(path.join(frontend,'style.css')));return;}
  if(req.url==='/js/pro-ticker-workspace.js'){res.setHeader('Content-Type','application/javascript');res.end(fs.readFileSync(path.join(__dirname,'pro-ticker-workspace.js')));return;}
  if(req.url==='/auto-trader/pro-ticker/proposal'){
   assert.equal(req.method,'POST');const chunks=[];for await(const chunk of req)chunks.push(chunk);
   const body=JSON.parse(Buffer.concat(chunks).toString());assert.equal(body.symbol,'AAPL');assert.equal(body.shares,5);
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({paper:true,hypothetical:true,order_submitted:false,order_execution_enabled:false,budget_reserved:false,within_virtual_budget:true,allocation_cents:5125,estimated_stop_risk_cents:103,available_virtual_cents:4990000,stop_price:'10.0450',take_profit_price:'10.6600',reason:'Operator estimate only.'}));return;
  }
  if(req.url==='/auto-trader/pro-ticker/workspace'){res.setHeader('Content-Type','application/json');res.statusCode=fail?503:200;res.end(JSON.stringify(fail?{detail:'unavailable'}:data));return;}
  res.statusCode=404;res.end();
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin='http://127.0.0.1:'+server.address().port;
 try{
  browser=await chromium.launch({headless:true});fs.mkdirSync('.artifacts/pro-ticker-workspace',{recursive:true});
  for(const width of [1280,390]){
   data=fixture();fail=false;requests=[];
   const context=await browser.newContext({viewport:{width,height:1000},acceptDownloads:true});const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto(origin);await page.waitForFunction(()=>document.getElementById('pt-status').textContent.includes('Ledger snapshot:'));
   assert.equal(await page.locator('[role=tab]').count(),8);assert.equal(await page.locator('[role=tabpanel]:visible').count(),1);
   assert.match(await page.locator('#pt-metrics').innerText(),/50,000/);assert.match(await page.locator('#pt-positions').innerText(),/AAPL/);
   for(const key of ['overview','scanner','research','trading','journal','performance','budget','health']){
    await page.locator('#pt-tab-'+key).click();assert.equal(await page.locator('#pt-panel-'+key).isVisible(),true);
    assert.equal(await page.locator('[role=tabpanel]:visible').count(),1);
    await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-'+key+'.png',fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true,key+' causes horizontal page overflow');
   }
   await page.locator('#pt-tab-scanner').click();assert.equal(await page.locator('#pt-candidates tr').count(),2);
   await page.locator('#pt-sort').selectOption('score');assert.match(await page.locator('#pt-candidates tr').first().innerText(),/AAPL/);
   await page.locator('#pt-search').fill('msft');assert.equal(await page.locator('#pt-candidates tr').count(),1);assert.match(await page.locator('#pt-candidates').innerText(),/MSFT/);
   await page.locator('#pt-search').fill('');await page.getByRole('button',{name:'Inspect AAPL',exact:true}).click();
   assert.equal(await page.locator('#pt-panel-research').isVisible(),true);assert.equal(await page.locator('#pt-research-detail img').count(),0);assert.match(await page.locator('#pt-research-detail').innerText(),/Stored evidence/);
   await page.locator('#pt-tab-trading').click();
   for(const [id,value]of [['symbol','aapl'],['price','10.25'],['shares','5'],['stop','2'],['target','4']])await page.locator('#pt-proposal-'+id).fill(value);
   await page.locator('#pt-proposal-button').click();await page.waitForFunction(()=>document.getElementById('pt-proposal-result').textContent.includes('No funds reserved'));
   assert.match(await page.locator('#pt-proposal-result').innerText(),/51\.25/);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-proposal.png',fullPage:true});
   await page.locator('#pt-proposal-shares').fill('6');assert.match(await page.locator('#pt-proposal-result').innerText(),/Recalculate/);
   await page.locator('#pt-tab-journal').click();assert.equal(await page.locator('#pt-journal tr').count(),2);
   const downloadPromise=page.waitForEvent('download');await page.locator('#pt-export').click();const download=await downloadPromise;const csv=fs.readFileSync(await download.path(),'utf8');assert.match(csv,/test-entry/);assert.doesNotMatch(csv,/realized_pnl/);
   await page.locator('#pt-journal-symbol').fill('MSFT');assert.match(await page.locator('#pt-journal').innerText(),/No persisted/);assert.equal(await page.locator('#pt-export').isDisabled(),true);
   await page.locator('#pt-tab-overview').focus();await page.keyboard.press('ArrowRight');assert.equal(await page.locator('#pt-tab-scanner').getAttribute('aria-selected'),'true');
   fail=true;await page.locator('#pt-refresh').click();await page.waitForFunction(()=>document.getElementById('pt-status').textContent.includes('Unable to load'));assert.equal(await page.locator('#pt-metrics').innerText(),'');assert.match(await page.locator('#pt-journal').innerText(),/Data unavailable/);
   fail=false;data=fixture();data.paper=false;await page.locator('#pt-refresh').click();await page.waitForFunction(()=>document.getElementById('pt-status').textContent.includes('safety flags'));assert.equal(await page.locator('#pt-metrics').innerText(),'');
   assert.deepEqual(errors,[]);assert.ok(requests.every(([method,url])=>method==='GET'||method==='POST'&&url==='/auto-trader/pro-ticker/proposal'),'workspace must never place an order');await context.close();
  }
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
});
