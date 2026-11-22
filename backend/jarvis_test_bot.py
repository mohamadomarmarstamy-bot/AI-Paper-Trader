"""Independent, quote-only Solana paper portfolios and strategy evaluation."""
import asyncio
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import math
import os
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

import database
import jarvis_experiments as experiments
import jarvis_outcomes as outcomes
import jarvis_research as research
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
EARLY_REVIEW_MIN = 500
EARLY_REVIEW_INTERVAL = 25
REVIEW_EVERY = 500
POLL = 2
WORKER_INTERVAL = 1.1  # Fill available default quote slots; shared provider pacing still applies.
DISCOVERY_INTERVAL = 10
EXIT_QUOTES_PER_ENTRY = 2
BACKLOG_EXIT_QUOTES_PER_ENTRY = 6
EXIT_BACKLOG_AGE = 90
NEAR_STOP_POINTS = 5
OLDEST_EXIT_INTERVAL = 3
EXIT_RETRY_INTERVAL = 6
EXIT_RETRY_LIMIT = 900
FINAL_PROFIT_SELL = 5
EXIT_COHORT = 'priority-stop-v1'
DISCOVERY_BATCH = 5 * MAX_SCAN_TOKENS
ENTRY_WINDOW = 900
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
        conn.executescript(SCHEMA + research.SCHEMA + experiments.SCHEMA + outcomes.SCHEMA)
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


class QuoteUnavailable(ValueError):
    def __init__(self, message, category='invalid_quote'):
        super().__init__(message)
        self.category = category


def quote_error(response, data):
    # Only fixed diagnostic labels are persisted; never echo arbitrary provider
    # bodies, credentials, HTML or transaction data into logs/the dashboard.
    status = response.status_code
    message = str(data.get('error', '')).lower().replace('_', ' ') if isinstance(data, dict) else ''
    if status in (401, 403):
        return QuoteUnavailable('Jupiter API access denied; check key or plan.', 'access')
    if status >= 500 or status in (408, 425):
        return QuoteUnavailable('Jupiter temporarily unavailable.', 'temporary')
    if status == 400 and any(text in message for text in (
            'failed to get quotes', 'could not find any route', 'no route', 'not tradable')):
        return QuoteUnavailable('Jupiter has no quote for this token and exact amount.', 'no_route')
    return QuoteUnavailable(f'Jupiter rejected the quote request (HTTP {status}).', 'request')


def quote_uint(value, *, allow_zero=False):
    if type(value) is int:
        result = value
    elif isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 20:
        result = int(value)
    else:
        raise QuoteUnavailable('Jupiter returned invalid quote amounts.')
    if not (0 if allow_zero else 1) <= result <= 2**64 - 1:
        raise QuoteUnavailable('Jupiter returned invalid quote amounts.')
    return result


