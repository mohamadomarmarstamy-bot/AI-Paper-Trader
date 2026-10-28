const {test} = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

test('Profile desktop/mobile save, reload, write-only key, removal and stale-tab handling', {timeout:60000}, async () => {
  const root = path.resolve(__dirname, '..');
  let saved, key, solscanKey, reject, browser;
  const requests = [];
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://local');
    if (url.pathname === '/meme-coins/profile/status') {
      res.setHeader('Content-Type', 'application/json');
      return res.end(JSON.stringify(saved));
    }
    if (url.pathname === '/meme-coins/profile' && req.method === 'POST') {
      let raw = ''; for await (const chunk of req) raw += chunk;
      const body = JSON.parse(raw); requests.push(body);
      res.setHeader('Content-Type', 'application/json');
      if (reject || body.expected_revision !== saved.revision) {
        res.statusCode = 409;
        return res.end(JSON.stringify({detail:'Profile changed in another tab. Reload before saving.'}));
      }
      if (body.remove_solscan_api_key) solscanKey = '';
      else if (body.solscan_api_key) solscanKey = body.solscan_api_key;
      if (body.remove_api_key) key = '';
      else if (body.api_key) key = body.api_key;
      saved = {name:body.name, wallet_address:body.wallet_address, revision:saved.revision+1,
        solscan_api_key_configured:Boolean(solscanKey), api_key_configured:Boolean(key), api_key_source:key?'profile':null, error:null};
      return res.end(JSON.stringify(saved));
    }
    const file = {'/meme-coins/profile':'meme-profile.html', '/js/meme-profile.js':'js/meme-profile.js'}[url.pathname];
    if (file) {
      res.setHeader('Content-Type', file.endsWith('.js')?'application/javascript':'text/html');
      return res.end(fs.readFileSync(path.join(root, file)));
    }
    res.statusCode = 404; res.end();
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    browser = await chromium.launch({headless:true, args:['--no-sandbox']});
    for (const width of [1440,390]) {
      saved = {name:'',wallet_address:'',revision:0,api_key_configured:false,api_key_source:null,error:null};
      key = ''; solscanKey = ''; reject = false;
      const page = await browser.newPage({viewport:{width,height:1000}}), errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(`http://127.0.0.1:${server.address().port}/meme-coins/profile`);
      await page.getByText('Profile loaded.', {exact:true}).waitFor();
      await page.getByLabel('Name', {exact:true}).fill('Omar');
      await page.getByLabel('Wallet address · Solana', {exact:true}).fill('1'.repeat(31)+'2');
      await page.getByLabel('Birdeye API key', {exact:true}).fill('synthetic-browser-key');
      assert.equal(await page.locator('#profile-key').getAttribute('type'), 'password');
      await page.getByRole('button', {name:'Save profile',exact:true}).click();
      await page.getByText('Profile saved. You can return to Copy traders.', {exact:true}).waitFor();
      assert.equal(key, 'synthetic-browser-key');
      assert.equal(await page.locator('#profile-key').inputValue(), '');
      assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length), 0);
      assert.equal((await page.locator('body').innerText()).includes('synthetic-browser-key'), false);
      await page.reload(); await page.getByText('Profile loaded.', {exact:true}).waitFor();
      assert.equal(await page.locator('#profile-name').inputValue(), 'Omar');
      assert.equal(await page.locator('#profile-wallet').inputValue(), '1'.repeat(31)+'2');
      assert.equal(await page.locator('#profile-key').inputValue(), '');
      await page.locator('#profile-wallet').fill('1'.repeat(31)+'3');
      await page.getByRole('button', {name:'Save profile',exact:true}).click();
      await page.getByText('Profile saved. You can return to Copy traders.', {exact:true}).waitFor();
      assert.equal(key, 'synthetic-browser-key');
      await page.getByLabel('Solscan Pro API key',{exact:true}).fill('solscan-browser-test');
      await page.locator('#profile-key').fill('replacement-browser-key');
      await page.getByRole('button', {name:'Save profile',exact:true}).click();
      await page.getByText('Profile saved. You can return to Copy traders.', {exact:true}).waitFor();
      assert.equal(key, 'replacement-browser-key');
      assert.equal(solscanKey,'solscan-browser-test');
      assert.equal(await page.locator('#profile-solscan-key').inputValue(),'');
      const directory = path.resolve('.artifacts/meme-profile'); fs.mkdirSync(directory,{recursive:true});
      await page.screenshot({path:path.join(directory,`profile-${width}.png`),fullPage:true});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await page.getByLabel('Remove the Solscan API key',{exact:true}).check();
      await page.getByLabel('Remove the current API key', {exact:true}).check();
      assert.equal(await page.locator('#profile-key').isDisabled(),true);
      await page.getByRole('button', {name:'Save profile',exact:true}).click();
      await page.getByText('Profile saved. You can return to Copy traders.', {exact:true}).waitFor();
      assert.equal(key,'');assert.equal(solscanKey,'');
      await page.locator('#profile-key').fill('stale-browser-key'); reject = true;
      await page.getByRole('button', {name:'Save profile',exact:true}).click();
      await page.waitForFunction(()=>document.getElementById('profile-status').textContent.includes('another tab'));
      assert.equal(await page.locator('#profile-save').isDisabled(),true);
      assert.equal(await page.locator('#profile-key').inputValue(),'');
      reject = false; page.once('dialog',dialog=>dialog.accept());
      await page.getByRole('button',{name:'Reload saved profile',exact:true}).click();
      await page.getByText('Profile loaded.',{exact:true}).waitFor();
      assert.equal(await page.locator('#profile-save').isEnabled(),true);
      assert.deepEqual(errors,[]);
      await page.close();
    }
    assert.equal(requests.filter(r=>r.api_key==='synthetic-browser-key').length,2);
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve=>server.close(resolve));
  }
});
