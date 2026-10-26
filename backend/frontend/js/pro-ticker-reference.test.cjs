const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function evidence(age=1){return {paper:true,account_verified:true,reference_only:true,order_execution_authorized:false,symbol:'OSUR',
    allocation_max_age_seconds:90,admission_max_age_seconds:30,feed_selection:'provider_default',allocation_reference_fresh:age<=90,admission_reference_fresh:age<=30,
    reference:{source:'alpaca_latest_trade',status:'captured',price:4.115,market_timestamp:'2026-10-23T19:22:56Z',age_seconds:age,fresh:age<=90}};}
function setup(fetcher){
    const nodes=new Map();
    for(const id of ['pt-reference-check','pt-reference-status','pt-signal-symbol'])nodes.set(id,{value:'OSUR',textContent:'',disabled:false,handlers:{},addEventListener(name,fn){this.handlers[name]=fn;}});
    const calls=[];
    vm.runInNewContext(fs.readFileSync(__dirname+'/pro-ticker-signals.js','utf8'),{document:{getElementById:id=>nodes.get(id)},Date,
        fetch:async(url,options)=>{calls.push({url,options});return fetcher(url,options);}});
    return {get:id=>nodes.get(id),calls,click:()=>nodes.get('pt-reference-check').handlers.click()};
}
test('explicit read-only reference check shows both independent age limits',async()=>{
    const ui=setup(async()=>({ok:true,json:async()=>evidence(45)}));assert.equal(ui.calls.length,0);
    await ui.click();assert.equal(ui.calls.length,1);assert.equal(ui.calls[0].options.method,undefined);
    assert.equal(ui.calls[0].url,'/auto-trader/pro-ticker/market-reference?symbol=OSUR');
    assert.match(ui.get('pt-reference-status').textContent,/allocation: passes/);assert.match(ui.get('pt-reference-status').textContent,/submission: STALE/);
    assert.match(ui.get('pt-reference-status').textContent,/No review saved, funds reserved, or order submitted/);
});
test('failure replaces old success and reenables the control',async()=>{
    const ui=setup(async()=>({ok:false,status:503}));ui.get('pt-reference-status').textContent='Old success';await ui.click();
    assert.doesNotMatch(ui.get('pt-reference-status').textContent,/Old success/);assert.match(ui.get('pt-reference-status').textContent,/No reference verified/);
    assert.equal(ui.get('pt-reference-check').disabled,false);
});
test('stale data cannot claim submission freshness',async()=>{
    const data=evidence(200);data.admission_reference_fresh=true;
    const ui=setup(async()=>({ok:true,json:async()=>data}));await ui.click();
    assert.match(ui.get('pt-reference-status').textContent,/Invalid broker reference evidence/);
});
test('changed symbol cannot receive an old in-flight result',async()=>{
    let resolve;const ui=setup(()=>new Promise(r=>{resolve=r;}));const pending=ui.click();
    ui.get('pt-signal-symbol').value='AAPL';ui.get('pt-signal-symbol').handlers.input();
    resolve({ok:true,json:async()=>evidence()});await pending;
    assert.match(ui.get('pt-reference-status').textContent,/Symbol changed/);assert.doesNotMatch(ui.get('pt-reference-status').textContent,/OSUR latest/);
});
test('invalid symbols never reach a provider',async()=>{
    const ui=setup(async()=>{throw Error('must not fetch');});ui.get('pt-signal-symbol').value='bad/path';await ui.click();
    assert.equal(ui.calls.length,0);assert.match(ui.get('pt-reference-status').textContent,/valid stock symbol/);
});