def quote(input_mint, output_mint, amount):
    """Use a minimum Jupiter output without requesting a transaction or supplying a wallet."""
    key = os.getenv('JUPITER_API_KEY')
    headers = {'x-api-key': key} if key else {}
    params = {'inputMint': input_mint, 'outputMint': output_mint, 'amount': str(amount), 'slippageBps': 100}
    try:
        with _budget.permit(research=True):
            response = solana_http.get('https://api.jup.ag/swap/v2/order', params=params, headers=headers,
                                       timeout=8, allow_redirects=False)
            if response.status_code == 429:
                _budget.limited(response.headers)
                raise RateLimited('Quote provider rate limited.')
            if len(response.content) > 500_000:
                raise QuoteUnavailable('Jupiter quote response too large.')
            try:
                data = response.json()
            except ValueError:
                if response.status_code != 200:
                    raise quote_error(response, {}) from None
                raise QuoteUnavailable('Jupiter returned invalid quote JSON.', 'temporary') from None
            if response.status_code != 200:
                raise quote_error(response, data)
    except requests.RequestException:
        raise QuoteUnavailable('Jupiter quote connection failed; retry pending.', 'temporary') from None
    if not isinstance(data, dict):
        raise QuoteUnavailable('Jupiter returned an invalid quote response.')
    if data.get('error') or data.get('errorCode') is not None:
        raise QuoteUnavailable('Jupiter returned a quote error.', 'request')
    if not isinstance(data.get('routePlan'), list) or not data['routePlan']:
        raise QuoteUnavailable('Jupiter returned no route', 'no_route')
    if (data.get('inputMint') != input_mint or data.get('outputMint') != output_mint
            or quote_uint(data.get('inAmount')) != amount or data.get('transaction')
            or data.get('taker') or data.get('swapMode') != 'ExactIn'):
        raise QuoteUnavailable('Jupiter returned an invalid exact-size quote')
    slippage = quote_uint(data.get('slippageBps'), allow_zero=True)
    if slippage > 100:
        raise QuoteUnavailable('Jupiter quote exceeds the 1% slippage limit.')
    expected = quote_uint(data.get('outAmount'))
    minimum = quote_uint(data.get('otherAmountThreshold'))
    # A tighter quote (including JupiterZ's zero-slippage RFQ) is valid.
    # Its floor must still match its own tolerance and our 1% maximum.
    if not 0 < minimum <= expected or minimum < expected * (10000 - slippage) // 10000:
        raise QuoteUnavailable('Jupiter returned an invalid quote floor')
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
    batches = [addresses[start:start + MAX_SCAN_TOKENS]
               for start in range(0, len(addresses), MAX_SCAN_TOKENS)]
    # Independent read-only batches overlap, keeping discovery from serially
    # blocking the next exit/entry quote. Consume results in discovery order.
    with ThreadPoolExecutor(max_workers=5) as pool:
        pending = [pool.submit(providers.pairs, batch) for batch in batches]
        for future in pending:
            try:
                pairs.extend(future.result())
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
        old.update(last=now, buys_m5=row['buys_m5'], sells_m5=row['sells_m5'],
                   liquidity_usd=row['liquidity_usd'], volume_m5_usd=row['volume_m5_usd'], age_minutes=row['age_minutes'])
        if price > old['high'] or price < .85 * old['high']:
            old.update(high=price, dip=None)
        elif price <= .97 * old['high']:
            old['dip'] = min(price, old['dip'] or price)
        if old['dip'] is not None and price >= 1.02 * old['dip'] and price <= .995 * old['high']:
            if old['pullback'] is None:
                old['pullback'] = now


def exit_floor(peak):
    return (max(0, math.floor(peak / 10)) - 1) * 10


def open_trade(state, bot, row, quantity, now, evidence=None):
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
        'exit_cohort': EXIT_COHORT, 'entry_evidence': evidence or {},
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
    if p['trims'] == 0 and 'first_sell_quote' not in p:
        p['first_sell_quote'] = {'net_usdc': value, 'seconds_after_entry': now - p['opened']}
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
        best, mint_count = review_candidate(trades)
        state['reviewed'] += batch_size
        state['review'] = {'closed_reviewed': state['reviewed'], 'distinct_mints': mint_count,
                           'result': 'No rules earned a positive score on both samples.'}
        if best and best[0] > 0 and best[1] is not None and best[1] > 0:
            _, validation, bot, liquidity, age, samples, rule, avoid = best
            settings = {'source': bot, 'entry': BOTS[bot][1], 'exit': BOTS[bot][2],
                        'min_liquidity': liquidity, 'min_age': age, 'entry_filter': rule, 'avoid_pattern': avoid}
            current = {'entry_filter': 'baseline', 'avoid_pattern': None, **(state['strategy'] or {})}
            if state.get('entry_guard') and all(current.get(key) == value for key, value in settings.items()):
                state['review']['result'] = 'The paused strategy led this review. Entries remain paused.'
            else:
                state['strategy'] = {**settings, 'version': state['reviewed'],
                                     'validation_pnl': validation, 'validation_count': samples}
                state['review']['result'] = 'Strategy adopted for new Jarvis entries.'
        elif state['strategy']:
            state['review']['result'] = 'No new rules passed validation. Jarvis kept its current strategy.'
        proposal = research.recommendation([t for t in trades if 'entry_evidence' in t])
        if proposal:
            conn.execute('INSERT OR IGNORE INTO research_mail(version, body, due) VALUES (?,?,?)',
                         (state['reviewed'], f"Jarvis paper review {state['reviewed']}\n\n{state['review']['result']}\n\n{proposal}", 0))


