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
 let marketScan=null,marketScanPosts=0,pricedExecutions=[],closedExecutionMetrics=false,invalidExecutionMetrics=false,invalidAnalytics=false,invalidCash=false,studyPlan=null,invalidStudies=false;
 let handoffRequests=[];
 let data=fixture(),fail=false,requests=[],browser,forwardDue=false,savedSignal=null,draftRequests=[],allocationRequests=[],admissionRequests=[],evidenceConfigured=false,ownerSubmissionConfigured=false,managementConfigured=false,managementRequests=0,submissionRequests=0,recoveryRequests=0,signalRequests=[],discoveries=0,invalidClosedPerformance=false,invalidDataset=false,invalidProposal=false,invalidSettings=false,settingsRevision=0,settingsValues={max_allocation_cents:5000000,max_stop_risk_cents:5000000,discovery_article_limit:5};
 const raw=fs.readFileSync(path.join(frontend,'index.html'),'utf8');const start=raw.indexOf('<section id="pro-ticker-manual-section"');
 const section=raw.slice(start,raw.indexOf('</section>',start)+10).replace('class="app-section hidden"','class="app-section"').replace(' hidden>','>');
 const server=http.createServer(async(req,res)=>{
  requests.push([req.method,req.url]);
  if(req.url==='/'){res.setHeader('Content-Type','text/html; charset=utf-8');res.end('<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/style.css"></head><body><main style="padding:16px;max-width:1100px;margin:auto">'+section+'</main><script src="/js/pro-ticker-workspace.js"></script><script src="/js/pro-ticker-signals.js"></script><script>window.loadProTickerWorkspace();window.loadProTickerSignals()</script></body></html>');return;}
  if(req.url==='/style.css'){res.setHeader('Content-Type','text/css');res.end(fs.readFileSync(path.join(frontend,'style.css')));return;}
  if(req.url==='/js/pro-ticker-workspace.js'){res.setHeader('Content-Type','application/javascript');res.end(fs.readFileSync(path.join(__dirname,'pro-ticker-workspace.js')));return;}
  if(req.url==='/js/pro-ticker-signals.js'){res.setHeader('Content-Type','application/javascript');res.end(fs.readFileSync(path.join(__dirname,'pro-ticker-signals.js')));return;}
  if(req.url.startsWith('/auto-trader/pro-ticker/market-reference?')){
   assert.equal(req.method,'GET');assert.equal(new URL(req.url,origin).searchParams.get('symbol'),'AAPL');
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({paper:true,account_verified:true,reference_only:true,order_execution_authorized:false,symbol:'AAPL',
    reference:{source:'alpaca_latest_trade',status:'captured',price:10.5,market_timestamp:new Date().toISOString(),age_seconds:1,fresh:true},
    allocation_reference_fresh:true,admission_reference_fresh:true,allocation_max_age_seconds:90,admission_max_age_seconds:30,feed_selection:'provider_default'}));return;
  }
  if(req.url==='/auto-trader/pro-ticker/forward-studies'){
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({version:'pro_ticker_intraday_forward_v1',duplicate_window_count:0,paper:true,research_only:true,order_execution_authorized:false,training_enabled:invalidStudies,model_updated:false,total_count:studyPlan?1:0,window_count:studyPlan?1:0,has_more:false,studies:studyPlan?[{plan:studyPlan,report:null,last_error:null,evaluation_in_progress:false}]:[],groups:[],cohorts:[],paired_comparison:{paired_triggered_count:0,mean_one_minus_two_return_percent:null,review_candidate_method:null,requires_explicit_approval:true,applied:false}}));return;
  }
  if(req.url.startsWith('/auto-trader/pro-ticker/manual-signals/')&&(req.url.endsWith('/forward-study')||req.url.endsWith('/forward-study/evaluate'))){
   assert.equal(req.method,'POST');assert.equal(req.headers.origin,origin);assert.equal(evidenceConfigured,true);const chunks=[];for await(const chunk of req)chunks.push(chunk);assert.deepEqual(JSON.parse(Buffer.concat(chunks).toString()),{});
   res.setHeader('Content-Type','application/json');if(req.url.endsWith('/evaluate')){res.statusCode=409;res.end(JSON.stringify({detail:'Not due'}));return;}
   studyPlan=studyPlan||{version:'pro_ticker_intraday_forward_v1',signal_id:savedSignal.request_id,symbol:'AAPL',registered_at:new Date().toISOString(),start:new Date(Date.now()+60000).toISOString(),end_exclusive:new Date(Date.now()+3660000).toISOString(),due_at:new Date(Date.now()+3720000).toISOString(),research_only:true,order_execution_authorized:false,training_eligible:false};
   res.end(JSON.stringify({paper:true,research_only:true,order_submitted:false,duplicate:false,plan:studyPlan}));return;
  }
  if(req.url.startsWith('/auto-trader/pro-ticker/cash-evidence')){
   res.setHeader('Content-Type','application/json');
   if(req.method==='POST'){
    assert.equal(req.headers.origin,origin);const chunks=[];for await(const chunk of req)chunks.push(chunk);assert.deepEqual(JSON.parse(Buffer.concat(chunks).toString()),{});
    res.end(JSON.stringify({version:'pro_ticker_cash_evidence_v1',paper:true,order_submitted:false,budget_released:false,net_pnl_usd:null,observed_activity_count:1,new_activity_count:0}));return;
   }
   res.end(JSON.stringify({version:'pro_ticker_cash_evidence_v1',scope:'dedicated_account_cash_history_not_trade_pnl',paper:true,order_execution_authorized:false,model_updated:false,cost_coverage_final:false,net_pnl_usd:invalidCash?'50000':null,total_count:1,unattributed_count:1,last_observed_at:stamp,rows:[{activity_id:'cash::1',activity_type:'CSD',net_amount_usd:'50000',effective_date:'2026-10-26',symbol:null,category:'external_cash_flow',attribution_status:'unattributed'}],closed_trade_known_costs:[]}));return;
  }
  if(req.url.startsWith('/auto-trader/pro-ticker/performance-analytics')){
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({version:'pro_ticker_performance_v1',paper:true,order_execution_authorized:false,model_updated:false,automatic_strategy_changes:false,net_pnl_usd:invalidAnalytics?'9':null,fees_usd:null,adjustments_usd:null,closed_count:1,incomplete_sync_count:0,gross_realized_usd:'3',observed_equity_drawdown_usd:null,
    daily:[{date:'2026-10-20',gross_realized_usd:'3',cumulative_gross_realized_usd:'3',gross_realized_drawdown_usd:'0'}],groups:[{dimension:'research_grade',label:'B',samples:1,gross_realized_usd:'3'}],equity_observations:[],
    strategy_evaluation:{training_enabled:false,model_updated:false,automatic_strategy_changes:false,risk_limits_changed:false,status:'unavailable_split_required',development:{samples:0,groups:[]},holdout:{samples:0,groups:[]}}}));return;
  }
  if(req.url==='/auto-trader/pro-ticker/closed-performance'){
   const kind='closed_gross_estimate_from_terminal_broker_averages';
   const row=(symbol,pnl)=>({paper:true,kind,symbol,recorded_at:stamp,status:pnl===null?'unavailable':'priced',shares:pnl===null?null:2,gross_pnl_cents:pnl,training_eligible:false,net_pnl:null,fees:null});
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({paper:true,kind,model_updated:false,net_pnl:invalidClosedPerformance?123:null,unrealized_pnl:null,drawdown:null,closed_reservations_count:3,recorded_count:3,priced_count:2,unpriced_count:1,gross_wins:1,gross_losses:1,gross_flat:0,has_more:false,rows:[row('AAPL',500),row('MSFT',-200),row('AMD',null)],gross_pnl_cents:300,gross_profit_cents:500,gross_loss_cents:200,gross_win_rate_percent:50,gross_profit_factor:2.5,average_gross_gain_cents:500,average_gross_loss_cents:-200}));return;
  }
  if(req.url.startsWith('/auto-trader/pro-ticker/manual-signals/dataset')){
   const count=savedSignal?1:0,evaluated=savedSignal?.forward_outcome?1:0;
   if(req.url.endsWith('?format=csv')){
    res.setHeader('Content-Type','text/csv; charset=utf-8');res.setHeader('X-Research-Total-Count',String(count));res.setHeader('X-Research-Window-Count',String(count));res.setHeader('X-Research-Has-More','false');
    res.end('dataset_version,symbol,forward_return_percent,training_eligible\npro_ticker_research_evidence_v1,AAPL,'+(savedSignal?.forward_outcome?.return_percent??'')+',false\n');return;
   }
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({paper:true,version:'pro_ticker_research_evidence_v1',kind:'forward_quote_return_not_trade_pnl',training_enabled:invalidDataset,model_updated:false,training_ready_samples:0,total_count:count,window_count:count,graded_count:count,evaluated_count:evaluated,unevaluated_count:count-evaluated,has_more:false}));return;
  }
  if(req.url==='/auto-trader/pro-ticker/market-scan'){
   res.setHeader('Content-Type','application/json');
   if(req.method==='POST'){
    assert.equal(req.headers.origin,origin);const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=JSON.parse(Buffer.concat(chunks).toString());if(body.mode==='discover')assert.deepEqual(body.symbols,[]);else assert.deepEqual(body.symbols,['AAPL','BAD']);marketScanPosts++;const independent=body.mode==='discover',scanSymbols=independent?['NEWMOVER']:body.symbols;
    marketScan={paper:true,research_only:true,order_execution_authorized:false,automatic_scanning_enabled:false,status:'completed',run:{id:body.request_id,symbols:scanSymbols,universe_source:independent?'yahoo_us_equity_movers':'operator_symbols',universe_count:independent?22:2,started_at:stamp,finished_at:new Date().toISOString(),requested:scanSymbols.length,accepted:1,failed:independent?0:1,research_only:true,order_execution_authorized:false,candidates:[{symbol:scanSymbols[0],price:'10.50',price_change_percent:'5',volume:300,average_volume:'100.5',relative_volume:'2.985',observed_at:new Date().toISOString(),source:'synthetic_fixture',research_score:null,market_data_verified:false,order_execution_authorized:false,research_only:true}],failures:independent?[]:[{symbol:'BAD',reason:'Quote unavailable or invalid'}]},saved_daily_grades:independent?{NEWMOVER:{grade:'B',score:3,recorded_at:stamp,kind:'saved_daily_technical_not_intraday'}}:{}};
   }
   res.end(JSON.stringify(marketScan||{paper:true,research_only:true,order_execution_authorized:false,automatic_scanning_enabled:false,status:'not_run',run:null}));return;
  }
  if(req.url==='/auto-trader/pro-ticker/execution-journal.csv'){
   res.setHeader('Content-Type','text/csv; charset=utf-8');res.setHeader('X-Execution-Total-Count',String(pricedExecutions.length));
   const fields=['activity_id','intent_id','broker_order_id','executed_at','recorded_at','symbol','side','shares','price'];
   res.end([fields.join(','),...pricedExecutions.map(row=>fields.map(key=>row[key]).join(','))].join('\r\n'));return;
  }
  if(req.url==='/auto-trader/pro-ticker/execution-journal'){
   const journalRows=closedExecutionMetrics?[10,15,10,8,10,10].map((price,index)=>({activity_id:'gross-fixture::'+index,intent_id:'closed-fixture-'+Math.floor(index/2),broker_order_id:'order-fixture-'+index,symbol:'AAPL',side:index%2?'sell':'buy',shares:1,price:String(price),executed_at:stamp,recorded_at:stamp})):pricedExecutions;
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({paper:true,kind:'owned_broker_fill_activities',rows:journalRows,total_count:journalRows.length,has_more:false,grade_source:'saved_allocation_entry_grade',training_eligible:false,unattributed_closed_count:0,entry_grade_groups:closedExecutionMetrics?[{grade:'B',grade_version:'daily_fixture_v1',closed_count:3,gross_wins:1,gross_losses:1,gross_flat:1,gross_realized_usd:'3',gross_profit_usd:'5',gross_loss_usd:'2',gross_profit_factor:'2.500000',average_gross_result_usd:invalidExecutionMetrics?'invalid':'1.000000'}]:[],closed_priced_count:closedExecutionMetrics?3:0,gross_profit_usd:closedExecutionMetrics?'5':null,gross_loss_usd:closedExecutionMetrics?'2':null,gross_profit_factor:closedExecutionMetrics?'2.500000':null,average_gross_result_usd:invalidExecutionMetrics?'invalid':closedExecutionMetrics?'1.000000':null,gross_wins:closedExecutionMetrics?1:0,gross_losses:closedExecutionMetrics?1:0,gross_flat:closedExecutionMetrics?1:0,gross_win_rate_percent:closedExecutionMetrics?33.33:null,gross_realized_usd:closedExecutionMetrics?'3':null,net_pnl:null,fees:null,model_updated:false}));return;
  }
  if(req.url.startsWith('/auto-trader/pro-ticker/order-intents/')&&req.url.endsWith('/execution-sync')){
   assert.equal(req.method,'POST');assert.equal(req.headers.origin,origin);const chunks=[];for await(const chunk of req)chunks.push(chunk);assert.deepEqual(JSON.parse(Buffer.concat(chunks).toString()),{});
   pricedExecutions=[{activity_id:'synthetic-activity::1',intent_id:savedSignal.order_intent.id,broker_order_id:'synthetic-broker-order',symbol:'AAPL',side:'buy',shares:2,price:'10.5',executed_at:stamp,recorded_at:stamp}];
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({paper:true,order_submitted:false,budget_released:false,intent_id:savedSignal.order_intent.id,execution_count:1,coverage_complete:true,net_pnl:null}));return;
  }
  if(req.url==='/auto-trader/pro-ticker/execution-status'){
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({paper:true,admission_review_enabled:evidenceConfigured,recovery_enabled:evidenceConfigured,protection_review_enabled:evidenceConfigured,owner_submission_enabled:ownerSubmissionConfigured,owner_management_enabled:managementConfigured,execution_enabled:false,automatic_execution_enabled:false,automatic_release_enabled:false,account_verified:false}));return;
  }
  if(req.url.startsWith('/auto-trader/pro-ticker/order-intents/')&&(req.url.endsWith('/manage')||req.url.endsWith('/manage-recover'))){
   assert.equal(req.method,'POST');assert.equal(req.headers.origin,origin);const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=JSON.parse(Buffer.concat(chunks).toString());
   if(req.url.endsWith('/manage')){assert.equal(managementConfigured,true);assert.equal(body.confirmed,true);assert.ok(['cancel_entry','close'].includes(body.action));managementRequests++;}else assert.deepEqual(body,{});
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({paper:true,budget_released:false,automatic_release_enabled:false,intent_id:savedSignal.order_intent.id,state:'close_new',entry_cancellation_confirmed:false,position_closed:false,remaining_shares:2,close_dispatch_attempted:true}));return;
  }
  if(req.url.startsWith('/auto-trader/pro-ticker/order-intents/')&&req.url.endsWith('/admission-review')){
   assert.equal(req.method,'POST');assert.equal(req.headers.origin,origin);const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=JSON.parse(Buffer.concat(chunks).toString());assert.deepEqual(Object.keys(body),['request_id']);admissionRequests.push(body);
   res.setHeader('Content-Type','application/json');if(admissionRequests.length===1){res.statusCode=503;res.end(JSON.stringify({detail:'Lost read-only review confirmation'}));return;}
   assert.equal(body.request_id,admissionRequests[0].request_id);
   const codes=['dedicated_paper_account','account_active_and_unblocked','broker_buying_power','regular_market_open','active_tradable_equity','broker_position_clear','broker_orders_clear','snapshot_fetch_fresh','current_trade_fresh','entry_within_reserved_ceiling','ledger_unchanged_during_review','current_allocation_limit','current_stop_risk_limit','fresh_at_review_commit'];
   res.end(JSON.stringify({paper:true,preview_only:true,review_id:body.request_id,intent_id:savedSignal.order_intent.id,execution_enabled:false,order_submitted:false,automatic_release_enabled:false,eligible_for_manual_submission:false,checks_passed:true,checks:codes.map(code=>({code,passed:true})),submission_blockers:['submission_not_enabled','protective_orders_not_verified'],expired:false,ledger_unchanged:true}));return;
  }
  if(req.url.startsWith('/auto-trader/pro-ticker/order-intents/')&&req.url.endsWith('/protection-review')){
   assert.equal(req.method,'POST');assert.equal(req.headers.origin,origin);const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=JSON.parse(Buffer.concat(chunks).toString());assert.deepEqual(Object.keys(body),['request_id']);
   const codes=['dedicated_paper_account','owned_bracket_entry','entry_fully_filled','entry_fills_accounted_without_exit','broker_position_matches','regular_market_open','two_independently_fetched_legs','leg_snapshots_match_parent','active_owned_sell_legs','stop_and_target_order_types','stop_and_target_within_plan','actual_entry_cost_and_stop_risk','fresh_trade_between_exit_levels','bounded_fetch_duration','ledger_unchanged_during_review','fresh_at_review_commit'];
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({paper:true,preview_only:true,review_id:body.request_id,intent_id:savedSignal.order_intent.id,execution_enabled:false,order_submitted:false,budget_released:false,loss_cap_guaranteed:false,checks_passed:true,checks:codes.map(code=>({code,passed:true})),expired:false,ledger_unchanged:true,protection_orders_verified:true,expires_at:Date.now()/1000+30}));return;
  }
  if(req.url.startsWith('/auto-trader/pro-ticker/order-intents/')&&req.url.endsWith('/submit')){
   assert.equal(req.method,'POST');assert.equal(req.headers.origin,origin);assert.equal(ownerSubmissionConfigured,true);const chunks=[];for await(const chunk of req)chunks.push(chunk);assert.deepEqual(JSON.parse(Buffer.concat(chunks).toString()),{confirmed:true});submissionRequests++;
   savedSignal.order_intent={...savedSignal.order_intent,status:'broker_linked',order_submitted:true,attempt_started_at:stamp};
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({paper:true,resolved:true,dispatch_attempted:true,order_resubmitted:false,budget_released:false,protection_verified:false,intent:savedSignal.order_intent}));return;
  }
  if(req.url.startsWith('/auto-trader/pro-ticker/order-intents/')&&req.url.endsWith('/monitor')){
   assert.equal(req.method,'POST');assert.equal(req.headers.origin,origin);const chunks=[];for await(const chunk of req)chunks.push(chunk);assert.deepEqual(JSON.parse(Buffer.concat(chunks).toString()),{});
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({paper:true,resolved:true,closed:false,protection_verified:true,order_submitted:false,budget_released:false,automatic_release_enabled:false,intent:savedSignal.order_intent,legs:[{id:'synthetic-stop',status:'new',shares:2,filled_shares:0},{id:'synthetic-target',status:'new',shares:2,filled_shares:0}]}));return;
  }
  if(req.url.startsWith('/auto-trader/pro-ticker/order-intents/')&&req.url.endsWith('/recover')){
   assert.equal(req.method,'POST');assert.equal(req.headers.origin,origin);const chunks=[];for await(const chunk of req)chunks.push(chunk);assert.deepEqual(JSON.parse(Buffer.concat(chunks).toString()),{});recoveryRequests++;
   if(recoveryRequests>1)savedSignal.order_intent={...savedSignal.order_intent,status:'broker_linked',order_submitted:true};
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({paper:true,resolved:recoveryRequests>1,order_resubmitted:false,budget_released:false,protection_verified:false,intent:savedSignal.order_intent}));return;
  }
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
    assert.deepEqual(Object.keys(body).sort(),['request_id','symbol']);
    if(body.symbol==='NEWMOVER'){handoffRequests.push(body);res.statusCode=503;res.end(JSON.stringify({detail:'Synthetic provider failure'}));return;}
    assert.equal(body.symbol,'AAPL');signalRequests.push(body);
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
   marketScan=null;pricedExecutions=[];studyPlan=null;invalidStudies=false;data=fixture();fail=false;requests=[];forwardDue=false;savedSignal=null;draftRequests=[];allocationRequests=[];admissionRequests=[];evidenceConfigured=false;recoveryRequests=0;signalRequests=[];discoveries=0;invalidClosedPerformance=false;invalidDataset=false;invalidProposal=false;invalidSettings=false;settingsRevision=0;settingsValues={max_allocation_cents:5000000,max_stop_risk_cents:5000000,discovery_article_limit:5};
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
   await page.locator('#pt-tab-signals').click();await page.locator('#pt-signal-symbol').fill('aapl');
   await page.getByRole('button',{name:'Check latest broker reference',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-reference-status').textContent.includes('AAPL latest broker trade'));
   assert.match(await page.locator('#pt-reference-status').innerText(),/submission: passes/);assert.equal(signalRequests.length,0);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-broker-reference.png',fullPage:true});
   await page.locator('#pt-signal-submit').click();
   await page.waitForFunction(()=>document.getElementById('pt-signal-status').textContent.includes('Review graded'));
   assert.equal(await page.locator('#pt-signal-history tr').count(),1);assert.match(await page.locator('#pt-signal-history').innerText(),/AAPL/);
   await page.getByRole('button',{name:'Inspect AAPL review',exact:true}).click();assert.match(await page.locator('#pt-signal-detail').innerText(),/Synthetic entry-time evidence/);
   await page.locator('#pt-signal-submit').click();await page.waitForFunction(()=>document.getElementById('pt-signal-status').textContent.includes('Review graded'));
   assert.equal(signalRequests.length,2);assert.equal(signalRequests[0].request_id,signalRequests[1].request_id);
   assert.equal(await page.locator('#pt-signal-history tr').count(),1);await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-manual-signals.png',fullPage:true});
   evidenceConfigured=true;
   await page.getByRole('button',{name:'Register 60-minute forward study',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Registered future window:'));
   await page.getByRole('button',{name:'Evaluate saved forward study',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('not due yet'));
   evidenceConfigured=false;
   await page.locator('#pt-signal-detail input[name="shares"]').fill('2');await page.locator('#pt-signal-detail input[name="stop_loss_percent"]').fill('5');await page.locator('#pt-signal-detail input[name="take_profit_percent"]').fill('10');
   await page.getByRole('button',{name:'Save PAPER draft (no order)',exact:true}).click();await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Unable to confirm draft'));
   assert.equal(await page.getByRole('button',{name:'Save PAPER draft (no order)',exact:true}).isDisabled(),false);
   await page.getByRole('button',{name:'Save PAPER draft (no order)',exact:true}).click();await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Saved PAPER research draft'));
   assert.equal(draftRequests.length,2);assert.equal(draftRequests[0].request_id,draftRequests[1].request_id);
   await page.getByRole('button',{name:'Inspect AAPL review',exact:true}).click();assert.equal(await page.locator('#pt-signal-detail form').count(),0);assert.match(await page.locator('#pt-signal-detail').innerText(),/draft itself does not reserve funds or place an order/);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-saved-draft.png',fullPage:true});
   assert.equal(await page.getByRole('button',{name:'Reserve virtual funds (no order)',exact:true}).isDisabled(),true);
   await page.getByLabel(/I approve reserving up to/).check();await page.getByRole('button',{name:'Reserve virtual funds (no order)',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Allocation update unconfirmed'));
   await page.getByRole('button',{name:'Reserve virtual funds (no order)',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Virtual funds reserved: $21.00'));
   assert.equal(allocationRequests.length,2);assert.equal(allocationRequests[0].request_id,allocationRequests[1].request_id);
   await page.locator('#pt-tab-trading').click();await page.waitForFunction(()=>document.getElementById('pt-order-intents').textContent.includes('Virtual funds reserved'));assert.match(await page.locator('#pt-order-intents').innerText(),/AAPL/);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-reserved-intent.png',fullPage:true});await page.locator('#pt-tab-signals').click();
   await page.waitForFunction(()=>document.getElementById('pt-execution-evidence').textContent.includes('not configured or approved'));
   assert.equal(await page.getByRole('button',{name:'Review PAPER admission (no order)',exact:true}).isDisabled(),true);
   evidenceConfigured=true;await page.getByRole('button',{name:'Inspect AAPL review',exact:true}).click();
   await page.waitForFunction(()=>!Array.from(document.querySelectorAll('#pt-signal-detail button')).find(b=>b.textContent==='Review PAPER admission (no order)').disabled);
   await page.getByRole('button',{name:'Review PAPER admission (no order)',exact:true}).click();await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Review could not be confirmed'));
   await page.getByRole('button',{name:'Review PAPER admission (no order)',exact:true}).click();await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Read-only checks passed. Submission remains disabled'));
   assert.equal(admissionRequests.length,2);assert.equal(admissionRequests[0].request_id,admissionRequests[1].request_id);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-admission-review.png',fullPage:true});
   assert.equal(await page.getByRole('button',{name:'Cancel unsubmitted allocation',exact:true}).isDisabled(),true);
   await page.getByLabel(/I approve canceling this never-submitted allocation/).check();await page.getByRole('button',{name:'Cancel unsubmitted allocation',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Allocation canceled. Returned virtual funds are not trading profit'));
   assert.equal(savedSignal.order_intent.status,'canceled');assert.equal(data.budget.committed_cents,10000);
   const canceledIntent=savedSignal.order_intent;
   savedSignal.order_intent={...canceledIntent,status:'uncertain',budget_reserved:true,order_submitted:null,attempt_started_at:stamp};
   await page.evaluate(()=>window.loadProTickerSignals());await page.getByRole('button',{name:'Inspect AAPL review',exact:true}).click();
   await page.waitForFunction(()=>!Array.from(document.querySelectorAll('#pt-signal-detail button')).find(b=>b.textContent==='Find existing PAPER order (no new buy)').disabled);
   assert.match(await page.locator('#pt-signal-detail').innerText(),/Submission outcome is uncertain/);assert.equal(await page.getByRole('button',{name:'Cancel unsubmitted allocation',exact:true}).count(),0);
   await page.getByRole('button',{name:'Find existing PAPER order (no new buy)',exact:true}).click();await page.waitForFunction(()=>document.getElementById('pt-signal-status').textContent.includes('No matching order found. Funds remain reserved'));
   assert.equal(savedSignal.order_intent.status,'uncertain');
   await page.waitForFunction(()=>!Array.from(document.querySelectorAll('#pt-signal-detail button')).find(b=>b.textContent==='Find existing PAPER order (no new buy)').disabled);
   await page.getByRole('button',{name:'Find existing PAPER order (no new buy)',exact:true}).click();await page.waitForFunction(()=>document.getElementById('pt-signal-status').textContent.includes('Existing PAPER order recovered; no new buy sent'));
   assert.equal(recoveryRequests,2);assert.equal(savedSignal.order_intent.status,'broker_linked');
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-recovered-intent.png',fullPage:true});
   await page.getByRole('button',{name:'Inspect existing PAPER protection',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Existing exit orders observed.'));
   assert.match(await page.locator('#pt-signal-detail').innerText(),/Stops do not guarantee a loss cap/);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-protection-review.png',fullPage:true});

   await page.locator('#pt-tab-scanner').click();await page.locator('#pt-market-symbols').fill('AAPL, BAD');
   await page.getByRole('button',{name:'Scan market candidates',exact:true}).click();await page.waitForFunction(()=>document.getElementById('pt-market-status').textContent.includes('1 accepted / 2 requested'));
   assert.match(await page.locator('#pt-market-scanner').innerText(),/Unavailable \(grade separately\)/);
   await page.getByRole('button',{name:'Refresh saved market scan',exact:true}).click();await page.waitForFunction(()=>document.getElementById('pt-market-status').textContent.includes('BAD: Quote unavailable or invalid'));
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-market-scanner.png',fullPage:true});
   await page.getByRole('button',{name:'Grade AAPL',exact:true}).click();assert.equal(await page.locator('#pt-signal-symbol').inputValue(),'AAPL');
   await page.locator('#pt-tab-scanner').click();await page.getByRole('button',{name:'Discover US market movers',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-market-status').textContent.includes('Yahoo discovery: 22 eligible symbols'));
   assert.match(await page.locator('#pt-market-scanner').innerText(),/NEWMOVER/);assert.match(await page.locator('#pt-market-scanner').innerText(),/not current intraday/);
   assert.equal(await page.locator('#pt-market-scanner tbody tr').first().locator('td').nth(5).evaluate(cell=>cell.scrollWidth<=cell.clientWidth+1),true,'saved grade and timestamp must fit inside their cell');
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-independent-discovery.png',fullPage:true});
   // Scanner assignments do not emit input events. New symbols must not reuse
   // the previous review's identity; failed retries of that symbol still do.
   handoffRequests=[];await page.getByRole('button',{name:'Grade NEWMOVER',exact:true}).click();
   await page.locator('#pt-signal-submit').click();await page.waitForFunction(()=>document.getElementById('pt-signal-status').textContent.includes('Unable to confirm review'));
   await page.locator('#pt-signal-submit').click();await page.waitForFunction(()=>!document.getElementById('pt-signal-submit').disabled);
   assert.equal(handoffRequests.length,2);assert.notEqual(handoffRequests[0].request_id,signalRequests[0].request_id);
   assert.equal(handoffRequests[0].request_id,handoffRequests[1].request_id);


   // Separate synthetic owner-dispatch scenario. Default configuration never
   // permits this POST and checking the explicit approval is required.
   savedSignal.order_intent={...canceledIntent,status:'prepared',budget_reserved:true,order_submitted:false,attempt_started_at:null};ownerSubmissionConfigured=true;
   await page.evaluate(()=>window.loadProTickerSignals());await page.getByRole('button',{name:'Inspect AAPL review',exact:true}).click();
   const submitPaper=page.getByRole('button',{name:'Submit approved PAPER bracket',exact:true});assert.equal(await submitPaper.isDisabled(),true);
   await page.getByLabel(/I authorize this dedicated PAPER limit bracket/).check();await submitPaper.click();
   await page.waitForFunction(()=>document.getElementById('pt-signal-status').textContent.includes('Owned PAPER order linked'));
   assert.equal(submissionRequests,width===1280?1:2);assert.equal(await page.getByRole('button',{name:'Submit approved PAPER bracket',exact:true}).isDisabled(),true);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-owner-paper-submission.png',fullPage:true});ownerSubmissionConfigured=false;
   await page.getByRole('button',{name:'Reconcile PAPER position & exits',exact:true}).click();await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Existing bracket protection observed for this regular-session snapshot.'));
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-position-monitor.png',fullPage:true});
   // Management remains disabled until server configuration AND explicit owner
   // approval. These requests terminate at the local synthetic HTTP fixture.
   const closeOwned=()=>page.getByRole('button',{name:'Close owned PAPER position / resume',exact:true});
   assert.equal(await closeOwned().isDisabled(),true);
   managementConfigured=true;await page.getByRole('button',{name:'Inspect AAPL review',exact:true}).click();
   assert.equal(await closeOwned().isDisabled(),true);
   await page.getByLabel(/I approve canceling this intent's pending orders and closing/).check();
   await page.waitForFunction(()=>!Array.from(document.querySelectorAll('#pt-signal-detail button')).find(b=>b.textContent==='Close owned PAPER position / resume').disabled);
   await closeOwned().click();await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Broker state: close_new'));
   assert.equal(await closeOwned().isDisabled(),true);assert.equal(managementRequests,width===1280?1:2);
   await page.getByRole('button',{name:'Inspect saved PAPER management',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Broker state: close_new'));
   assert.equal(managementRequests,width===1280?1:2);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-owner-management.png',fullPage:true});managementConfigured=false;

   await page.getByRole('button',{name:'Sync priced PAPER executions',exact:true}).click();await page.waitForFunction(()=>document.getElementById('pt-signal-detail').textContent.includes('Saved 1 owned broker FILL activities'));
   await page.locator('#pt-tab-journal').click();assert.match(await page.locator('#pt-priced-executions').innerText(),/synthetic-broker-order/);assert.match(await page.locator('#pt-priced-executions').innerText(),/No zero-profit result is inferred/);
   const [pricedDownload]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Export full execution history (CSV)',exact:true}).click()]);const pricedCSV=fs.readFileSync(await pricedDownload.path(),'utf8');assert.match(pricedCSV,/executed_at/);assert.match(pricedCSV,/synthetic-activity::1/);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-priced-executions.png',fullPage:true});
   closedExecutionMetrics=true;await page.getByRole('button',{name:'Refresh priced execution journal',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-priced-executions').textContent.includes('gross profit factor: 2.500000'));
   assert.match(await page.locator('#pt-priced-executions').innerText(),/average gross result per closed trade: \$1.000000/);
   assert.match(await page.locator('#pt-priced-executions').innerText(),/net P&L remain unavailable/);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-execution-gross-metrics.png',fullPage:true});
   await page.locator('#pt-tab-performance').click();
   await page.getByRole('button',{name:'Refresh execution analytics',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-performance-analytics').textContent.includes('Gross realized: $3'));
   invalidAnalytics=true;await page.getByRole('button',{name:'Refresh execution analytics',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-performance-analytics').textContent.includes('Invalid analytics evidence'));
   assert.doesNotMatch(await page.locator('#pt-performance-analytics').innerText(),/Gross realized: \$3/);
   invalidAnalytics=false;
   await page.getByRole('button',{name:'Refresh forward studies',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-forward-studies').textContent.includes('Showing 1 of 1 manually registered plans'));
   assert.equal(await page.getByRole('button',{name:'Evaluate AAPL study',exact:true}).isDisabled(),true);
   invalidStudies=true;await page.getByRole('button',{name:'Refresh forward studies',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-forward-studies').textContent.includes('Invalid forward study evidence'));
   assert.equal(await page.locator('#pt-forward-studies').getByRole('button',{name:'Evaluate AAPL study',exact:true}).count(),0);invalidStudies=false;
   await page.getByRole('button',{name:'Sync read-only PAPER cash evidence',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-cash-evidence').textContent.includes('1 saved account cash activities'));
   assert.match(await page.locator('#pt-cash-evidence').innerText(),/external_cash_flow/);
   invalidCash=true;await page.getByRole('button',{name:'Refresh saved cash evidence',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-cash-evidence').textContent.includes('Invalid cash evidence'));
   assert.doesNotMatch(await page.locator('#pt-cash-evidence').innerText(),/external_cash_flow/);invalidCash=false;
   await page.locator('#pt-tab-performance').click();await page.getByRole('button',{name:'Refresh execution grade outcomes',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-execution-grade-outcomes').textContent.includes('3 of 3 completely covered closed trades'));
   assert.match(await page.locator('#pt-execution-grade-outcomes').innerText(),/daily_fixture_v1/);
   assert.match(await page.locator('#pt-execution-grade-outcomes').innerText(),/model training stays disabled/);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-execution-grade-outcomes.png',fullPage:true});
   invalidExecutionMetrics=true;await page.getByRole('button',{name:'Refresh execution grade outcomes',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-execution-grade-outcomes').textContent.includes('Invalid grouped execution metrics'));
   assert.doesNotMatch(await page.locator('#pt-execution-grade-outcomes').innerText(),/daily_fixture_v1/);
   await page.locator('#pt-tab-journal').click();
   invalidExecutionMetrics=true;await page.getByRole('button',{name:'Refresh priced execution journal',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-priced-executions').textContent.includes('Invalid execution gross metrics'));
   assert.doesNotMatch(await page.locator('#pt-priced-executions').innerText(),/gross profit factor: 2.500000/);
   invalidExecutionMetrics=false;closedExecutionMetrics=false;await page.getByRole('button',{name:'Refresh priced execution journal',exact:true}).click();
   await page.waitForFunction(()=>document.getElementById('pt-priced-executions').textContent.includes('No zero-profit result is inferred'));
   await page.locator('#pt-tab-signals').click();


   // Read-only fixture projections exercise how independent broker accounting
   // is rendered. The browser does not create an order or release broker funds.
   savedSignal.order_intent={...canceledIntent,status:'broker_linked',budget_reserved:true,order_submitted:true};
   await page.evaluate(()=>window.loadProTickerSignals());await page.getByRole('button',{name:'Inspect AAPL review',exact:true}).click();
   assert.match(await page.locator('#pt-signal-detail').innerText(),/A broker order is linked/);
   assert.equal(await page.getByRole('button',{name:'Cancel unsubmitted allocation',exact:true}).count(),0);
   await page.locator('#pt-tab-trading').click();await page.waitForFunction(()=>document.getElementById('pt-order-intents').textContent.includes('Broker order linked; reconcile fills'));
   savedSignal.order_intent={...canceledIntent,status:'closed',budget_reserved:false,order_submitted:true};
   await page.evaluate(()=>window.loadProTickerSignals());await page.waitForFunction(()=>document.getElementById('pt-order-intents').textContent.includes('Closed; owner-approved funds returned'));
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-reconciled-intent.png',fullPage:true});
   await page.locator('#pt-tab-signals').click();await page.getByRole('button',{name:'Inspect AAPL review',exact:true}).click();
   assert.match(await page.locator('#pt-signal-detail').innerText(),/Allocation state: closed/);


   await page.getByRole('button',{name:'Check 24+ hour forward quote',exact:true}).click();await page.waitForFunction(()=>document.getElementById('pt-signal-status').textContent.includes('Wait until a fresh market quote'));assert.equal(savedSignal.forward_outcome,undefined);assert.match(await page.locator('#pt-forward-quote-status').innerText(),/Wait until a fresh market quote/);
   forwardDue=true;await page.getByRole('button',{name:'Check 24+ hour forward quote',exact:true}).click();await page.waitForFunction(()=>document.getElementById('pt-signal-status').textContent.includes('did not update an AI model'));
   assert.match(await page.locator('#pt-signal-detail').innerText(),/-14\.285714/);assert.equal(savedSignal.entry_grade.score,4);
   await page.locator('#pt-tab-performance').click();assert.match(await page.locator('#pt-signal-learning').innerText(),/not realized P&L/);assert.match(await page.locator('#pt-signal-learning-rows').innerText(),/-14\.29/);
   await page.waitForFunction(()=>document.getElementById('pt-research-export').textContent.includes('1 with a separate forward quote'));
   assert.match(await page.locator('#pt-research-export').innerText(),/Training-ready samples: 0/);
   const [researchDownload]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Export research evidence (CSV)',exact:true}).click()]);
   assert.equal(researchDownload.suggestedFilename(),'pro-ticker-research-evidence.csv');
   const researchCSV=fs.readFileSync(await researchDownload.path(),'utf8');assert.match(researchCSV,/-14\.285714,false/);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-forward-research.png',fullPage:true});
   invalidDataset=true;await page.evaluate(()=>window.loadProTickerSignals());await page.waitForFunction(()=>document.getElementById('pt-research-export').textContent.includes('Invalid research readiness evidence'));
   assert.equal(await page.getByRole('button',{name:'Export research evidence (CSV)',exact:true}).isDisabled(),true);
   await page.waitForFunction(()=>document.getElementById('pt-closed-performance').textContent.includes('Gross closed estimate: $3.00'));
   assert.match(await page.locator('#pt-closed-performance').innerText(),/Gross win rate: 50.0%/);assert.match(await page.locator('#pt-closed-performance').innerText(),/Gross profit factor: 2.50/);assert.match(await page.locator('#pt-closed-performance').innerText(),/-\$2.00/);
   await page.screenshot({path:'.artifacts/pro-ticker-workspace/'+width+'-closed-gross-estimates.png',fullPage:true});
   invalidClosedPerformance=true;await page.evaluate(()=>window.loadProTickerWorkspace());await page.waitForFunction(()=>document.getElementById('pt-closed-performance').textContent.includes('Invalid closed PAPER report'));
   assert.doesNotMatch(await page.locator('#pt-closed-performance').innerText(),/Gross closed estimate: \$3.00/);
   invalidClosedPerformance=false;await page.evaluate(()=>window.loadProTickerWorkspace());

   invalidDataset=false;await page.evaluate(()=>window.loadProTickerSignals());await page.locator('#pt-tab-signals').click();
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
   savedSignal={...savedSignal,symbol:'GCTK',status:'unavailable',proposal:null,order_intent:null,forward_outcome:null,entry_grade:{version:'pro_ticker_daily_technical_grade_v1',status:'unavailable',grade:null,score:null,signal:null,training_eligible:false}};
   await page.locator('#pt-tab-signals').click();await page.getByRole('button',{name:'Refresh saved reviews',exact:true}).click();
   await page.getByRole('button',{name:'Inspect GCTK review',exact:true}).click();
   for(const name of ['Register 60-minute forward study','Evaluate saved forward study','Check 24+ hour forward quote'])assert.equal(await page.getByRole('button',{name,exact:true}).isDisabled(),true);
   assert.match(await page.locator('#pt-forward-study-status').innerText(),/at least 50 valid price-history sessions/);
   assert.match(await page.locator('#pt-forward-quote-status').innerText(),/a fresh quote is not enough/);
   await page.locator('#pt-tab-overview').focus();await page.keyboard.press('ArrowRight');assert.equal(await page.locator('#pt-tab-scanner').getAttribute('aria-selected'),'true');
   fail=true;await page.locator('#pt-refresh').click();await page.waitForFunction(()=>document.getElementById('pt-status').textContent.includes('Unable to load'));assert.equal(await page.locator('#pt-metrics').innerText(),'');assert.match(await page.locator('#pt-journal').innerText(),/Data unavailable/);
   fail=false;data=fixture();data.paper=false;await page.locator('#pt-refresh').click();await page.waitForFunction(()=>document.getElementById('pt-status').textContent.includes('safety flags'));assert.equal(await page.locator('#pt-metrics').innerText(),'');
   assert.deepEqual(errors,[]);assert.ok(requests.every(([method,url])=>method==='GET'||method==='POST'&&['/auto-trader/pro-ticker/proposal','/auto-trader/pro-ticker/discovery','/auto-trader/pro-ticker/manual-signals','/auto-trader/pro-ticker/manual-signals/proposal','/auto-trader/pro-ticker/order-intents','/auto-trader/pro-ticker/market-scan','/auto-trader/pro-ticker/cash-evidence/sync'].includes(url)||method==='POST'&&url.startsWith('/auto-trader/pro-ticker/manual-signals/')&&url.endsWith('/forward-review')||method==='POST'&&/^\/auto-trader\/pro-ticker\/manual-signals\/[0-9a-f-]{36}\/forward-study(?:\/evaluate)?$/.test(url)||method==='POST'&&url.startsWith('/auto-trader/pro-ticker/order-intents/')&&['/cancel','/admission-review','/recover','/protection-review','/submit','/monitor','/execution-sync','/manage','/manage-recover'].some(suffix=>url.endsWith(suffix))||method==='PUT'&&url==='/auto-trader/pro-ticker/settings'),'workspace writes must use explicit authenticated controls');await context.close();
  }
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
});
