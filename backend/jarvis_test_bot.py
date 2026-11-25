"""Independent, quote-only Solana paper portfolios and strategy evaluation."""
import asyncio
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import math
import os
import re
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

import database
import jarvis_hourly_mail as hourly_mail
import jarvis_audit as audit
import jarvis_local_lab as local_lab
import jarvis_autonomy as autonomy
import jarvis_experiments as experiments
import jarvis_backlog as backlog
import jarvis_strategies as lab
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
NO_ROUTE_RETRY_SECONDS = 15
FINAL_PROFIT_SELL = 5
EXIT_COHORT = 'priority-stop-v1'
DISCOVERY_BATCH = 5 * MAX_SCAN_TOKENS
ENTRY_WINDOW = 900
MARK_MAX_AGE = 90
ROUTINE_EXIT_INTERVAL = 60
HELD_BATCH = 100
# Production startup for the batched-monitor / urgent-exit scheduler (PR #127).
EXIT_SCHEDULER_STARTED = datetime.fromisoformat("2026-11-23T01:01:51+00:00").timestamp()
BOTS = {
    'instant-step': ('Instant Ladder', 'instant', 'step'),
    'instant-trim': ('Instant Trim', 'instant', 'trim'),
    'patient-step': ('Pullback Ladder', 'pullback', 'step'),
    'patient-trim': ('Pullback Trim', 'pullback', 'trim'),
    **lab.BOTS,
    **local_lab.SLOTS,
    'jarvis': ('Jarvis Trader', None, None),
}
FIXED = tuple(bot for bot in BOTS if bot != 'jarvis' and bot not in local_lab.SLOTS)
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
        conn.executescript(SCHEMA + research.SCHEMA + experiments.SCHEMA + outcomes.SCHEMA + backlog.SCHEMA + autonomy.SCHEMA + local_lab.SCHEMA + hourly_mail.SCHEMA + audit.SCHEMA)
        conn.execute('INSERT OR IGNORE INTO run(id,state) VALUES (1,?)', (json.dumps(initial()),))
        conn.commit()
        yield conn


def read(conn):
    row = conn.execute('SELECT state,revision FROM run WHERE id=1').fetchone()
    state = json.loads(row['state'])
    for bot, portfolio in initial()['bots'].items():
        state['bots'].setdefault(bot, portfolio)
    return state, row['revision']


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


def set_preset_pause(paused):
    if type(paused) is not bool:
        raise ValueError('Expected a preset pause toggle.')
    with connection() as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        state, _ = read(conn)
        state['preset_entries_paused'] = paused
        conn.execute('UPDATE run SET state=?,revision=revision+1 WHERE id=1',(json.dumps(state),))
    return status()


def apply_preset_pause():
    if not __import__('jarvis_focus').enabled():
        return
    with connection() as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        state, _ = read(conn)
        if state.get('preset_pause_v1'):
            return
        state.update(preset_entries_paused=True,preset_pause_v1=True)
        conn.execute('UPDATE run SET state=?,revision=revision+1 WHERE id=1',(json.dumps(state),))
    print('Jarvis focus: preset strategy entries paused; research and Jarvis remain enabled; existing exits monitored.',flush=True)


def apply_pbh_closeout():
    if not __import__('jarvis_focus').enabled():
        return
    now=time.time()
    with connection() as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        state,_=read(conn)
        if state.get('pbh_closeout_v1'):
            if not state.get('pbh_liquidity_probe_v1'):
                for b in state['bots'].values():
                    for p in b['positions'].values():
                        if p.get('owner_closeout') and 'insufficient liquidity' in (p.get('route_failure') or {}).get('reason','').lower():
                            p['due']=now
                state['pbh_liquidity_probe_v1']=True
                conn.execute('UPDATE run SET state=?,revision=revision+1 WHERE id=1',(json.dumps(state),))
            if not state.get('pbh_error_probe_v1'):
                for b in state['bots'].values():
                    for p in b['positions'].values():
                        if p.get('owner_closeout') and (p.get('route_failure') or {}).get('category')=='request':
                            p['due']=now
                state['pbh_error_probe_v1']=True
                conn.execute('UPDATE run SET state=?,revision=revision+1 WHERE id=1',(json.dumps(state),))
            return
        retired={row['id'] for row in conn.execute("SELECT id FROM local_strategies WHERE status IN ('failed','inactive')")}
        count=0
        for bot,portfolio in state['bots'].items():
            for p in portfolio['positions'].values():
                ident=p.get('policy',{}).get('local_rule',{}).get('id')
                if bot in FIXED or (bot in local_lab.SLOTS and ident in retired):
                    p.update(owner_closeout=True,pending='pbh_closeout',due=now)
                    p['exit_signal']={'at':now,'basis':'owner_closeout'}
                    count+=1
        state.update(pbh_closeout_v1={'requested':now,'positions':count},preset_entries_paused=True,pbh_error_probe_v1=True,pbh_liquidity_probe_v1=True)
        conn.execute('UPDATE run SET state=?,revision=revision+1 WHERE id=1',(json.dumps(state),))
    print(f'PBH paper closeout queued: {count} positions; active research holdings excluded.',flush=True)


class QuoteUnavailable(ValueError):
    def __init__(self, message, category='invalid_quote'):
        super().__init__(message)
        self.category = category