def review_candidate(trades):
    # Keep a mint together across variants and hold out the latest entries.
    ordered_mints = list(dict.fromkeys(trade['mint'] for trade in sorted(trades, key=lambda item: item.get('opened', 0))))
    cutoff = int(.7 * len(ordered_mints))
    training = set(ordered_mints[:cutoff])
    candidates = []
    for bot in FIXED:
        subset = [trade for trade in trades if trade['bot'] == bot]
        for liquidity in (0, 25_000, 100_000, 250_000):
            for min_age in (0, 30, 120):
                selected = [trade for trade in subset
                            if trade['liquidity'] >= liquidity and trade['age'] >= min_age]
                fit = [trade for trade in selected if trade['mint'] in training]
                check = [trade for trade in selected if trade['mint'] not in training]
                for rule in research.RULES:
                    fit_score = score_rule(fit, rule, 20)
                    check_score = score_rule(check, rule, 10)
                    if fit_score is not None:
                        candidates.append((fit_score, check_score, bot, liquidity, min_age, len(check), rule, None))
                        for avoid in outcomes.WARNINGS:
                            if avoid == 'failed_breakout' and rule not in ('baseline', 'social_confirmed'):
                                continue
                            warning_score = score_rule(fit, rule, 20, avoid=avoid)
                            if warning_score is not None:
                                warning_check = score_rule(check, rule, 10, avoid=avoid)
                                candidates.append((warning_score, warning_check, bot, liquidity, min_age, len(check), rule, avoid))
    # Pick a rule on training data only. Keep a mint together across variants.
    best = max(candidates, key=lambda row: (row[0], row[-1] is None), default=None)
    return best, len(ordered_mints)


def refresh_early_review(conn, state):
    if state['reviewed']:
        return
    last = (state.get('early_review') or {}).get('closed', 0)
    count = conn.execute("SELECT count(*) FROM trades WHERE bot!='jarvis'").fetchone()[0]
    if count < EARLY_REVIEW_MIN or count >= FIRST_REVIEW or count < last + EARLY_REVIEW_INTERVAL:
        return
    trades = [json.loads(row['data']) for row in conn.execute(
        "SELECT data FROM trades WHERE bot!='jarvis' ORDER BY id LIMIT ?", (count,))]
    best, mint_count = review_candidate(trades)
    bots = {}
    for bot in FIXED:
        sample = [t for t in trades if t['bot'] == bot]
        pnl = [trade['pnl'] for trade in sample]
        net = sum(pnl)
        bots[bot] = {
            'name': BOTS[bot][0], 'closed': len(sample), 'net': net / DOLLAR,
            'per_trade': net / (len(sample) * DOLLAR) if sample else None,
        }
    chart = sum(bool((t.get('entry_evidence') or {}).get('chart')) for t in trades)
    social = sum(any(key in (t.get('entry_evidence') or {}) for key in ('x', 'bluesky')) for t in trades)
    result = ('The training leader did not pass the held-out coin test.' if best else
              'Too few comparable closes to select a training candidate.')
    candidate = None
    if best and best[0] > 0 and best[1] is not None and best[1] > 0:
        fit, holdout, bot, liquidity, age, _, rule, avoid = best
        candidate = {'name': BOTS[bot][0], 'min_liquidity': liquidity, 'min_age': age,
                     'entry_filter': rule, 'avoid_pattern': outcomes.WARNINGS.get(avoid), 'training': fit / DOLLAR, 'holdout': holdout / DOLLAR}
        result = 'A candidate passed this early review. Jarvis can reconsider it at the first full review.'
    state['early_review'] = {'closed': count, 'mints': mint_count, 'chart': chart, 'social': social,
                             'bots': bots, 'candidate': candidate, 'result': result}


