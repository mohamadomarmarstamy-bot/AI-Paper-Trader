"""Matched-entry prospective PAPER exit comparison; no signer or live imports."""
from contextlib import closing
from copy import deepcopy
import json
import time
import database

TABLE = 'solana_trailing_comparison'


def read(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone(): return None
    row = conn.execute('SELECT state_json FROM solana_trailing_comparison WHERE id=1').fetchone()
    return json.loads(row[0]) if row else None


def watched_mints():
    with closing(database.get_connection()) as conn: state = read(conn)
    return list({p['mint'] for arm in (state or {}).get('arms', {}).values() for p in arm['positions'].values()})


def initial(now, config):
    return {'started_at': now, 'last_cycle': None, 'entry_count': 0, 'arms': {
        name: {'config': deepcopy(config), 'positions': {}, 'cash_usd': 100000.,
               'stop_loss_cooldowns': {}, 'closed': [], 'peak_equity_usd': 100000.,
               'max_drawdown_usd': 0.} for name in ('baseline', 'trailing_20')}}


def mark(arm, quotes, at, allow_profit, trailing):
    import solana_copy_trader as paper
    closes = []
    if trailing:
        for key, p in list(arm['positions'].items()):
            if not p.get('profit_take_stages') or p.get('pending_sell'): continue
            q = paper.holding_quote(p, quotes, at)
            if not q: continue  # Missing/stale prices never trigger a simulated fill.
            peak = max(p.get('trailing_peak_price', q['price']), q['price'])
            p['trailing_peak_price'] = peak
            floor = max(p['cost_usd'], max(0, p['quantity']*peak*.8*.99-.1))
            value = max(0, p['quantity']*q['price']*.99-.1)
            p['trailing_floor_usd'] = floor
            if value <= floor:
                closes.append(paper.record_close(arm, key, q, at, 'trailing_profit_protection'))
    paper.mark_and_protect(arm, quotes, at, closes, allow_profit_takes=allow_profit)
    if trailing:
        for p in arm['positions'].values():
            if p.get('profit_take_stages'):
                q = paper.holding_quote(p, quotes, at)
                if q: p['trailing_peak_price'] = max(p.get('trailing_peak_price', q['price']), q['price'])
    arm['closed'].extend(closes)
    if all(p.get('mark_usd') is not None and p.get('marked_at') is not None and 0 <= at-p['marked_at'] <= 45 for p in arm['positions'].values()):
        equity = arm['cash_usd']+sum(p['mark_usd'] for p in arm['positions'].values())
        arm['peak_equity_usd'] = max(arm['peak_equity_usd'], equity)
        arm['max_drawdown_usd'] = max(arm['max_drawdown_usd'], arm['peak_equity_usd']-equity)


def advance(conn, actions, config, now):
    """Called inside the paper cycle transaction; experiment failure rolls back only its savepoint."""
    conn.execute('CREATE TABLE IF NOT EXISTS solana_trailing_comparison (id INTEGER PRIMARY KEY CHECK(id=1),state_json TEXT NOT NULL)')
    state = read(conn) or initial(now, config)
    for arm in state['arms'].values(): arm['config'] = deepcopy(config)
    for action in actions:
        kind = action[0]
        if kind == 'entry':
            state['entry_count'] += 1
            for arm in state['arms'].values():
                p = deepcopy(action[1]); arm['positions'][p['id']] = p; arm['cash_usd'] -= p['cost_usd']
        elif kind == 'sell':
            event = action[1]
            for arm in state['arms'].values():
                for p in arm['positions'].values():
                    if p['wallet'] == event['wallet'] and p['mint'] == event['mint'] and event['source_at'] >= p['source_at']:
                        p['pending_sell'] = event['id']
        elif kind == 'mark':
            for name, arm in state['arms'].items(): mark(arm, action[1], action[2], action[3], name == 'trailing_20')
    state['last_cycle'] = now
    conn.execute('INSERT OR REPLACE INTO solana_trailing_comparison VALUES (1,?)', (json.dumps(state, allow_nan=False),))


def arm_summary(arm, now):
    groups = {}
    for r in arm['closed']: groups.setdefault(r['lot_id'], []).append(r)
    complete = {key: sum(r['pnl_usd'] for r in rows) for key, rows in groups.items() if any(not r.get('partial_exit') for r in rows)}
    unpriced = sum(p.get('mark_usd') is None or p.get('marked_at') is None or not 0 <= now-p['marked_at'] <= 45 for p in arm['positions'].values())
    realized = sum(r['pnl_usd'] for r in arm['closed'])
    unrealized = None if unpriced else sum(p['mark_usd']-p['cost_usd'] for p in arm['positions'].values())
    return {'realized_pnl_usd': realized, 'unrealized_pnl_usd': unrealized,
            'total_pnl_usd': realized+unrealized if unrealized is not None else None,
            'completed_positions': len(complete), 'winning_completed_positions': sum(p>0 for p in complete.values()),
            'win_rate_percent': 100*sum(p>0 for p in complete.values())/len(complete) if complete else None,
            'open_holdings': len(arm['positions']), 'unpriced_holdings': unpriced,
            'max_observed_drawdown_usd': arm['max_drawdown_usd'],
            'positions': list(arm['positions'].values()), 'recent_exits': arm['closed'][-20:],
            'exit_records_total': len(arm['closed']), 'exit_records_returned': min(20, len(arm['closed']))}


def status(now=None):
    now = time.time() if now is None else now
    with closing(database.get_connection()) as conn: state = read(conn)
    if not state: return {'started': False, 'paper_only': True, 'automatic_promotion': False}
    arms = {name: arm_summary(arm, now) for name, arm in state['arms'].items()}
    return {'started': True, 'paper_only': True, 'automatic_promotion': False, 'started_at': state['started_at'],
            'last_cycle': state['last_cycle'], 'matched_entries': state['entry_count'], 'arms': arms,
            'basis': 'Prospective matched main-paper entries and identical observed pool prices/cost model. No extra entries after an arm exits early; exits only differ by the post-trim 20% price trail with net break-even floor. Sampled drawdown, not every market tick. No live fills or performance guarantees.'}
