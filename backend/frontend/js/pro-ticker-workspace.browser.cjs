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
 let data=fixture(),fail=false,requests=[],browser,forwardDue=false,savedSignal=null,draftRequests=[],allocationRequests=[],signalRequests=[],discoveries=0,invalidProposal=false,invalidSettings=false,settingsRevision=0,settingsValues={max_allocation_cents:5000000,max_stop_risk_cents:5000000,discovery_article_limit:5};
 const raw=fs.readFileSync(path.join(frontend,'index.html'),'utf8');const start=raw.indexOf('<section id="pro-ticker-manual-section"');
 const section=raw.slice(start,raw.indexOf('</section>',start)+10).replace('class="app-section hidden"','class="app-section"').replace(' hidden>','>');
 const server=http.createServer(async(req,res)=>{
  requests.push([req.method,req.url]);
  if(req.url==='/'){res.setHeader('Content-Type','text/html; charset=utf-8');res.end('<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/style.css"></head><body><main style="padding:16px;max-width:1100px;margin:auto">'+section+'</main><script src="/js/pro-ticker-workspace.js"></script><script src="/js/pro-ticker-signals.js"></script><script>window.loadProTickerWorkspace();window.loadProTickerSignals()</script></body></html>');return;}
  if(req.url==='/style.css'){res.setHeader('Content-Type','text/css');res.end(fs.readFileSync(path.join(frontend,'style.css')));return;}
  if(req.url==='/js/pro-ticker-workspace.js'){res.setHeader('Content-Type','application/javascript');res.end(fs.readFileSync(path.join(__dirname,'pro-ticker-workspace.js')));return;}
  if(req.url==='/js/pro-ticker-signals.js'){res.setHeader('Content-Type','application/javascript');res.end(fs.readFileSync(path.join(__dirname,'pro-ticker-signals.js')));return;}
  if(req.url==='/auto-trader/pro-ticker/order-intents'){
   res.setHeader('Content-Type','application/json');
   if(req.method==='GET'){res.end(JSON.stringify({paper:true,execution_enabled:false,automatic_release_enabled:false,intents:savedSignal?.order_intent?[savedSignal.order_intent]:[],total_count:savedSignal?.order_intent?1:0,has_more:false}));return;}
   assert.equal(req.method,'POST');assert.equal(req.headers.origin,origin);const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=JSON.parse(Buffer.concat(chunks).toString());
   assert.deepEqual(Object.keys(body).sort(),['confirmed','proposal_id','request_id']);assert.equal(body.confirmed,true);assert.equal(body.proposal_id,savedSignal.proposal.request_id);allocationRequests.push(body);
   savedSignal.order_intent=savedSignal.order_intent||{paper:true,id:body.request_id,proposal_id:body.proposal_id,reservation_id:'test-allocation-reservation',client_order_id:'pt-synthetic',status:'prepared',created_at:stamp,budget_reserved:true,execution_enabled:false,order_submitted:false,automatic_release_enabled:false,plan:{symbol:'AAPL',shares:2,allocation_cents:2100,order_submitted:false,order_execution_enabled:false}};
   data.budget.committed_cents=12100;data.budget.available_cents=4987900;
   if(allocationRequests.length===1){res.statusCode=503;res.end(JSON.stringify({detail:'Simulated lost allocation confirmation'}));return;}
   assert.equal(body.request_id,allocationRequests[0].request_id);res.end(JSON.stringify({paper:true,duplicate:true,intent:savedSignal.order_intent}));return;
  }
  if(req.url.startsWith('/auto-trader/pro-ticker/order-intents/')&&req.url.endsWith('/cancel')){
   assert.equal(req.method,'POST');assert.equal(req.headers.origin,origin);const chunks=[];for await(const chunk of req)chunks.push(chunk);assert.deepEqual(JSON.parse(Buffer.concat(chunks).toString()),{confirmed:true});
   savedSignal.order_intent={...savedSignal.order_intent,status:'canceled',budget_reserved:false,virtual_funds_returned_cents:2100};data.budget.committed_cents=10000;data.budget.available_cents=4990000;
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({paper:true,duplicate:false,intent:savedSignal.order_intent}));return;
  }
  if(req.url==='/auto-trader/pro-ticker/manual-signals/proposal'){
   assert.equal(req.method,'POST');assert.equal(req.headers.origin,origin);
   const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=JSON.parse(Buffer.concat(chunks).toString());draftRequests.push(body);
   assert.deepEqual(Object.keys(body).sort(),['request_id','shares','signal_id','stop_loss_percent','take_profit_percent']);assert.equal(body.signal_id,savedSignal.request_id);assert.equal(body.shares,2);
   savedSignal.proposal=savedSignal.proposal||{paper:true,signal_id:body.signal_id,request_id:body.request_id,symbol:'AAPL',shares:2,allocation_cents:2100,budget_reserved:false,order_submitted:false,execution_authorized:false,status:'research_draft'};
   res.setHeader('Content-Type','application/json');
   if(draftRequests.length===1){res.statusCode=503;res.end(JSON.stringify({detail:'Simulated lost confirmation after persistence'}));return;}
   assert.equal(body.request_id,draftRequests[0].request_id);res.end(JSON.stringify({paper:true,proposal:savedSignal.proposal,duplicate:true}));return;
  }
  if(req.url==='/auto-trader/pro-ticker/manual-signals/learning'){
   const outcome=savedSignal?.forward_outcome;
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({paper:true,kind:'forward_quote_return_not_trade_pnl',model_updated:false,training_enabled:false,training_ready_samples:0,review_count:savedSignal?1:0,window_count:savedSignal?1:0,evaluated_count:outcome?1:0,has_more:false,groups:outcome?[{version:'pro_ticker_daily_technical_grade_v1',grade:'A',samples:1,positive:0,negative:1,flat:0,mean_forward_return_percent:outcome.return_percent}]:[],reason:'Research only; no model updates.'}));return;
  }
  if(req.url.startsWith('/auto-trader/pro-ticker/manual-signals/')&&req.url.endsWith('/forward-review')){
   assert.equal(req.method,'POST');const chunks=[];for await(const chunk of req)chunks.push(chunk);assert.deepEqual(JSON.parse(Buffer.concat(chunks).toString()),{});
   res.setHeader('Content-Type','application/json');if(!forwardDue){res.statusCode=409;res.end(JSON.stringify({detail:'not due'}));return;}
   savedSignal.forward_outcome=savedSignal.forward_outcome||{request_id:savedSignal.request_id,kind:'forward_quote_return_not_trade_pnl',training_eligible:false,return_percent:-14.285714};
   res.end(JSON.stringify({paper:true,model_updated:false,outcome:savedSignal.forward_outcome}));return;
  }
  if(req.url==='/auto-trader/pro-ticker/manual-signals'){
   res.setHeader('Content-Type','application/json');
   if(req.method==='POST'){
    const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=JSON.parse(Buffer.concat(chunks).toString());
    assert.deepEqual(Object.keys(body).sort(),['request_id','symbol']);assert.equal(body.symbol,'AAPL');signalRequests.push(body);
    const duplicate=!!savedSignal;
    savedSignal=savedSignal||{request_id:body.request_id,symbol:'AAPL',submitted_at:stamp,completed_at:stamp,status:'graded',market_snapshot:{status:'captured',price:10.5,market_timestamp:stamp,fresh:true},entry_grade:{version:'pro_ticker_daily_technical_grade_v1',status:'graded',grade:'A',score:4,confidence:85,signal:'BUY',training_eligible:false,indicators:{rsi:55},reasons:['Synthetic entry-time evidence.'],limitations:['Not a win probability.']}};
    res.end(JSON.stringify({paper:true,orders_submitted:0,automatic_execution_enabled:false,signal:savedSignal,duplicate}));return;
   }
   res.end(JSON.stringify({paper:true,orders_submitted:0,automatic_execution_enabled:false,signals:savedSignal?[savedSignal]:[],total_count:savedSignal?1:0,has_more:false}));return;
  }
  if(req.url==='/auto-trader/pro-ticker/settings'){
   res.setHeader('Content-Type','application/json');
   if(req.method==='PUT'){
    assert.equal(req.headers.origin,origin);const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=JSON.parse(Buffer.concat(chunks).toString());
    assert.deepEqual(Object.keys(body).sort(),['expected_revision','settings']);
    if(body.expected_revision!==settingsRevision){res.statusCode=409;res.end(JSON.stringify({detail:'conflict'}));return;}
    settingsValues=body.settings;settingsRevision++;
   }
   res.end(JSON.stringify({paper:true,scope:'pro_ticker_proposals_and_manual_discovery',order_execution_enabled:invalidSettings,automatic_release_enabled:false,revision:settingsRevision,settings:settingsValues,updated_at:settingsRevision?stamp:null}));return;
  }
  if(req.url==='/auto-trader/pro-ticker/discovery'){
   if(req.method==='POST'){
    const chunks=[];for await(const chunk of req)chunks.push(chunk);assert.deepEqual(JSON.parse(Buffer.concat(chunks).toString()),{});
    discoveries++;data.research.rows.unshift({symbol:'NVDA',article_title:'Discovered test research',updated_at:stamp});data.research.total++;
   }
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({paper:true,order_execution_enabled:false,automatic_discovery_enabled:false,status:discoveries?'completed':'not_run',run:discoveries?{id:'synthetic-run',started_at:stamp,finished_at:stamp,saved:1,skipped:0,failed:0,reason:'Public research only.'}:null}));return;
  }
  if(req.url==='/auto-trader/pro-ticker/proposal'){
   assert.equal(req.method,'POST');const chunks=[];for await(const chunk of req)chunks.push(chunk);
   const body=JSON.parse(Buffer.concat(chunks).toString());assert.equal(body.symbol,'AAPL');assert.equal(body.shares,5);
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({paper:true,hypothetical:true,order_submitted:false,order_execution_enabled:false,budget_reserved:false,within_virtual_budget:true,settings_revision:settingsRevision,risk_limits_satisfied:settingsValues.max_allocation_cents>=5125&&settingsValues.max_stop_risk_cents>=103,risk_limit_reasons:invalidProposal?[]:settingsValues.max_allocation_cents<5125?['allocation_exceeds_pro_ticker_limit']:[],allocation_cents:5125,estimated_stop_risk_cents:103,available_virtual_cents:4990000,stop_price:'10.0450',take_profit_price:'10.6600',reason:'Operator estimate only.'}));return;
  }
  if(req.url==='/auto-trader/pro-ticker/workspace'){res.setHeader('Content-Type','application/json');res.statusCode=fail?503:200;res.end(JSON.stringify(fail?{detail:'unavailable'}:data));return;}
  res.statusCode=404;res.end();
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin='http://127.0.0.1:'+server.address().port;
 try{
  browser=await chromium.launch({headless:true});fs.mkdirSync('.artifacts/pro-ticker-workspace',{recursive:true});
  for(const width of [1280,390]){
   data=fixture();fail=false;requests=[];forwardDue=false;savedSignal=null;draftRequests=[];allocationRequests=[];signalRequests=[];discoveries=0;invalidProposal=false;invalidSettings=false;settingsRevision=0;settingsValues={max_allocation_cents:5000000,max_stop_risk_cents:5000000,discovery_article_limit:5};
   const context=await browser.newContext({viewport:{width,height:1000},acceptDownloads:true});const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto(origin);await page.waitForFunction(()=>document.getElementById('pt-status').textContent.includes('Ledger snapshot:'));
   assert.equal(await page.locator('[role=tab]').count(),9);assert.equal(await page.locator('[role=tabpanel]:visible').count(),1);
   assert.match(await page.locator('#pt-metrics').innerText(),/50,000/);assert.match(await page.locator('#pt-positions').innerText(),/AAPL/);
   for(const key of ['overview','scanner','signals','research','trading','journal','performance','budget','health']){
    await page.locator('#pt-tab-'+key).click();assert.equal(await page.locator('#pt-panel-'+key).isVisible(),true);
    assert.equal(await page.locator('[role=tabpanel]:visible').count(),1);
    await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-'+key+'.png',fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true,key+' causes horizontal page overflow');
   }
   await page.locator('#pt-tab-signals').click();await page.locator('#pt-signal-symbol').fill('aapl');await page.locator('#pt-signal-submit').click();
   await page.waitForFunction(()=>document.getElementById('pt-signal-status').textContent.includes('Review graded'));
   assert.equal(await page.locator('#pt-signal-history tr').count(),1);assert.match(await page.locator('#pt-signal-history').innerText(),/AAPL/);
   await page.getByRole('button',{name:'Inspect AAPL review',exact:true}).click();assert.match(await page.locator('#pt-signal-detail').innerText(),/Synthetic entry-time evidence/);
   await page.locator('#pt-signal-submit').click();await page.waitForFunction(()=>document.getElementById('pt-signal-status').textContent.includes('Review graded'));
   assert.equal(signalRequests.length,2);assert.equal(signalRequests[0].request_id,signalRequests[1].request_id);
   assert.equal(await page.locator('#pt-signal-history tr').count(),1);await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-manual-signals.png',fullPage:true});
   await page.locator('#pt-signal-detail input[name="shares"]').fill('2');await page.locator('#pt-signal-detail input[name="stop_loss_percent"]').fill('5');await page.locator('#pt-signal-detail input[name="take_profit_percent"]').fill('10');
   await page.getByRole('button',{name:'Save PAPER draft (no order)',exact:true}).click();await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Unable to confirm draft'));
   assert.equal(await page.getByRole('button',{name:'Save PAPER draft (no order)',exact:true}).isDisabled(),false);
   await page.getByRole('button',{name:'Save PAPER draft (no order)',exact:true}).click();await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Saved PAPER research draft'));
   assert.equal(draftRequests.length,2);assert.equal(draftRequests[0].request_id,draftRequests[1].request_id);
   await page.getByRole('button',{name:'Inspect AAPL review',exact:true}).click();assert.equal(await page.locator('#pt-signal-detail form').count(),0);assert.match(await page.locator('#pt-signal-detail').innerText(),/draft itself does not reserve funds or place an order/);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-saved-draft.png',fullPage:true});
   assert.equal(await page.getByRole('button',{name:'Reserve virtual funds (no order)',exact:true}).isDisabled(),true);
   await page.locator('#pt-signal-detail input[type="checkbox"]').check();await page.getByRole('button',{name:'Reserve virtual funds (no order)',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Allocation update unconfirmed'));
   await page.getByRole('button',{name:'Reserve virtual funds (no order)',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Virtual funds reserved: $21.00'));
   assert.equal(allocationRequests.length,2);assert.equal(allocationRequests[0].request_id,allocationRequests[1].request_id);
   await page.locator('#pt-tab-trading').click();await page.waitForFunction(()=>document.getElementById('pt-order-intents').textContent.includes('Virtual funds reserved'));assert.match(await page.locator('#pt-order-intents').innerText(),/AAPL/);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-reserved-intent.png',fullPage:true});await page.locator('#pt-tab-signals').click();
   assert.equal(await page.getByRole('button',{name:'Cancel unsubmitted allocation',exact:true}).isDisabled(),true);
   await page.locator('#pt-signal-detail input[type="checkbox"]').check();await page.getByRole('button',{name:'Cancel unsubmitted allocation',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Allocation canceled. Returned virtual funds are not trading profit'));
   assert.equal(savedSignal.order_intent.status,'canceled');assert.equal(data.budget.committed_cents,10000);

   await page.getByRole('button',{name:'Check 24+ hour forward quote',exact:true}).click();await page.waitForFunction(()=>document.getElementById('pt-signal-status').textContent.includes('Wait until a fresh market quote'));assert.equal(savedSignal.forward_outcome,undefined);
   forwardDue=true;await page.getByRole('button',{name:'Check 24+ hour forward quote',exact:true}).click();await page.waitForFunction(()=>document.getElementById('pt-signal-status').textContent.includes('did not update an AI model'));
   assert.match(await page.locator('#pt-signal-detail').innerText(),/-14\.285714/);assert.equal(savedSignal.entry_grade.score,4);
   await page.locator('#pt-tab-performance').click();assert.match(await page.locator('#pt-signal-learning').innerText(),/not realized P&L/);assert.match(await page.locator('#pt-signal-learning-rows').innerText(),/-14\.29/);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-forward-research.png',fullPage:true});await page.locator('#pt-tab-signals').click();
   await page.locator('#pt-signal-new').click();assert.equal(await page.locator('#pt-signal-symbol').inputValue(),'');
   await page.evaluate(()=>{window.testOriginalUUID=crypto.randomUUID.bind(crypto);crypto.randomUUID=()=>{throw Error('Synthetic UUID unavailable');};});
   await page.locator('#pt-signal-symbol').fill('AAPL');await page.locator('#pt-signal-submit').click();await page.waitForFunction(()=>document.getElementById('pt-signal-status').textContent.includes('Synthetic UUID unavailable'));
   assert.equal(await page.locator('#pt-signal-submit').isDisabled(),false);assert.equal(await page.locator('#pt-signal-symbol').isDisabled(),false);
   await page.evaluate(()=>{crypto.randomUUID=window.testOriginalUUID;});

   await page.locator('#pt-tab-health').click();
   await page.waitForFunction(()=>document.getElementById('pt-settings-status').textContent.includes('revision 0'));
   await page.locator('#pt-settings-allocation').fill('40');await page.locator('#pt-settings-risk').fill('1');await page.locator('#pt-settings-articles').fill('2');
   await page.locator('#pt-refresh').click();assert.equal(await page.locator('#pt-settings-allocation').inputValue(),'40');
   await page.locator('#pt-settings-save').click();await page.waitForFunction(()=>document.getElementById('pt-settings-status').textContent.includes('revision 1'));
   assert.equal(settingsValues.max_allocation_cents,4000);assert.equal(settingsValues.max_stop_risk_cents,100);assert.equal(settingsValues.discovery_article_limit,2);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-settings.png',fullPage:true});
   settingsRevision=2;await page.locator('#pt-settings-risk').fill('0.50');await page.locator('#pt-settings-save').click();
   await page.waitForFunction(()=>document.getElementById('pt-settings-status').textContent.includes('another session'));assert.equal(await page.locator('#pt-settings-save').isDisabled(),true);
   await page.locator('#pt-settings-reload').click();await page.waitForFunction(()=>document.getElementById('pt-settings-status').textContent.includes('revision 2'));
   assert.equal(await page.locator('#pt-settings-risk').inputValue(),'1.00');
   invalidSettings=true;await page.locator('#pt-settings-reload').click();await page.waitForFunction(()=>document.getElementById('pt-settings-status').textContent.includes('safety flags'));assert.equal(await page.locator('#pt-settings-save').isDisabled(),true);
   invalidSettings=false;await page.locator('#pt-settings-reload').click();await page.waitForFunction(()=>document.getElementById('pt-settings-status').textContent.includes('revision 2'));
   await page.locator('#pt-tab-scanner').click();assert.equal(await page.locator('#pt-candidates tr').count(),2);
   await page.locator('#pt-sort').selectOption('score');assert.match(await page.locator('#pt-candidates tr').first().innerText(),/AAPL/);
   await page.locator('#pt-search').fill('msft');assert.equal(await page.locator('#pt-candidates tr').count(),1);assert.match(await page.locator('#pt-candidates').innerText(),/MSFT/);
   await page.locator('#pt-search').fill('');await page.getByRole('button',{name:'Inspect AAPL',exact:true}).click();
   assert.equal(await page.locator('#pt-panel-research').isVisible(),true);assert.equal(await page.locator('#pt-research-detail img').count(),0);assert.match(await page.locator('#pt-research-detail').innerText(),/Stored evidence/);
   for(const [id,value]of [['price','999'],['shares','8'],['stop','10'],['target','20']])await page.locator('#pt-proposal-'+id).evaluate((node,value)=>node.value=value,value);
   await page.getByRole('button',{name:'Draft an estimate for AAPL',exact:true}).click();
   assert.equal(await page.locator('#pt-panel-trading').isVisible(),true);assert.equal(await page.locator('#pt-proposal-symbol').inputValue(),'AAPL');
   for(const id of ['price','shares','stop','target'])assert.equal(await page.locator('#pt-proposal-'+id).inputValue(),'');
   assert.match(await page.locator('#pt-proposal-result').innerText(),/historical research is not current broker evidence/);
   await page.locator('#pt-tab-scanner').click();await page.locator('#pt-discover').click();
   await page.waitForFunction(()=>document.getElementById('pt-discovery-status').textContent.includes('completed'));
   await page.waitForFunction(()=>document.getElementById('pt-candidates').textContent.includes('NVDA'));
   assert.equal(discoveries,1);await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-discovery.png',fullPage:true});
   await page.locator('#pt-tab-trading').click();
   for(const [id,value]of [['symbol','aapl'],['price','10.25'],['shares','5'],['stop','2'],['target','4']])await page.locator('#pt-proposal-'+id).fill(value);
   await page.locator('#pt-proposal-button').click();await page.waitForFunction(()=>document.getElementById('pt-proposal-result').textContent.includes('No funds reserved'));
   assert.match(await page.locator('#pt-proposal-result').innerText(),/51\.25/);
   assert.match(await page.locator('#pt-proposal-result').innerText(),/BLOCKED by Pro Ticker proposal limits/);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-proposal.png',fullPage:true});
   invalidProposal=true;await page.locator('#pt-proposal-button').click();await page.waitForFunction(()=>document.getElementById('pt-proposal-result').textContent.includes('Invalid proposal safety'));invalidProposal=false;
   await page.locator('#pt-proposal-shares').fill('6');assert.match(await page.locator('#pt-proposal-result').innerText(),/Recalculate/);
   await page.locator('#pt-tab-journal').click();assert.equal(await page.locator('#pt-journal tr').count(),2);
   const downloadPromise=page.waitForEvent('download');await page.locator('#pt-export').click();const download=await downloadPromise;const csv=fs.readFileSync(await download.path(),'utf8');assert.match(csv,/test-entry/);assert.doesNotMatch(csv,/realized_pnl/);
   await page.locator('#pt-journal-symbol').fill('MSFT');assert.match(await page.locator('#pt-journal').innerText(),/No persisted/);assert.equal(await page.locator('#pt-export').isDisabled(),true);
   await page.locator('#pt-tab-overview').focus();await page.keyboard.press('ArrowRight');assert.equal(await page.locator('#pt-tab-scanner').getAttribute('aria-selected'),'true');
   fail=true;await page.locator('#pt-refresh').click();await page.waitForFunction(()=>document.getElementById('pt-status').textContent.includes('Unable to load'));assert.equal(await page.locator('#pt-metrics').innerText(),'');assert.match(await page.locator('#pt-journal').innerText(),/Data unavailable/);
   fail=false;data=fixture();data.paper=false;await page.locator('#pt-refresh').click();await page.waitForFunction(()=>document.getElementById('pt-status').textContent.includes('safety flags'));assert.equal(await page.locator('#pt-metrics').innerText(),'');
   assert.deepEqual(errors,[]);assert.ok(requests.every(([method,url])=>method==='GET'||method==='POST'&&['/auto-trader/pro-ticker/proposal','/auto-trader/pro-ticker/discovery','/auto-trader/pro-ticker/manual-signals','/auto-trader/pro-ticker/manual-signals/proposal','/auto-trader/pro-ticker/order-intents'].includes(url)||method==='POST'&&url.startsWith('/auto-trader/pro-ticker/manual-signals/')&&url.endsWith('/forward-review')||method==='POST'&&url.startsWith('/auto-trader/pro-ticker/order-intents/')&&url.endsWith('/cancel')||method==='PUT'&&url==='/auto-trader/pro-ticker/settings'),'workspace must never place an order');await context.close();
  }
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
});
