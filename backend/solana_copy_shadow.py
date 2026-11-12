"""Prospective quote-based copy portfolio. Never prepares, signs or sends swaps.

The main paper portfolio and every live table are read-only inputs. Missing
routes stay missing; quotes and estimated costs are never called actual fills.
"""
import asyncio
from contextlib import closing
from decimal import Decimal, ROUND_CEILING
import json
import logging
import time
import uuid

import database
from solana_copy_entry import SOL, check_quote
from solana_jupiter import build_quote, Deferred, RateLimited, Unavailable

SCHEMA = '''CREATE TABLE IF NOT EXISTS solana_copy_shadow_run(
 wallet TEXT PRIMARY KEY, state_json TEXT NOT NULL,
 lease_owner TEXT, lease_until REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS solana_copy_shadow_entries(
 wallet TEXT NOT NULL, source_id TEXT NOT NULL, created_at REAL NOT NULL,
 status TEXT NOT NULL, details_json TEXT NOT NULL, PRIMARY KEY(wallet,source_id));
CREATE INDEX IF NOT EXISTS solana_copy_shadow_pending
 ON solana_copy_shadow_entries(wallet,status,created_at);'''
PENDING = ('preparing', 'signed', 'submitted', 'unknown')
BASIS = ('Forward quote simulation, not actual fills or guaranteed profit. Uses live buy sizing, '
         'source eligibility, hours, token checks, 5% entry cap and live exit rules. '
         'Starts with a snapshot of available native SOL; simulated cash then evolves independently. '
         'Uses minimum quoted output, maximum configured priority fee plus 5,000 lamports per swap, '
         'and entry rent with no assumed refund. Quotes cannot reproduce transaction failures, '
         'MEV, final landing prices or manual wallet activity. Live activation and signer availability '
         'do not gate this hypothetical portfolio. Original paper uses its own size and rules; '
         'its USD P/L is not directly comparable to SOL P/L. Missing observations remain visible.')


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def state_row(conn, table, names):
    row = conn.execute(f'SELECT state_json FROM {table} WHERE id=1').fetchone() if table in names else None
    return json.loads(row[0]) if row else {}


def held_sources():
    """Let the existing source reader retain sell monitoring while buys pause."""
    if database._paper_trial_path.get() is not None: return set()
    with closing(database.get_connection()) as conn:
        names = tables(conn)
        if not {'solana_copy_shadow_run', 'solana_real_copy_wallet'} <= names: return set()
        row = conn.execute('SELECT s.state_json FROM solana_copy_shadow_run s '
                           'JOIN solana_real_copy_wallet w ON w.wallet_address=s.wallet WHERE w.id=1').fetchone()
    state = json.loads(row[0]) if row else {}
    return {p['source_wallet'] for p in state.get('positions', {}).values()}


def timing_model(conn, owner, names):
    """Recent measured current-pipeline source→observed-finalization, no backfill."""
    from solana_copy_performance import stats
    from solana_execution_timing import PROFILE
    values = []; exits = []
    if 'solana_real_orders' in names:
        for row in conn.execute("SELECT details_json FROM solana_real_orders WHERE status='confirmed' ORDER BY created_at DESC LIMIT 200"):
            order = json.loads(row[0])
            value = (order.get('timing_ms') or {}).get('source_to_finalized_ms')
            if (order.get('wallet') == owner and order.get('side') == 'buy'
                    and order.get('execution_profile') == PROFILE
                    and type(value) in (int, float) and 0 <= value <= 90000):
                values.append(value)
            exit_value = (order.get('timing_ms') or {}).get('trigger_to_finalized_ms')
            if (order.get('wallet') == owner and order.get('side') == 'sell'
                    and order.get('execution_profile') == PROFILE
                    and type(exit_value) in (int, float) and 0 <= exit_value <= 90000):
                exits.append(exit_value)
    median = stats(values)['median']
    return {'seconds': max(0, median / 1000) if median is not None else 5,
            'samples': len(values), 'basis': 'Measured current-pipeline median' if values else 'Assumed 5 seconds; no measured samples',
            'exit_seconds': stats(exits)['median']/1000 if exits else 2,
            'exit_samples': len(exits)}