def refresh_outcome_review(conn, state):
    count = conn.execute("SELECT COUNT(*) FROM trades WHERE bot!='jarvis'").fetchone()[0]
    previous = state.get('outcome_review')
    if previous and count < previous['closed'] + 25:
        return
    recent = conn.execute("SELECT data FROM trades WHERE bot!='jarvis' ORDER BY id DESC LIMIT 1000").fetchall()
    trades = [json.loads(row['data']) for row in reversed(recent)]
    state['outcome_review'] = {'closed': count, **outcomes.collection(trades)}


def score_rule(trades, rule, minimum, *, avoid=None):
    eligible = [t for t in trades if research.allows(rule, t.get('entry_evidence') or {})]
    if avoid:
        blocked, eligible = outcomes.split(avoid, eligible)
        # Both losers avoided and winners sacrificed count at their original net P/L.
        if len(blocked) < minimum // 2 or sum(t['pnl'] for t in blocked) >= 0:
            return None
    pnl = [t['pnl'] for t in eligible]
    if len(pnl) < minimum:
        return None
    # Skipped opportunities count as zero. Remove the biggest observed winner.
    score = (sum(pnl) - max(0, max(pnl))) / (len(trades) - 1)
    if rule != 'baseline' or avoid:
        baseline = score_rule(trades, 'baseline', minimum)
        if score <= baseline:
            return None
    return score


def entry_evidence(conn, row, now):
    found = conn.execute('SELECT evidence FROM research WHERE mint=? AND pair=?',
                         (row['mint'], row.get('pair'))).fetchone()
    if not found:
        return {}
    evidence = {key: record['value'] for key, record in json.loads(found['evidence']).items()
                if record['value'] is not None and 0 <= now - record['at'] <= research.FRESH_SECONDS}
    if 'chart' in evidence and not 0 <= now - evidence['chart']['closed_at'] <= research.FRESH_SECONDS:
        del evidence['chart']
    return evidence


def opportunity_evidence(conn, row, now):
    evidence = entry_evidence(conn, row, now)
    evidence['market'] = {key: row.get(key) for key in ('volume_m5_usd', 'buys_m5', 'sells_m5', 'liquidity_usd')}
    return evidence


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
                if (state.get('entry_guard') or not strategy or portfolio['cash'] < BUY_COST or row['first'] < strategy['activated_at']
                        or row['liquidity_usd'] < strategy['min_liquidity'] or row['age_minutes'] < strategy['min_age']):
                    continue
                rule = strategy.get('entry_filter', 'baseline')
                avoid = strategy.get('avoid_pattern')
                if rule != 'baseline' or avoid:
                    evidence = opportunity_evidence(conn, row, now)
                    if not research.allows(rule, evidence) or outcomes.warning(
                            avoid, evidence, row['liquidity_usd'], row['age_minutes']):
                        continue
                entry = strategy['entry']
            else:
                entry = BOTS[bot][1]
            signal = row['first'] if entry == 'instant' else row['pullback']
            window = ENTRY_WINDOW if entry == 'instant' else 90
            if signal is not None and 0 <= now - signal <= window:
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


def retry_group(group):
    return all(p.get('mark_stale') and 'quote_due_since' in p for _, p in group)


def exit_queue(jobs, now):
    regular = [group for group in jobs.values() if not retry_group(group)]
    oldest = max((now - p['due'] for group in regular for _, p in group), default=0)
    return {'due': len(jobs), 'oldest_wait': max(0, oldest), 'backed_up': oldest >= EXIT_BACKLOG_AGE,
            'retry_due': len(jobs) - len(regular)}


def stop_distance(position):
    mark = position['mark']
    if mark is None:
        return math.inf
    gain = 100 * (mark - position['cost']) / position['cost']
    return gain - exit_floor(position['peak'])


