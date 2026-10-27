const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function setup(failure={status:503}){
 const nodes=new Map(),calls=[];let sequence=0;
 const element=()=>({value:'',textContent:'',disabled:false,style:{},handlers:{},
  addEventListener(name,fn){this.handlers[name]=fn;},append(){},appendChild(){},setAttribute(){},replaceChildren(){},focus(){}});
 const get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
 vm.runInNewContext(fs.readFileSync(__dirname+'/pro-ticker-signals.js','utf8'),{
  document:{getElementById:get,createElement:element,createTextNode:text=>({textContent:text})},window:{},
  crypto:{randomUUID:()=>String(++sequence)},
  fetch:async(url,options)=>{calls.push({url,...JSON.parse(options.body)});return {ok:false,status:failure.status,json:async()=>({detail:failure.detail})};}});
 return {get,calls,submit:()=>get('pt-signal-form').handlers.submit({preventDefault(){}})};
}
test('unchanged failed research retries retain their request identity',async()=>{
 const ui=setup();ui.get('pt-signal-symbol').value='aapl';await ui.submit();await ui.submit();
 assert.equal(ui.calls.length,2);assert.equal(ui.calls[0].symbol,'AAPL');
 assert.equal(ui.calls[0].request_id,ui.calls[1].request_id);
});
test('programmatic scanner handoff gets a new identity without an input event',async()=>{
 const ui=setup();ui.get('pt-signal-symbol').value='AAPL';await ui.submit();
 ui.get('pt-signal-symbol').value='CLRO';await ui.submit();await ui.submit();
 assert.notEqual(ui.calls[0].request_id,ui.calls[1].request_id);
 assert.equal(ui.calls[1].request_id,ui.calls[2].request_id);
 assert.equal(ui.calls[1].symbol,'CLRO');
});

test('409 exposes the exact server blocker and preserves request identity',async()=>{
 const ui=setup({status:409,detail:'A new submission is required; saved reviews never create delayed buys.'});
 ui.get('pt-signal-symbol').value='BRAI';await ui.submit();
 assert.match(ui.get('pt-signal-status').textContent,/A new submission is required/);
 assert.doesNotMatch(ui.get('pt-signal-status').textContent,/Review request failed/);
 assert.doesNotMatch(ui.get('pt-signal-status').textContent,/Retrying unchanged input/);
 await ui.submit();assert.equal(ui.calls[0].request_id,ui.calls[1].request_id);
});