class Reader:
    """Only expose the provider's read methods; no prepare/sign/send path."""
    def __init__(self):
        from solana_real_execution import Provider
        provider = Provider()
        self.network = provider.network
        self.mint = provider.mint
        self.rent = provider.entry_rent

    def balance(self, owner):
        from solana_real_budget import observe
        now = time.time(); result = observe(owner, now)
        if result.get('observed_at') is None or not 0 <= now-result['observed_at'] <= 45:
            raise Unavailable('Fresh native SOL balance needed to start comparison.')
        return result['lamports']

    def price(self):
        from solana_real_budget import sol_price
        return sol_price()['usd']

    def age(self, mint):
        from solana_token_age import require_age
        return require_age(mint)


def fee(policy):
    return 5000 + policy['max_priority_fee_lamports']


def write_entry(conn, owner, row):
    conn.execute('INSERT INTO solana_copy_shadow_entries VALUES(?,?,?,?,?) '
                 'ON CONFLICT(wallet,source_id) DO UPDATE SET status=excluded.status,details_json=excluded.details_json',
                 (owner, row['id'], row['created_at'], row['status'], json.dumps(row, allow_nan=False)))


def admission(event, source, state, now):
    from solana_live_hours import entry_allowed
    from solana_live_source_policy import paper_only
    from solana_real_worker import source_eligible
    if paper_only(event['wallet'], source.get('traders', [])): return 'Paper-only source'
    if not source_eligible(source, event['wallet'], event['source_at'], state['started_at']): return 'Source paused or outside prospective follow window'
    if source.get('error') or not 0 <= now-(source.get('last_cycle') or 0) <= 60: return 'Source feed incomplete or stale'
    if not entry_allowed(now, event['wallet']) or not entry_allowed(event['source_at'], event['wallet']): return 'Outside live entry hours'
    if not 0 <= now-event['source_at'] <= 90: return 'Source signal expired or future-dated'
    if event.get('source') != 'solana_rpc_pump' and event.get('status') != 'paper_buy': return 'No verified source or admitted paper buy'
    if state['cooldowns'].get(event['mint'], 0) > now: return 'Protective-exit cooldown'
    if any(p['mint'] == event['mint'] for p in state['positions'].values()): return 'One position per coin; duplicate skipped'
    return None


def ingest(conn, state, source, model, now):
    names = tables(conn); updates = {}; sales = []
    for table, key in (('solana_live_source_events', 'direct_cursor'), ('solana_copy_events', 'paper_cursor')):
        if table not in names: continue
        for raw in conn.execute(f'SELECT rowid,details_json FROM {table} WHERE rowid>? ORDER BY rowid LIMIT 500', (state[key],)):
            state[key] = raw[0]; event = json.loads(raw[1])
            if event['source_at'] < state['started_at']: continue
            if event['side'] == 'sell':
                sales.append(event); continue
            if event['id'] in updates or conn.execute('SELECT 1 FROM solana_copy_shadow_entries WHERE wallet=? AND source_id=?', (state['wallet'], event['id'])).fetchone(): continue
            reason = admission(event, source, state, now)
            updates[event['id']] = {'id': event['id'], 'mint': event['mint'], 'source_wallet': event['wallet'],
                'source_at': event['source_at'], 'created_at': now, 'event': event,
                'status': 'skipped' if reason else 'queued', 'reason': reason or 'Waiting for delayed quote',
                'eligible_at': max(now, event['source_at']+model['seconds']), 'delay_model': model,
                'quote_attempts': 0, 'exits': [], 'realized_lamports': 0}
    # Persist source sells even when there is no quote capacity this cycle.
    for sale in sales:
        for p in state['positions'].values():
            if (p['source_wallet'], p['mint']) == (sale['wallet'], sale['mint']) and sale['source_at'] >= p['source_at']:
                p['pending_reason'] = 'source_sell'
                p.setdefault('exit_eligible_at', now+model['exit_seconds'])
        for row in updates.values():
            if (row['source_wallet'], row['mint']) == (sale['wallet'], sale['mint']) and sale['source_at'] >= row['source_at']:
                row.update(status='skipped', reason='Source already sold before simulated entry')
        for raw in conn.execute("SELECT details_json FROM solana_copy_shadow_entries WHERE wallet=? AND status='queued'", (state['wallet'],)):
            row = json.loads(raw[0])
            if (row['source_wallet'], row['mint']) == (sale['wallet'], sale['mint']) and sale['source_at'] >= row['source_at']:
                row.update(status='skipped', reason='Source already sold before simulated entry'); updates[row['id']] = row
    return updates