def next_exit(jobs, attempts):
    regular = {job: group for job, group in jobs.items() if not retry_group(group)}
    retries = {job: group for job, group in jobs.items() if job not in regular}
    if regular and retries:
        jobs = retries if (attempts + 1) % EXIT_RETRY_INTERVAL == 0 else regular

    def oldest(job):
        return min(quote_due_since(p) for _, p in jobs[job])

    if attempts % OLDEST_EXIT_INTERVAL:
        urgent = [job for job, group in jobs.items()
                  if not retry_group(group) and any(stop_distance(p) <= NEAR_STOP_POINTS for _, p in group)]
        if urgent:
            return min(urgent, key=lambda job: (min(stop_distance(p) for _, p in jobs[job]), oldest(job)))
    return min(jobs, key=oldest)


def record_quote_failure(state, side, error, now, mint=None, amount=None):
    failure = str(error) if isinstance(error, QuoteUnavailable) else type(error).__name__
    key = 'last_' + side + '_quote_failure'
    last = state.get(key, {})
    if last.get('reason') != failure or now - last.get('at', 0) >= 120:
        LOG.warning('Jarvis %s quote failed: %s', side, failure)
        state[key] = {'reason': failure, 'at': now, 'category': getattr(error, 'category', 'unknown'),
                      'mint': mint, 'amount': amount}


def step(conn, state, providers, get_quote, now):
    completed = []
    activity = state.setdefault('quote_activity', {'since': now, 'entry_quotes': 0, 'paper_entries': 0,
                                                  'exit_quotes': 0, 'deferrals': 0})
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
            activity['deferrals'] += 1
            state['error'] = 'Quote capacity temporarily unavailable.'
        except Exception as error:
            record_quote_failure(state, 'entry', error, now, mint, TRADE)
            state['exit_attempts_since_entry'] = 0
            row['retry'] = now + 30
            for bot in entries[mint]:
                state['bots'][bot]['entry_misses'] += 1
            state['error'] = 'Entry route unavailable for a candidate.'
        else:
            state['exit_attempts_since_entry'] = 0
            evidence = opportunity_evidence(conn, row, now)
            activity['entry_quotes'] += 1
            for bot in entries[mint]:
                open_trade(state, bot, row, quantity, now, evidence)
                activity['paper_entries'] += 1
    elif exits:
        attempts = state.get('exit_quote_attempts', 0)
        job = next_exit(exits, attempts)
        mint, size = job
        group = exits[job]
        try:
            amount = get_quote(mint, USDC, size)
        except (Deferred, RateLimited):
            activity['deferrals'] += 1
            state['error'] = 'Quote capacity temporarily unavailable.'
        except Exception as error:
            record_quote_failure(state, 'exit', error, now, mint, size)
            state['exit_quote_attempts'] = attempts + 1
            state['exit_attempts_since_entry'] = min(exit_attempts + 1, BACKLOG_EXIT_QUOTES_PER_ENTRY)
            for bot, p in group:
                p.setdefault('quote_due_since', p['due'])
                # Existing failed routes start at the second interval after upgrade.
                failures = min(6, p.get('route_failures', int(p.get('mark_stale', False))) + 1)
                temporary = getattr(error, 'category', None) == 'temporary'
                delay = min(30 if temporary else EXIT_RETRY_LIMIT,
                            (6 if temporary else 45) * 2 ** (failures - 1))
                p['route_failure'] = {'category': getattr(error, 'category', 'unknown'),
                                      'reason': str(error) if isinstance(error, QuoteUnavailable) else type(error).__name__}
                p.update(due=now + delay, mark_stale=True, pending=None, route_failures=failures)
                state['bots'][bot]['quote_misses'] += 1
            state['error'] = 'An exit route is unavailable; holding remains open.'
        else:
            state['exit_quote_attempts'] = attempts + 1
            state['exit_attempts_since_entry'] = min(exit_attempts + 1, BACKLOG_EXIT_QUOTES_PER_ENTRY)
            activity['exit_quotes'] += 1
            for bot, p in group:
                was_pending = p['pending']
                apply_exit(completed, state['bots'][bot], bot, p, size, amount, now)
                p.pop('quote_due_since', None)
                p.pop('route_failures', None)
                p.pop('route_failure', None)
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
                    guard_entries(conn, state)
                if completed or 'outcome_review' not in state:
                    refresh_outcome_review(conn, state)
                if completed or not state.get('early_review_initialized'):
                    refresh_early_review(conn, state)
                    state['early_review_initialized'] = True
                if state['strategy'] and state['strategy']['version'] != old_version:
                    state['strategy']['activated_at'] = clock()
                    state.pop('entry_guard', None)
                conn.execute('UPDATE run SET state=?,revision=revision+1 WHERE id=1', (json.dumps(state),))
        finally:
            with conn:
                conn.execute('UPDATE run SET lease_until=0 WHERE id=1 AND lease_until=?', (lease,))


