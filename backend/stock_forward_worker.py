"""Bounded automatic collection for existing read-only stock research tools.

The collector cannot send orders. It freezes new minute-bar experiments and
settles due experiments without requiring a browser click. Missed old manual
reviews are reported rather than measured as if their horizon were timely.
"""
import asyncio
from contextlib import closing
from copy import deepcopy
from datetime import datetime
import json
from queue import Empty, Full, Queue
import time
import uuid
from zoneinfo import ZoneInfo
import app_controls as controls
import database
from stock_momentum import number

_queue = Queue(maxsize=1)
_rotation = 0
SCHEMA = '''CREATE TABLE IF NOT EXISTS stock_forward_runtime(
 id INTEGER PRIMARY KEY CHECK(id=1), last_tick REAL NOT NULL, report_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stock_forward_attempts(request_id TEXT PRIMARY KEY,last_attempt REAL NOT NULL);'''


def enqueue_scanner(candidates):
    try:_queue.get_nowait()
    except Empty:pass
    try:_queue.put_nowait((deepcopy(candidates), time.time()))
    except Full:pass


def get_only(request):
    def read(method, path, **kwargs):
        if method != 'GET':raise ValueError('Research permits GET only')
        return request(method, path, **kwargs)
    return read


def step(data_request, broker_read, feed, quote_fetcher, *, now=None):
    import day_trader_lab as lab
    import pro_ticker_signal_learning as signals
    clock = (lambda: now) if now is not None else time.time
    at = clock()
    data_request, broker_read = get_only(data_request), get_only(broker_read)
    result = {'intraday_observed': 0, 'intraday_evaluated': 0, 'manual_reviews_evaluated': 0,
              'missed_manual_horizons': 0, 'errors': [], 'state': 'waiting_for_scan'}
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA + lab.SCHEMA + signals._SCHEMA)
        # Bound retries to recent observations: old holes cannot monopolize the
        # collector forever. Their missing outcomes remain visible in the lab.
        pending = conn.execute('''SELECT e.request_id,e.decision_json FROM day_trader_experiments e
            LEFT JOIN stock_forward_attempts a ON a.request_id='intraday:'||e.request_id
            WHERE outcome_json IS NULL AND frozen_at>=?
            AND EXISTS (SELECT 1 FROM json_each(e.decision_json,'$.setups') p WHERE json_extract(p.value,'$.status')='candidate')
            ORDER BY COALESCE(a.last_attempt,0),frozen_at LIMIT 100''', (at-7*86400,)).fetchall()
        reviews = []
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_manual_signal_reviews'").fetchone():
            reviews = conn.execute('''SELECT r.request_id,r.completed_at FROM pro_ticker_manual_signal_reviews r
                LEFT JOIN pro_ticker_signal_forward_outcomes o ON o.request_id=r.request_id
                LEFT JOIN stock_forward_attempts a ON a.request_id='manual:'||r.request_id
                WHERE r.status='graded' AND o.request_id IS NULL ORDER BY COALESCE(a.last_attempt,0),r.completed_at DESC LIMIT 500''').fetchall()
        last_observed = conn.execute('SELECT MAX(frozen_at) FROM day_trader_experiments').fetchone()[0] or 0
        conn.commit()
    due = []
    for row in pending:
        plans = json.loads(row['decision_json'])['setups']
        candidates = [p for p in plans if p['status']=='candidate']
        if candidates and at >= max(p['expires_at']+60 for p in candidates):due.append(row['request_id'])
    def attempted(kind,ident):
        with closing(database.get_connection()) as conn:
            conn.execute('INSERT INTO stock_forward_attempts VALUES(?,?) ON CONFLICT(request_id) DO UPDATE SET last_attempt=excluded.last_attempt',(kind+':'+ident,clock()));conn.commit()
    for ident in due[:2]:
        attempted('intraday',ident)
        try:
            measured = lab.evaluate_saved(ident, data_request=data_request, clock=clock)
            if measured.get('evaluation') and all(o['status'] in ('modeled','not_applicable') for o in measured['evaluation']):
                result['intraday_evaluated'] += 1
        except Exception:result['errors'].append('intraday_forward_path_unavailable')
    due_reviews = []
    for row in reviews:
        try:elapsed = at-signals._date(row['completed_at'])
        except (ValueError, TypeError):continue
        if elapsed > 48*3600:result['missed_manual_horizons'] += 1
        elif elapsed >= signals.HORIZON_SECONDS:due_reviews.append(row['request_id'])
    for ident in due_reviews[:2]:
        attempted('manual',ident)
        try:
            signals.evaluate_forward_outcome(ident, quote_fetcher=quote_fetcher, clock=clock)
            result['manual_reviews_evaluated'] += 1
        except Exception:result['errors'].append('manual_forward_quote_unavailable')
    current = controls.snapshot()['settings']
    local = datetime.fromtimestamp(at, ZoneInfo('America/New_York'))
    minute = local.hour*60+local.minute
    if not current['stock_parallel_validation_enabled']:result['state'] = 'paused'
    elif local.weekday() >= 5 or not 580 <= minute < 930:result['state'] = 'outside_session'
    elif at-last_observed < 300:result['state'] = 'sampling_interval'
    else:
        try:
            rows, queued_at = _queue.get_nowait()
        except Empty:rows, queued_at = [], at
        if not 0 <= at-queued_at <= 180:result['state'] = 'stale_scan'
        else:
            candidates = []
            for candidate in rows:
                symbol = candidate.get('symbol')
                price = number(candidate.get('price'))
                if not isinstance(symbol, str) or candidate.get('scanner_stale') is not False:continue
                if price is None or price <= 0:continue
                if current['stock_filters_enabled'] and (price < current['stock_min_price_usd'] or
                    current['stock_max_price_usd'] and price > current['stock_max_price_usd']):continue
                candidates.append(symbol)
            candidates = sorted(set(candidates))
            result['eligible_symbols'] = len(candidates)
            if candidates:
                global _rotation
                symbol = candidates[_rotation % len(candidates)];_rotation += 1
                ident = str(uuid.uuid5(uuid.NAMESPACE_URL, f'stock-intraday:{symbol}:{int(at//300)}'))
                try:
                    lab.observe({'request_id': ident, 'symbol': symbol}, data_request=data_request,
                                broker_read=broker_read, feed=feed(), clock=clock)
                    result['intraday_observed'] = 1
                    result['state'] = 'observed'
                    result['symbol'] = symbol
                except Exception:
                    result['state'] = 'evidence_unavailable'
                    result['errors'].append('intraday_observation_unavailable')
            elif rows:result['state'] = 'no_eligible_symbols'
    with closing(database.get_connection()) as conn:
        conn.execute('INSERT INTO stock_forward_runtime VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET last_tick=excluded.last_tick,report_json=excluded.report_json',
                     (clock(), json.dumps(result, allow_nan=False)))
        conn.commit()
    return result


def status(conn):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='stock_forward_runtime'").fetchone()
    row = conn.execute('SELECT * FROM stock_forward_runtime WHERE id=1').fetchone() if exists else None
    return {'last_tick': row['last_tick'] if row else None, 'report': json.loads(row['report_json']) if row else None,
            'basis': 'Automatic read-only research: up to one intraday symbol every five minutes, two due minute-path evaluations and two manual-review markouts per cycle. Intraday selection is independent of the daily BUY gate. New manual grades are measured after 24–48 hours, with actual elapsed time retained. Older missed horizons remain unmeasured. No orders, model training or automatic strategy promotion.'}


async def worker(data_request, broker_read, feed, quote_fetcher):
    while True:
        try:await asyncio.to_thread(step, data_request, broker_read, feed, quote_fetcher)
        except Exception:
            try:
                with closing(database.get_connection()) as conn:
                    conn.executescript(SCHEMA)
                    conn.execute('INSERT INTO stock_forward_runtime VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET last_tick=excluded.last_tick,report_json=excluded.report_json',
                                 (time.time(), json.dumps({'state':'retrying','errors':['research_collection_failed']})))
                    conn.commit()
            except Exception:
                pass  # A telemetry write failure must not stop future collection.
        await asyncio.sleep(60)