def quote_output(quote, owner, mint, side, amount, policy, now):
    q = quote(owner, SOL if side == 'buy' else mint, mint if side == 'buy' else SOL,
              amount, policy, research=True)
    # Do not persist Jupiter instruction payloads, only prices and observation time.
    if not 0 <= now()-q['_observed_at'] <= 20: raise Unavailable('Quote expired during observation')
    minimum = int(q['otherAmountThreshold'])
    if minimum <= 0: raise Unavailable('Quote has no positive minimum output')
    return minimum, q['_observed_at']


def buy(row, state, source, config, policy, reader, quote, clock):
    now = clock(); reason = admission(row['event'], source, state, now)
    if reason: row.update(status='skipped', reason=reason); return
    if now < row['eligible_at']: return
    if any(p.get('exit_value_lamports') is None or now-(p.get('marked_at') or 0)>60 for p in state['positions'].values()):
        row['reason'] = 'Existing simulated holding lacks a fresh exit quote'; return
    reader.network(); reader.age(row['mint']); info = reader.mint(row['mint']); rent = reader.rent(row['mint'])
    reserve = int(Decimal(str(config['fee_reserve_sol']))*10**9)
    available = state['cash_lamports']-reserve-fee(policy)-rent
    if config['buy_mode'] == 'fixed_usd':
        price = Decimal(str(reader.price()))
        if not price.is_finite() or price <= 0: raise Unavailable('Current SOL dollar price unavailable')
        amount = int((Decimal(str(config['buy_usd'] or 0))/price*10**9).to_integral_value(rounding=ROUND_CEILING))
    else: amount = int(Decimal(max(0, available))*Decimal(str(config['buy_percent'] or 0))/100)
    if not 0 < amount <= available:
        row.update(status='skipped', reason='Buy size does not fit simulated cash after fees, rent and reserve'); return
    minimum, observed = quote_output(quote, state['wallet'], row['mint'], 'buy', amount, policy, clock)
    check_quote(row['event'].get('source_entry'), row['mint'], info['decimals'], amount, minimum)
    # Reads can cross the entry deadline or scheduled pause.
    reason = admission(row['event'], source, state, clock())
    if reason: row.update(status='skipped', reason=reason); return
    from solana_copy_exit_settings import live_stop
    rules = {k: source['config'][k] for k in ('profit_take_enabled', 'profit_take_first_percent', 'stop_loss_cooldown_seconds')}
    rules['stop_loss_percent'] = live_stop(source['config'])
    cost = amount+fee(policy)+rent
    p = {'id': row['id'], 'mint': row['mint'], 'source_wallet': row['source_wallet'],
         'source_at': row['source_at'], 'quantity_raw': minimum, 'cost_lamports': cost,
         'opened_at': clock(), 'stages': 0, 'next_gain_percent': rules['profit_take_first_percent'],
         'protection': rules, 'policy': dict(policy), 'pending_reason': None, 'marked_at': None,
         'exit_value_lamports': None, 'last_exit_attempt_at': 0}
    p['exit_delay_seconds'] = state['delay_model']['exit_seconds']
    state['positions'][row['id']] = p; state['cash_lamports'] -= cost
    row.update(status='open', reason='Simulated entry at minimum quoted output',
               input_lamports=amount, entry_cost_lamports=cost, quantity_raw=minimum,
               entry_quote_at=observed, source_to_quote_seconds=observed-row['source_at'],
               estimated_fee_lamports=fee(policy), estimated_rent_lamports=rent,
               settings={'buy': dict(config), 'policy': dict(policy), 'protection': rules,
                         'paper_revision': source.get('settings_revision')})