def quote_error(response, data):
    # Only fixed diagnostic labels are persisted; never echo arbitrary provider
    # bodies, credentials, HTML or transaction data into logs/the dashboard.
    status = response.status_code
    message = ' '.join(data.get(key,'') for key in ('error','errorMessage','message','errorCode','code')
                       if isinstance(data.get(key),str)).lower().replace('_',' ') if isinstance(data,dict) else ''
    if status in (401, 403):
        return QuoteUnavailable('Jupiter API access denied; check key or plan.', 'access')
    if status >= 500 or status in (408, 425):
        return QuoteUnavailable('Jupiter temporarily unavailable.', 'temporary')
    if status == 400 and any(text in message for text in (
            'failed to get quotes', 'could not find any route', 'no route', 'not tradable')):
        return QuoteUnavailable('Jupiter has no quote for this token and exact amount.', 'no_route')
    if status==400 and 'route plan does not consume all' in message:
        return QuoteUnavailable('Jupiter cannot quote the full input amount.', 'partial_route')
    if status==400 and 'insufficient liquidity' in message:
        return QuoteUnavailable('Jupiter reports insufficient liquidity for this amount.','liquidity')
    if status==400 and any(text in message for text in ('invalid amount','invalid input amount','invalid mint','invalid input mint','invalid output mint','invalid slippage','invalid digit','deserialize query','query parameter')):
        return QuoteUnavailable('Jupiter rejected an input parameter; the holding remains tracked.', 'invalid_parameter')
    vocabulary={'amount','input','output','mint','token','not','found','unsupported','supported','invalid','too','small','large','insufficient','liquidity','route','balance','slippage','parameter','parameters','query','required','missing','deserialize','parse','fee','gas','minimum','maximum','limit','error','failed','could','find','quotes','price','impact','exceeds','same','different','disabled','requested','expired','request','size','zero','positive','negative','out','range','exist','exists','available','unavailable'}
    hint=' '.join(word for word in re.findall('[a-z]+',message) if word in vocabulary)[:160]
    return QuoteUnavailable(f'Jupiter rejected the quote request (HTTP {status}).'+(' Diagnostic: '+hint if hint else ''), 'request')


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
    amount=quote_uint(amount)
    if not all(isinstance(mint,str) and research.ADDRESS.fullmatch(mint) for mint in (input_mint,output_mint)):
        raise QuoteUnavailable('Invalid quote mint; no request sent.','invalid_parameter')
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
    held = sorted({mint for portfolio in state['bots'].values() for mint in portfolio['positions']})
    try:
        mints = [mint for mint in providers.profiles() if mint not in (USDC, lab.SOL)]
    except (ValueError, OSError, RuntimeError):
        mints = []
        state['error'] = 'Discovery profiles unavailable.'
    tracked = [mint for mint, row in sorted(state['watch'].items(), key=lambda item: item[1]['last'])
               if mint not in held and not row['pullback']][:12]
    offset = state['offset'] % max(1, len(mints))
    rotated = mints[offset:] + mints[:offset]
    fresh = rotated[:DISCOVERY_BATCH - len(tracked)]
    state['offset'] = (offset + len(fresh)) % max(1, len(mints))
    held_offset = state.get('held_offset', 0) % max(1, len(held))
    held_batch = (held[held_offset:] + held[:held_offset])[:HELD_BATCH]
    state['held_offset'] = (held_offset + len(held_batch)) % max(1, len(held))
    addresses = list(dict.fromkeys(held_batch + tracked + fresh + [lab.SOL]))
    requested = set(addresses)
    pairs = []
    failed_batches = 0
    batches = [addresses[start:start + MAX_SCAN_TOKENS]
               for start in range(0, len(addresses), MAX_SCAN_TOKENS)]
    # Independent read-only batches overlap, keeping discovery from serially
    # blocking the next exit/entry quote. Consume results in discovery order.
    with ThreadPoolExecutor(max_workers=6) as pool:
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
            row['monitor_only'] = row['volume_m5_usd'] <= 0 or row['buys_m5'] + row['sells_m5'] == 0
            if mint not in held and row['monitor_only']:
                continue
            if mint not in best or row['liquidity_usd'] > best[mint]['liquidity_usd']:
                row['symbol'] = str(pair['baseToken'].get('name') or row['symbol'])[:80]
                best[mint] = row
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    benchmark_row = best.pop(lab.SOL, None)
    state['watch'].pop(lab.SOL, None)
    if benchmark_row:
        benchmark = state.get('benchmark') or {}
        samples = benchmark.get('samples', []) if benchmark.get('pair') == benchmark_row['pair'] else []
        samples = [sample for sample in samples if 0 <= now - sample['at'] <= 900]
        if not samples or samples[-1]['at'] < now:
            samples.append({'at': now, 'price': benchmark_row['price_usd']})
        state['benchmark'] = {'pair': benchmark_row['pair'], 'samples': samples[-100:]}
    for mint, row in best.items():
        old = state['watch'].get(mint)
        if old is None or old['pair'] != row['pair']:
            state['watch'][mint] = {**row, 'first': now, 'last': now, 'high': row['price_usd'],
                                    'dip': None, 'pullback': None}
            lab.observe(state['watch'][mint], now)
            continue
        price = row['price_usd']
        old.update(last=now, price_usd=price, monitor_only=row['monitor_only'],
                   buys_m5=row['buys_m5'], sells_m5=row['sells_m5'],
                   liquidity_usd=row['liquidity_usd'], volume_m5_usd=row['volume_m5_usd'], age_minutes=row['age_minutes'])
        lab.observe(old, now)
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
    if bot != 'jarvis' and bot not in local_lab.SLOTS and portfolio['cash'] < BUY_COST:
        funding = BUY_COST - portfolio['cash']
        portfolio['cash'] += funding
        portfolio['funding'] += funding
    if portfolio['cash'] < BUY_COST:
        return
    portfolio['cash'] -= BUY_COST
    portfolio['last_trade_at'] = now
    source = bot if bot != 'jarvis' else (state.get('strategy') or {}).get('source')
    policy = dict(state['strategy']) if bot == 'jarvis' else {'entry': BOTS[bot][1], 'exit': BOTS[bot][2]}
    if bot in local_lab.SLOTS:
        policy['local_rule'] = dict(state['local_rules'][bot])
    portfolio['positions'][row['mint']] = {
        'mint': row['mint'], 'pair': row.get('pair'), 'symbol': row['symbol'], 'opened': now, 'quantity': quantity,
        'entry_cost': BUY_COST, 'cost': BUY_COST, 'banked': 0, 'peak': 0,
        'exit_cohort': EXIT_COHORT, 'entry_evidence': evidence or {},
        'strategy_version': lab.version(source) if source in lab.BOTS else None,
        'target': 20, 'trims': 0, 'mark': None, 'mark_at': None, 'due': now + POLL,
        'pending': None, 'liquidity': row['liquidity_usd'], 'age': row['age_minutes'],
        'policy': policy,
    }


def sell(completed, portfolio, bot, position, quantity, amount, now, reason):
    if not position.get('paper_writeoff'):
        portfolio['last_trade_at'] = now
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
        position.pop('exit_signal', None)
        return
    pnl = position['banked'] - position['entry_cost']
    event = {**position, 'bot': bot, 'closed': now, 'pnl': pnl, 'reason': reason}
    if position.get('exit_signal') and not position.get('paper_writeoff'):
        event['signal_to_close_seconds'] = max(0, now - position['exit_signal']['at'])
    completed.append(event)
    del portfolio['positions'][position['mint']]


def write_off_no_routes(completed, state, now):
    """Owner-selected paper accounting policy; this does not execute a sale."""
    for bot, portfolio in state['bots'].items():
        for p in list(portfolio['positions'].values()):
            if (p.get('route_failure') or {}).get('category') != 'no_route':
                continue
            p['paper_writeoff'] = {'category': 'no_route', 'remaining_cost': p['cost'],
                                   'quantity': p['quantity'], 'proceeds': 0,
                                   'loss_percent': 100, 'at': now}
            sell(completed, portfolio, bot, p, p['quantity'], 0, now,
                 'Rug Pull · no-route paper write-off · remaining position −100% (no sale executed)')


