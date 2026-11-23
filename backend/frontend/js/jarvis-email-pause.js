(() => {
  const token = location.hash.slice(1);
  history.replaceState(null, '', location.pathname);
  const message = document.getElementById('message');
  const button = document.getElementById('apply');
  let busy = false;
  async function apply() {
    if (busy) return;
    busy = button.disabled = true;
    try {
      if (!/^[A-Za-z0-9_-]{43}$/.test(token)) throw new Error('Pause link missing or invalid. Open app settings.');
      const response = await fetch('/jarvis-test-bot/email-pause', {
        method: 'POST', credentials: 'omit', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({token}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not apply pause.');
      message.textContent = data.message + (data.pause_until ? ' Timer ends ' + new Date(data.pause_until * 1000).toLocaleString() + '. A catch-up pause or the overall stop switch can keep buys off longer.' : '');
      button.hidden = true;
    } catch (error) {
      message.textContent = error.message;
      busy = button.disabled = false;
    }
  }
  button.onclick = apply;
  if (document.body.dataset.auto === 'yes') apply();
})();
