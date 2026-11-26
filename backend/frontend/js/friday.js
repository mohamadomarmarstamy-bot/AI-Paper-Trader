const element = id => document.getElementById(id);
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
        items('phase-results', extended.probes.length ? extended.probes.map(p => p.symbol + ' · ' + p.phase.replaceAll('_',' ') + ' · ' + p.state + (p.net === null ? '' : ' · modeled net $' + p.net.toFixed(3))) : ['No extended-hours probes recorded.']);
        items('positions', trials.positions.length ? trials.positions.map(p => p.symbol + ' · ' + p.shares + ' modeled shares · regular session') : ['No open simulated positions.']);
        items('outcomes', trials.history.length ? trials.history.map(p => p.symbol + ' · ' + p.source_group.replaceAll('_', ' ') + ' · ' + p.outcome.scenarios.map(s =>
            s.bps_per_side + ' bps/side: $' + s.modeled_pnl_usd.toFixed(2) + ' vs matched hold $' + s.matched_hold_pnl_usd.toFixed(2)).join(' / ')) : ['No closed simulations.']);
        items('reasons', (latest?.reasons || []).map(r => r.reason.replaceAll('_', ' ') + ' · ' + r.count));
        items('queue', latest?.candidates.length ? latest.candidates.map(r =>
            r.symbol + ' · move ' + r.change.toFixed(2) + '% · spread ' + r.spread.toFixed(2) + '%') : ['No candidates recorded.']);
    } catch {
        element('message').textContent = 'Unable to load Friday research. Try again.';
    } finally {
        element('capture').disabled = false;
    }
}
element('capture').addEventListener('click', () => refresh('POST'));
refresh('GET');
