const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function harness(){
 const children=[],attributes={},summary={textContent:''},timers=[];let applied=[],fit=0;
 const container={clientWidth:600,isConnected:true,setAttribute:(k,v)=>attributes[k]=v,removeAttribute:k=>delete attributes[k],appendChild:el=>children.push(el),querySelectorAll:()=>children.map(el=>({remove:()=>children.splice(children.indexOf(el),1)}))};
 const document={hidden:false,getElementById:id=>id==='portfolio-chart'?container:id==='portfolio-chart-summary'?summary:id==='dashboard-section'?{hidden:false}:null,createElement:()=>({classList:{add(){}},setAttribute(){},textContent:''}),addEventListener(){}};
 const series={setData:data=>{applied=data;}};const chart={addLineSeries:()=>series,timeScale:()=>({fitContent:()=>fit++}),remove(){},resize(){}};
 const context={document,console,AbortController,Intl,Map,Date,Number,String,Array,Math,Error,window:{API_URL:'https://example.test',LightweightCharts:{createChart:()=>chart},addEventListener(){},removeEventListener(){},setTimeout:(fn,ms)=>{const item={fn,ms};timers.push(item);return item;},clearTimeout:item=>{if(item)item.canceled=true;},setInterval(){}}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'portfolio.js'),'utf8'),context);
 return {context,children,summary,timers,attributes,get applied(){return applied;},get fit(){return fit;}};
}
const response=(data,{ok=true,status=200,stale=false}={})=>({ok,status,json:async()=>data,headers:{get:()=>stale?'stale':'fresh'}});

test('initial broker failure retries instead of pretending there is no history',async()=>{
 const h=harness();let calls=0;h.context.fetch=async()=>++calls===1?response({detail:'Broker unavailable'},{ok:false,status:503}):response([{time:1793232000,value:94347.21}]);
 await h.context.loadPortfolioChart();assert.match(h.children[0].textContent,/Broker unavailable.*Retrying/);assert.ok(!h.children[0].textContent.includes('No portfolio history'));
 const retry=h.timers.find(t=>t.ms===15000&&!t.canceled);assert.ok(retry);await retry.fn();
 assert.equal(h.applied.length,1);assert.match(h.summary.textContent,/94,347.21/);assert.equal(h.children.length,0);assert.equal(h.attributes['aria-label'],'Portfolio value over time');
});

test('a failed refresh preserves the displayed series and does not reset zoom',async()=>{
 const h=harness();h.context.fetch=async()=>response([{time:1793232000,value:94347}]);await h.context.loadPortfolioChart();
 h.context.fetch=async()=>{throw Error('network failure');};await h.context.loadPortfolioChart();
 assert.equal(h.applied[0].value,94347);assert.match(h.children[0].textContent,/last successful history/);assert.equal(h.fit,1);
 h.context.fetch=async()=>response([{time:1793232000,value:94347}],{stale:true});await h.context.loadPortfolioChart();assert.match(h.summary.textContent,/Saved history/);assert.equal(h.fit,1);
});

test('superseded responses cannot overwrite a newer graph or its loading state',async()=>{
 const h=harness();let resolveFirst;h.context.fetch=()=>new Promise(r=>resolveFirst=r);const first=h.context.loadPortfolioChart();
 h.context.fetch=async()=>response([{time:200,value:200}]);await h.context.loadPortfolioChart();resolveFirst(response([{time:100,value:100}]));await first;
 assert.equal(h.applied[0].value,200);
});

test('normalization excludes null balances and invalid dates, sorts and deduplicates',()=>{
 const h=harness();const data=h.context.normalizePortfolioHistory([{time:20,value:2},{time:10,value:1},{time:20,value:3},{time:30,value:null},{time:0,value:20},{time:40,value:''},{time:50,value:false}]);
 assert.equal(JSON.stringify(data),JSON.stringify([{time:10,value:1},{time:20,value:3}]));
});
