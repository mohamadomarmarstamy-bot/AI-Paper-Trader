const {test} = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');

test('Strategy Lab is compact on desktop/mobile, expands details, scopes history and preserves rows on refresh', {timeout:60000}, async () => {
  const backend = path.resolve(__dirname, '../..');
  const root = path.resolve(__dirname, '..');
  const initial = JSON.parse(execFileSync('python', ['-c', `
import tempfile,json
from pathlib import Path
import database,jarvis_test_bot as jarvis
with tempfile.TemporaryDirectory() as temp:
 database.DATABASE_PATH=Path(temp)/'test.db'
 print(json.dumps(jarvis.status()))
`], {cwd:backend, encoding:'utf8', env:{...process.env, PYTHONPATH:backend}}));
  let state = structuredClone(initial);
  const requests = [];
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://local');
    const send = data => {res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));};
    requests.push({path:url.pathname, bot:url.searchParams.get('bot'), method:req.method});
    if (url.pathname === '/jarvis-test-bot/status') return send(state);
    if (url.pathname === '/jarvis-test-bot/history') return send({coins:[],next:null});
    if (url.pathname === '/jarvis-test-bot/run') {
      let body=''; for await (const chunk of req) body += chunk;
      state.enabled = JSON.parse(body).enabled;
      return send(state);
    }
    const filename = url.pathname === '/jarvis-test-bot' ? 'jarvis-test-bot.html' : url.pathname.slice(1);
    const target = path.resolve(root, filename);
    if (!target.startsWith(root + path.sep) || !fs.existsSync(target)) {res.statusCode=404;return res.end();}
    res.setHeader('Content-Type',filename.endsWith('.js')?'application/javascript':filename.endsWith('.css')?'text/css':'text/html');
    res.end(fs.readFileSync(target));
  });
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  let browser;
  try {
    browser = await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined), args:['--no-sandbox']});
    for (const viewport of [{width:1440,height:1000},{width:390,height:844}]) {
      state = structuredClone(initial);
      const page = await browser.newPage({viewport});
      const errors=[]; page.on('pageerror',error=>errors.push(error.message));
      await page.clock.install();
      await page.goto(`http://127.0.0.1:${server.address().port}/jarvis-test-bot#lab`);
      await page.locator('#jarvis-lab-rows details').last().waitFor();
      assert.equal(await page.locator('#jarvis-lab-rows details').count(),9);
      assert.equal(await page.locator('nav a[href="#lab"]').count(),1);
      assert.equal(await page.locator('nav a').count(),8);
      assert.equal(await page.locator('#jarvis-lab-rows details[open]').count(),0);
      const first=page.locator('details[data-bot="lab-pressure"]');
      await first.locator('summary').click();
      await first.getByRole('button',{name:'View history',exact:true}).click();
      await first.getByText('No completed positions yet.',{exact:true}).waitFor();
      assert(requests.some(request=>request.bot==='lab-pressure'));
      const target=page.locator('details[data-bot="lab-tp5-sl10"]');
      await target.locator('summary').click();
      await target.getByText(/Fees reduce net P\/L/).waitFor();
      await page.clock.fastForward(11000);
      await page.waitForFunction(()=>document.querySelector('details[data-bot="lab-pressure"]').open);
      assert.equal(await page.locator('#jarvis-lab-rows details[open]').count(),2);
      await page.getByRole('button',{name:'Start all paper tests',exact:true}).click();
      await page.getByRole('button',{name:'Pause all paper entries',exact:true}).waitFor();
      assert.equal(state.enabled,true);
      await page.getByRole('button',{name:'Pause all paper entries',exact:true}).click();
      await page.getByRole('button',{name:'Start all paper tests',exact:true}).waitFor();
      assert.equal(state.enabled,false);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth + 1));
      assert.deepEqual(errors,[]);
      await page.close();
    }
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve=>server.close(resolve));
  }
});
