"""Owner-triggered forward strategy experiments; no execution capabilities."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import json
import re
import time
import uuid
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
import database
from day_trader_strategy import VERSION, POLICY, evaluate, simulate, stamp
from execution_quotes import read_quote
from pro_ticker_http_security import require_same_origin

SCHEMA = '''CREATE TABLE IF NOT EXISTS day_trader_experiments (
 request_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, frozen_at REAL NOT NULL,
 decision_json TEXT NOT NULL, outcome_json TEXT);'''
LIMITATIONS = [
    'Experimental long-only rules; not a replication of published returns or a trained forecast.',
    'IEX volume and VWAP cover one venue, not the consolidated market. No feed changes occur.',
    'Forward OHLC simulations are not orders, fills, realized profits, or a portfolio backtest.',
    'Stops can gap; same-bar stop/target ambiguity is resolved against the strategy.',
    'Cost scenarios are assumptions, not measured execution costs. No profitability established.',
    'Current automatic and Pro Ticker execution rules are unchanged. Experiments place no orders.',
    'Selected-symbol sampling is biased; overlapping hypotheses are correlated. No automatic promotion.',
]


class Conflict(ValueError):
    pass


def _initialize(conn):
    conn.executescript(SCHEMA)


def status():
    with closing(database.get_connection()) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='day_trader_experiments'").fetchone():
            rows = []
        else:
            rows = conn.execute('SELECT * FROM day_trader_experiments ORDER BY frozen_at DESC LIMIT 200').fetchall()
    results = [{'request_id':r['request_id'], 'symbol':r['symbol'], 'frozen_at':r['frozen_at'],
                'decision':json.loads(r['decision_json']),
                'outcomes':json.loads(r['outcome_json']) if r['outcome_json'] else None} for r in rows]
    summary = []
    for name in ('opening_range_breakout', 'vwap_pullback'):
        outcomes = [o for r in results for o in r['outcomes'] or [] if o['strategy']==name and o['status']=='modeled']
        summary.append({'strategy':name, 'modeled_samples':len(outcomes),
                        'base_cost_modeled_pnl_usd':round(sum(o['scenarios'][0]['modeled_pnl_usd'] for o in outcomes),4) if outcomes else None,
                        'base_cost_matched_hold_pnl_usd':round(sum(o['scenarios'][0]['matched_hold_pnl_usd'] for o in outcomes),4) if outcomes else None})
    return {'version':VERSION, 'research_only':True, 'orders_enabled':False, 'policy':POLICY,
            'limitations':LIMITATIONS, 'summary':summary, 'results':results[:20],
            'summary_scope':'Most recent 200 experiments; overlapping per-symbol simulations, not portfolio P/L.'}


def history(symbol, *, start, end, feed, data_request):
    collected = []
    token = None
    for _ in range(2):
        params = {'timeframe':'1Min', 'start':start, 'end':end, 'limit':10000,
                  'feed':feed, 'adjustment':'raw', 'sort':'asc'}
        if token:
            params['page_token'] = token
        payload = data_request('GET', f'/v2/stocks/{symbol}/bars', params=params, timeout=3.0)
        if not isinstance(payload, dict) or not isinstance(payload.get('bars'), list):
            raise ValueError('Minute history unavailable.')
        collected.extend(payload['bars'])
        if len(collected)>20000:
            raise ValueError('Minute history exceeds bounded request.')
        token = payload.get('next_page_token')
        if not token:
            return collected
    raise ValueError('History pagination incomplete; no partial evidence used.')


def observe(payload, *, data_request, broker_read, feed, clock=time.time):
    if not isinstance(payload,dict) or set(payload)!={'request_id','symbol'}:
        raise ValueError('One symbol and request ID required.')
    request_id = str(uuid.UUID(payload['request_id']))
    symbol = payload['symbol']
    if request_id != payload['request_id'] or not isinstance(symbol,str) or not re.fullmatch(r'[A-Z][A-Z0-9.]{0,9}',symbol):
        raise ValueError('Canonical request ID and stock symbol required.')
    if feed not in ('iex','sip'):
        raise ValueError('Configured feed unavailable.')
    with closing(database.get_connection()) as conn:
        _initialize(conn)
        row = conn.execute('SELECT symbol FROM day_trader_experiments WHERE request_id=?',(request_id,)).fetchone()
        if row:
            if row['symbol'] != symbol:
                raise Conflict('Request ID belongs to another symbol.')
            return status()
    now = datetime.fromtimestamp(clock(),timezone.utc)
    local = now.astimezone(ZoneInfo('America/New_York'))
    day = local.date().isoformat()
    calendar = broker_read('GET','/v2/calendar',params={'start':day,'end':day},timeout=3.0)
    if not isinstance(calendar,list) or len(calendar)!=1 or calendar[0].get('date')!=day:
        raise ValueError('Current exchange session unavailable.')
    def session_time(key):
        return datetime.fromisoformat(day+'T'+calendar[0][key]).replace(tzinfo=ZoneInfo('America/New_York')).timestamp()
    opened, closed = session_time('open'), session_time('close')
    if not opened+360 <= clock() < closed-1800:
        raise ValueError('Observe during the regular-session entry window, after 09:36 ET and before the final 30 minutes.')
    raw = history(symbol,start=(now-timedelta(days=30)).isoformat(),end=now.isoformat(),feed=feed,data_request=data_request)
    quote = read_quote(symbol,data_request,feed=feed)
    frozen = clock()
    from day_trader_strategy import bars
    sessions = {}
    for bar in bars(raw,observed_at=frozen):
        dt = datetime.fromtimestamp(bar['t'],ZoneInfo('America/New_York'))
        if dt.date()<local.date() and dt.hour==9 and 30<=dt.minute<35:
            sessions.setdefault(dt.date(),[]).append(bar)
    volumes = [sum(b['v'] for b in sessions[d]) for d in sorted(sessions)
               if len(sessions[d])==5 and sessions[d][0]['t']%60==0]
    decision = evaluate(raw,quote,session_open=opened,session_close=closed,observed_at=frozen,opening_volumes=volumes)
    with closing(database.get_connection()) as conn:
        # One frozen set per symbol/session/minute, even across browser retries.
        conn.execute('BEGIN IMMEDIATE')
        prior = conn.execute('SELECT symbol FROM day_trader_experiments WHERE request_id=?',(request_id,)).fetchone()
        if prior and prior['symbol']!=symbol:
            raise Conflict('Request ID belongs to another symbol.')
        duplicate = conn.execute('SELECT 1 FROM day_trader_experiments WHERE symbol=? AND frozen_at>=? AND frozen_at<?',
                                 (symbol,int(frozen//60)*60,int(frozen//60)*60+60)).fetchone()
        if not prior and not duplicate:
            conn.execute('INSERT INTO day_trader_experiments VALUES (?,?,?,?,NULL)',
                         (request_id,symbol,frozen,json.dumps(decision,allow_nan=False)))
        conn.commit()
    return status()


def evaluate_saved(request_id, *, data_request, clock=time.time):
    if str(uuid.UUID(request_id))!=request_id:
        raise ValueError('Canonical experiment ID required.')
    with closing(database.get_connection()) as conn:
        _initialize(conn)
        row = conn.execute('SELECT * FROM day_trader_experiments WHERE request_id=?',(request_id,)).fetchone()
    if not row:
        raise ValueError('Saved experiment required.')
    if row['outcome_json']:
        return status()
    decision = json.loads(row['decision_json'])
    plans = decision['setups']
    if not any(p['status']=='candidate' for p in plans):
        return status()
    due = max(p['expires_at']+60 for p in plans if p['status']=='candidate')
    if clock()<due:
        result = status()
        result['evaluation'] = [{'status':'pending','evaluate_after':due}]
        return result
    raw = history(row['symbol'],start=datetime.fromtimestamp(row['frozen_at'],timezone.utc).isoformat(),
                  end=datetime.fromtimestamp(due,timezone.utc).isoformat(),feed=decision['feed'],data_request=data_request)
    outcomes = [{'strategy':p['strategy'],**simulate(p,raw,frozen_at=row['frozen_at'],observed_at=clock())} for p in plans]
    # Missing paths can be retried after provider recovery; modeled outcomes are immutable.
    if all(o['status'] in ('modeled','not_applicable') for o in outcomes):
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE day_trader_experiments SET outcome_json=? WHERE request_id=? AND outcome_json IS NULL',
                         (json.dumps(outcomes,allow_nan=False),request_id))
            conn.commit()
    result = status()
    result['evaluation'] = outcomes
    return result


def build_router(*,require_session,data_request,broker_read,feed):
    router = APIRouter()
    @router.get('/auto-trader/day-trader')
    def saved(request:Request):
        require_session(request)
        return status()
    async def body(request):
        require_session(request)
        require_same_origin(request)
        if request.headers.get('content-type','').split(';')[0]!='application/json':
            raise HTTPException(415,'Expected JSON.')
        raw = await request.body()
        if len(raw)>2048:
            raise HTTPException(413,'Request too large.')
        try:
            return json.loads(raw)
        except ValueError:
            raise HTTPException(422,'Valid JSON required.') from None
    @router.post('/auto-trader/day-trader/observe')
    async def capture(request:Request):
        payload = await body(request)
        try:
            return await run_in_threadpool(observe,payload,data_request=data_request,broker_read=broker_read,feed=feed())
        except Conflict as error:
            raise HTTPException(409,str(error)) from None
        except (ValueError,TypeError,KeyError) as error:
            raise HTTPException(422,'Intraday evidence unavailable or invalid: '+str(error)) from None
        except Exception:
            raise HTTPException(503,'Data unavailable or rate limited. No order sent; retry the same experiment.') from None
    @router.post('/auto-trader/day-trader/evaluate')
    async def measure(request:Request):
        payload = await body(request)
        if not isinstance(payload,dict) or set(payload)!={'request_id'}:
            raise HTTPException(422,'Saved experiment ID required.')
        try:
            return await run_in_threadpool(evaluate_saved,payload['request_id'],data_request=data_request)
        except (ValueError,TypeError,KeyError):
            raise HTTPException(422,'Saved experiment and complete minute path required.') from None
        except Exception:
            raise HTTPException(503,'Forward history unavailable. No order sent.') from None
    return router
