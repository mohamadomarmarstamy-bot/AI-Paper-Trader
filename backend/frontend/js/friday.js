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
        const {latest, message, passive_capture} = await response.json();
        element('message').textContent = message || '';
        element('coverage').textContent = latest
            ? `${latest.screened} of ${latest.universe} supported symbols screened. Scan completed ${new Date(latest.finished * 1000).toLocaleString()}; reviewed ${new Date(latest.reviewed * 1000).toLocaleString()}.`
            : 'No scan recorded yet.';
        element('passive').textContent = 'Passive research capture · ' + (passive_capture ? 'enabled' : 'disabled');
        items('scans', latest?.history.length ? latest.history.map(s =>
            new Date(s.finished * 1000).toLocaleString() + ' · ' + s.screened + ' screened') : ['No scan history yet.']);
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