def guard_entries(conn, state):
    strategy = state['strategy']
    if not strategy or strategy.get('activated_at') is None:
        return
    if (state.get('entry_guard') or {}).get('version') != strategy['version']:
        state.pop('entry_guard', None)
    if state.get('entry_guard'):
        return
    matched = conn.execute("""
        SELECT main.pnl AS own, baseline.pnl AS baseline
        FROM trades main JOIN trades baseline ON main.mint=baseline.mint AND baseline.bot=?
        WHERE main.bot='jarvis' AND json_extract(main.data,'$.policy.version')=?
        ORDER BY main.id DESC LIMIT 25
    """, (strategy['source'], strategy['version'])).fetchall()
    if len(matched) != 25:
        return
    pnl = sum(row['own'] for row in matched)
    difference = sum(row['own'] - row['baseline'] for row in matched)
    if pnl <= -5 * DOLLAR and difference <= -5 * DOLLAR:
        state['entry_guard'] = {'version': strategy['version'], 'reason':
            'Entries paused after 25 matched trades lost money and lagged the source bot. Open exits remain monitored.'}


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
                              'estimated_value': value / DOLLAR, 'valuation_basis': basis,
                              'route_failure': p.get('route_failure'), 'next_quote_at': p['due']}
                             for p, (value, basis) in zip(positions, valuations)],
            })
        coverage = conn.execute("""SELECT count(*) FROM research WHERE checked>=? AND (
            json_extract(evidence,'$.chart.value') IS NOT NULL OR json_extract(evidence,'$.bluesky.value') IS NOT NULL
            OR json_extract(evidence,'$.x.value') IS NOT NULL)""", (now - research.FRESH_SECONDS,)).fetchone()[0]
        latest_mail = conn.execute('SELECT version, sent FROM research_mail ORDER BY version DESC LIMIT 1').fetchone()
        comparison = experiments.read_board(conn)
        route_retries = sum(bool(p.get('mark_stale') and 'quote_due_since' in p)
                            for bot in state['bots'].values() for p in bot['positions'].values())
        legacy_summary = conn.execute('SELECT data FROM legacy_outcome_summary WHERE id=1').fetchone()
    return {'enabled': state['enabled'], 'last_cycle': state['last_cycle'], 'error': state['error'], 'day': day,
            'research': {'recent_coins': coverage, 'x_configured': bool(os.getenv('JARVIS_X_BEARER_TOKEN', '').strip()),
                         'latest_proposal': dict(latest_mail) if latest_mail else None},
            'exit_queue': {**exit_queue(quote_jobs(state, now), now), 'retry_positions': route_retries},
            'last_quote_failures': {side: state.get(f'last_{side}_quote_failure') for side in ('entry', 'exit')},
            'comparison': comparison, 'entry_guard': state.get('entry_guard'),
            'early_review': state.get('early_review'), 'outcome_review': state.get('outcome_review'),
            'legacy_outcomes': json.loads(legacy_summary['data']) if legacy_summary else None,
            'active_warning': outcomes.WARNINGS.get((state['strategy'] or {}).get('avoid_pattern')),
            'bots': rows, 'strategy': state['strategy'], 'review': state['review'], 'reviewed': state['reviewed'],
            'review_every': review_size(state['reviewed']), 'watching': len(state['watch']),
            'quote_activity': state.get('quote_activity'),
            'worker_interval_seconds': WORKER_INTERVAL,
            'discovery': {'batch_size': DISCOVERY_BATCH, 'interval_seconds': DISCOVERY_INTERVAL,
                          'instant_entry_window_seconds': ENTRY_WINDOW,
                          'coverage': 'Discoverable active Solana pairs; not the entire meme-coin market.'}}


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
                 'held_seconds': max(0, int(t['closed'] - t['opened'])),
                 'entry_pattern': (t.get('entry_evidence', {}).get('chart') or {}).get('pattern'),
                 'social_match': research.allows('social_confirmed', t.get('entry_evidence', {}))}
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
        await asyncio.sleep(max(.1, WORKER_INTERVAL - (time.monotonic() - started)))


