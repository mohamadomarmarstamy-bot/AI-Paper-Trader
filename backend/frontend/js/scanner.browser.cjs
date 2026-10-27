const {test}=require('node:test'),assert=require('node:assert/strict'),http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
test('Fast-mover scanner desktop/mobile ranking, coverage and filters',{timeout:60000},async()=>{
 const raw=fs.readFileSync(path.join(root,'index.html'),'utf8');
 const section=raw.slice(raw.indexOf('<section id="scanner-section"'),raw.indexOf('<section id="pro-ticker-manual-section"'));
 let fail=false,requests=[],browser;
 const live={percent_change:12,session_dollar_volume:800000,spread_percent:1,stale:false};
 const data={results:[{symbol:'SLOW',price:50,score:99,confidence:90,signal:'BUY'},{symbol:'FAST',price:2,score:80,confidence:80,signal:'BUY',fast_mover_priority:true,fast_mover_score:45,live_discovery:live}],coverage:{valid_candidates:200,stale:false,deferred_daily_histories:25,discovery:{profile:'fast_movers_v1',screened_symbols:600,rotating_symbols:300,fresh_selected_snapshots:180,partial:false}}};
 const server=http.createServer((req,res)=>{
  requests.push([req.method,req.url]);const url=new URL(req.url,'http://local');
  if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end('<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/style.css"></head><body><main style="padding:12px;max-width:1400px;margin:auto">'+section+'</main><script>window.API_URL=location.origin;const s=document.getElementById("scanner-section");s.hidden=false;s.classList.remove("hidden")</script><script src="/js/scanner.js"></script></body></html>');return;}
  if(url.pathname==='/scanner'){assert.equal(url.searchParams.get('limit'),'150');res.statusCode=fail?503:200;res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));return;}
  if(url.pathname==='/style.css'||url.pathname==='/js/scanner.js'){res.setHeader('Content-Type',url.pathname.endsWith('.js')?'application/javascript':'text/css');res.end(fs.readFileSync(path.join(root,url.pathname)));return;}
  res.statusCode=404;res.end();
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  for(const width of [1440,390]){
   const page=await browser.newPage({viewport:{width,height:1100}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto('http://127.0.0.1:'+server.address().port);
   await page.waitForFunction(()=>document.querySelectorAll('.scanner-card').length===2);
   assert.equal(await page.locator('.scanner-card').first().getAttribute('data-symbol'),'FAST');
   assert.match(await page.locator('#scanner-coverage').innerText(),/600 symbols screened.*300 rotating/);
   assert.match(await page.locator('.scanner-card').first().innerText(),/IEX session \$800,000/);
   await page.locator('#scanner-sort').selectOption('score-desc');
   assert.equal(await page.locator('.scanner-card').first().getAttribute('data-symbol'),'SLOW');
   await page.locator('#scanner-search').fill('FAST');
   assert.equal(await page.locator('.scanner-card').count(),1);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
   fail=true;await page.evaluate(()=>window.loadScanner());
   assert.match(await page.locator('#scanner-coverage').innerText(),/refresh failed/);
   fail=false;assert.deepEqual(errors,[]);await page.close();
  }
  assert.equal(requests.some(([method])=>method!=='GET'),false);
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
});
