const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
const {chromium}=require(process.env.PAPER_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..');
const sample={
  enabled:true,cloud_configured:true,monthly_budget_usd:25,reserved_this_month_usd:2.50,
  paper_repair:{enabled:true,runner_recent:false,runner_seen:1795659976,runs:[]},
  last_call:{at:1795654329,status:'no_research_sources'},
  improvement_backlog:{tasks:[
    {kind:'chart_data_gap',status:'investigating',goal:'Improve fresh chart coverage.',updated:1795654329},
    {kind:'worker_stalled',status:'observed_recovery',goal:'Restore the worker heartbeat.',updated:1795654329,recurrences:1}
  ]},
  incidents:[
    {kind:'chart_data_gap',status:'needs_attention',opened:1795654329,last_error:'no_research_sources',
     plan:{summary:'Long historical assessment',hypothesis:'Possible chart timing issue',checks:['Check timing'],
           applied_action:'monitor',sources:[{url:'https://docs.coingecko.com',title:'Chart docs'},{url:'javascript:alert(1)',title:'Unsafe link'}]},
     evidence:{latest:{metrics:{chart_coverage:{reasons:{complete:3},eligible:39},queue:{oldest_wait:0}}}}},
    {kind:'engine_error',status:'resolved',opened:1795654129,resolved:1795654329}
  ],
  proposals:[{id:'abc',status:'waiting_slot',created:1795654329,data:{rule:{name:'Paper trial',hypothesis:'Paper only',entry:'A',exit:'B'}},
              report:{admission_check:{reason:'no_safe_rotation'}}}]
};
test('investigations summarize active work and preserve disclosures at phone and desktop widths',async t=>{
  const browser=await chromium.launch({headless:true,...(process.env.PAPER_CHROMIUM?{executablePath:process.env.PAPER_CHROMIUM}:{}),args:['--no-sandbox']});
  t.after(()=>browser.close());
  for(const width of [360,1100]){
    const page=await browser.newPage({viewport:{width,height:850}});
    const errors=[];page.on('pageerror',e=>errors.push(e));
    let fail=false;
    await page.route('https://jarvis.test/**',route=>{
      const p=new URL(route.request().url()).pathname;
      if(p==='/jarvis-test-bot/brain/status')return route.fulfill(fail?{status:401,body:'{}'}:{json:sample});
      if(p==='/meme-coins.css')return route.fulfill({status:200,contentType:'text/css',body:fs.readFileSync(path.join(root,'meme-coins.css'),'utf8')});
      return route.fulfill({status:200,contentType:'text/html',body:fs.readFileSync(path.join(root,'jarvis-brain.html'),'utf8')});
    });
    await page.goto('https://jarvis.test/jarvis-test-bot/brain');
    await page.getByText('3 of 39 charts complete').waitFor();
    assert.equal(await page.locator('#state').textContent(),'Monitoring');
    assert.match(await page.locator('#runner').textContent(),/overdue/i);
    assert.match(await page.locator('#call').textContent(),/No usable sources returned/);
    assert.equal(await page.locator('#budget').textContent(),'$2.50 / $25.00');
    assert.equal(await page.locator('#incidents article').count(),1);
    assert.equal(await page.locator('#improvement-tasks article').count(),1);
    assert.equal(await page.getByText('Long historical assessment').isVisible(),false);
    assert.equal(await page.locator('#incident-history article').first().isVisible(),false);
    assert.equal(await page.locator('#improvement-history article').first().isVisible(),false);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.getByText('Investigation details').click();
    await page.getByText('Long historical assessment').waitFor({state:'visible'});
    assert.equal(await page.getByRole('link',{name:'Chart docs'}).count(),1);
    assert.equal(await page.getByRole('link',{name:'Unsafe link'}).count(),0);
    await page.getByText('Resolved incidents (1)').click();
    await page.locator('#incident-history article').first().waitFor({state:'visible'});
    fail=true;
    await page.getByRole('button',{name:'Refresh'}).click();
    await page.getByText('Couldn’t load the latest status.',{exact:false}).waitFor();
    assert.equal(await page.getByText('3 of 39 charts complete').count(),1);
    assert.deepEqual(errors,[]);
    if(process.env.JARVIS_SCREENSHOT)await page.screenshot({path:process.env.JARVIS_SCREENSHOT+'-'+width+'.png',fullPage:true});
    await page.close();
  }
});
