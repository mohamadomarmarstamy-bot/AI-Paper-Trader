const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/pro-ticker-workspace.js','utf8');
function setup(fetch){
    let click;
    const button={disabled:false,addEventListener(event,fn){assert.equal(event,'click');click=fn;}};
    const status={textContent:''};
    vm.runInNewContext(source,{document:{getElementById:id=>id==='pt-account-check'?button:id==='pt-account-status'?status:null},fetch,Date});
    return {button,status,click};
}
test('account verification reads only on explicit click and shows verified identity',async()=>{
    let calls=0;
    const view=setup(async(url,options)=>{
        calls++;assert.equal(url,'/auto-trader/pro-ticker/account-status');assert.equal(options.method,undefined);
        assert.equal(options.cache,'no-store');assert.equal(options.credentials,'same-origin');
        return {ok:true,json:async()=>({paper:true,account_verified:true,order_execution_authorized:false,account_active:true,currency_usd:true,trading_unblocked:true})};
    });
    assert.equal(calls,0);await view.click();assert.equal(calls,1);
    assert.match(view.status.textContent,/identity verified/);assert.match(view.status.textContent,/active, USD, and unblocked/);
    assert.equal(view.button.disabled,false);
});
test('failed connection clears previous verification and restores button',async()=>{
    const view=setup(async()=>({ok:false,status:503}));
    view.status.textContent='Previously verified';await view.click();
    assert.match(view.status.textContent,/could not be verified/);assert.doesNotMatch(view.status.textContent,/Previously/);
    assert.equal(view.button.disabled,false);
});
test('unexpected evidence cannot appear verified',async()=>{
    const view=setup(async()=>({ok:true,json:async()=>({paper:false,account_verified:true})}));
    await view.click();assert.match(view.status.textContent,/Unexpected verification/);
});
