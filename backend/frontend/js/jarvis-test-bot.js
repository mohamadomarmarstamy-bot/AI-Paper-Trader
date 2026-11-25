(() => {
  const $ = id => document.getElementById(id);
  const tabs = [...document.querySelectorAll('nav a')];
  const currency = new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD'});
  const date = new Intl.DateTimeFormat('en-US', {timeZone: 'America/New_York', dateStyle: 'medium', timeStyle: 'short'});
  const money = value => value == null ? 'Unavailable' : currency.format(value);
  const sign = value => value > 0 ? 'positive' : value < 0 ? 'negative' : '';
  const result = value => value > 0 ? 'win' : value < 0 ? 'loss' : 'flat';
  const displayTime = value => date.format(new Date(value * 1000));

  function displayDuration(seconds) {
    if (seconds < 1) return '<1s';
    const units = [[86400, 'd'], [3600, 'h'], [60, 'm'], [1, 's']];
    const parts = [];
    for (const [size, label] of units) {
      const amount = Math.floor(seconds / size);
      if (amount) parts.push(amount + label);
      seconds %= size;
    }
    return parts.slice(0, 2).join(' ');
  }

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
    if (bot.research_slot && !state.local_lab?.strategies.some(rule => rule.slot === bot.id && ['testing', 'proven'].includes(rule.status))) return bot.open ? 'Retired strategy · exits monitored' : 'Waiting for local researcher';
    if (bot.id === 'jarvis' && !state.strategy) return bot.open ? 'Waiting for strategy · exits monitored' : 'Waiting for strategy';
    if (bot.id === 'jarvis' && state.entry_guard) return 'Entries paused · exits monitored';
    if (state.backlog_guard?.timer_active || state.backlog_guard?.until_all_closed || state.entry_catchup_pause?.active) return 'Buys paused · exits monitored';
    if (!state.enabled) return bot.open ? 'Paused · exits monitored' : 'Paused';
    if (state.preset_entries_paused && !bot.research_slot && bot.id !== 'jarvis') return 'Preset entries paused · exits monitored';
    if (!state.last_cycle || Date.now() / 1000 - state.last_cycle > 30) return 'Waiting for scan';
    if (bot.research_slot && state.local_lab?.entry_status?.[bot.id]) return state.local_lab.entry_status[bot.id].label;
    return 'Running';
  }

  function estimateNote(bot) {
    const counts = bot.valuation || {};
    return [
      counts.last_quote ? `${counts.last_quote} at last sell quote` : '',
      counts.at_cost ? `${counts.at_cost} at cost` : '',
    ].filter(Boolean).join(' · ');
  }

  function renderEarlyReview(review) {
    $('jarvis-early-summary').textContent =
      `${review.closed} closed trades across ${review.mints} coins · ${review.chart} with chart data · ${review.social} with social data. ${review.result}`;
    const rows = Object.values(review.bots).map(bot => {
      const row = el('div', '', 'meme-row');
      row.append(el('strong', bot.name),
        el('p', `${bot.closed} closed · ${money(bot.net)} realized · ${money(bot.per_trade)} per close`));
      return row;
    });
    const candidate = review.candidate;
    if (candidate) {
      rows.push(el('p', `Candidate: ${candidate.name} · ${candidate.entry_filter.replaceAll('_', ' ')} · ${money(candidate.min_liquidity)} minimum liquidity · ${candidate.min_age} min minimum age`));
      if (candidate.avoid_pattern) rows.push(el('small', `Proposed skip rule: ${candidate.avoid_pattern}`));
      rows.push(el('small', `${money(candidate.training)} per training opportunity · ${money(candidate.holdout)} per held-out opportunity, after removing the largest winner from each sample`));
    }
    $('jarvis-early-results').replaceChildren(...rows,
      el('small', 'The first strategy can activate after the 1,000-trade review.'));
  }

  function renderOutcomes(review, legacy, activeWarning) {
    $('jarvis-outcome-summary').textContent =
      `Recent baseline closes · ${review.wins} wins · ${review.losses} losses · ${review.flat} flat. ${activeWarning ? 'Active skip rule: ' + activeWarning : 'Waiting for a validated skip rule.'}`;
    const entries = review.warnings.filter(item => item.wins || item.losses).map(item =>
      el('p', `${item.label} · ${item.losses} losses · ${item.wins} wins`));
    for (const [label, trades] of Object.entries(review.examples)) {
      const group = el('div', '', 'meme-row');
      group.append(el('strong', label === 'winners' ? 'Recent winners' : 'Recent losers'));
      for (const item of trades) {
        const signs = [...item.positive, ...item.warnings];
        group.append(el('p', `${item.name} · ${money(item.pnl / 1000000)}${signs.length ? ' · ' + signs.join(', ') : ''}`));
      }
      entries.push(group);
    }
    if (legacy) {
      const sources = el('div', '', 'meme-row');
      sources.append(el('strong', 'Older Auto Trader · reference sample'),
        el('p', `${legacy.wins} wins · ${legacy.losses} losses · ${legacy.flat} flat`));
      for (const item of legacy.warnings) if (item.wins || item.losses)
        sources.append(el('p', `${item.label} · ${item.losses} losses · ${item.wins} wins`));
      entries.push(sources);
    }
    $('jarvis-outcomes').replaceChildren(...entries);
  }

  function renderComparison(study) {
    const leader = study.arms[study.policy.mode || 'instant'].name;
    $('jarvis-experiment-summary').textContent =
      `${study.completed} matched coins settled · ${study.active} in progress · Experiment leader: ${leader}. ${study.policy.result || ''}`;
    const notes = Object.entries(study.arms).map(([mode, arm]) => {
      const row = el('div', '', 'meme-row');
      row.append(el('strong', arm.name),
        el('p', `${money(arm.per_opportunity)} per settled opportunity · ${money(arm.drawdown)} settled drawdown`),
        el('p', `${arm.closed} closed · ${study.running[mode]} open · ${arm.skipped} skipped · ${arm.unpriced} unpriced at deadline`),
        el('small', `${arm.failed_entries} entry quote misses · ${arm.failed_exits} exit quote misses`));
      if (arm.matched_instant != null) row.append(el('small',
        `Instant on the same ${arm.opportunities} coins: ${money(arm.matched_instant)} per opportunity`));
      if (arm.avg_entry_wait != null) row.append(el('small',
        `Average entry delay ${displayDuration(arm.avg_entry_wait)} · Exit quote overdue by ${displayDuration(arm.avg_exit_wait)} on average`));
      return row;
    });
    const history = study.reviews.map(review =>
      el('small', `Review through coin ${review.checkpoint} · ${review.result}`));
    $('jarvis-experiment-rows').replaceChildren(...notes, ...history,
      el('small', 'Same $50 size and 15-minute time exit. The 20% trail starts after a 20% gain and compares with Instant Ladder. Unpriced positions at the deadline are scored at zero proceeds. Stats cover up to 250 settled coins.'));
  }

  const labHistory = new Map();
  let archiveItems = [], archiveNext = null, archiveBusy = false;
  async function loadArchive(append = false) {
    if (archiveBusy) return;
    archiveBusy = true;
    try {
      const data = await call('archive' + (append && archiveNext ? '?before=' + archiveNext : ''));
      archiveItems = [...(append ? archiveItems : []), ...data.strategies];
      archiveNext = data.next;
      const rows = archiveItems.map(item => {
        const row = el('details', '', 'lab-row');
        row.append(el('summary', `${item.rule.name} · ${item.status === 'failed' ? 'Failed test' : 'Inactive · no trades'} · ${money(item.net)} realized`));
        const body = el('div', '', 'lab-details');
        body.append(el('p', item.rule.hypothesis), el('p', 'Entry: ' + item.rule.entry), el('p', 'Exit: ' + item.rule.exit), el('small', `${item.closed} closed · ${item.open} open · ${item.slot} · created ${displayTime(item.created)} ET`));
        row.append(body); return row;
      });
      $('jarvis-archive-rows').replaceChildren(...(rows.length ? rows : [el('p', 'No retired AI strategies yet.', 'meme-muted')]));
      $('jarvis-archive-more').hidden = !archiveNext;
    } catch (error) { $('jarvis-archive-rows').replaceChildren(el('p', error.message)); }
    finally { archiveBusy = false; }
  }
  $('jarvis-archive-more').onclick = () => loadArchive(true);
  let pendingLabExpansion = null;

  async function loadLabHistory(id, append = false) {
    const cached = labHistory.get(id);
    if (cached?.loading) return;
    labHistory.set(id, {...cached, loading: true, error: null});
    render();
    try {
      const data = await call(`history?bot=${encodeURIComponent(id)}${append ? '&before=' + cached.next : ''}`);
      labHistory.set(id, {coins: [...(append ? cached.coins : []), ...data.coins], next: data.next,
        closed: state.bots.find(bot => bot.id === id).closed});
    } catch (error) {
      labHistory.set(id, {...cached, error: error.message, loading: false});
    }
    render();
  }

  function renderLab() {
    const archived = active === 'pbh';
    const bots = state.bots.filter(bot => archived ? bot.id !== 'jarvis' && !bot.research_slot : bot.research_slot);
    $('jarvis-lab-title').textContent = archived ? 'PBH' : 'Strategy Lab';
    $('jarvis-lab-description').textContent = archived ? 'Poopy Butt Hole · Older presets and retired AI strategies. History preserved.' : 'AI strategy tests and validated candidates. Winners stay here until Jarvis can adopt them.';
    if (archived && state.pbh_closeout?.requested) $('jarvis-lab-description').textContent += ` Closeout: ${state.pbh_closeout.quoted_closes} closed at quotes · ${state.pbh_closeout.pending} pending · ${state.pbh_closeout.writeoffs} paper write-offs (not sales).`;
    $('jarvis-archive-panel').hidden = !archived;
    const open = new Set([...$('jarvis-lab-rows').querySelectorAll('details[open]')].map(row => row.dataset.bot));
    const sum = key => bots.reduce((total, bot) => total + bot[key], 0);
    $('jarvis-lab-summary').textContent = `${bots.length} strategies · ${sum('closed')} completed paper positions · ${sum('open')} open · ${money(sum('realized'))} realized P/L`;
    const run = $('jarvis-lab-run');
    run.hidden = archived;
    run.textContent = state.enabled ? 'Pause all paper entries' : 'Start all paper tests';
    run.disabled = runBusy;
    const rows = bots.map(bot => {
      const detail = el('details', '', 'lab-row');
      detail.dataset.bot = bot.id;
      detail.open = open.has(bot.id) || pendingLabExpansion === bot.id;
      const summary = el('summary', '', 'lab-summary');
      const name = el('span');
      const candidate = state.local_lab?.strategies.find(item => item.slot === bot.id && ['testing', 'proven'].includes(item.status));
      name.append(el('strong', candidate?.rule.name || bot.name), el('small', botState(bot)));
      const total = el('span', '', sign(bot.estimated_pnl));
      total.append(el('strong', money(bot.pnl ?? bot.estimated_pnl)), el('small', bot.unpriced ? 'Estimated total P/L' : 'Total P/L'));
      const today = el('span', '', sign(bot.today.realized));
      today.append(el('strong', money(bot.today.realized)), el('small', 'Today realized'));
      const stats = el('span');
      stats.append(el('strong', `${bot.closed} closed · ${bot.open} open`),
        el('small', `${bot.wins} wins / ${bot.losses} losses${bot.closed ? ' · ' + Math.round(100 * bot.wins / bot.closed) + '% wins' : ''}`));
      summary.append(name, total, today, stats);
      const body = el('div', '', 'lab-details');
      body.append(el('p', bot.description), el('small', `Realized P/L ${money(bot.realized)} · Virtual funding ${money(bot.funding)} · Cash ${money(bot.cash)} · Entry misses ${bot.entry_misses} · Exit misses ${bot.quote_misses}`));
      if (candidate) body.append(el('p', candidate.rule.hypothesis), el('p', 'Entry: ' + candidate.rule.entry), el('p', 'Exit: ' + candidate.rule.exit), el('small', candidate.status === 'proven' ? (state.strategy?.local_rule?.id === candidate.id ? 'Validated · adopted by Jarvis Trader' : 'Validated · available for adoption') : 'Testing · 100 distinct closed coins required before profitability review'));
      const holdings = el('div', '', 'meme-scroll');
      for (const holding of bot.holdings) {
        const item = el('p', `${holding.symbol} · ${money(holding.mark ?? holding.estimated_value)}${holding.mark == null ? ' estimated' : ''} · ${displayTime(holding.opened)} ET`);
        if (holding.route_failure) item.append(el('small', ` · ${holding.route_failure.reason} Next check ${displayTime(holding.next_quote_at)} ET.`));
        holdings.append(item);
      }
      if (!bot.holdings.length) holdings.append(el('p', 'No open holdings. Entries wait for the strategy signal and available quotes.', 'meme-muted'));
      body.append(el('h3', 'Holdings'), holdings, el('h3', 'Trade history'));
      const cached = labHistory.get(bot.id);
      const history = el('div', '', 'meme-scroll');
      if (cached?.coins) history.append(...cached.coins.map(coinCard));
      if (cached?.coins?.length === 0) history.append(el('p', 'No completed positions yet.', 'meme-muted'));
      if (cached?.error) history.append(el('p', cached.error));
      const button = el('button', cached?.loading ? 'Loading…' : !cached ? 'View history' : cached.closed !== bot.closed ? 'Refresh history' : cached?.next ? 'Load older trades' : 'View history');
      button.type = 'button';
      button.disabled = !!cached?.loading;
      button.hidden = cached?.closed === bot.closed && cached.next == null && !cached.error;
      button.onclick = () => loadLabHistory(bot.id, !!cached?.next && cached.closed === bot.closed);
      body.append(history, button);
      detail.append(summary, body);
      return detail;
    });
    $('jarvis-lab-rows').replaceChildren(...rows);
    pendingLabExpansion = null;
  }

  function render() {
    if (!state) return;
    const selected = state.bots.find(bot => bot.id === active);
    const jarvis = active === 'jarvis';
    $('jarvis-bot').hidden = !selected;
    $('jarvis-lab').hidden = !['lab','pbh'].includes(active);
    if (['lab','pbh'].includes(active)) renderLab();
    $('jarvis-health').hidden = active !== 'health';
    $('jarvis-settings').hidden = active !== 'settings';
    if (active === 'settings') {
      $('jarvis-preset-status').textContent = state.preset_entries_paused ? 'Preset entries paused. AI research continues.' : 'Preset entries enabled.';
      $('jarvis-preset-toggle').textContent = state.preset_entries_paused ? 'Resume preset entries' : 'Pause preset entries';
      $('jarvis-email-focus').textContent = state.notification_focus ? 'Jarvis focus is on: only the hourly research report and Jarvis health / 30-minute inactivity alerts are emailed. All other emails, including backlog and timer notices, are paused.' : '';
      const guard = state.backlog_guard || {};
      const form = $('jarvis-backlog-form');
      if (!form.contains(document.activeElement)) {
        $('jarvis-backlog-enabled').checked = guard.enabled !== false;
        $('jarvis-backlog-threshold').value = guard.threshold || 100;
      }
      const labels = [`${guard.positions || 0} waiting positions across ${guard.coins || 0} distinct coins.`];
      if (guard.until_all_closed) labels.push('Buys paused until all positions close; automatic resumption and email when empty.');
      if (guard.timer_active) labels.push(`Buys paused until ${displayTime(guard.pause_until)} ET.`);
      if (state.entry_catchup_pause?.active) labels.push('Catch-up pause active: buys resume when exits catch up.');
      if (!state.enabled) labels.push('Overall paper-run switch is off.');
      if (state.enabled && !guard.timer_active && !guard.until_all_closed && !state.entry_catchup_pause?.active) labels.push('Buys permitted.');
      if ((guard.timer_active || guard.until_all_closed) && guard.timer_report) labels.push(guard.timer_report.partial
        ? `Expiry email ready; closed-position counts start from ${displayTime(guard.timer_report.started)} ET because this older timer had no recorded start.`
        : 'Expiry email ready; counts cover the full timer.');
      if (guard.latest_alert) labels.push(guard.latest_alert.sent
        ? `Last backlog email accepted ${displayTime(guard.latest_alert.sent)} ET.`
        : `Backlog email pending (${guard.latest_alert.attempts} delivery attempts).`);
      $('jarvis-pause-status').textContent = labels.join(' ');
    }
    $('jarvis-history').hidden = !['history', 'rug-pulls'].includes(active) && !jarvis;
    $('jarvis-note').textContent = state.error || '';
    if (selected) {
      $('jarvis-title').textContent = selected.name;
      $('jarvis-description').textContent = selected.description;
      $('jarvis-run').textContent = state.enabled ? 'Pause paper entries' : 'Start paper tests';
      $('jarvis-run').disabled = runBusy;
      const estimate = estimateNote(selected);
      const balance = selected.balance ?? selected.estimated_balance;
      const pnl = selected.pnl ?? selected.estimated_pnl;
      $('jarvis-portfolio').replaceChildren(
        portfolioCard(estimate ? 'Estimated total balance' : 'Total balance', money(balance), estimate || 'Cash + current sell quotes'),
        portfolioCard('Available cash', money(selected.cash), jarvis ? '$20,000 starting budget' : selected.research_slot ? '$2,000 fixed paper funding; no automatic top-ups' : 'Automatic virtual top-ups'),
        portfolioCard(estimate ? 'Estimated total P/L' : 'Total P/L', money(pnl), `Net of ${money(selected.funding)} virtual funding`, sign(pnl)),
        portfolioCard('Open holdings', String(selected.open), botState(selected)),
      );
      $('jarvis-today').textContent = money(selected.today.realized);
      $('jarvis-today').className = 'meme-stat ' + sign(selected.today.realized);
      $('jarvis-today-details').textContent = `${selected.today.closed} closed · ${selected.today.wins} wins · ${selected.today.losses} losses · ${state.day} ET`;
      $('jarvis-strategy').hidden = !jarvis;
      if ($('jarvis-local-lab')) {
        const local = state.local_lab;
        $('jarvis-local-lab').hidden = !jarvis || !local;
        if (jarvis && local) {
          const age = local.last_seen ? Math.max(0, Date.now() / 1000 - local.last_seen) : null;
          $('jarvis-local-summary').textContent = `${age == null ? 'Laptop worker has not connected' : age < 180 ? 'Laptop worker connected' : 'Laptop worker last seen ' + displayDuration(age) + ' ago'} · ${local.available_slots.length} slots available · ${local.minimum_coins} distinct closed coins before judging profitability. Quotes and market opportunities determine testing speed.`;
          $('jarvis-local-rules').replaceChildren(...local.strategies.map(candidate => {
            const row = el('details', '', 'lab-row');
            const report = candidate.report;
            row.append(el('summary', `${candidate.rule.name} · ${candidate.status} · ${report.coins || 0}/${local.minimum_coins} coins · ${money(report.net ?? 0)} net`));
            row.append(el('p', candidate.rule.hypothesis), el('p', 'Entry: ' + candidate.rule.entry), el('p', 'Exit: ' + candidate.rule.exit));
            row.append(el('small', `Latest window excludes open positions. Net excluding biggest winner: ${money(report.net_without_best ?? 0)}. Fixed -10% sampled safety stop and one-hour maximum hold; fill prices are not guaranteed.`));
            for (const source of candidate.sources || []) {
              if (!source.url.startsWith('https://')) continue;
              const link = el('a', source.title || source.url);
              link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
              row.append(el('p'), link);
            }
            return row;
          }));
        }
      }
      $('jarvis-outcome-review').hidden = !jarvis || !state.outcome_review;
      $('jarvis-early-review').hidden = !jarvis || !state.early_review;
      $('jarvis-experiments').hidden = !jarvis || !state.comparison;
      if (jarvis) {
        const completed = state.bots.filter(bot => bot.id !== 'jarvis' && !bot.research_slot).reduce((sum, bot) => sum + bot.closed, 0);
        const next = state.reviewed + state.review_every;
        const strategy = state.strategy;
        const filter = (strategy?.entry_filter || 'baseline').replaceAll('_', ' ');
        const summary = strategy ? `Version ${strategy.version} · minimum liquidity ${money(strategy.min_liquidity)} · minimum pool age ${strategy.min_age} min · Entry filter: ${filter}.` : 'Waiting for a validated strategy.';
        const sources = state.research;
        const proposal = sources?.latest_proposal;
        $('jarvis-research').textContent = sources
          ? `Recent chart or social research on ${sources.recent_coins} coins · X ${sources.x_configured ? 'configured' : 'awaiting API token'}${proposal ? ` · Code proposal from review ${proposal.version} ${proposal.sent ? 'emailed' : 'queued'}` : ''}`
          : '';
        const autonomy = state.autonomy;
        if (autonomy) {
          $('jarvis-research').textContent += ` · Strategy researcher: ${autonomy.configured ? 'paid web research enabled' : 'paid web research off'} · Web research every ${autonomy.interval_hours} hours · ${autonomy.rules.length} recent hypotheses${autonomy.last_error_at ? ' · A research request failed; retry scheduled' : ''}`;
          for (const candidate of autonomy.rules) {
            const result = candidate.report.prospective;
            const line = el('small', `${candidate.rule.name} · ${candidate.status} · ${result.coins} future coins · net ${money(result.net)} · excluding best ${money(result.net_without_best)}. ${candidate.rule.hypothesis}`);
            $('jarvis-research').append(line);
            for (const source of candidate.sources) {
              const link = el('a', source.title || source.url);
              link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
              $('jarvis-research').append(link, document.createTextNode(' '));
            }
          }
        }
        $('jarvis-review').textContent = `${summary} ${completed}/${next} completed baseline trades toward the next review. ${state.review?.result || ''} ${state.entry_guard?.reason || ''}`;
        if (state.outcome_review) renderOutcomes(state.outcome_review, state.legacy_outcomes, state.active_warning);
        if (state.early_review) renderEarlyReview(state.early_review);
        if (state.comparison) renderComparison(state.comparison);
      }
      const holdings = selected.holdings.map(holding => {
        const row = el('div', '', 'meme-row');
        const value = holding.mark ?? holding.estimated_value;
        const basis = holding.valuation_basis === 'cost' ? ' · at cost' : holding.valuation_basis === 'last_quote' ? ' · last sell quote' : '';
        row.append(el('strong', holding.symbol), el('p', `${money(value)}${basis} · Opened ${displayTime(holding.opened)} ET`));
        if (holding.route_failure) row.append(el('small', `${holding.route_failure.reason} Next quote check ${displayTime(holding.next_quote_at)} ET.`));
        return row;
      });
      $('jarvis-holdings').replaceChildren(...(holdings.length ? holdings : [el('p', 'No open holdings.', 'meme-muted')]));
    }
    if (active === 'health') {
      $('jarvis-cycle').textContent = `${state.last_cycle ? 'Updated ' + displayTime(state.last_cycle) + ' ET' : 'Awaiting first cycle'} · ${state.watching} coins watched`;
      const queue = state.exit_queue || {};
      const work = state.exit_work;
      const pause = state.entry_catchup_pause;
      const guard = state.backlog_guard || {};
      const urgent = state.exit_monitor?.urgent_positions || 0;
      let banner = queue.backed_up ? 'Exit queue delayed · Entry capacity reduced' : urgent ? 'Urgent exits take priority' : 'Monitoring active';
      if (!state.enabled) banner = 'Paper entries paused · Exits monitored';
      else if (guard.until_all_closed) banner = 'Buys paused until holdings close · Exits monitored';
      else if (guard.timer_active) banner = `Buys paused until ${displayTime(guard.pause_until)} ET`;
      else if (pause?.active) banner = 'Buy pause active · Clearing exit backlog';
      $('jarvis-exit-queue').textContent = banner;
      $('jarvis-exit-queue').className = 'health-banner' + (queue.backed_up || urgent || pause?.active ? ' warning' : '');

      const workPanel = $('jarvis-exit-work');
      const cards = el('div', '', 'health-cards');
      if (work) {
        for (const [key, label, warning] of [['holding', 'Holding', false], ['exit_triggered', 'Exit triggered', true], ['quote_delayed', 'Quote delayed', true]]) {
          const group = work[key];
          const card = el('article', '', 'health-card' + (warning && group.positions ? ' warning' : ''));
          card.append(el('span', label), el('strong', String(group.positions)), el('small', `${group.coins} coins`));
          cards.append(card);
        }
      }
      const metrics = el('div', '', 'health-metrics');
      for (const [label, value] of [['Checks due', queue.due || 0], ['Oldest wait', displayDuration(queue.oldest_wait || 0)], ['Route retries', queue.retry_positions || 0]]) {
        const metric = el('span', label);
        metric.append(el('strong', String(value)));
        metrics.append(metric);
      }
      workPanel.replaceChildren(cards, metrics);

      const diagnostics = [];
      const activity = state.quote_activity;
      if (activity) diagnostics.push(el('p', `${activity.paper_entries.toLocaleString()} paper entries · ${activity.entry_quotes.toLocaleString()} buy quotes · ${activity.exit_quotes.toLocaleString()} sell quotes · ${activity.deferrals.toLocaleString()} capacity deferrals`, 'health-notes'), el('small', `Activity since ${displayTime(activity.since)} ET`, 'health-notes'));
      if (state.discovery) diagnostics.push(el('p', `Scanner: up to ${state.discovery.batch_size} candidates every ${state.discovery.interval_seconds}s`, 'health-notes'));
      const failure = state.last_quote_failures?.exit;
      if (failure) diagnostics.push(el('p', `Last exit quote error${failure.at ? ' · ' + displayTime(failure.at) + ' ET' : ''}: ${failure.reason}`, 'health-notes'));
      if (work) {
        const bases = work.trigger_bases;
        diagnostics.push(el('p', `Exit signals: ${bases.confirmed_quote} quote-confirmed · ${bases.strategy_timer} timer · ${bases.indicative_price_or_market} indicative · ${bases.owner_closeout || 0} owner closeouts · oldest ${displayDuration(work.oldest_signal_seconds)}`, 'health-notes'));
        const latency = work.close_latency;
        diagnostics.push(el('p', latency.samples ? `Observed signal → close: ${displayDuration(latency.average_seconds)} average · ${displayDuration(latency.p95_seconds)} p95 · ${latency.samples} measured closes` : 'Signal → close: no measured closes yet', 'health-notes'));
      }
      if (state.exit_monitor) diagnostics.push(el('p', `Fresh pool prices: ${state.exit_monitor.fresh_market_positions} holdings · ${urgent} urgent`, 'health-notes'));
      if (pause?.active) diagnostics.push(el('p', 'Catch-up pause clears after ≤2 regular checks, oldest ≤5s and no urgent checks for 30 seconds. Failed routes retry separately.', 'health-notes'));
      diagnostics.push(el('p', 'Counts refer to paper positions, not submitted sell orders. Coins can overlap. Quote delay measures overdue checks; signal-to-close timing starts at first observation and excludes write-offs.', 'health-notes'));
      $('jarvis-health-details').replaceChildren(...diagnostics);

      const comparison = state.exit_comparison;
      const comparisonPanel = $('jarvis-exit-comparison');
      comparisonPanel.hidden = !comparison;
      if (comparison) {
        const table = el('table', '', 'health-table');
        const head = el('thead'); const header = el('tr');
        for (const title of ['Period', 'Closed / open', 'Quote delay', 'Stops ≥90s late', 'Stop gap']) header.append(el('th', title));
        head.append(header); table.append(head);
        const body = el('tbody');
        const labels = {before:'Before update', carried:'After · older holdings', new:'After · new holdings'};
        for (const c of comparison.cohorts) {
          const row = el('tr'); const period = el('td', labels[c.id]);
          period.append(el('small', `${c.quote_samples} quote samples · ${c.route_retries} retries`));
          const counts = el('td', `${c.closed} / ${c.open}`);
          const wait = el('td', c.mean_wait == null ? '—' : displayDuration(c.mean_wait));
          if (c.p95_wait != null) wait.append(el('small', `${displayDuration(c.p95_wait)} p95`));
          row.append(period, counts, wait, el('td', `${c.late_stops}/${c.stop_wait_samples}`), el('td', c.mean_stop_gap == null ? '—' : `${c.mean_stop_gap.toFixed(1)} pp`));
          body.append(row);
        }
        table.append(body);
        const wrap = el('div', '', 'health-table-wrap'); wrap.append(table);
        comparisonPanel.replaceChildren(el('summary', 'Exit performance comparison'), el('p', `Updated ${displayTime(comparison.started_at)} ET · Before: 30 min · After: ${displayDuration(comparison.after_seconds)}`, 'health-notes'), wrap, el('p', 'Stop gap: average percentage points below the sampled stop. Different periods and coins are not a controlled comparison.', 'health-notes'), el('p', comparison.note, 'health-notes'));
      }
      const table = el('table', '', 'health-table');
      const head = el('thead'); const header = el('tr');
      for (const title of ['Strategy', 'Status', 'Open', 'Exit quote misses']) header.append(el('th', title));
      head.append(header); table.append(head);
      const body = el('tbody');
      for (const bot of state.bots) {
        const row = el('tr');
        row.append(el('td', bot.name), el('td', botState(bot)), el('td', String(bot.open)), el('td', bot.quote_misses.toLocaleString()));
        body.append(row);
      }
      table.append(body);
      $('jarvis-health-bots').replaceChildren(table);
    }
    if (['history', 'rug-pulls'].includes(active) || jarvis) {
      const group = state.bots.filter(bot => (bot.id === 'jarvis') === jarvis);
      const sum = key => group.reduce((n, bot) => n + bot[key], 0);
      $('jarvis-history-title').textContent = jarvis ? 'Jarvis Trader history' : 'Completed trades · baseline paper bots';
      $('jarvis-history-summary').replaceChildren(
        el('span', `${sum('closed')} completed`), el('span', `${sum('wins')} wins`), el('span', `${sum('losses')} losses`),
        el('span', `${money(sum('realized'))} realized`, sign(sum('realized'))),
      );
      if (active === 'rug-pulls') {
        const rugs = state.rug_pulls || {};
        $('jarvis-history-title').textContent = 'Rug Pull · paper write-offs';
        $('jarvis-history-summary').replaceChildren(
          el('span', `${rugs.positions || 0} closed positions across ${rugs.coins || 0} coins`),
          el('span', `${money(rugs.pnl || 0)} recorded P/L`, 'negative'),
          el('p', 'No-route positions are closed at zero remaining value: a 100% loss on the remaining holding. This is a paper write-off, not an executed sale or proof of a rug pull. Earlier partial-sale proceeds stay recorded.')
        );
      }

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
      const reason = Number.isFinite(trade.held_seconds)
        ? `${trade.reason} · Held ${displayDuration(trade.held_seconds)}`
        : trade.reason;
      const evidence = [
        trade.entry_pattern && trade.entry_pattern !== 'none' ? trade.entry_pattern.replaceAll('_', ' ') : '',
        trade.social_match ? 'mint mentioned by multiple social accounts' : '',
      ].filter(Boolean).join(' · ');
      row.append(title, el('p', evidence ? `${reason} · Entry: ${evidence}` : reason, 'history-reason'),
        values, el('small', displayTime(trade.closed) + ' ET'));
      card.append(row);
    }
    return card;
  }

  async function loadHistory(append = false) {
    if (historyBusy) return;
    const requested = active;
    if (!['history', 'jarvis', 'rug-pulls'].includes(requested)) return;
    const more = $('jarvis-more');
    historyBusy = true;
    more.disabled = true;
    try {
      const data = await call(`history?rugs_only=${requested === 'rug-pulls'}&jarvis_only=${requested === 'jarvis'}${append ? '&before=' + cursor : ''}`);
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
    const moved = ['instant-step', 'instant-trim', 'patient-step', 'patient-trim'].includes(hash);
    active = moved ? 'pbh' : tabs.some(tab => tab.hash === '#' + hash) ? hash : 'jarvis';
    if (moved) {
      history.replaceState(null, '', '#pbh');
      pendingLabExpansion = hash;
    }
    for (const tab of tabs) {
      if (tab.hash === '#' + active) tab.setAttribute('aria-current', 'page');
      else tab.removeAttribute('aria-current');
    }
    historyLoaded = false;
    cursor = null;
    $('jarvis-more').hidden = true;
    $('jarvis-history-rows').replaceChildren();
    render();
    if (active === 'pbh') loadArchive();
    loadHistory();
  }

  async function refresh() {
    if (statusBusy || runBusy) return;
    statusBusy = true;
    try {
      const next = await call('status');
      if (runBusy) return;
      const historyChanged = state && next.bots.some((bot, index) => bot.closed !== state.bots.find(previous => previous.id === bot.id)?.closed);
      const archiveChanged = JSON.stringify(next.local_lab?.strategies.map(item => [item.id,item.status])) !== JSON.stringify(state?.local_lab?.strategies.map(item => [item.id,item.status]));
      state = next;
      render();
      if (active === 'pbh' && archiveChanged) loadArchive();
      if (historyChanged || !historyLoaded) loadHistory();
    } catch (error) { $('jarvis-note').textContent = error.message; }
    finally { statusBusy = false; }
  }

  const toggleRun = async () => {
    if (!state || runBusy) return;
    const button = $('jarvis-run');
    runBusy = button.disabled = true;
    try {
      state = await call('run', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({enabled: !state.enabled})});
      render();
    } catch (error) { $('jarvis-note').textContent = error.message; }
    finally { runBusy = button.disabled = false; render(); }
  };
  async function saveControl(path, payload) {
    if (runBusy) return;
    runBusy = true;
    const buttons = [...$('jarvis-settings').querySelectorAll('button')];
    buttons.forEach(button => { button.disabled = true; });
    try {
      state = await call(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
      render();
      $('jarvis-settings-result').textContent = 'Saved. Sell monitoring continues.';
    } catch (error) { $('jarvis-settings-result').textContent = error.message; }
    finally { runBusy = false; buttons.forEach(button => { button.disabled = false; }); }
  }
  $('jarvis-preset-toggle').onclick = () => saveControl('preset-pause', {paused: !state.preset_entries_paused});
  $('jarvis-backlog-form').onsubmit = event => {
    event.preventDefault();
    saveControl('backlog-settings', {enabled: $('jarvis-backlog-enabled').checked,
      threshold: Number($('jarvis-backlog-threshold').value)});
  };
  for (const button of document.querySelectorAll('[data-buy-pause]')) {
    button.onclick = () => saveControl('buy-pause', {minutes: Number(button.dataset.buyPause)});
  }
  $('jarvis-run').onclick = toggleRun;
  $('jarvis-lab-run').onclick = toggleRun;
  $('jarvis-more').onclick = () => loadHistory(true);
  window.addEventListener('hashchange', selectTab);
  selectTab();
  refresh();
  setInterval(refresh, 10000);
})();
