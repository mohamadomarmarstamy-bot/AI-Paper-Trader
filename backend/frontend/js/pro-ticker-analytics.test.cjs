'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/pro-ticker-workspace.js','utf8');
const block=source.slice(source.lastIndexOf('(() => {'));
const fixture=()=>({version:'pro_ticker_performance_v1',paper:true,order_execution_authorized:false,model_updated:false,automatic_strategy_changes:false,net_pnl_usd:null,fees_usd:null,adjustments_usd:null,closed_count:1,incomplete_sync_count:0,gross_realized_usd:'3',observed_equity_drawdown_usd:null,daily:[{date:'2026-10-20',gross_realized_usd:'3',cumulative_gross_realized_usd:'3',gross_realized_drawdown_usd:'0'}],groups:[],equity_observations:[],strategy_evaluation:{training_enabled:false,model_updated:false,automatic_strategy_changes:false,risk_limits_changed:false,status:'unavailable_split_required',development:{samples:0,groups:[]},holdout:{samples:0,groups:[]}}});
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function setup(){
 const nodes=[],requests=[];
 function node(){const n={children:[],handlers:{},value:'',textContent:'',append(...children){this.children.push(...children);},appendChild(child){this.children.push(child);},replaceChildren(...children){this.children=children;},setAttribute(){},addEventListener(name,fn){this.handlers[name]=fn;}};nodes.push(n);return n;}
 const panel=node(),refresh=node();let data=fixture();
 vm.runInNewContext(block,{document:{getElementById:id=>id==='pt-panel-performance'?panel:id==='pt-refresh'?refresh:null,createElement:node},URLSearchParams,Number,Date,
  fetch:async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>data};}});
 const article=nodes.find(n=>n.id==='pt-performance-analytics');
 return {nodes,requests,article,change:value=>data=value,refresh:()=>refresh.handlers.click(),text:()=>nodes.map(n=>n.textContent).join(' ')};
}
test('analytics renders gross and unknown net, and sends UTC filters',async()=>{
 const ui=setup();await settle();assert.match(ui.text(),/Gross realized: \$3/);assert.match(ui.text(),/Net P\/L, fees and adjustments: unavailable/);
 const start=ui.nodes.find(n=>n.name==='start');start.value='2026-10-20T12:00';await ui.refresh();
 assert.match(ui.requests.at(-1).url,/start=2026-10-20T12%3A00Z/);
 assert.equal(ui.requests.at(-1).options.credentials,'same-origin');
});
test('malformed totals and unsafe flags clear previously displayed analytics',async()=>{
 for(const change of [{net_pnl_usd:'9'},{training_enabled:true},{gross_realized_usd:'NaN'},{automatic_strategy_changes:true}]){
  const ui=setup();await settle();const bad={...fixture(),...change};if(change.training_enabled)bad.strategy_evaluation.training_enabled=true;
  ui.change(bad);await ui.refresh();const summary=ui.article.children[4],results=ui.article.children[5];
  assert.equal(summary.textContent,'');assert.equal(results.children.length,0);assert.match(ui.text(),/Execution analytics unavailable/);
 }
});