def mark_or_sell(row, p, state, quote, clock):
    from solana_real_worker import trailing_profit_trigger
    policy = p['policy']; quantity = p['quantity_raw']; now = clock()
    minimum, at = quote_output(quote, state['wallet'], p['mint'], 'sell', quantity, policy, clock)
    value = max(0, minimum-fee(policy)); p.update(marked_at=at, exit_value_lamports=value)
    rules = p['protection']; gain = 100*(value-p['cost_lamports'])/p['cost_lamports']
    reason = p.get('pending_reason')
    trailing = trailing_profit_trigger(p, value, fee(policy))
    if not reason:
        if rules['stop_loss_percent'] > 0 and gain <= -rules['stop_loss_percent']: reason = 'stop_loss'
        elif trailing: reason = 'profit_protection'
        elif rules['profit_take_enabled'] and gain >= p['next_gain_percent'] and quantity >= 2: reason = 'profit_take_half'
    if reason:
        if not p.get('pending_reason'):
            p['pending_reason'] = reason; p['exit_eligible_at'] = clock()+p['exit_delay_seconds']; return
        if clock() >= p.get('exit_eligible_at', 0): sell(row, p, state, quantity, value, reason, at, now)


def trim(row, p, state, quote, clock):
    quantity = p['quantity_raw']//2
    minimum, at = quote_output(quote, state['wallet'], p['mint'], 'sell', quantity, p['policy'], clock)
    value = max(0, minimum-fee(p['policy'])); cost = p['cost_lamports']*quantity//p['quantity_raw']
    target = int((Decimal(cost)*(100+Decimal(str(p['next_gain_percent'])))/100).to_integral_value(rounding=ROUND_CEILING))
    p['pending_reason'] = None
    p.pop('exit_eligible_at', None)
    if value >= target: sell(row, p, state, quantity, value, 'profit_take_half', at, clock())
    else: row['reason'] = 'Half-sale quote does not meet net profit target'


def sell(row, p, state, quantity, value, reason, at, now):
    cost = p['cost_lamports'] if quantity == p['quantity_raw'] else p['cost_lamports']*quantity//p['quantity_raw']
    pnl = value-cost; state['cash_lamports'] += value; state['realized_lamports'] += pnl
    row['realized_lamports'] += pnl
    row['exits'].append({'quantity_raw': quantity, 'net_lamports': value, 'cost_lamports': cost,
                         'pnl_lamports': pnl, 'reason': reason, 'quote_at': at})
    p['quantity_raw'] -= quantity; p['cost_lamports'] -= cost; p['pending_reason'] = None
    p.pop('exit_eligible_at', None)
    row['reason'] = reason
    if reason == 'profit_take_half': p['stages'] += 1; p['next_gain_percent'] *= 2
    if not p['quantity_raw']:
        row.update(status='closed', closed_at=now)
        if reason in ('stop_loss', 'profit_protection'): state['cooldowns'][p['mint']] = now+p['protection']['stop_loss_cooldown_seconds']
        del state['positions'][p['id']]
    else:
        # Whole-position valuation is invalid after a half sale.
        p.update(marked_at=None, exit_value_lamports=None)


