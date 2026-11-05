const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'coin-labels.js'),'utf8');
const mint='BDXUaN4Bx6yaCBKAmCyGfdXd3UkRobiQwbV8mB1Kpump';
const element=()=>({dataset:{},isConnected:true,setAttribute(k,v){this[k]=v;}});
function setup(fetch){const context={window:{},fetch,queueMicrotask,setTimeout,clearTimeout,AbortController,Date};vm.runInNewContext(source,context);return context.window.MemeCoinLabels;}
const settled=()=>new Promise(resolve=>setImmediate(resolve));
test('paper and real labels coalesce by mint and render names as text, retaining identity',async()=>{
 let count=0;const api=setup(async()=>{count++;return {ok:true,json:async()=>({labels:{[mint]:{name:'<b>Chihuahua</b>',symbol:'CHI'}}})};});
 const paper=element(),real=element();api.attach(paper,mint,'CHI');api.attach(real,mint);await settled();
 assert.equal(count,1);assert.equal(paper.textContent,'<b>Chihuahua</b> (CHI)');assert.equal(real.textContent,paper.textContent);assert.equal(real.title,mint);
 const later=element();api.attach(later,mint);await settled();assert.equal(count,1);assert.equal(later.textContent,paper.textContent);
});
test('failed metadata keeps a distinct fallback and never blocks rendering',async()=>{
 const api=setup(async()=>{throw Error('offline');}),el=element();api.attach(el,mint);await settled();assert.match(el.textContent,/Unknown coin/);assert.equal(el.title,mint);
});
test('late name responses cannot label a row reused for another mint',async()=>{
 let respond;const api=setup(()=>new Promise(resolve=>respond=resolve)),el=element();api.attach(el,mint,'CHI');await settled();
 el.dataset.coinMint='another-mint';el.textContent='Another coin';respond({ok:true,json:async()=>({labels:{[mint]:{name:'Chihuahua',symbol:'CHI'}}})});await settled();assert.equal(el.textContent,'Another coin');
});
