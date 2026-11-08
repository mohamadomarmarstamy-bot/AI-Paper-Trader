const {test}=require('node:test'),assert=require('node:assert/strict'),http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
test('Real-copy report: receipt costs, incomplete accounting, pagination and mobile tables',{timeout:60000},async()=>{
 const root=path.resolve(__dirname,'..'),requests=[];let fail=false,incomplete=false,empty=false;
 const wallet='1'.repeat(31)+'2',metric={side:'buy',key:'source_to_broadcast_ms',label:'Source buy → broadcast attempt',count:2,median:2000,p90:3000,eligible_orders:3};
 const payload=offset=>({checked_at:Date.now()/1000,wallet:'owner-wallet',order_count:30,pending_orders:1,accounting_complete:!incomplete,ledger_realized_lamports:10000000,allocated_realized_lamports:incomplete?9000000:10000000,ledger_difference_lamports:incomplete?1000000:0,failed_fees_lamports:100000,unknown_fee_records:0,entry_premium:{count:22,median:2,p90:4},entry_count:empty?0:22,offset,limit:20,
  wallets:empty?[]:[{wallet,name:'<img src=x onerror=alert(1)>',confirmed_buys:22,confirmed_sales:5,completed_positions:3,win_rate_percent:66.67,open_positions:1,net_realized_lamports:10000000,failed_fees_lamports:100000,failed_orders:1,rejected_orders:2,unallocated_sales:0,unknown_fee_records:0,entry_premium:{median:2}}],
  benchmark:{live_mode:'standard',samples:25,comparable_pairs:22,last_sample_at:Date.now()/1000,modes:[{mode:'standard',attempts:25,successful:24,rate_limited:1,unavailable:0,paired_latency_ms:{count:22,median:80,p90:100}},{mode:'fast',attempts:24,successful:22,rate_limited:0,unavailable:2,paired_latency_ms:{count:22,median:40,p90:60}}],latency_change_ms:{median:-40},minimum_output_change_percent:{median:-.5},basis:'Read-only quotes. Live routing stays normal.'},
  timings:[metric],cohorts:[{label:'Earlier · version unrecorded',confirmed_buys:2,entry_premium:{count:2,median:3},timings:[metric]},{label:'Current measured pipeline',confirmed_buys:1,entry_premium:{count:1,median:1},timings:[{...metric,count:1,median:1000}]}],
  entries:empty?[]:Array.from({length:Math.min(20,22-offset)},(_,i)=>({id:String(offset+i),mint:wallet,source_wallet:wallet,source_name:'Test source',created_at:1000+offset+i,signature:'public-receipt',our_sol_per_token:.00000001,source_sol_per_token:offset+i===0?null:.000000009,premium_percent:offset+i===0?null:2,source_to_broadcast_ms:2000})),basis:'Finalized receipts. Open holdings excluded.',price_basis:'All-in SOL per received token includes fees and account funding.',comparison_basis:'Earlier trades may already use the speed updates.'});
 const section=fs.readFileSync(path.join(root,'index.html'),'utf8').match(/<section id="real-copy-section"[\s\S]*?<\/section>/)[0].replace('class="app-section hidden"','class="app-section"').replace(/ hidden(?=>)/,'');
 const server=http.createServer((req,res)=>{const u=new URL(req.url,'http://test');requests.push({method:req.method,url:u.pathname,offset:u.searchParams.get('offset')});
  if(u.pathname==='/real-copy/performance'){res.setHeader('Content-Type','application/json');res.statusCode=fail?503:200;return res.end(JSON.stringify(fail?{detail:'Offline'}:payload(Number(u.searchParams.get('offset')))));}
  if(u.pathname==='/'){res.setHeader('Content-Type','text/html');return res.end(`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/real-copy.css"><style>body{margin:0;padding:16px;background:#101525;font-family:Arial;color:white}</style></head><body>${section}<script src="/real-copy-performance.js"></script></body></html>`);}
  const file=u.pathname==='/real-copy.css'?'real-copy.css':u.pathname==='/real-copy-performance.js'?'js/real-copy-performance.js':null;
  if(file){res.setHeader('Content-Type',file.endsWith('.js')?'application/javascript':'text/css');return res.end(fs.readFileSync(path.join(root,file)));}res.statusCode=404;res.end();
 });await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;
 try{browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||undefined,headless:true,args:['--no-sandbox']});
  for(const width of [1440,390]){fail=false;incomplete=false;empty=false;const page=await browser.newPage({viewport:{width,height:1100}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto('http://127.0.0.1:'+server.address().port);const before=requests.filter(r=>r.url==='/real-copy/performance').length;
   await page.locator('#rc-performance>summary').click();await page.locator('#rc-report-page').filter({hasText:'1–20 of 22 buys'}).waitFor();
   assert.equal(requests.filter(r=>r.url==='/real-copy/performance').length,before+1);
   assert.match(await page.locator('#rc-report-wallets').innerText(),/<img src=x/);assert.equal(await page.locator('#rc-report-wallets img').count(),0);
   assert.equal(await page.locator('#rc-report-premium').innerText(),'+2.00%');assert.equal(await page.locator('#rc-report-speed').innerText(),'2.000s');
   await page.locator('#rc-quote-benchmark>summary').click();assert.match(await page.locator('#rc-benchmark-status').innerText(),/22 comparable pairs/);assert.match(await page.locator('#rc-benchmark-modes').innerText(),/Fast \(research\)/);assert.match(await page.locator('#rc-benchmark-difference').innerText(),/-40.0ms/);assert.match(await page.locator('#rc-benchmark-difference').innerText(),/-0.50%/);
   assert.equal(await page.locator('#rc-report-entries tr').count(),20);assert.equal(await page.locator('#rc-report-entries tr').first().locator('td').nth(3).innerText(),'—');
   await page.locator('#rc-report-next').click();await page.locator('#rc-report-page').filter({hasText:'21–22 of 22 buys'}).waitFor();assert.equal(await page.locator('#rc-report-next').isDisabled(),true);
   await page.locator('#rc-report-prev').click();await page.locator('#rc-report-page').filter({hasText:'1–20 of 22 buys'}).waitFor();
   incomplete=true;await page.locator('#rc-report-refresh').click();await page.locator('#rc-report-warning').waitFor({state:'visible'});assert.equal(await page.locator('#rc-report-pnl').innerText(),'Needs review');
   fail=true;await page.locator('#rc-report-refresh').click();await page.getByText('Report unavailable. Displayed results may be old. Retry to refresh.').waitFor();assert.match(await page.locator('#rc-report-wallets').innerText(),/Test source|img src=x/);
   fail=false;empty=true;incomplete=false;await page.locator('#rc-report-refresh').click();await page.getByText('No confirmed real buys.').waitFor();assert.equal(await page.locator('#rc-report-next').isDisabled(),true);
   empty=false;await page.locator('#rc-report-refresh').click();await page.locator('#rc-report-page').filter({hasText:'1–20 of 22 buys'}).waitFor();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);assert.deepEqual(errors,[]);
   fs.mkdirSync('.artifacts/real-copy',{recursive:true});await page.locator('#rc-performance').scrollIntoViewIfNeeded();await page.screenshot({path:`.artifacts/real-copy/performance-${width}.png`,fullPage:true});await page.close();
  }
  assert.equal(requests.some(r=>r.method!=='GET'),false);
 }finally{await browser?.close();await new Promise(r=>server.close(r));}
});