def tick(*, reader=None, quote=build_quote, clock=time.time):
    from solana_real_copy import wallet
    from solana_real_budget import settings
    saved = wallet(); owner = saved['wallet_address']
    if not owner or not saved.get('verified_at') or database._paper_trial_path.get() is not None: return
    reader = reader or Reader(); now = clock(); lease = str(uuid.uuid4()); updates = {}
    with closing(database.get_connection()) as conn:
        names = tables(conn); source = state_row(conn, 'solana_copy_state', names)
        if not source: return
        conn.executescript(SCHEMA)
        raw = conn.execute('SELECT state_json FROM solana_copy_shadow_run WHERE wallet=?', (owner,)).fetchone()
        if not raw:
            # Start prospectively even when real buys are paused. No history replay.
            cash = reader.balance(owner)
            state = {'wallet': owner, 'started_at': now, 'initial_lamports': cash, 'cash_lamports': cash,
                     'positions': {}, 'cooldowns': {}, 'realized_lamports': 0, 'last_cycle': None,
                     'last_error': None, 'last_quote_at': None, 'next_request_at': 0}
            for table, key in (('solana_live_source_events', 'direct_cursor'), ('solana_copy_events', 'paper_cursor')):
                state[key] = conn.execute(f'SELECT COALESCE(MAX(rowid),0) FROM {table}').fetchone()[0] if table in names else 0
            conn.execute('INSERT OR IGNORE INTO solana_copy_shadow_run(wallet,state_json) VALUES(?,?)', (owner, json.dumps(state)))
            conn.commit()
        if conn.execute('UPDATE solana_copy_shadow_run SET lease_owner=?,lease_until=? WHERE wallet=? AND lease_until<=?', (lease, now+120, owner, now)).rowcount != 1: return
        conn.commit()
        state = json.loads(conn.execute('SELECT state_json FROM solana_copy_shadow_run WHERE wallet=?', (owner,)).fetchone()[0])
        model = timing_model(conn, owner, names); state['delay_model'] = model
        updates = ingest(conn, state, source, model, now)
        for raw in conn.execute("SELECT details_json FROM solana_copy_shadow_entries WHERE wallet=? AND status IN ('queued','open') ORDER BY created_at", (owner,)):
            row = json.loads(raw[0]); updates.setdefault(row['id'], row)
        run = state_row(conn, 'solana_real_run', names)
        live_pending = 'solana_real_orders' in names and conn.execute("SELECT 1 FROM solana_real_orders WHERE status IN ('preparing','signed','submitted','unknown') LIMIT 1").fetchone()
    try:
        policy = run.get('policy') or __import__('solana_real_worker').DEFAULT_POLICY
        config = settings()['config']; state['last_error'] = None
        for row in updates.values():
            if row['status'] == 'queued':
                reason = admission(row['event'], source, state, clock())
                if reason: row.update(status='skipped', reason=reason)
        # One research quote per five seconds at most; existing live request
        # priority/cooldown also applies. Never queue behind a pending live order.
        if live_pending: state['last_error'] = 'Live transaction has priority; comparison deferred'
        elif now >= state['next_request_at']:
            positions = sorted((p for p in state['positions'].values()
                                if p.get('pending_reason') and now >= p.get('exit_eligible_at', 0)
                                or not p.get('pending_reason') and now-p.get('last_checked_at', 0) >= 15),
                               key=lambda p: (not bool(p.get('pending_reason')), p.get('last_checked_at', 0), p['opened_at']))
            queued = [r for r in updates.values() if r['status'] == 'queued' and now >= r['eligible_at']]
            selected = positions[0] if positions else None
            row = updates.get(selected['id']) if selected else next(iter(queued), None)
            if row:
                state['next_request_at'] = now+5
                row['quote_attempts'] += 1
                try:
                    if selected:
                        selected['last_checked_at'] = now
                        if selected.get('pending_reason') == 'profit_take_half': trim(row, selected, state, quote, clock)
                        else: mark_or_sell(row, selected, state, quote, clock)
                    else: buy(row, state, source, config, policy, reader, quote, clock)
                    state['last_quote_at'] = clock() if row.get('entry_quote_at') or selected and selected.get('marked_at') else state['last_quote_at']
                except (Deferred, RateLimited):
                    row['reason'] = 'Quote capacity reserved for live execution or provider cooldown'; state['last_error'] = row['reason']
                except (Unavailable, ValueError) as error:
                    # No fabricated fee, proceeds or successful exit on a failed quote.
                    from solana_token_age import AgeUnavailable
                    row['reason'] = str(error) if isinstance(error, (Unavailable, AgeUnavailable)) else 'Comparison input unavailable'
                    row['last_quote_error'] = row['reason']
                    if selected: selected.update(marked_at=None, exit_value_lamports=None)
                    state['last_error'] = row['reason']
        state['last_cycle'] = clock()
    except Exception:
        logging.getLogger(__name__).warning('Realistic comparison deferred; live trading unchanged')
        state['last_error'] = 'Comparison cycle unavailable; missing observations are not fills'
        state['last_cycle'] = clock()
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        changed = conn.execute('UPDATE solana_copy_shadow_run SET state_json=?,lease_owner=NULL,lease_until=0 WHERE wallet=? AND lease_owner=? AND lease_until>?',
                               (json.dumps(state, allow_nan=False), owner, lease, clock())).rowcount
        if not changed: return
        for row in updates.values(): write_entry(conn, owner, row)
        conn.commit()