def apply_exit(completed, portfolio, bot, p, quote_size, amount, now, evidence=None, market=None):
    if p.get('owner_closeout'):
        if 0<quote_size<=p['quantity']:
            sell(completed,portfolio,bot,p,quote_size,amount,now,'PBH owner-requested paper closeout')
            if p['quantity']:
                p.update(pending='pbh_closeout',pbh_quote_size=min(quote_size,p['quantity']),pbh_chunk_attempts=0)
        return
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
    if gain > p['peak']:
        p['last_high_at'] = now
    p['peak'] = max(p['peak'], gain)
    if p['policy'].get('local_rule'):
        reason = local_lab.exit_reason(p, gain, now, market or {}, evidence or {})
        if reason:
            p.setdefault('exit_signal', {'at': now, 'basis': 'confirmed_quote'})
            sell(completed, portfolio, bot, p, quote_size, amount, now, reason)
        return
    chart = (evidence or {}).get('chart') or {}
    special = lab.exit_reason(p, gain, amount, now, chart, market or {}, TRADE, (evidence or {}).get('benchmark'))
    floor = lab.floor(p['policy']['exit'], p['peak'], exit_floor)
    if special:
        reason = special
    elif p['policy']['exit'] in lab.BRACKETS:
        reason = None
    elif gain <= floor:
        reason = 'Step stop' if floor <= 0 else 'Step profit protection'
        p['stop_gap_points'] = floor - gain
    elif p['policy']['exit'] == 'trim' and gain >= p['target']:
        reason = 'Took profit · fifth sell closed remainder' if p['trims'] >= FINAL_PROFIT_SELL - 1 else None
        if reason is None and p['quantity'] >= 2:
            p['pending'] = 'trim'
    else:
        reason = None
    if p.get('pending'):
        p.setdefault('exit_signal', {'at': now})['basis'] = 'confirmed_quote'
    if reason:
        p.setdefault('exit_signal', {'at': now, 'basis': 'confirmed_quote'})
        p['exit_quote_wait'] = max(0, now - quote_due_since(p))
        sell(completed, portfolio, bot, p, quote_size, amount, now, reason)


def review_size(reviewed):
    return REVIEW_EVERY if reviewed else FIRST_REVIEW


