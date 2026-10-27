/* Forward experiments share no execution controls with the running trader. */
(() => {
    const form = document.getElementById('day-trader-form');
    if (!form) return;
    const input = document.getElementById('day-trader-symbol');
    const note = document.getElementById('day-trader-note');
    const results = document.getElementById('day-trader-results');
    let identity = null;
    let busy = false;
    const api = () => typeof API_URL === 'string' ? API_URL : '';
    const money = value => typeof value === 'number' && Number.isFinite(value) ? `$${value.toFixed(2)}` : 'Unavailable';
    const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };
    function render(data) {
        results.replaceChildren();
        results.append(node('p', data.summary_scope));
        for (const group of data.summary || []) {
            results.append(node('p', `${group.strategy}: ${group.modeled_samples} modeled outcomes · strategy ${money(group.base_cost_modeled_pnl_usd)} · matched hold ${money(group.base_cost_matched_hold_pnl_usd)}. These are independent simulations, not account profit.`));
        }
        for (const item of data.results || []) {
            const card = node('article', ''); card.className = 'card';
            const d = item.decision;
            card.append(node('h3', `${item.symbol} · ${new Date(item.frozen_at*1000).toLocaleString()}`));
            card.append(node('p', `${d.status} · ${d.reason} Feed: ${d.feed}. Opening RVOL: ${d.opening_rvol?.toFixed(2) ?? 'unavailable'}. Session VWAP: ${money(d.vwap)}.`));
            for (const plan of d.setups || []) {
                card.append(node('p', `${plan.strategy}: ${plan.status} · ${plan.reason}`));
                if (plan.status === 'candidate') {
                    card.append(node('p', `${plan.shares} hypothetical whole shares · reference ask ${money(plan.entry_ask)} · stop ${money(plan.stop)} · target ${money(plan.target)} · planned base-cost risk ${money(plan.planned_risk_usd)}. Evaluate after ${new Date((plan.expires_at+60)*1000).toLocaleTimeString()}.`));
                }
            }
            for (const outcome of item.outcomes || []) {
                card.append(node('p', `${outcome.strategy}: ${outcome.status} · ${outcome.exit_reason || ''}`));
                for (const scenario of outcome.scenarios || []) card.append(node('p', `${scenario.bps_per_side} bps per-side cost stress: modeled ${money(scenario.modeled_pnl_usd)} · hold ${money(scenario.matched_hold_pnl_usd)}`));
            }
            if (!item.outcomes && d.setups?.some(p => p.status === 'candidate')) {
                const button = node('button', 'Evaluate saved forward outcome'); button.type = 'button'; button.className = 'secondary-button';
                button.addEventListener('click', () => request('/evaluate', {request_id:item.request_id})); card.append(button);
            }
            results.append(card);
        }
        for (const limitation of data.limitations || []) results.append(node('p', limitation));
        const pending = data.evaluation?.find(o => o.status === 'pending');
        if (pending) note.textContent = `Not due yet. Evaluate after ${new Date(pending.evaluate_after*1000).toLocaleString()}. No result was invented.`;
        if (data.evaluation?.some(o => o.status === 'inconclusive')) note.textContent = 'Forward path is incomplete. No result was invented; retry later.';
    }
    async function request(path = '', body) {
        if (busy) return;
        busy = true;
        const controls = [form.querySelector('button'), document.getElementById('day-trader-refresh')];
        controls.forEach(b => { if (b) b.disabled = true; });
        note.textContent = body ? 'Reading evidence. No trading order will be sent…' : 'Loading saved experiments…';
        const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 30000);
        try {
            const response = await fetch(`${api()}/auto-trader/day-trader${path}`, {method:body ? 'POST' : 'GET', cache:'no-store', signal:controller.signal,
                ...(body ? {headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {})});
            const data = await response.json();
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status}).`);
            note.textContent = 'Saved research loaded. No order placed; current trading rules are unchanged.';
            render(data);
        } catch (error) { note.textContent = error.name === 'AbortError' ? 'Data request timed out. Refresh saved experiments before retrying; no trading order was sent.' : error.message; }
        finally { clearTimeout(timer); busy = false; controls.forEach(b => { if (b) b.disabled = false; }); }
    }
    form.addEventListener('submit', event => {
        event.preventDefault(); const symbol = input.value.trim().toUpperCase();
        if (!/^[A-Z][A-Z0-9.]{0,9}$/.test(symbol)) { note.textContent = 'Enter one stock symbol.'; return; }
        if (!identity || identity.symbol !== symbol) identity = {symbol, request_id:crypto.randomUUID()};
        request('/observe', identity);
    });
    document.getElementById('day-trader-new').addEventListener('click', () => { identity = null; note.textContent = 'Ready for a new observation. Submit to freeze current evidence.'; });
    document.getElementById('day-trader-refresh').addEventListener('click', () => request());
})();
