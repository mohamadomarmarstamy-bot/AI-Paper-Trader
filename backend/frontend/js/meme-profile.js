(() => {
  const $ = id => document.getElementById(id);
  let revision = null, busy = false, dirty = false;
  let phantom = null, restoring = false;
  const fields = ['profile-name', 'profile-wallet', 'profile-key', 'profile-remove', 'profile-solscan-key', 'profile-solscan-remove', 'profile-save'];
  function controls() {
    for (const id of fields) $(id).disabled = busy || revision === null;
    $('profile-key').disabled ||= $('profile-remove').checked;
    $('profile-solscan-key').disabled ||= $('profile-solscan-remove').checked;
    $('profile-reload').disabled = busy;
    $('profile-phantom-connect').disabled = busy || restoring || revision === null;
    $('profile-phantom-disconnect').disabled = busy || !phantom?.isConnected;
  }
  function render(data) {
    if (!Number.isInteger(data.revision) || typeof data.name !== 'string' || typeof data.wallet_address !== 'string' || typeof data.api_key_configured !== 'boolean') {
      throw Error('Profile response unavailable. Reload before saving.');
    }
    revision = data.revision;
    $('profile-name').value = data.name;
    $('profile-wallet').value = data.wallet_address;
    $('profile-key').value = '';
    $('profile-remove').checked = false;
    $('profile-solscan-key').value = '';
    $('profile-solscan-remove').checked = false;
    $('profile-solscan-connection').textContent = data.solscan_error || (data.solscan_api_key_configured ? 'Solscan key saved · provider access checked when copying starts.' : 'Solscan not connected. Birdeye swap history is used without this optional key.');
    $('profile-connection').textContent = data.error || (data.api_key_configured
      ? `API key saved${data.api_key_source === 'server' ? ' in server settings' : ''} · provider access has not been verified here.`
      : 'No API key connected. Add a key below to set up Copy traders.');
    dirty = false;
  }
  async function load() {
    if (busy) return;
    busy = true; controls();
    $('profile-key').value = '';
    $('profile-solscan-key').value = '';
    try {
      const response = await fetch('/meme-coins/profile/status', {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw Error(`Profile unavailable (${response.status}).`);
      render(await response.json());
      $('profile-status').textContent = 'Profile loaded.';
    } catch (error) {
      revision = null;
      $('profile-status').textContent = error.message;
    } finally { busy = false; controls(); void restorePhantom(); }
  }
  $('profile-form').addEventListener('input', () => { dirty = true; });
  function connectedWallet(publicKey) {
    const wallet=publicKey?.toString();
    if(typeof wallet!=='string'||!/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(wallet))throw Error('Invalid wallet address.');
    $('profile-wallet').value=wallet;dirty=true;
    $('profile-phantom-status').textContent='Phantom connected · public address only. Save profile to remember this wallet. PAPER copying uses separate read-only chain data.';
  }
  function bindPhantom(provider) {
    if(phantom===provider)return;phantom=provider;
    provider.on('accountChanged',key=>{if(key&&!restoring){try{connectedWallet(key);}catch{$('profile-phantom-status').textContent='Wallet address unavailable.';}}else if(!key){$('profile-phantom-status').textContent='Phantom disconnected. Your saved profile has not changed.';}controls();});
    provider.on('disconnect',()=>{$('profile-phantom-status').textContent='Phantom disconnected. Your saved profile has not changed.';controls();});
  }
  async function restorePhantom() {
    const provider=window.PhantomSession?.provider();if(!provider||restoring||busy)return;
    bindPhantom(provider);restoring=true;controls();
    try{const publicKey=await window.PhantomSession.reconnect();if(publicKey){
      const matches=publicKey.toString()===$('profile-wallet').value;
      $('profile-phantom-status').textContent=matches?'Phantom connected · saved public wallet restored.':'Phantom connected · selected wallet differs from this profile. Connect explicitly to update the field.';
    }}finally{restoring=false;controls();}
  }
  $('profile-phantom-connect').onclick=async()=>{
    if(busy||restoring||revision===null)return;
    const provider=window.PhantomSession?.provider();
    if(!provider){$('profile-phantom-status').textContent='Phantom was not detected. Use its browser extension on desktop, or open this app inside Phantom’s mobile browser.';return;}
    busy=true;controls();
    try {
      bindPhantom(provider);
      const result=await window.PhantomSession.connect();connectedWallet(result.publicKey);
    }catch(error){$('profile-phantom-status').textContent=error.code===4001?'Connection declined. Profile unchanged.':'Phantom connection failed. Unlock Phantom and try again.';}
    finally{busy=false;controls();}
  };
  $('profile-phantom-disconnect').onclick=async()=>{
    if(busy||!phantom)return;
    busy=true;controls();
    try{await window.PhantomSession.disconnect();$('profile-phantom-status').textContent='Phantom disconnected. Your saved public address stays in your profile.';}
    catch{$('profile-phantom-status').textContent='Could not disconnect Phantom. Disconnect this app in Phantom settings.';}
    finally{busy=false;controls();}
  };
  $('profile-remove').onchange = () => {
    if ($('profile-remove').checked) $('profile-key').value = '';
    controls();
  };
  $('profile-solscan-remove').onchange = () => {
    if ($('profile-solscan-remove').checked) $('profile-solscan-key').value = '';
    controls();
  };
  $('profile-reload').onclick = () => {
    if (!dirty || confirm('Discard unsaved profile changes and reload?')) load();
  };
  $('profile-form').onsubmit = async event => {
    event.preventDefault();
    if (busy || revision === null) return;
    busy = true; controls();
    const payload = {
      name: $('profile-name').value,
      wallet_address: $('profile-wallet').value,
      api_key: $('profile-key').value,
      remove_api_key: $('profile-remove').checked,
      solscan_api_key: $('profile-solscan-key').value,
      remove_solscan_api_key: $('profile-solscan-remove').checked,
      expected_revision: revision
    };
    // Never retain credentials in web storage or repopulate them after a request.
    $('profile-key').value = '';
    $('profile-solscan-key').value = '';
    $('profile-status').textContent = 'Saving profile…';
    try {
      const response = await fetch('/meme-coins/profile', {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) {
        if (response.status === 409) revision = null;
        throw Error(typeof data.detail === 'string' ? data.detail : `Save failed (${response.status}).`);
      }
      render(data);
      $('profile-status').textContent = 'Profile saved. You can return to Copy traders.';
    } catch (error) {
      $('profile-status').textContent = `${error.message} If replacing the key, enter it again before retrying.`;
    } finally { payload.api_key = ''; payload.solscan_api_key = ''; busy = false; controls(); }
  };
  window.addEventListener('pagehide', () => { $('profile-key').value = ''; $('profile-solscan-key').value = ''; });
  window.addEventListener('load',()=>void restorePhantom());
  load();
})();
