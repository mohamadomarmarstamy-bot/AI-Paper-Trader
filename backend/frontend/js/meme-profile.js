(() => {
  const $ = id => document.getElementById(id);
  let revision = null, busy = false, dirty = false;
  const fields = ['profile-name', 'profile-wallet', 'profile-key', 'profile-remove', 'profile-solscan-key', 'profile-solscan-remove', 'profile-save'];
  function controls() {
    for (const id of fields) $(id).disabled = busy || revision === null;
    $('profile-key').disabled ||= $('profile-remove').checked;
    $('profile-solscan-key').disabled ||= $('profile-solscan-remove').checked;
    $('profile-reload').disabled = busy;
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
    } finally { busy = false; controls(); }
  }
  $('profile-form').addEventListener('input', () => { dirty = true; });
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
  load();
})();