def claim_research(conn, now):
    """Reserve provider calls independently of the two-second trading worker."""
    state, _ = read(conn)
    if not state['enabled']:
        return []
    rows = [r for r in state['watch'].values()
            if 0 <= now - r['last'] <= 90
            and all(research.ADDRESS.fullmatch(r[field]) for field in ('mint', 'pair'))]
    if not rows:
        return []
    study_mints = {r['mint'] for r in conn.execute(
        'SELECT mint FROM experiment_coins WHERE done IS NULL AND opened>?', (now - experiments.ENTRY_WINDOW,))}
    records = {r['mint']: (r['pair'], json.loads(r['evidence']))
               for r in conn.execute('SELECT mint,pair,evidence FROM research WHERE checked>=?', (now - 900,))}
    tasks = []
    for source, interval in (('chart', 12), ('bluesky', 30), ('x', research.X_INTERVAL)):
        if source == 'x' and not os.getenv('JARVIS_X_BEARER_TOKEN', '').strip():
            continue
        due = conn.execute('SELECT due FROM research_tasks WHERE name=?', (source,)).fetchone()
        if due and due['due'] > now:
            continue

        def priority(row):
            pair, evidence = records.get(row['mint'], (None, {}))
            last = evidence.get(source, {}).get('at', 0) if pair == row['pair'] else 0
            fresh = now - last < research.FRESH_SECONDS
            return (fresh, row['mint'] not in study_mints, last if fresh else -row['first'])

        if not tasks:
            # Keep recurring storage bounded and retain recent coverage for diagnosis.
            conn.execute('DELETE FROM research WHERE checked<?', (now - 900,))
        chosen = min(rows, key=priority)
        conn.execute('INSERT OR REPLACE INTO research_tasks(name,due) VALUES (?,?)', (source, now + interval))
        tasks.append((source, chosen))
    return tasks


def refresh_legacy_outcomes(now):
    with connection() as conn, conn:
        claimed = conn.execute("""
            INSERT INTO research_tasks(name,due) VALUES ('legacy-outcomes',?)
            ON CONFLICT(name) DO UPDATE SET due=excluded.due WHERE due<=?
        """, (now + 900, now)).rowcount
    if not claimed:
        return
    report = outcomes.legacy_counts(database.DATABASE_PATH)
    if report is not None:
        with connection() as conn, conn:
            conn.execute('INSERT OR REPLACE INTO legacy_outcome_summary(id,data) VALUES (1,?)', (json.dumps(report),))


def research_tick(clock=time.time, chart=research.chart, social=research.social_source, deliver=research.deliver):
    now = clock()
    with connection() as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        tasks = claim_research(conn, now)
        mail = conn.execute('SELECT version,body FROM research_mail WHERE sent IS NULL AND due<=? ORDER BY version LIMIT 1',
                            (now,)).fetchone()
        if mail:
            conn.execute('UPDATE research_mail SET due=? WHERE version=?', (now + 900, mail['version']))
    if mail:
        try:
            sent = deliver(mail['version'], mail['body'])
        except Exception:
            LOG.warning('Jarvis code proposal email could not be delivered')
        else:
            if sent:
                with connection() as conn, conn:
                    conn.execute('UPDATE research_mail SET sent=? WHERE version=?', (clock(), mail['version']))
    for source, row in tasks:
        try:
            value = chart(row, clock()) if source == 'chart' else social(source, row, clock())
        except Exception:
            LOG.warning('Jarvis %s research is unavailable', source)
            value = None
        at = clock()
        with connection() as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.execute('SELECT evidence FROM research WHERE mint=? AND pair=?',
                                    (row['mint'], row['pair'])).fetchone()
            evidence = json.loads(existing['evidence']) if existing else {}
            # Missing data never qualifies as positive research evidence.
            evidence[source] = {'at': at, 'value': value}
            conn.execute('INSERT OR REPLACE INTO research(mint,pair,checked,evidence) VALUES (?,?,?,?)',
                         (row['mint'], row['pair'], at, json.dumps(evidence)))