def public(conn, orders, now=None):
    now = time.time() if now is None else now; names = tables(conn)
    linked = conn.execute('SELECT wallet_address FROM solana_real_copy_wallet WHERE id=1').fetchone() if 'solana_real_copy_wallet' in names else None
    owner = linked[0] if linked else ''
    raw = conn.execute('SELECT state_json FROM solana_copy_shadow_run WHERE wallet=?', (owner,)).fetchone() if 'solana_copy_shadow_run' in names else None
    if not raw: return {'started': False, 'status': 'Waiting for verified wallet and fresh SOL balance', 'basis': BASIS, 'entries': [], 'wallets': []}
    state = json.loads(raw[0])
    rows = [json.loads(r[0]) for r in conn.execute('SELECT details_json FROM solana_copy_shadow_entries WHERE wallet=? ORDER BY created_at DESC', (owner,))]
    source = state_row(conn, 'solana_copy_state', names); names_by_wallet = {t['wallet']: t.get('name') or t['wallet'] for t in source.get('traders', [])}
    original = {}; decisions = {}; actual = {o['id']: o for o in orders if o.get('wallet') == owner}
    if 'solana_copy_closes' in names:
        for raw in conn.execute('SELECT details_json FROM solana_copy_closes'):
            close = json.loads(raw[0])
            if not close.get('voided'): original.setdefault(close.get('lot_id'), []).append(close)
    if 'solana_real_decisions' in names:
        decisions = dict(conn.execute('SELECT source_id,reason FROM solana_real_decisions'))
    entries = []; wallets = {}
    for row in rows:
        buy_order = actual.get('buy:'+row['id']); exits = [o for o in actual.values() if o.get('position_id') == 'buy:'+row['id'] and o.get('side') == 'sell' and o.get('status') == 'confirmed']
        failed = [o for o in actual.values() if (o['id'] == 'buy:'+row['id'] or o.get('position_id') == 'buy:'+row['id']) and o.get('status') == 'failed']
        known = all(type(o.get('realized_pnl_lamports')) is int for o in exits) and all(type((o.get('result') or {}).get('fee_lamports')) is int for o in failed)
        actual_pnl = sum(o['realized_pnl_lamports'] for o in exits)-sum(o['result']['fee_lamports'] for o in failed) if (exits or failed) and known else None
        paper_closes = original.get(row['id'], [])
        actual_status = buy_order['status'] if buy_order else decisions.get(row['id'], 'No recorded live attempt')
        if buy_order and buy_order['status'] == 'confirmed':
            bought = (buy_order.get('result') or {}).get('token_delta')
            sold = [(o.get('result') or {}).get('token_delta') for o in exits]
            actual_status = 'Confirmed buy; closure unknown'
            if type(bought) is int and bought > 0 and all(type(q) is int and q < 0 for q in sold):
                actual_status = 'Closed' if -sum(sold) == bought else 'Partial' if sold else 'Open / no recorded sale'
        entry = {k: row.get(k) for k in ('id', 'mint', 'source_wallet', 'created_at', 'status', 'reason', 'last_quote_error', 'realized_lamports', 'entry_cost_lamports', 'source_to_quote_seconds', 'delay_model', 'quote_attempts')}
        entry.update(source_name=names_by_wallet.get(row['source_wallet'], row['source_wallet']),
                     paper_realized_usd=sum(r['pnl_usd'] for r in paper_closes) if paper_closes else None,
                     paper_status='Closed' if any(not r.get('partial_exit', False) for r in paper_closes) else 'Partial' if paper_closes else 'No recorded exit',
                     actual_realized_lamports=actual_pnl,
                     actual_status=actual_status,
                     actual_exit_reasons=list(dict.fromkeys(o.get('reason') or 'Unknown exit reason' for o in exits)),
                     actual_reason=(buy_order.get('error') or buy_order.get('reason')) if buy_order else decisions.get(row['id']))
        entries.append(entry)
        w = wallets.setdefault(row['source_wallet'], {'name': entry['source_name'], 'signals': 0, 'closed': 0, 'wins': 0, 'open': 0, 'skipped': 0, 'queued': 0, 'realized_lamports': 0})
        w['signals'] += 1; w['closed'] += row['status'] == 'closed'; w['wins'] += row['status'] == 'closed' and row['realized_lamports'] > 0
        w['open'] += row['status'] == 'open'; w['skipped'] += row['status'] == 'skipped'; w['queued'] += row['status'] == 'queued'; w['realized_lamports'] += row['realized_lamports']
    missing = sum(p.get('exit_value_lamports') is None or not 0 <= now-(p.get('marked_at') or 0) <= 60 for p in state['positions'].values())
    equity = None if missing else state['cash_lamports']+sum(p['exit_value_lamports'] for p in state['positions'].values())
    return {'started': True, 'started_at': state['started_at'], 'last_cycle': state['last_cycle'],
            'status': state.get('last_error') or ('Source buys paused; existing simulated exits monitored' if not source.get('config', {}).get('enabled') else 'Collecting forward quotes'),
            'stale': state['last_cycle'] is None or now-state['last_cycle'] > 60,
            'initial_lamports': state['initial_lamports'], 'cash_lamports': state['cash_lamports'],
            'realized_lamports': state['realized_lamports'], 'equity_lamports': equity,
            'net_lamports': equity-state['initial_lamports'] if equity is not None else None,
            'unpriced_positions': missing, 'open_positions': len(state['positions']), 'signals': len(rows),
            'queued': sum(r['status'] == 'queued' for r in rows), 'skipped': sum(r['status'] == 'skipped' for r in rows),
            'delay_model': state.get('delay_model'), 'wallets': sorted(wallets.values(), key=lambda w: w['realized_lamports'], reverse=True),
            'entries': entries[:50], 'basis': BASIS}


async def worker():
    reader = Reader()
    while True:
        try: await asyncio.to_thread(tick, reader=reader)
        except asyncio.CancelledError: raise
        except Exception: logging.getLogger(__name__).warning('Realistic comparison waiting for inputs')
        await asyncio.sleep(5)
