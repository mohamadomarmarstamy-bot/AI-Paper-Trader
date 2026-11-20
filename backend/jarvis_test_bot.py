"""Independent, quote-only Solana paper portfolios and strategy evaluation."""
import asyncio
from contextlib import closing, contextmanager
import json
import logging
import math
import os
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import database
import solana_http
from solana_jupiter import Deferred, RateLimited, _budget
from solana_meme_bot import MAX_SCAN_TOKENS, ReadOnlyProviders, normalize_pair

LOG = logging.getLogger(__name__)
ET = ZoneInfo('America/New_York')
# Native Solana USDC mint. Simulation accounts use its six decimal places.
USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
DOLLAR = 1_000_000
TRADE = 50 * DOLLAR
BUY_COST = TRADE + 250_000  # $0.10 network allowance plus $0.15 estimated account rent
SELL_FEE = 100_000
JARVIS_BUDGET = 20_000 * DOLLAR
FIRST_REVIEW = 1000
REVIEW_EVERY = 500
POLL = 2
DISCOVERY_INTERVAL = 20
EXIT_QUOTES_PER_ENTRY = 2
BACKLOG_EXIT_QUOTES_PER_ENTRY = 6
EXIT_BACKLOG_AGE = 90
NEAR_STOP_POINTS = 5
OLDEST_EXIT_INTERVAL = 3
FINAL_PROFIT_SELL = 5
EXIT_COHORT = 'priority-stop-v1'
DISCOVERY_BATCH = 2 * MAX_SCAN_TOKENS
MARK_MAX_AGE = 90
BOTS = {
    'instant-step': ('Instant Ladder', 'instant', 'step'),
    'instant-trim': ('Instant Trim', 'instant', 'trim'),
    'patient-step': ('Pullback Ladder', 'pullback', 'step'),
    'patient-trim': ('Pullback Trim', 'pullback', 'trim'),
    'jarvis': ('Jarvis Trader', None, None),
}
FIXED = tuple(BOTS)[:-1]
DESCRIPTIONS = {
    'instant': 'Buys at the first available quote after discovery.',
    'pullback': 'Waits for a 3–15% dip from the observed high and a 2% rebound from the low.',
    'step': 'Starts at −10%. Each 10-point gain in the observed peak raises the full exit trigger 10 points.',
    'trim': 'Sells half at +20%, +40%, +80%, and +160%. Sells the full remainder at +320%. Starts with a −10% stop. Each 10-point gain in the observed peak raises the full exit trigger 10 points.',
}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS run (
    id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
    lease_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY, bot TEXT NOT NULL, mint TEXT NOT NULL, opened REAL NOT NULL,
    closed REAL NOT NULL, day TEXT NOT NULL, pnl INTEGER NOT NULL, entry_cost INTEGER NOT NULL,
    data TEXT NOT NULL, UNIQUE(bot,mint)
);
CREATE INDEX IF NOT EXISTS trades_bot_day ON trades(bot,day);
CREATE INDEX IF NOT EXISTS trades_mint ON trades(mint);
'''


def initial():
    return {
        'enabled': False, 'last_cycle': None, 'error': None, 'offset': 0,
        'watch': {}, 'strategy': None, 'reviewed': 0, 'review': None,
        'bots': {bot: {'cash': JARVIS_BUDGET if bot == 'jarvis' else 0,
                       'funding': JARVIS_BUDGET if bot == 'jarvis' else 0,
                       'positions': {}, 'quote_misses': 0, 'entry_misses': 0}
                 for bot in BOTS},
    }


@contextmanager
def connection():
    path = database.DATABASE_PATH.with_name('jarvis_paper.sqlite3')
    with closing(sqlite3.connect(path, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute('INSERT OR IGNORE INTO run(id,state) VALUES (1,?)', (json.dumps(initial()),))
        conn.commit()
        yield conn


def read(conn):
    row = conn.execute('SELECT state,revision FROM run WHERE id=1').fetchone()
    return json.loads(row['state']), row['revision']


def set_enabled(enabled):
    if type(enabled) is not bool:
        raise ValueError('Expected a paper-run toggle.')
    with connection() as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        state, revision = read(conn)
        if state['enabled'] != enabled:
            state['enabled'] = enabled
            state['error'] = None
            state.pop('next_discovery', None)
            if not enabled:
                state['watch'].clear()
            conn.execute('UPDATE run SET state=?,revision=? WHERE id=1', (json.dumps(state), revision + 1))
    return status()


def quote(input_mint, output_mint, amount):
    """Use a minimum Jupiter output without requesting a transaction or supplying a wallet."""
    key = os.getenv('JUPITER_API_KEY')
    headers = {'x-api-key': key} if key else {}
    params = {'inputMint': input_mint, 'outputMint': output_mint, 'amount': str(amount), 'slippageBps': 100}
    with _budget.permit(research=True):
        response = solana_http.get('https://api.jup.ag/swap/v2/order', params=params, headers=headers,
                                   timeout=3, allow_redirects=False)
        if response.status_code == 429:
            _budget.limited(response.headers)
            raise RateLimited('Quote provider rate limited.')
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError('Unexpected quote status.')
        if len(response.content) > 500_000:
            raise ValueError('Quote too large.')
        data = response.json()
    if (data.get('inputMint') != input_mint or data.get('outputMint') != output_mint
            or str(data.get('inAmount')) != str(amount) or data.get('transaction')
            or data.get('swapMode') != 'ExactIn' or data.get('slippageBps') != 100 or not data.get('routePlan')):
        raise ValueError('Missing exact-size quote.')
    expected, minimum = int(data['outAmount']), int(data['otherAmountThreshold'])
    if not 0 < minimum <= expected or minimum < expected * 99 // 100:
        raise ValueError('Invalid quote floor.')
    return minimum


def observe(state, providers, now):
    # Reuse the scanner discovery cache. Rotate fresh candidates and active pullbacks.
    mints = [mint for mint in providers.profiles() if mint != USDC]
    if not mints:
        return
    tracked = [mint for mint, row in sorted(state['watch'].items(), key=lambda item: item[1]['last'])
               if not row['pullback']][:12]
    offset = state['offset'] % len(mints)
    rotated = mints[offset:] + mints[:offset]
    fresh = rotated[:DISCOVERY_BATCH - len(tracked)]
    state['offset'] = (offset + len(fresh)) % len(mints)
    addresses = list(dict.fromkeys(tracked + fresh))
    requested = set(addresses)
    pairs = []
    failed_batches = 0
    for start in range(0, len(addresses), MAX_SCAN_TOKENS):
        try:
            pairs.extend(providers.pairs(addresses[start:start + MAX_SCAN_TOKENS]))
        except (ValueError, OSError, RuntimeError):
            failed_batches += 1
    if failed_batches:
        state['error'] = 'Some discovery data is unavailable.' if pairs else 'Discovery data unavailable.'
    best = {}
    for pair in pairs:
        try:
            mint = pair['baseToken']['address']
            if mint not in requested:
                continue
            row = normalize_pair(pair, mint, now)
            if row['volume_m5_usd'] <= 0 or row['buys_m5'] + row['sells_m5'] == 0:
                continue
            if mint not in best or row['liquidity_usd'] > best[mint]['liquidity_usd']:
                row['symbol'] = str(pair['baseToken'].get('name') or row['symbol'])[:80]
                best[mint] = row
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    for mint, row in best.items():
        old = state['watch'].get(mint)
        if old is None or old['pair'] != row['pair']:
            state['watch'][mint] = {**row, 'first': now, 'last': now, 'high': row['price_usd'],
                                    'dip': None, 'pullback': None}
            continue
        price = row['price_usd']
        old.update(last=now, liquidity_usd=row['liquidity_usd'], volume_m5_usd=row['volume_m5_usd'],
                   age_minutes=row['age_minutes'])
        if price > old['high'] or price < .85 * old['high']:
            old.update(high=price, dip=None)
        elif price <= .97 * old['high']:
            old['dip'] = min(price, old['dip'] or price)
        if old['dip'] is not None and price >= 1.02 * old['dip'] and price <= .995 * old['high']:
            if old['pullback'] is None:
                old['pullback'] = now


def exit_floor(peak):
    return (max(0, math.floor(peak / 10)) - 1) * 10


def open_trade(state, bot, row, quantity, now):
    portfolio = state['bots'][bot]
    if bot != 'jarvis' and portfolio['cash'] < BUY_COST:
        funding = BUY_COST - portfolio['cash']
        portfolio['cash'] += funding
        portfolio['funding'] += funding
    if portfolio['cash'] < BUY_COST:
        return
    portfolio['cash'] -= BUY_COST
    portfolio['positions'][row['mint']] = {
        'mint': row['mint'], 'symbol': row['symbol'], 'opened': now, 'quantity': quantity,
        'entry_cost': BUY_COST, 'cost': BUY_COST, 'banked': 0, 'peak': 0,
        'exit_cohort': EXIT_COHORT,
        'target': 20, 'trims': 0, 'mark': None, 'mark_at': None, 'due': now + POLL,
        'pending': None, 'liquidity': row['liquidity_usd'], 'age': row['age_minutes'],
        'policy': dict(state['strategy']) if bot == 'jarvis' else {'entry': BOTS[bot][1], 'exit': BOTS[bot][2]},
    }


def sell(completed, portfolio, bot, position, quantity, amount, now, reason):
    if quantity == position['quantity']:
        cost = position['cost']
    else:
        cost = position['cost'] * quantity // position['quantity']
    value = max(0, amount - SELL_FEE)
    portfolio['cash'] += value
    position['banked'] += value
    position['quantity'] -= quantity
    position['cost'] -= cost
    position['pending'] = None
    if position['quantity']:
        position['trims'] += 1
        position['target'] *= 2
        position.update(mark=None, mark_at=None, mark_stale=False)
        return
    pnl = position['banked'] - position['entry_cost']
    event = {**position, 'bot': bot, 'closed': now, 'pnl': pnl, 'reason': reason}
    completed.append(event)
    del portfolio['positions'][position['mint']]


def apply_exit(completed, portfolio, bot, p, quote_size, amount, now):
    if quote_size != p['quantity']:
        if p['trims'] >= FINAL_PROFIT_SELL - 1:
            return
        target = p['cost'] * quote_size / p['quantity'] * (1 + p['target'] / 100)
        if amount - SELL_FEE >= target:
            sell(completed, portfolio, bot, p, quote_size, amount, now, 'Took profit · sold half')
        else:
            p['pending'] = None
        return
    value = max(0, amount - SELL_FEE)
    p.update(mark=value, mark_at=now, mark_stale=False, pending=None)
    gain = 100 * (value - p['cost']) / p['cost']
    p['peak'] = max(p['peak'], gain)
    floor = exit_floor(p['peak'])
    if gain <= floor:
        reason = 'Step stop' if floor <= 0 else 'Step profit protection'
        p['stop_gap_points'] = floor - gain
    elif p['policy']['exit'] == 'trim' and gain >= p['target']:
        reason = 'Took profit · fifth sell closed remainder' if p['trims'] >= FINAL_PROFIT_SELL - 1 else None
        if reason is None and p['quantity'] >= 2:
            p['pending'] = 'trim'
    else:
        reason = None
    if reason:
        p['exit_quote_wait'] = max(0, now - quote_due_since(p))
        sell(completed, portfolio, bot, p, quote_size, amount, now, reason)


def review_size(reviewed):
    return REVIEW_EVERY if reviewed else FIRST_REVIEW


def evaluate(conn, state):
    total = conn.execute("SELECT count(*) FROM trades WHERE bot!='jarvis'").fetchone()[0]
    while True:
        start = state['reviewed']
        batch_size = review_size(start)
        if total < start + batch_size:
            return
        rows = conn.execute("SELECT data FROM trades WHERE bot!='jarvis' ORDER BY id LIMIT ? OFFSET ?",
                            (batch_size, start)).fetchall()
        trades = [json.loads(row['data']) for row in rows]
        # Keep a mint together across variants and hold back the newest mints.
        ordered_mints = list(dict.fromkeys(trade['mint'] for trade in trades))
        cutoff = int(.7 * len(ordered_mints))
        training = set(ordered_mints[:cutoff])
        candidates = []
        for bot in FIXED:
            subset = [trade for trade in trades if trade['bot'] == bot]
            for liquidity in (0, 25_000, 100_000, 250_000):
                for min_age in (0, 30, 120):
                    selected = [trade for trade in subset
                                if trade['liquidity'] >= liquidity and trade['age'] >= min_age]
                    fit = [trade['pnl'] for trade in selected if trade['mint'] in training]
                    check = [trade['pnl'] for trade in selected if trade['mint'] not in training]
                    if len(fit) >= 20 and len(check) >= 10:
                        # Remove the largest winner before scoring a candidate.
                        fit_score = (sum(fit) - max(fit)) / (len(fit)-1)
                        check_score = (sum(check) - max(check)) / (len(check)-1)
                        candidates.append((fit_score, check_score, bot, liquidity, min_age, len(check)))
        # Select on training only, then evaluate that single choice on the holdout.
        best = max(candidates, key=lambda row: (row[0], row[5]), default=None)
        state['reviewed'] += batch_size
        state['review'] = {'closed_reviewed': state['reviewed'], 'distinct_mints': len(ordered_mints),
                           'result': 'No rules earned a positive score on both samples.'}
        if best and best[0] > 0 and best[1] > 0:
            _, validation, bot, liquidity, age, samples = best
            state['strategy'] = {'version': state['reviewed'], 'source': bot, 'entry': BOTS[bot][1],
                                 'exit': BOTS[bot][2], 'min_liquidity': liquidity, 'min_age': age,
                                 'validation_pnl': validation, 'validation_count': samples}
            state['review']['result'] = 'Strategy adopted for new Jarvis entries.'
        elif state['strategy']:
            state['review']['result'] = 'No new rules passed validation. Jarvis kept its current strategy.'


def entry_jobs(conn, state, now):
    possible = {}
    for mint, row in state['watch'].items():
        if now - row['last'] > 90 or now < row.get('retry', 0):
            continue
        closed = {r['bot'] for r in conn.execute('SELECT bot FROM trades WHERE mint=?', (mint,))}
        for bot, portfolio in state['bots'].items():
            if bot in closed or mint in portfolio['positions']:
                continue
            if bot == 'jarvis':
                strategy = state['strategy']
                if (not strategy or portfolio['cash'] < BUY_COST or row['first'] < strategy['activated_at']
                        or row['liquidity_usd'] < strategy['min_liquidity'] or row['age_minutes'] < strategy['min_age']):
                    continue
                entry = strategy['entry']
            else:
                entry = BOTS[bot][1]
            signal = row['first'] if entry == 'instant' else row['pullback']
            if signal is not None and 0 <= now - signal <= 90:
                possible.setdefault(mint, []).append(bot)
    return possible


def quote_jobs(state, now):
    jobs = {}
    for bot, portfolio in state['bots'].items():
        for mint, p in portfolio['positions'].items():
            if p['due'] <= now:
                partial = p['pending'] == 'trim' and p['trims'] < FINAL_PROFIT_SELL - 1 and p['quantity'] >= 2
                quantity = p['quantity'] // 2 if partial else p['quantity']
                jobs.setdefault((mint, quantity), []).append((bot, p))
    return jobs


def quote_due_since(position):
    return position.get('quote_due_since', position['due'])


def exit_queue(jobs, now):
    oldest = max((now - quote_due_since(p) for group in jobs.values() for _, p in group), default=0)
    return {'due': len(jobs), 'oldest_wait': max(0, oldest), 'backed_up': oldest >= EXIT_BACKLOG_AGE}


def stop_distance(position):
    mark = position['mark']
    if mark is None:
        return math.inf
    gain = 100 * (mark - position['cost']) / position['cost']
    return gain - exit_floor(position['peak'])


def next_exit(jobs, attempts):
    def oldest(job):
        return min(quote_due_since(p) for _, p in jobs[job])

    if attempts % OLDEST_EXIT_INTERVAL:
        urgent = [job for job, group in jobs.items()
                  if any(stop_distance(p) <= NEAR_STOP_POINTS for _, p in group)]
        if urgent:
            return min(urgent, key=lambda job: (min(stop_distance(p) for _, p in jobs[job]), oldest(job)))
    return min(jobs, key=oldest)


def step(conn, state, providers, get_quote, now):
    completed = []
    if state['enabled']:
        state['watch'] = {mint: row for mint, row in state['watch'].items() if now - row['first'] <= 900}
        if now >= state.get('next_discovery', 0):
            state['next_discovery'] = now + DISCOVERY_INTERVAL
            try:
                observe(state, providers, now)
            except (ValueError, OSError, RuntimeError):
                state['error'] = 'Discovery data unavailable.'
    exits = quote_jobs(state, now)
    entries = entry_jobs(conn, state, now) if state['enabled'] else {}
    # Keep new entries moving at a reduced rate during sustained exit pressure.
    # Provider deferrals do not spend an attempt in either queue.
    exit_attempts = state.get('exit_attempts_since_entry', 0)
    queued = exit_queue(exits, now)
    exit_budget = BACKLOG_EXIT_QUOTES_PER_ENTRY if queued['backed_up'] else EXIT_QUOTES_PER_ENTRY
    buy = entries and (not exits or exit_attempts >= exit_budget)
    if buy:
        mint = min(entries, key=lambda mint: state['watch'][mint]['first'])
        row = state['watch'][mint]
        try:
            quantity = get_quote(USDC, mint, TRADE)
        except (Deferred, RateLimited):
            state['error'] = 'Quote capacity temporarily unavailable.'
        except Exception:
            state['exit_attempts_since_entry'] = 0
            row['retry'] = now + 30
            for bot in entries[mint]:
                state['bots'][bot]['entry_misses'] += 1
            state['error'] = 'Entry route unavailable for a candidate.'
        else:
            state['exit_attempts_since_entry'] = 0
            for bot in entries[mint]:
                open_trade(state, bot, row, quantity, now)
    elif exits:
        attempts = state.get('exit_quote_attempts', 0)
        job = next_exit(exits, attempts)
        mint, size = job
        group = exits[job]
        try:
            amount = get_quote(mint, USDC, size)
        except (Deferred, RateLimited):
            state['error'] = 'Quote capacity temporarily unavailable.'
        except Exception:
            state['exit_quote_attempts'] = attempts + 1
            state['exit_attempts_since_entry'] = min(exit_attempts + 1, BACKLOG_EXIT_QUOTES_PER_ENTRY)
            for bot, p in group:
                p.setdefault('quote_due_since', p['due'])
                p.update(due=now + 45, mark_stale=True, pending=None)
                state['bots'][bot]['quote_misses'] += 1
            state['error'] = 'An exit route is unavailable; holding remains open.'
        else:
            state['exit_quote_attempts'] = attempts + 1
            state['exit_attempts_since_entry'] = min(exit_attempts + 1, BACKLOG_EXIT_QUOTES_PER_ENTRY)
            for bot, p in group:
                was_pending = p['pending']
                apply_exit(completed, state['bots'][bot], bot, p, size, amount, now)
                p.pop('quote_due_since', None)
                p['due'] = now + (POLL if was_pending or p['pending'] else 15)
    state['last_cycle'] = now
    return completed


def tick(*, providers=None, get_quote=quote, clock=time.time):
    now = clock()
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        state, revision = read(conn)
        if not state['enabled'] and not any(bot['positions'] for bot in state['bots'].values()):
            conn.rollback()
            return
        lease = now + 45
        if not conn.execute('UPDATE run SET lease_until=? WHERE id=1 AND lease_until<=?', (lease, now)).rowcount:
            conn.rollback()
            return
        conn.commit()
        try:
            state['error'] = None
            completed = step(conn, state, providers or ReadOnlyProviders(), get_quote, now)
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                current = conn.execute('SELECT revision,lease_until FROM run WHERE id=1').fetchone()
                if current['revision'] != revision or current['lease_until'] != lease or clock() >= lease:
                    return
                for trade in completed:
                    day = datetime.fromtimestamp(trade['closed'], ET).date().isoformat()
                    conn.execute(
                        'INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',
                        (trade['bot'], trade['mint'], trade['opened'], trade['closed'], day,
                         trade['pnl'], trade['entry_cost'], json.dumps(trade)),
                    )
                old_version = (state['strategy'] or {}).get('version')
                if completed:
                    evaluate(conn, state)
                if state['strategy'] and state['strategy']['version'] != old_version:
                    state['strategy']['activated_at'] = clock()
                conn.execute('UPDATE run SET state=?,revision=revision+1 WHERE id=1', (json.dumps(state),))
        finally:
            with conn:
                conn.execute('UPDATE run SET lease_until=0 WHERE id=1 AND lease_until=?', (lease,))


def stats(row):
    return {'closed': row['closed'], 'wins': row['wins'], 'losses': row['losses'], 'realized': row['pnl'] / DOLLAR}


def holding_value(position, now):
    # Use the last exact-size quote if available. A trim invalidates that quote
    # and the remaining cost is only an estimate until the next full mark.
    amount = position.get('mark')
    at = position.get('mark_at')
    if amount is None:
        return position['cost'], 'cost'
    if at is None or position.get('mark_stale') or not 0 <= now - at <= MARK_MAX_AGE:
        return amount, 'last_quote'
    return amount, 'current_quote'


def status():
    now = time.time()
    day = datetime.fromtimestamp(now, ET).date().isoformat()
    totals_sql = ("SELECT count(*) AS closed, count(CASE WHEN pnl>0 THEN 1 END) AS wins, "
                  "count(CASE WHEN pnl<0 THEN 1 END) AS losses, COALESCE(sum(pnl),0) AS pnl "
                  "FROM trades WHERE bot=?")
    with connection() as conn:
        conn.execute('BEGIN')
        state, _ = read(conn)
        reviewed = {row['bot']: dict(row) for row in conn.execute(
            'SELECT bot, count(*) AS closed, sum(pnl) AS pnl, '
            "avg(json_extract(data,'$.exit_quote_wait')) AS quote_wait, "
            "avg(json_extract(data,'$.stop_gap_points')) AS stop_gap, "
            "count(json_extract(data,'$.stop_gap_points')) AS stop_exits "
            "FROM trades WHERE json_extract(data,'$.exit_cohort')=? GROUP BY bot",
            (EXIT_COHORT,),
        )}
        rows = []
        for bot, (name, entry, exit_rule) in BOTS.items():
            portfolio = state['bots'][bot]
            positions = list(portfolio['positions'].values())
            valuations = [holding_value(p, now) for p in positions]
            cost_count = sum(basis == 'cost' for _, basis in valuations)
            stale_count = sum(basis == 'last_quote' for _, basis in valuations)
            missing = cost_count + stale_count
            estimate = portfolio['cash'] + sum(value for value, _ in valuations)
            if bot == 'jarvis' and state['strategy']:
                entry, exit_rule = (state['strategy'][key] for key in ('entry', 'exit'))
            description = f'{DESCRIPTIONS[entry]} {DESCRIPTIONS[exit_rule]}' if entry else (
                'Waiting for a validated strategy learned from the four fixed bots.')
            sample = reviewed.get(bot)
            rows.append({
                'exit_sample': {
                    'open': sum(p.get('exit_cohort') == EXIT_COHORT for p in positions),
                    'closed': sample['closed'] if sample else 0,
                    'pnl': sample['pnl'] / DOLLAR if sample else 0,
                    'quote_wait': sample['quote_wait'] if sample else None,
                    'stop_gap': sample['stop_gap'] if sample else None,
                    'stop_exits': sample['stop_exits'] if sample else 0,
                },
                'id': bot, 'name': name, 'description': description,
                **stats(conn.execute(totals_sql, (bot,)).fetchone()),
                'today': stats(conn.execute(totals_sql + ' AND day=?', (bot, day)).fetchone()),
                'cash': portfolio['cash'] / DOLLAR, 'funding': portfolio['funding'] / DOLLAR,
                'balance': estimate / DOLLAR if not missing else None,
                'pnl': (estimate - portfolio['funding']) / DOLLAR if not missing else None,
                'estimated_balance': estimate / DOLLAR,
                'estimated_pnl': (estimate - portfolio['funding']) / DOLLAR,
                'valuation': {'at_cost': cost_count, 'last_quote': stale_count},
                'open': len(positions), 'unpriced': missing, 'quote_misses': portfolio['quote_misses'],
                'entry_misses': portfolio['entry_misses'],
                'holdings': [{'mint': p['mint'], 'symbol': p['symbol'], 'opened': p['opened'],
                              'mark': value / DOLLAR if basis == 'current_quote' else None,
                              'estimated_value': value / DOLLAR, 'valuation_basis': basis}
                             for p, (value, basis) in zip(positions, valuations)],
            })
    return {'enabled': state['enabled'], 'last_cycle': state['last_cycle'], 'error': state['error'], 'day': day,
            'exit_queue': exit_queue(quote_jobs(state, now), now),
            'bots': rows, 'strategy': state['strategy'], 'review': state['review'], 'reviewed': state['reviewed'],
            'review_every': review_size(state['reviewed']), 'watching': len(state['watch'])}


def history(jarvis, before=None):
    limit = 25
    condition = "bot='jarvis'" if jarvis else "bot!='jarvis'"
    with connection() as conn:
        groups = conn.execute(
            f"""SELECT mint, MAX(id) AS last_id FROM trades WHERE {condition}
                GROUP BY mint HAVING MAX(id) < ? ORDER BY last_id DESC LIMIT ?""",
            (before if before is not None else 2**63-1, limit + 1),
        ).fetchall()
        output = []
        for group in groups[:limit]:
            trades = [json.loads(row['data']) for row in conn.execute(
                f'SELECT data FROM trades WHERE {condition} AND mint=? ORDER BY id DESC', (group['mint'],))]
            output.append({'mint': group['mint'], 'symbol': trades[0]['symbol'], 'trades': [
                {'bot': BOTS[t['bot']][0], 'pnl': t['pnl'] / DOLLAR, 'cost': t['entry_cost'] / DOLLAR,
                 'proceeds': t['banked'] / DOLLAR, 'reason': t['reason'], 'closed': t['closed'],
                 'held_seconds': max(0, int(t['closed'] - t['opened']))}
                for t in trades
            ]})
    return {'coins': output, 'next': groups[limit-1]['last_id'] if len(groups) > limit else None}


async def worker():
    while True:
        started = time.monotonic()
        try:
            await asyncio.to_thread(tick)
        except Exception:
            LOG.exception('Jarvis paper run tick failed')
        await asyncio.sleep(max(.1, POLL - (time.monotonic() - started)))