async def research_worker():
    while True:
        try:
            await asyncio.to_thread(research_tick)
            await asyncio.to_thread(refresh_legacy_outcomes, time.time())
        except Exception:
            LOG.exception('Jarvis research cycle failed')
        await asyncio.sleep(10)


def experiment_tick(get_quote=quote, clock=time.time):
    now = clock()
    with connection() as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        state, _ = read(conn)
        # Give the main portfolios precedence when exits are waiting.
        if exit_queue(quote_jobs(state, now), now)['backed_up']:
            return
        active = conn.execute('SELECT id,data FROM experiment_coins WHERE done IS NULL ORDER BY id').fetchall()
        if not state['enabled'] and not active:
            return
        lease = now + 35
        claimed = conn.execute('UPDATE experiment_state SET lease=? WHERE id=1 AND lease<=?', (lease, now))
        if not claimed.rowcount:
            return
        last = conn.execute('SELECT opened FROM experiment_coins ORDER BY id DESC LIMIT 1').fetchone()
        if state['enabled'] and len(active) < experiments.MAX_OPEN and (not last or now - last['opened'] >= experiments.START_INTERVAL):
            for row in sorted(state['watch'].values(), key=lambda item: item['first'], reverse=True):
                if not 0 <= now - row['first'] <= 90:
                    break
                coin = experiments.new_coin(row, now)
                inserted = conn.execute(
                    'INSERT OR IGNORE INTO experiment_coins(mint,opened,data) VALUES (?,?,?)',
                    (row['mint'], now, json.dumps(coin)))
                if inserted.rowcount:
                    active = [*active, {'id': inserted.lastrowid, 'data': json.dumps(coin)}]
                    break
        plans = []
        updated = {}
        for item in active:
            coin = json.loads(item['data'])
            mint = coin['mint']
            row = state['watch'].get(mint)
            evidence = entry_evidence(conn, row, now) if row else {}
            updated[item['id']] = coin
            jobs = experiments.advance(coin, row, evidence, now, TRADE, BUY_COST)
            for (side, size), arms in jobs.items():
                if state['enabled'] or side == 'sell':
                    plans.append((side, size, mint, arms))
    outcome = None
    if plans:
        side, size, mint, arms = min(plans, key=lambda plan: min(arm['due'] for arm in plan[3]))
        source, target = (USDC, mint) if side == 'buy' else (mint, USDC)
        try:
            amount = get_quote(source, target, size)
        except (Deferred, RateLimited):
            pass
        except Exception:
            outcome = (arms, side, None, clock())
        else:
            outcome = (arms, side, amount, clock())
    with connection() as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute('SELECT lease FROM experiment_state WHERE id=1').fetchone()[0]
        if current != lease or clock() >= lease:
            return
        if outcome and (outcome[1] == 'sell' or read(conn)[0]['enabled']):
            experiments.quotes(*outcome, BUY_COST, SELL_FEE, exit_floor)
        for key, coin in updated.items():
            done = all('pnl' in arm for arm in coin['arms'].values())
            conn.execute('UPDATE experiment_coins SET done=?,data=? WHERE id=?',
                         (clock() if done else None, json.dumps(coin), key))
        experiments.update_policy(conn)
        # Keep the reservation until the next worker interval on every replica.
        conn.execute('UPDATE experiment_state SET lease=? WHERE id=1', (now + experiments.ENTRY_DELAY,))


async def experiment_worker():
    while True:
        try:
            await asyncio.to_thread(experiment_tick)
        except Exception:
            LOG.exception('Jarvis experiment cycle failed')
        await asyncio.sleep(experiments.ENTRY_DELAY)