def evaluate(conn, state):
    total = conn.execute("SELECT count(*) FROM trades WHERE bot!='jarvis' AND bot NOT LIKE 'research-%'").fetchone()[0]
    while True:
        start = state['reviewed']
        batch_size = review_size(start)
        if total < start + batch_size:
            return
        rows = conn.execute("SELECT data FROM trades WHERE bot!='jarvis' AND bot NOT LIKE 'research-%' ORDER BY id LIMIT ? OFFSET ?",
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
    count = conn.execute("SELECT count(*) FROM trades WHERE bot!='jarvis' AND bot NOT LIKE 'research-%'").fetchone()[0]
    if count < EARLY_REVIEW_MIN or count >= FIRST_REVIEW or count < last + EARLY_REVIEW_INTERVAL:
        return
    trades = [json.loads(row['data']) for row in conn.execute(
        "SELECT data FROM trades WHERE bot!='jarvis' AND bot NOT LIKE 'research-%' ORDER BY id LIMIT ?", (count,))]
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
    count = conn.execute("SELECT COUNT(*) FROM trades WHERE bot!='jarvis' AND bot NOT LIKE 'research-%'").fetchone()[0]
    previous = state.get('outcome_review')
    if previous and count < previous['closed'] + 25:
        return
    recent = conn.execute("SELECT data FROM trades WHERE bot!='jarvis' AND bot NOT LIKE 'research-%' ORDER BY id DESC LIMIT 1000").fetchall()
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


def opportunity_evidence(conn, row, now, benchmark=None):
    evidence = entry_evidence(conn, row, now)
    evidence['benchmark'] = benchmark
    evidence['lab_signal'] = {key: lab.entry_signal(key, row, evidence, now) for key in ('pressure', 'volume', 'liquidity', 'vwap', 'trend', 'squeeze', 'rsi', 'relative')}
    evidence['relative_strength'] = lab.relative_metrics(row, benchmark, now)
    evidence.pop('benchmark')
    evidence['market'] = {key: row.get(key) for key in ('volume_m5_usd', 'buys_m5', 'sells_m5', 'liquidity_usd')}
    return evidence


def entry_jobs(conn, state, now):
    possible = {}
    for mint, row in state['watch'].items():
        if row.get('monitor_only') or now - row['last'] > 90 or now < row.get('retry', 0):
            continue
        market_evidence = entry_evidence(conn, row, now)
        market_evidence['benchmark'] = state.get('benchmark')
        closed = {r['bot'] for r in conn.execute('SELECT bot FROM trades WHERE mint=?', (mint,))}
        for bot, portfolio in state['bots'].items():
            if state.get('preset_entries_paused') and bot in FIXED:
                continue
            if bot in closed or mint in portfolio['positions']:
                continue
            if bot in local_lab.SLOTS:
                # Research shares the same bounded entry queue as every other
                # strategy. step() retains urgent-exit priority and buy pauses.
                if local_lab.entry_allowed(state, bot, row, market_evidence, now):
                    possible.setdefault(mint, []).append(bot)
                continue
            if bot == 'jarvis':
                strategy = state['strategy']
                if (state.get('entry_guard') or not strategy or portfolio['cash'] < BUY_COST or row['first'] < strategy['activated_at']
                        or row['liquidity_usd'] < strategy['min_liquidity'] or row['age_minutes'] < strategy['min_age']):
                    continue
                if strategy.get('local_rule') and not local_lab.run(
                        strategy['local_rule']['entry'], local_lab.observations(row, market_evidence)):
                    continue
                if strategy.get('autonomous_rule') and not autonomy.allows(
                        strategy['autonomous_rule'], row['liquidity_usd'], row['age_minutes'],
                        opportunity_evidence(conn, row, now, state.get('benchmark'))):
                    continue
                rule = strategy.get('entry_filter', 'baseline')
                avoid = strategy.get('avoid_pattern')
                if rule != 'baseline' or avoid:
                    evidence = opportunity_evidence(conn, row, now, state.get('benchmark'))
                    if not research.allows(rule, evidence) or outcomes.warning(
                            avoid, evidence, row['liquidity_usd'], row['age_minutes']):
                        continue
                entry = strategy['entry']
            else:
                entry = BOTS[bot][1]
            signal = lab.entry_signal(entry, row, market_evidence, now)
            window = ENTRY_WINDOW if entry == 'instant' else 90
            if signal is not None and 0 <= now - signal <= window:
                possible.setdefault(mint, []).append(bot)
    return possible


def local_entry_status(conn, state, now):
    """Explain current research eligibility without placing or scheduling trades."""
    import ast
    matches = entry_jobs(conn,state,now)
    urgent = any(not p.get('owner_closeout') and p.get('due',math.inf)<=now and (p.get('exit_priority')==0 or p.get('exit_signal'))
                 for portfolio in state['bots'].values() for p in portfolio['positions'].values())
    labels = {}
    for slot in local_lab.SLOTS:
        rule = state.get('local_rules',{}).get(slot)
        if not rule:
            labels[slot]={'label':'Waiting for local researcher','matching_coins':0}
            continue
        portfolio=state['bots'][slot]
        matching=sum(slot in bots for bots in matches.values())
        fresh=[row for row in state['watch'].values()
               if not row.get('monitor_only') and 0<=now-row['last']<=90
               and row['first']>=rule['created'] and 0<=now-row['first']<=900
               and now>=row.get('retry',0)]
        required={node.id for node in ast.walk(local_lab.parse(rule['entry'])) if isinstance(node,ast.Name)}
        missing={field:0 for field in required}
        complete=0
        for row in fresh:
            values=local_lab.observations(row,entry_evidence(conn,row,now))
            absent=[field for field in required if type(values.get(field)) not in (int,float)
                    or not math.isfinite(values[field])]
            for field in absent:missing[field]+=1
            complete+=not absent
        if not state['enabled']:
            label='Paper entries paused · exits monitored'
        elif backlog.paused(state,now) or (state.get('entry_catchup_pause') or {}).get('active'):
            label='Buy pause active · exits monitored'
        elif len(portfolio['positions'])>=local_lab.MAX_OPEN:
            label='Experiment position limit reached · exits monitored'
        elif portfolio['cash']<BUY_COST:
            label='Insufficient experiment cash'
        elif urgent:
            label=f'Waiting for urgent exits · {matching} matching coins'
        elif matching:
            label=f'{matching} matching coins · waiting for shared quote capacity'
        elif not fresh:
            label='Waiting for newly discovered eligible coins'
        elif not complete:
            fields=', '.join(sorted(field for field,count in missing.items() if count))
            label='Waiting for fresh data: '+fields
        else:
            label=f'No matching signal · {complete} coins with required data'
        labels[slot]={'label':label,'matching_coins':matching,'fresh_eligible_coins':len(fresh),
                      'complete_observations':complete,'missing_observations':missing}
    return labels


def quote_jobs(state, now):
    jobs = {}
    for bot, portfolio in state['bots'].items():
        for mint, p in portfolio['positions'].items():
            if p['due'] <= now:
                partial = p['pending'] == 'trim' and p['trims'] < FINAL_PROFIT_SELL - 1 and p['quantity'] >= 2
                quantity = p['quantity'] // 2 if partial else p['quantity']
                if p.get('owner_closeout'):
                    quantity=min(quantity,p.get('pbh_quote_size',quantity))
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
    return gain - lab.floor(position['policy']['exit'], position['peak'], exit_floor)


def fresh_market(position, state, now):
    row = state['watch'].get(position['mint']) or {}
    if (row and 0 <= now - row['last'] <= MARK_MAX_AGE and position.get('pair')
            and row.get('pair') == position['pair'] and lab.finite(row.get('price_usd'))
            and row['price_usd'] > 0):
        return row
    return {}


def exit_priority(position, now, market=None, benchmark=None):
    """Indicative prices only schedule quotes; they never mark or fill positions."""
    p = position
    rule = p['policy']['exit']
    if p.get('pending'):
        return 0
    gain = None
    gross = None
    anchor = p.get('market_anchor') or {}
    if (market and anchor.get('quantity') == p['quantity']
            and anchor.get('pair') == market.get('pair')
            and lab.finite(anchor.get('price')) and anchor['price'] > 0):
        gross = anchor['amount'] * market['price_usd'] / anchor['price']
        gain = 100 * (max(0, gross - SELL_FEE) - p['cost']) / p['cost']
    elif p.get('mark') is not None:
        gain = 100 * (p['mark'] - p['cost']) / p['cost']
        gross = p['mark'] + SELL_FEE
    if p['policy'].get('local_rule'):
        rule_gain = gain if gain is not None else 0
        return 0 if local_lab.exit_reason(p, rule_gain, now, market or {}, {}) else 3
    if lab.exit_reason(p, gain if gain is not None else 0, gross if gross is not None else TRADE,
                       now, {}, market or {}, TRADE, benchmark):
        return 0
    if gain is None:
        return 3
    # Bracket stops are gross token value; other stops protect net costs.
    if rule in lab.BRACKETS:
        stop = 100 * (gross / TRADE - 1) + 10
        profit = lab.BRACKETS[rule] - 100 * (gross / TRADE - 1)
    else:
        stop = gain - lab.floor(rule, p['peak'], exit_floor)
        profit = p['target'] - gain if rule == 'trim' else math.inf
    if min(stop, profit) <= 0:
        return 0
    if min(stop, profit) <= NEAR_STOP_POINTS:
        return 1
    return 3


def observe_exit_signal(p, state, now):
    if p.get('owner_closeout'):
        p['exit_priority']=1
        p.setdefault('exit_signal',{'at':now,'basis':'owner_closeout'})
        return 1
    market = fresh_market(p, state, now)
    priority = exit_priority(p, now, market, state.get('benchmark'))
    p['exit_priority'] = priority
    if priority == 0:
        basis = ('confirmed_quote' if p.get('pending') else
                 'strategy_timer' if ((p['policy']['exit'] == 'time5' and now - p['opened'] >= 300) or
                                      (p['policy']['exit'] == 'stalled' and now - p.get('last_high_at', p['opened']) >= 180)) else
                 'indicative_price_or_market')
        p.setdefault('exit_signal', {'at': now, 'basis': basis})
    else:
        p.pop('exit_signal', None)
    return priority


def position_exit_state(p, now):
    if p.get('exit_signal') or p.get('pending'):
        return 'exit_triggered'
    if p.get('mark_stale') or p['due'] <= now:
        return 'quote_delayed'
    return 'holding'


def exit_work_summary(conn, state, now):
    groups = {key: [] for key in ('holding', 'exit_triggered', 'quote_delayed')}
    for portfolio in state['bots'].values():
        for p in portfolio['positions'].values():
            groups[position_exit_state(p, now)].append(p)
    waits = [max(0, now - p['exit_signal']['at']) for p in groups['exit_triggered'] if p.get('exit_signal')]
    latencies = sorted(row[0] for row in conn.execute(
        "SELECT json_extract(data,'$.signal_to_close_seconds') FROM trades "
        "WHERE json_extract(data,'$.signal_to_close_seconds') IS NOT NULL "
        "AND json_extract(data,'$.paper_writeoff') IS NULL ORDER BY id DESC LIMIT 200"))
    return {**{key: {'positions': len(ps), 'coins': len({p['mint'] for p in ps})} for key, ps in groups.items()},
            'trigger_bases': {basis: sum((p.get('exit_signal') or {}).get('basis') == basis for p in groups['exit_triggered'])
                             for basis in ('confirmed_quote', 'strategy_timer', 'indicative_price_or_market','owner_closeout')},
            'oldest_signal_seconds': max(waits, default=0),
            'close_latency': {'samples': len(latencies), 'average_seconds': sum(latencies)/len(latencies) if latencies else None,
                              'p95_seconds': latencies[max(0, math.ceil(len(latencies)*.95)-1)] if latencies else None}}


def schedule_market_exits(state, now):
    for portfolio in state['bots'].values():
        for p in portfolio['positions'].values():
            priority = observe_exit_signal(p, state, now)
            # Keep retry cooldowns even when an exit signal is present.
            if p.get('mark_stale') and 'quote_due_since' in p:
                continue
            if priority <= 1:
                earliest = max(now, (p.get('mark_at') or p['opened']) + POLL)
                p['due'] = min(p['due'], earliest)


def next_exit(jobs, attempts):
    # Give observed exits the next eligible quote, reserving every third
    # attempt for the existing oldest-check fairness path.
    triggered = {job: group for job, group in jobs.items()
                 if any(not p.get('owner_closeout') and (p.get('exit_signal') or p.get('pending')) for _, p in group)}
    if triggered and attempts % OLDEST_EXIT_INTERVAL:
        return min(triggered, key=lambda job: min((p.get('exit_signal') or {}).get('at', p['due'])
                                                 for _, p in triggered[job]))
    active={job:group for job,group in jobs.items() if any(
        (bot=='jarvis' or bot in local_lab.SLOTS) and not p.get('owner_closeout') for bot,p in group)}
    if active and len(active)<len(jobs) and attempts%3==0:
        return min(active,key=lambda job:min(quote_due_since(p) for _,p in active[job]))
    regular = {job: group for job, group in jobs.items() if not retry_group(group)}
    retries = {job: group for job, group in jobs.items() if job not in regular}
    if regular and retries:
        jobs = retries if (attempts + 1) % EXIT_RETRY_INTERVAL == 0 else regular

    def oldest(job):
        return min(quote_due_since(p) for _, p in jobs[job])

    if attempts % OLDEST_EXIT_INTERVAL:
        urgent = [job for job, group in jobs.items()
                  if not retry_group(group) and any(p.get('exit_priority', 3) <= 1 or stop_distance(p) <= NEAR_STOP_POINTS for _, p in group)]
        if urgent:
            return min(urgent, key=lambda job: (min(p.get('exit_priority', 3) for _, p in jobs[job]),
                                              min(stop_distance(p) for _, p in jobs[job]), oldest(job)))
    return min(jobs, key=oldest)


def entry_quote_budget(exits, now):
    queue = exit_queue(exits, now)
    if queue['backed_up']:
        return BACKLOG_EXIT_QUOTES_PER_ENTRY
    urgent = any(p['pending'] or p.get('exit_priority', 3) <= 1 or stop_distance(p) <= NEAR_STOP_POINTS
                 for group in exits.values() for _, p in group if not retry_group(group))
    if urgent or queue['oldest_wait'] > 15:
        return EXIT_QUOTES_PER_ENTRY
    # Healthy exits can alternate with buys. Keep the existing 2:1/6:1
    # exit preference as soon as stops are close or monitoring falls behind.
    return 1


def catchup_requested(state):
    request = os.getenv('JARVIS_ENTRY_CATCHUP_REQUEST', '').strip()
    pause = state.get('entry_catchup_pause') or {}
    return bool((request and request != pause.get('request')) or pause.get('active'))


def update_catchup_pause(state, exits, now):
    request = os.getenv('JARVIS_ENTRY_CATCHUP_REQUEST', '').strip()
    pause = state.get('entry_catchup_pause') or {}
    if request and request != pause.get('request'):
        pause = {'request': request, 'active': True, 'requested_at': now, 'healthy_since': None}
        state['entry_catchup_pause'] = pause
    if not pause.get('active'):
        return False
    queue = exit_queue(exits, now)
    urgent = any(p.get('exit_priority') == 0 for group in exits.values()
                 if not retry_group(group) for _, p in group)
    # Unroutable holdings retain their own backoff; they cannot block resumption forever.
    healthy = queue['due'] - queue['retry_due'] <= 2 and queue['oldest_wait'] <= 5 and not urgent
    if healthy:
        if pause.get('healthy_since') is None:
            pause['healthy_since'] = now
        if now - pause['healthy_since'] >= 30:
            pause.update(active=False, resumed_at=now)
            LOG.info('Jarvis paper buys resumed after temporary exit catch-up pause.')
    else:
        pause['healthy_since'] = None
    return pause['active']


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
    write_off_no_routes(completed, state, now)
    activity = state.setdefault('quote_activity', {'since': now, 'entry_quotes': 0, 'paper_entries': 0,
                                                  'exit_quotes': 0, 'deferrals': 0})
    if state['enabled'] or any(p['positions'] for p in state['bots'].values()):
        held = {mint for portfolio in state['bots'].values() for mint in portfolio['positions']}
        state['watch'] = {mint: row for mint, row in state['watch'].items() if mint in held or now - row['first'] <= 900}
        if providers is not None and now >= state.get('next_discovery', 0):
            state['next_discovery'] = now + DISCOVERY_INTERVAL
            try:
                observe(state, providers, now)
            except (ValueError, OSError, RuntimeError):
                state['error'] = 'Discovery data unavailable.'
    schedule_market_exits(state, now)
    exits = quote_jobs(state, now)
    catchup_paused = update_catchup_pause(state, exits, now)
    entries = entry_jobs(conn, state, now) if state['enabled'] and not catchup_paused and not backlog.paused(state, now) else {}
    # Keep new entries moving at a reduced rate during sustained exit pressure.
    # Provider deferrals do not spend an attempt in either queue.
    exit_attempts = state.get('exit_attempts_since_entry', 0)
    exit_budget = entry_quote_budget(exits, now)
    urgent_exit = any(not p.get('owner_closeout') and (p.get('exit_priority') == 0 or p.get('exit_signal'))
                      for group in exits.values() for _, p in group)
    buy = entries and not urgent_exit and (not exits or exit_attempts >= exit_budget)
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
            evidence = opportunity_evidence(conn, row, now, state.get('benchmark'))
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
                category = getattr(error, 'category', None)
                temporary = category == 'temporary'
                delay = (EXIT_RETRY_LIMIT if category=='invalid_parameter' else NO_ROUTE_RETRY_SECONDS if category == 'no_route' else
                         min(30 if temporary else EXIT_RETRY_LIMIT,
                             (6 if temporary else 45) * 2 ** (failures - 1)))
                if p.get('owner_closeout') and category in ('liquidity','partial_route'):
                    attempts=p.get('pbh_chunk_attempts',0)+1
                    p['pbh_chunk_attempts']=attempts
                    if size>1 and attempts<=8:
                        p['pbh_quote_size']=max(1,size//2)
                        delay=6
                    else:
                        delay=EXIT_RETRY_LIMIT
                p['route_failure'] = {'category': getattr(error, 'category', 'unknown'),
                                      'reason': str(error) if isinstance(error, QuoteUnavailable) else type(error).__name__}
                p.update(due=now + delay, mark_stale=True, route_failures=failures)
                state['bots'][bot]['quote_misses'] += 1
            state['error'] = ('No-route position recorded as a Rug Pull paper write-off.'
                              if getattr(error, 'category', None) == 'no_route' else
                              'An exit quote is unavailable; holding remains open for retry.')
        else:
            state['exit_quote_attempts'] = attempts + 1
            state['exit_attempts_since_entry'] = min(exit_attempts + 1, BACKLOG_EXIT_QUOTES_PER_ENTRY)
            activity['exit_quotes'] += 1
            for bot, p in group:
                was_pending = p['pending']
                current = fresh_market(p, state, now)
                fresh = bool(current)
                if fresh and size == p['quantity']:
                    p['market_anchor'] = {'price': current['price_usd'], 'pair': current['pair'],
                                          'quantity': size, 'amount': amount, 'at': now}
                evidence = entry_evidence(conn, current, now) if fresh else {}
                evidence['benchmark'] = state.get('benchmark')
                apply_exit(completed, state['bots'][bot], bot, p, size, amount, now, evidence, current if fresh else {})
                p.pop('quote_due_since', None)
                p.pop('route_failures', None)
                p.pop('route_failure', None)
                if mint not in state['bots'][bot]['positions']:
                    continue
                priority = observe_exit_signal(p, state, now)
                interval = ROUTINE_EXIT_INTERVAL if fresh and p.get('market_anchor') and priority == 3 else 15
                p['due'] = now + (POLL if was_pending or p['pending'] else interval)
    state['last_cycle'] = now
    write_off_no_routes(completed, state, now)
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
                local_lab.review(conn, state, now)
                old_version = (state['strategy'] or {}).get('version')
                if completed:
                    evaluate(conn, state)
                    guard_entries(conn, state)
                    autonomy.guard(conn, state)
                    autonomy.review(conn, state, now)
                if completed or 'outcome_review' not in state:
                    refresh_outcome_review(conn, state)
                if completed or not state.get('early_review_initialized'):
                    refresh_early_review(conn, state)
                    state['early_review_initialized'] = True
                if state['strategy'] and state['strategy']['version'] != old_version:
                    state['strategy']['activated_at'] = clock()
                    state.pop('entry_guard', None)
                backlog.resume_when_empty(conn, state, now)
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
    if strategy.get('local_rule'):
        local_lab.guard(conn, state)
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


def exit_comparison(conn, state, now):
    """Descriptive cohorts, not a controlled estimate of scheduler performance."""
    cutoff = EXIT_SCHEDULER_STARTED
    groups = {key: {'closed': 0, 'waits': [], 'gaps': [], 'delayed_gaps': [],
                    'stop_waits': [], 'open': 0, 'route_retries': 0}
              for key in ('before', 'carried', 'new')}
    for row in conn.execute(
            "SELECT opened,closed,json_extract(data,'$.exit_quote_wait') AS wait, "
            "json_extract(data,'$.stop_gap_points') AS gap FROM trades WHERE closed>=? AND closed<=?",
            (cutoff - 1800, now)):
        key = 'before' if row['closed'] < cutoff else ('carried' if row['opened'] < cutoff else 'new')
        group = groups[key]
        group['closed'] += 1
        if lab.finite(row['wait']) and row['wait'] >= 0:
            group['waits'].append(row['wait'])
        if lab.finite(row['gap']) and row['gap'] >= 0:
            group['gaps'].append(row['gap'])
            if lab.finite(row['wait']) and row['wait'] >= 0:
                group['stop_waits'].append(row['wait'])
                if row['wait'] >= 90:
                    group['delayed_gaps'].append(row['gap'])
    for portfolio in state['bots'].values():
        for p in portfolio['positions'].values():
            group = groups['carried' if p['opened'] < cutoff else 'new']
            group['open'] += 1
            group['route_retries'] += int(bool(p.get('mark_stale') and 'quote_due_since' in p))
    def mean(values):
        return sum(values) / len(values) if values else None
    rows = []
    for key, group in groups.items():
        waits = sorted(group['waits'])
        rows.append({'id': key, 'closed': group['closed'], 'open': group['open'],
                     'route_retries': group['route_retries'], 'quote_samples': len(waits),
                     'mean_wait': mean(waits),
                     'p95_wait': waits[math.ceil(.95 * len(waits)) - 1] if waits else None,
                     'stop_samples': len(group['gaps']), 'stop_wait_samples': len(group['stop_waits']),
                     'late_stops': len(group['delayed_gaps']), 'mean_stop_gap': mean(group['gaps']),
                     'late_stop_gap': mean(group['delayed_gaps'])})
    return {'started_at': cutoff, 'before_window_seconds': 1800,
            'after_seconds': max(0, now - cutoff), 'cohorts': rows,
            'note': 'Quote wait measures overdue scheduled checks, not trigger-to-fill latency. '
                    'Stop gaps cover instrumented ladder stops; bracket exits are not included. '
                    'Different coins and market conditions prevent a causal comparison; open holdings are excluded from exit averages.'}


def status():
    now = time.time()
    day = datetime.fromtimestamp(now, ET).date().isoformat()
    totals_sql = ("SELECT count(*) AS closed, count(CASE WHEN pnl>0 THEN 1 END) AS wins, "
                  "count(CASE WHEN pnl<0 THEN 1 END) AS losses, COALESCE(sum(pnl),0) AS pnl "
                  "FROM trades WHERE bot=?")
    with connection() as conn:
        conn.execute('BEGIN')
        state, _ = read(conn)
        comparison_exits = exit_comparison(conn, state, now)
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
            description = ((DESCRIPTIONS.get(entry) or lab.DESCRIPTIONS.get(entry, 'Research entry expression.')) + ' ' +
                           (DESCRIPTIONS.get(exit_rule) or lab.DESCRIPTIONS.get(exit_rule + '_exit') or lab.DESCRIPTIONS.get(exit_rule, 'Research exit expression; fixed safety limits apply.'))) if entry else (
                'Waiting for a validated strategy learned from the paper bots.')
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
                'id': bot, 'name': name, 'research_slot': bot in local_lab.SLOTS, 'description': description, 'lab': bot in lab.BOTS,
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
        coverage_details = chart_coverage(conn, state, now)
        latest_mail = conn.execute('SELECT version, sent FROM research_mail ORDER BY version DESC LIMIT 1').fetchone()
        comparison = experiments.read_board(conn)
        autonomous = autonomy.status(conn)
        hourly = hourly_mail.status(conn)
        audit_report = audit.latest(conn)
        route_retries = sum(bool(p.get('mark_stale') and 'quote_due_since' in p)
                            for bot in state['bots'].values() for p in bot['positions'].values())
        legacy_summary = conn.execute('SELECT data FROM legacy_outcome_summary WHERE id=1').fetchone()
        latest_backlog_mail = conn.execute("SELECT created,sent,attempts,expires FROM backlog_mail WHERE id NOT LIKE 'resume/%' ORDER BY created DESC LIMIT 1").fetchone()
        exit_work = exit_work_summary(conn, state, now)
        rug_pulls = dict(conn.execute("SELECT count(*) AS positions, count(DISTINCT mint) AS coins, COALESCE(sum(pnl),0)/1000000.0 AS pnl FROM trades WHERE json_extract(data,'$.paper_writeoff.category')='no_route'").fetchone())
        timer_report = conn.execute("SELECT started,ends,(id LIKE 'partial/%') AS partial FROM backlog_pause_report WHERE ends=? AND cancelled IS NULL ORDER BY started DESC LIMIT 1", (backlog.pause_key(state),)).fetchone()
        closeout={**(state.get('pbh_closeout_v1') or {}),
                  'pending':sum(bool(p.get('owner_closeout')) for b in state['bots'].values() for p in b['positions'].values()),
                  'quoted_closes':conn.execute("SELECT count(*) FROM trades WHERE json_extract(data,'$.reason')='PBH owner-requested paper closeout'").fetchone()[0],
                  'writeoffs':conn.execute("SELECT count(*) FROM trades WHERE json_extract(data,'$.owner_closeout')=1 AND json_extract(data,'$.paper_writeoff') IS NOT NULL").fetchone()[0]}
    local = local_lab.context(__import__(__name__))
    return {'automated_audit':audit_report,'pbh_closeout':closeout,'preset_entries_paused':bool(state.get('preset_entries_paused')), 'notification_focus': __import__('jarvis_focus').enabled(), 'hourly_email': hourly, 'local_lab': local, 'autonomy': autonomous, 'enabled': state['enabled'], 'last_cycle': state['last_cycle'], 'error': state['error'], 'day': day,
            'research': {'recent_coins': coverage, 'chart_coverage': coverage_details, 'x_configured': bool(os.getenv('JARVIS_X_BEARER_TOKEN', '').strip()),
                         'latest_proposal': dict(latest_mail) if latest_mail else None},
            'backlog_guard': {**backlog.summary(state, now),
                              'latest_alert': dict(latest_backlog_mail) if latest_backlog_mail else None,
                              'timer_report': dict(timer_report) if timer_report else None},
            'exit_work': exit_work,
            'rug_pulls': rug_pulls,
            'entry_catchup_pause': state.get('entry_catchup_pause'),
            'exit_comparison': comparison_exits,
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
            'exit_monitor': {'held_batch_size': HELD_BATCH, 'routine_quote_seconds': ROUTINE_EXIT_INTERVAL,
                             'urgent_positions': sum(p.get('exit_priority') == 0 for b in state['bots'].values()
                                                     for p in b['positions'].values()),
                             'fresh_market_positions': sum(bool(fresh_market(p, state, now)) for b in state['bots'].values()
                                                           for p in b['positions'].values())},
            'discovery': {'batch_size': DISCOVERY_BATCH, 'interval_seconds': DISCOVERY_INTERVAL,
                          'instant_entry_window_seconds': ENTRY_WINDOW,
                          'coverage': 'Discoverable active Solana pairs; not the entire meme-coin market.'}}


def archive(before=None):
    with connection() as conn:
        state,_=read(conn)
        rows=conn.execute("SELECT rowid AS cursor,* FROM local_strategies WHERE status IN ('failed','inactive') AND rowid<? ORDER BY rowid DESC LIMIT 26",
                          (before if before is not None else 2**63-1,)).fetchall()
        stats={row['version']:dict(row) for row in conn.execute("SELECT json_extract(data,'$.policy.local_rule.id') AS version,count(*) AS closed,COALESCE(sum(pnl),0)/1000000.0 AS net FROM trades WHERE bot LIKE 'research-%' GROUP BY version")}
        items=[]
        for row in rows[:25]:
            sample=stats.get(row['id'],{})
            items.append({'id':row['id'],'slot':row['slot'],'created':row['created'],'status':row['status'],
                          'rule':json.loads(row['rule']),'report':json.loads(row['report']),
                          'closed':sample.get('closed',0),'net':sample.get('net',0),
                          'open':sum(p.get('policy',{}).get('local_rule',{}).get('id')==row['id'] for p in state['bots'][row['slot']]['positions'].values())})
    return {'strategies':items,'next':rows[24]['cursor'] if len(rows)>25 else None}


def history(jarvis, before=None, bot=None, rugs_only=False):
    limit = 25
    condition = "bot='jarvis'" if jarvis else "bot!='jarvis'"
    parameters = ()
    if bot is not None:
        if bot not in BOTS:
            raise ValueError('Unknown paper bot.')
        condition, parameters = 'bot=?', (bot,)
    if rugs_only:
        condition = "json_extract(data,'$.paper_writeoff.category')='no_route'"
        parameters = ()
    with connection() as conn:
        groups = conn.execute(
            f"""SELECT mint, MAX(id) AS last_id FROM trades WHERE {condition}
                GROUP BY mint HAVING MAX(id) < ? ORDER BY last_id DESC LIMIT ?""",
            (*parameters, before if before is not None else 2**63-1, limit + 1),
        ).fetchall()
        output = []
        for group in groups[:limit]:
            trades = [json.loads(row['data']) for row in conn.execute(
                f'SELECT data FROM trades WHERE {condition} AND mint=? ORDER BY id DESC', (*parameters, group['mint']))]
            output.append({'mint': group['mint'], 'symbol': trades[0]['symbol'], 'trades': [
                {'bot': BOTS[t['bot']][0], 'pnl': t['pnl'] / DOLLAR, 'cost': t['entry_cost'] / DOLLAR,
                 'proceeds': t['banked'] / DOLLAR, 'reason': t['reason'], 'closed': t['closed'],
                 'held_seconds': max(0, int(t['closed'] - t['opened'])),
                 'paper_writeoff': t.get('paper_writeoff'),
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


def chart_coverage(conn, state, now):
    starts = [rule['created'] for rule in state.get('local_rules', {}).values()]
    rows = [row for row in state['watch'].values()
            if starts and not row.get('monitor_only') and 0 <= now - row['last'] <= 90
            and 0 <= now - row['first'] <= ENTRY_WINDOW and row['first'] >= min(starts)
            and now >= row.get('retry', 0)]
    records = {r['mint']: (r['pair'], json.loads(r['evidence']))
               for r in conn.execute('SELECT mint,pair,evidence FROM research WHERE checked>=?', (now - 900,))}
    return research.chart_coverage(rows, records, now)


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
    for source, interval in (('chart', research.CHART_INTERVAL), ('bluesky', 30), ('x', research.X_INTERVAL)):
        if source == 'x' and not os.getenv('JARVIS_X_BEARER_TOKEN', '').strip():
            continue
        due = conn.execute('SELECT due FROM research_tasks WHERE name=?', (source,)).fetchone()
        if due and due['due'] > now:
            continue

        def priority(row):
            pair, evidence = records.get(row['mint'], (None, {}))
            last = evidence.get(source, {}).get('at', 0) if pair == row['pair'] else 0
            record=evidence.get(source,{}) if pair==row['pair'] else {}
            fresh = record.get('value') is not None and 0 <= now-last < research.FRESH_SECONDS
            if source=='chart':
                local_held=any(row['mint'] in state['bots'][slot]['positions'] for slot in local_lab.SLOTS)
                eligible=(not row.get('monitor_only') and 0<=now-row['first']<=900
                          and any(row['first']>=rule['created'] for rule in state.get('local_rules',{}).values()))
                # Research opportunities precede old preset holdings; oldest
                # attempt wins within each group so new discoveries cannot starve it.
                tier=0 if local_held else 1 if eligible else 2 if row['mint'] in study_mints else 3
                return (tier, research.chart_is_fresh(record, now),
                        record.get('attempt_at', last), -row['first'])
            return (fresh, row['mint'] not in study_mints, last if fresh else -row['first'])

        candidates=rows
        if source=='chart':
            def due_for_chart(row):
                if research.pool_warming(row):
                    return False
                pair,evidence=records.get(row['mint'],(None,{}))
                record=evidence.get('chart',{}) if pair==row['pair'] else {}
                due=record.get('next_attempt',0)
                if record.get('value') is not None:
                    due=max(due,record.get('at',0)+research.CHART_REFRESH)
                return now>=due
            candidates=[row for row in rows if due_for_chart(row)]
            if not candidates:
                continue

        if not tasks:
            # Keep recurring storage bounded and retain recent coverage for diagnosis.
            conn.execute('DELETE FROM research WHERE checked<?', (now - 900,))
        chosen = min(candidates, key=priority)
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
        http_status=None
        retry_after=None
        try:
            value = chart(row, clock()) if source == 'chart' else social(source, row, clock())
            result_status='ready' if value is not None else 'insufficient_history' if source=='chart' else 'unavailable'
        except research.ChartUnavailable as error:
            result_status, value = error.status, None
        except Exception as error:
            response = getattr(error, 'response', None)
            http_status = getattr(response, 'status_code', None)
            if http_status == 429:
                retry_after = getattr(response, 'headers', None)
            result_status='rate_limited' if http_status==429 else 'provider_error'
            LOG.warning('Jarvis %s research is unavailable (%s%s)', source,result_status,
                        f', HTTP {http_status}' if http_status is not None else '')
            value = None
        at = clock()
        with connection() as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.execute('SELECT evidence FROM research WHERE mint=? AND pair=?',
                                    (row['mint'], row['pair'])).fetchone()
            evidence = json.loads(existing['evidence']) if existing else {}
            previous=evidence.get(source,{})
            # Preserve a previously valid observation on a failed refresh, but
            # never extend its timestamp or the existing candle freshness limit.
            record={'at':at,'value':value} if value is not None else {
                'at':previous.get('at',at),'value':previous.get('value')}
            retry = (research.rate_limit_delay(retry_after, at) if source=='chart' and http_status==429
                     else research.CHART_REFRESH if value is not None
                     else 120 if result_status in ('insufficient_history', 'stale_candles') else 30)
            record.update(attempt_at=at,status=result_status,next_attempt=at+retry)
            if http_status is not None:record['http_status']=http_status
            evidence[source] = record
            if source=='chart' and http_status==429:
                conn.execute("""INSERT INTO research_tasks(name,due) VALUES ('chart',?)
                    ON CONFLICT(name) DO UPDATE SET due=max(due,excluded.due)""", (at + retry,))
            conn.execute('INSERT OR REPLACE INTO research(mint,pair,checked,evidence) VALUES (?,?,?,?)',
                         (row['mint'], row['pair'], at, json.dumps(evidence)))


def research_diagnostic():
    now=time.time()
    with connection() as conn:
        state,_=read(conn)
        entries=local_entry_status(conn,state,now)
        with conn:
            audit.refresh(conn,state,entries,now)
        counts={row['bot']:dict(row) for row in conn.execute(
            "SELECT bot,count(*) AS closed FROM trades WHERE bot LIKE 'research-%' GROUP BY bot")}
        slots=[]
        for slot in local_lab.SLOTS:
            slots.append({'slot':slot,'closed':counts.get(slot,{}).get('closed',0),
                          'open':len(state['bots'][slot]['positions']),**entries[slot]})
        queue=exit_queue(quote_jobs(state,now),now)
        coverage_details = chart_coverage(conn, state, now)
        chart_status={row['status'] or 'legacy':row['count'] for row in conn.execute(
            "SELECT json_extract(evidence,'$.chart.status') AS status,count(*) AS count FROM research WHERE checked>=? GROUP BY status",(now-900,))}
    # Fixed labels and aggregate counts only: no tokens, wallet/coin IDs,
    # provider bodies, generated source content or rule expressions.
    pending_pbh=sum(bool(p.get('owner_closeout')) for b in state['bots'].values() for p in b['positions'].values())
    failures={}
    for b in state['bots'].values():
        for p in b['positions'].values():
            category=(p.get('route_failure') or {}).get('category')
            if category:failures[category]=failures.get(category,0)+1
    with connection() as conn:
        sold=conn.execute("SELECT count(*) FROM trades WHERE json_extract(data,'$.reason')='PBH owner-requested paper closeout'").fetchone()[0]
        written=conn.execute("SELECT count(*) FROM trades WHERE json_extract(data,'$.owner_closeout')=1 AND json_extract(data,'$.paper_writeoff') IS NOT NULL").fetchone()[0]
    return {'enabled':state['enabled'],'presets_paused':bool(state.get('preset_entries_paused')),
            'pbh_closeout':{'requested':(state.get('pbh_closeout_v1') or {}).get('positions',0),'quoted_closes':sold,'writeoffs':written,'pending':pending_pbh},'quote_failure_categories':failures,
            'cycle_age':round(now-state['last_cycle']) if state['last_cycle'] else None,
            'oldest_quote_wait':round(queue['oldest_wait']),'chart_status':chart_status, 'chart_coverage':coverage_details, 'slots':slots}


async def diagnostic_worker():
    await asyncio.sleep(15)
    startup_checks=0
    while True:
        report={}
        try:
            report=await asyncio.to_thread(research_diagnostic)
            print('Jarvis research diagnostic '+json.dumps(report,separators=(',',':')),flush=True)
        except Exception:
            LOG.warning('Jarvis research diagnostic unavailable')
        startup_checks+=1
        await asyncio.sleep(30 if startup_checks<3 or (startup_checks<10 and report.get('pbh_closeout',{}).get('pending')) else 300)


async def research_worker():
    while True:
        try:
            await asyncio.to_thread(research_tick)
            await asyncio.to_thread(refresh_legacy_outcomes, time.time())
            import sys
            if os.getenv('JARVIS_RESEARCH_PROVIDER') == 'openai':
                await asyncio.to_thread(autonomy.tick, sys.modules[__name__])
        except Exception:
            LOG.exception('Jarvis research cycle failed')
        await asyncio.sleep(2)


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
        if state['enabled'] and not state.get('preset_entries_paused') and not catchup_requested(state) and not backlog.paused(state, now) and len(active) < experiments.MAX_OPEN and (not last or now - last['opened'] >= experiments.START_INTERVAL):
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
                if (state['enabled'] and not state.get('preset_entries_paused') and not catchup_requested(state) and not backlog.paused(state, now)) or side == 'sell':
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
