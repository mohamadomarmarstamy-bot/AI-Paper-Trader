const {test}=require('node:test'),assert=require('node:assert/strict'),http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');const root=path.resolve(__dirname,'..');
test('Trade quote desktop/mobile: ask rather than midpoint, timestamp, refresh and no stale fallback',{timeout:60000},async()=>{
 const raw=fs.readFileSync(path.join(root,'index.html'),'utf8');
 const section=raw.slice(raw.indexOf('<section id="trade-section"'),raw.indexOf('<section id="positions-section"'));
 let ask=3.9,stale=false,fail=false,browser,requests=[];
 const server=http.createServer((req,res)=>{
  requests.push([req.method,req.url]);const url=new URL(req.url,'http://local');
  if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end('<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/style.css"></head><body><main style="padding:12px;max-width:1000px;margin:auto">'+section+'</main><script>window.API_URL=location.origin;const s=document.getElementById("trade-section");s.hidden=false;s.classList.remove("hidden")</script><script src="/js/trades.js"></script><script src="/js/search.js"></script></body></html>');return;}
  const send=data=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));};
  if(url.pathname==='/search'){send([{symbol:'BRAI',name:'Synthetic quote fixture'}]);return;}
  if(url.pathname==='/quote/BRAI'){if(fail)res.statusCode=503;send({symbol:'BRAI',price:3.77,bid:3.56,ask,quote_age_seconds:stale?31:1,quote_fresh:!stale,quote_timestamp:new Date(Date.now()-(stale?31000:1000)).toISOString(),source:'alpaca_snapshot',feed:'iex'});return;}
  if(url.pathname==='/risk-plan/BRAI'){if(stale){send({success:false,error:'Fresh quote unavailable for this risk plan.'});return;}send({symbol:'BRAI',entry_price:ask,stop_loss:3.5,take_profit:4.5,recommended_shares:10});return;}
  if(['/style.css','/js/trades.js','/js/search.js'].includes(url.pathname)){res.setHeader('Content-Type',url.pathname.endsWith('.js')?'application/javascript':'text/css');res.end(fs.readFileSync(path.join(root,url.pathname)));return;}
  res.statusCode=404;res.end();
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  for(const width of [1440,390]){
   ask=3.9;stale=false;fail=false;const page=await browser.newPage({viewport:{width,height:1100}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.addInitScript(()=>{const real=Date.now.bind(Date);Date.now=()=>real()+120000;});
   await page.goto('http://127.0.0.1:'+server.address().port);
   await page.locator('#symbol').fill('BRAI');
   await page.waitForFunction(()=>Number(document.getElementById('price').value)===3.9);
   assert.match(await page.locator('#trade-quote-status').innerText(),/Sell estimate \(bid\): \$3.56.*IEX/);
   // Search selection must also prefer ask over the response's last-trade price.
   await page.locator('#symbol').press('Enter');
   await page.waitForFunction(()=>Number(document.getElementById('price').value)===3.9);
   ask=4;
   await page.waitForFunction(()=>Number(document.getElementById('price').value)===4,{timeout:10000});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
   stale=true;await page.evaluate(()=>window.loadLivePrice('BRAI'));
   assert.equal(await page.locator('#price').inputValue(),'');
   assert.match(await page.locator('#trade-quote-status').innerText(),/unavailable|stale/i);
   assert.match(await page.locator('#trade-quote-status').innerText(),/31s old/);
   await page.evaluate(()=>window.loadRiskPlan('BRAI'));
   assert.match(await page.locator('#trade-risk-plan').innerText(),/Fresh quote unavailable/);
   assert.doesNotMatch(await page.locator('#trade-risk-plan').innerText(),/\$0\.00/);

   stale=false;fail=true;await page.evaluate(()=>window.loadLivePrice('BRAI'));
   assert.equal(await page.locator('#price').inputValue(),'');
   assert.deepEqual(errors,[]);await page.close();
  }
  assert.equal(requests.some(([method])=>method!=='GET'),false);
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
});
