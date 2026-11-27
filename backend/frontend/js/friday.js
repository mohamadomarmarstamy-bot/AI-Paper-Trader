const element = id => document.getElementById(id);
const sections = Array.from(document.querySelectorAll('main > section'));
function showPage() {
    const target = sections.find(section => '#' + section.id === location.hash) || sections[0];
    sections.forEach(section => { section.hidden = section !== target; });
    document.querySelectorAll('nav a').forEach(link => {
        if (link.hash === '#' + target.id) link.setAttribute('aria-current', 'page');
        else link.removeAttribute('aria-current');
    });
}
window.addEventListener('hashchange', showPage);
showPage();

function renderQueue(candidates) {
    const body = element('queue');
    body.replaceChildren();
    if (!candidates?.length) {
        const cell = body.insertRow().insertCell();
        cell.colSpan = 3;
        cell.textContent = 'No candidates recorded.';
        return;
    }
    candidates.forEach(candidate => {
        const row = body.insertRow();
        [candidate.symbol, candidate.change.toFixed(2) + '%', candidate.spread.toFixed(2) + '%']
            .forEach(value => { row.insertCell().textContent = value; });
    });
}

function items(id, values) {
    element(id).replaceChildren(...values.map(value => {
        const li = document.createElement('li');
        li.textContent = value;
        return li;
    }));
}
async function refresh(method) {
    element('capture').disabled = true;
    try {
        const response = await fetch('/ai-friday/research', {method});
        if (!response.ok) throw new Error();
        const {latest, message, passive_capture, trials, extended} = await response.json();
        element('message').textContent = message || '';
        element('coverage').textContent = latest
            ? `${latest.screened} of ${latest.universe} supported symbols screened. Scan completed ${new Date(latest.finished * 1000).toLocaleString()}; reviewed ${new Date(latest.reviewed * 1000).toLocaleString()}.`
            : 'No scan recorded yet.';
        element('passive').textContent = 'Passive research capture · ' + (passive_capture ? 'enabled' : 'disabled');
        items('scans', latest?.history.length ? latest.history.map(s =>
            new Date(s.finished * 1000).toLocaleString() + ' · ' + s.screened + ' screened') : ['No scan history yet.']);
        element('trial-health').textContent = 'Simulation · ' + trials.health.replaceAll('_', ' ');
        const remedies = {
            due_exit_waiting_for_complete_forward_path: 'New admissions paused. Friday rechecks only its own due trials for a complete forward path.',
            no_new_timely_matching_chart: 'Waiting for a fresh independent stock chart and a matching Friday candidate.',
            stock_chart_source_unavailable: 'Waiting for the existing Stock Research Lab to publish completed-bar observations.',
            awaiting_fresh_friday_scan: 'Waiting for the stock scanner to publish fresh evidence.',
            open_position_cap: 'Admissions resume after an owned position reconciles.'
        };
        element('remedy').textContent = remedies[trials.health] || '';
        element('phase-health').textContent = extended.health ? extended.health.phase.replaceAll('_',' ') + ' · ' + extended.health.reason.replaceAll('_',' ') + ' · ' + new Date(extended.health.checked*1000).toLocaleString() : 'Extended-hours capture awaits its first cycle.';
        items('replays', extended.replays?.length ? extended.replays.map(r =>
            r.symbol + ' · ' + new Date(r.decision_at*1000).toLocaleString() + ' · ' +
            r.reason.replaceAll('_',' ') + ' · ' + r.outcome.replaceAll('_',' ') +
            (r.net === null ? '' : ' · $' + r.net.toFixed(3)) + ' · gate: ' + r.evidence.decision_gate.replaceAll('_',' ') +
            ' · scan lag ' + (r.decision_at-r.scanned_at).toFixed(1) + 's · quote ' + (r.evidence.quote_at || 'missing') +
            ' · minute bar ' + (r.evidence.candle_at || 'missing') + ' · ' + r.evidence.version +
            (r.evidence.alert ? ' · Pro Ticker published ' + r.evidence.alert.published + ', first seen ' + r.evidence.alert.first_seen : '')) : ['No replay observations recorded.']);
        items('phase-results', extended.probes.length ? extended.probes.map(p => p.symbol + ' · ' + p.phase.replaceAll('_',' ') + ' · ' + p.state + (p.net === null ? '' : ' · modeled net $' + p.net.toFixed(3))) : ['No extended-hours probes recorded.']);
        const study = trials.rsi_shadow;
        element('rsi-shadow').textContent = study?.admissions
            ? 'Latest ' + study.admissions + ' admissions · ' + study.matched + ' matched closes · ' + study.pending +
              ' awaiting a forward outcome · ' + study.missing + ' unavailable RSI (' + Object.entries(study.missing_reasons).map(([k,v]) => k.replaceAll('_',' ')+' '+v).join(', ') + '). ' +
              study.confirmed + ' confirmed closes · ' + study.blocked_winners + ' blocked winners · ' + study.filtered_false_moves + ' filtered losses · ' + study.filtered_flat + ' filtered flat.' +
              (study.matched ? ' Matched modeled net: baseline $' + study.baseline_net.toFixed(2) + ', RSI $' + study.rsi_net.toFixed(2) + '.' : '')
            : 'Awaiting prospective Friday breakout admissions.';
        items('positions', trials.positions.length ? trials.positions.map(p => p.symbol + ' · ' + p.shares + ' modeled shares · regular session') : ['No open simulated positions.']);
        items('outcomes', trials.history.length ? trials.history.map(p => p.symbol + ' · ' + p.source_group.replaceAll('_', ' ') + ' · ' + p.outcome.scenarios.map(s =>
            s.bps_per_side + ' bps/side: $' + s.modeled_pnl_usd.toFixed(2) + ' vs matched hold $' + s.matched_hold_pnl_usd.toFixed(2)).join(' / ')) : ['No closed simulations.']);
        items('reasons', latest?.reasons.length ? latest.reasons.map(r => r.reason.replaceAll('_', ' ') + ' · ' + r.count) : ['No scan decisions recorded.']);
        renderQueue(latest?.candidates);
        element('open-count').textContent = trials.positions.length;
        element('queue-count').textContent = latest ? latest.candidates.length : '—';
        element('screen-count').textContent = latest ? latest.screened : '—';
    } catch {
        element('message').textContent = 'Unable to load Friday research. Try again.';
    } finally {
        element('capture').disabled = false;
    }
}
element('capture').addEventListener('click', () => refresh('POST'));
refresh('GET');

element('load-baseline').addEventListener('click', async () => {
    const button = element('load-baseline');
    button.disabled = true;
    try {
        const response = await fetch('/ai-friday/paper-history');
        const report = await response.json();
        if (!response.ok) {
            element('baseline').textContent = report.detail || 'Historical analysis unavailable.';
            return;
        }
        const total = report.groups.all;
        element('baseline').textContent = total
            ? report.verified_closed + ' explicitly linked closed entries. At 10 bps per side plus modeled fees: $' + total.modeled_net_usd['10'].toFixed(2) + '. Holdout: ' + (report.groups.holdout?.trades || 0) + ' trades. Unresolved entries: ' + (report.counts.open_incomplete_or_unlinked_entries || 0) + '.'
            : 'No fully linked PAPER equity round trips found. Check source coverage and exit links.';
    } catch {
        element('baseline').textContent = 'The PAPER stock fill ledger is unavailable.';
    } finally {
        button.disabled = false;
    }
});
