(() => {
  const $ = id => document.getElementById(id);
  const tabs = [...document.querySelectorAll('nav a')];
  const currency = new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD'});
  const date = new Intl.DateTimeFormat('en-US', {timeZone: 'America/New_York', dateStyle: 'medium', timeStyle: 'short'});
  const money = value => value == null ? 'Unavailable' : currency.format(value);
  const sign = value => value > 0 ? 'positive' : value < 0 ? 'negative' : '';
  const result = value => value > 0 ? 'win' : value < 0 ? 'loss' : 'flat';
  const displayTime = value => date.format(new Date(value * 1000));
  let state, active, cursor, historyLoaded = false, statusBusy = false, historyBusy = false, runBusy = false;

  function el(tag, text = '', className = '') {
    const node = document.createElement(tag);
    node.textContent = text;
    node.className = className;
    return node;
  }

  async function call(path, options) {
    const response = await fetch('/jarvis-test-bot/' + path, {cache: 'no-store', ...options});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Request failed.');
    return data;
  }

  function portfolioCard(label, value, note, style = '') {
    const card = el('article', '', 'panel');
    card.append(el('p', label), el('div', value, 'meme-stat ' + style), el('small', note));
    return card;
  }

  function botState(bot) {
    if (bot.id === 'jarvis' && !state.strategy) return bot.open ? 'Waiting for strategy · exits monitored' : 'Waiting for strategy';
    if (!state.enabled) return bot.open ? 'Paused · exits monitored' : 'Paused';
    if (!state.last_cycle || Date.now() / 1000 - state.last_cycle > 30) return 'Waiting for scan';
    if (bot.unpriced) return 'Holding valuation incomplete';
    return 'Running';
  }

  function render() {
    if (!state) return;
    const selected = state.bots.find(bot => bot.id === active);
    const jarvis = active === 'jarvis';
    $('jarvis-bot').hidden = !selected;
    $('jarvis-health').hidden = active !== 'health';
    $('jarvis-history').hidden = active !== 'history' && !jarvis;
    $('jarvis-note').textContent = state.error || '';
    if (selected) {
      $('jarvis-title').textContent = selected.name;
      $('jarvis-description').textContent = selected.description;
      $('jarvis-run').textContent = state.enabled ? 'Pause paper entries' : 'Start paper tests';
      $('jarvis-run').disabled = runBusy;
      $('jarvis-portfolio').replaceChildren(
        portfolioCard('Total balance', money(selected.balance), selected.unpriced ? `${selected.unpriced} holding quote(s) missing` : 'Cash + current sell quotes'),
        portfolioCard('Available cash', money(selected.cash), jarvis ? '$20,000 starting budget' : 'Automatic virtual top-ups'),
        portfolioCard('Total P/L', money(selected.pnl), `Net of ${money(selected.funding)} virtual funding`, sign(selected.pnl)),
        portfolioCard('Open holdings', String(selected.open), botState(selected)),
      );
      $('jarvis-today').textContent = money(selected.today.realized);
      $('jarvis-today').className = 'meme-stat ' + sign(selected.today.realized);
      $('jarvis-today-details').textContent = `${selected.today.closed} closed · ${selected.today.wins} wins · ${selected.today.losses} losses · ${state.day} ET`;
      $('jarvis-strategy').hidden = !jarvis;
      if (jarvis) {
        const completed = state.bots.filter(bot => bot.id !== 'jarvis').reduce((sum, bot) => sum + bot.closed, 0);
        const next = state.reviewed + state.review_every;
        const strategy = state.strategy;
        const summary = strategy ? `Version ${strategy.version} · minimum liquidity ${money(strategy.min_liquidity)} · minimum pool age ${strategy.min_age} min.` : 'Waiting for a validated strategy.';
        $('jarvis-review').textContent = `${summary} ${completed}/${next} completed baseline trades toward the next review. ${state.review?.result || ''}`;
      }
      const holdings = selected.holdings.map(holding => {
        const row = el('div', '', 'meme-row');
        row.append(el('strong', holding.symbol), el('p', `${money(holding.mark)} · Opened ${displayTime(holding.opened)} ET`));
        return row;
      });
      $('jarvis-holdings').replaceChildren(...(holdings.length ? holdings : [el('p', 'No open holdings.', 'meme-muted')]));
    }
    if (active === 'health') {
      $('jarvis-cycle').textContent = state.last_cycle ? `Last cycle ${displayTime(state.last_cycle)} ET · ${state.watching} coins on watch` : 'Waiting for the first cycle.';
      $('jarvis-health-bots').replaceChildren(...state.bots.map(bot => {
        const card = portfolioCard(bot.name, money(bot.pnl), botState(bot), sign(bot.pnl));
        card.append(el('p', `${bot.wins} wins · ${bot.losses} losses`), el('small', `${money(bot.realized)} realized · ${bot.quote_misses} exit quote misses`));
        return card;
      }));
    }
    if (active === 'history' || jarvis) {
      const group = state.bots.filter(bot => (bot.id === 'jarvis') === jarvis);
      const sum = key => group.reduce((n, bot) => n + bot[key], 0);
      $('jarvis-history-title').textContent = jarvis ? 'Jarvis Trader history' : 'Completed trades · four fixed bots';
      $('jarvis-history-summary').replaceChildren(
        el('span', `${sum('closed')} completed`), el('span', `${sum('wins')} wins`), el('span', `${sum('losses')} losses`),
        el('span', `${money(sum('realized'))} realized`, sign(sum('realized'))),
      );
    }
  }

  function coinCard(coin) {
    const card = el('article', '', 'panel jarvis-coin');
    const header = el('header');
    const copy = el('button', 'Copy address');
    copy.type = 'button';
    copy.setAttribute('aria-label', `Copy address for ${coin.symbol}`);
    copy.onclick = async () => {
      try {
        await navigator.clipboard.writeText(coin.mint);
        copy.textContent = 'Copied';
      } catch {
        $('jarvis-note').textContent = coin.mint;
      }
    };
    header.append(el('strong', coin.symbol), copy);
    card.append(header);
    for (const trade of coin.trades) {
      const earned = result(trade.pnl);
      const row = el('div', '', 'meme-row history-card ' + earned);
      const title = el('div', '', 'meme-title-row');
      title.append(el('strong', trade.bot), el('span', earned.toUpperCase(), 'result-badge'));
      const values = el('div', '', 'history-values');
      for (const [label, amount] of [['Entry cost', trade.cost], ['Proceeds', trade.proceeds], ['Net P/L', trade.pnl]]) {
        const cell = el('div');
        cell.append(el('small', label), el('strong', money(amount), label === 'Net P/L' ? 'result-pnl' : ''));
        values.append(cell);
      }
      row.append(title, el('p', trade.reason, 'history-reason'), values, el('small', displayTime(trade.closed) + ' ET'));
      card.append(row);
    }
    return card;
  }

  async function loadHistory(append = false) {
    if (historyBusy) return;
    const requested = active;
    if (!['history', 'jarvis'].includes(requested)) return;
    const more = $('jarvis-more');
    historyBusy = true;
    more.disabled = true;
    try {
      const data = await call(`history?jarvis_only=${requested === 'jarvis'}${append ? '&before=' + cursor : ''}`);
      if (active !== requested) return;
      const rows = $('jarvis-history-rows');
      if (!append) rows.replaceChildren();
      if (!append && !data.coins.length) rows.append(el('p', 'No completed positions yet.', 'meme-muted'));
      else rows.append(...data.coins.map(coinCard));
      cursor = data.next;
      more.hidden = cursor == null;
      historyLoaded = true;
    } catch (error) {
      if (active === requested) $('jarvis-note').textContent = error.message;
    } finally {
      historyBusy = false;
      more.disabled = false;
      if (active !== requested && !historyLoaded) loadHistory();
    }
  }

  function selectTab() {
    const hash = location.hash.slice(1);
    active = tabs.some(tab => tab.hash === '#' + hash) ? hash : 'instant-step';
    for (const tab of tabs) {
      if (tab.hash === '#' + active) tab.setAttribute('aria-current', 'page');
      else tab.removeAttribute('aria-current');
    }
    historyLoaded = false;
    cursor = null;
    $('jarvis-more').hidden = true;
    $('jarvis-history-rows').replaceChildren();
    render();
    loadHistory();
  }

  async function refresh() {
    if (statusBusy || runBusy) return;
    statusBusy = true;
    try {
      const next = await call('status');
      if (runBusy) return;
      const historyChanged = state && next.bots.some((bot, index) => bot.closed !== state.bots[index].closed);
      state = next;
      render();
      if (historyChanged || !historyLoaded) loadHistory();
    } catch (error) { $('jarvis-note').textContent = error.message; }
    finally { statusBusy = false; }
  }

  $('jarvis-run').onclick = async () => {
    if (!state || runBusy) return;
    const button = $('jarvis-run');
    runBusy = button.disabled = true;
    try {
      state = await call('run', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({enabled: !state.enabled})});
      render();
    } catch (error) { $('jarvis-note').textContent = error.message; }
    finally { runBusy = button.disabled = false; }
  };
  $('jarvis-more').onclick = () => loadHistory(true);
  window.addEventListener('hashchange', selectTab);
  selectTab();
  refresh();
  setInterval(refresh, 10000);
})();
