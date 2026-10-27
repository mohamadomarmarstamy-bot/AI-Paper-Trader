"""Immutable, idempotent entry-time research grades. Never submit broker orders.

These provisional grades reuse the existing daily technical analysis; they are
not an AI profit forecast, proof of a Pro Ticker alert, or execution permission.
"""
from pro_ticker_http_security import require_same_origin
from contextlib import closing
from datetime import datetime, timezone
import json
import math
import time
import uuid
from fastapi import APIRouter, Request, HTTPException
import database
from pro_ticker_manual_intake import parse_manual_symbols

VERSION = 'pro_ticker_daily_technical_grade_v1'
REFERENCE_MAX_AGE_SECONDS = 90
_SCHEMA = '''CREATE TABLE IF NOT EXISTS pro_ticker_manual_signal_reviews (
 request_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, submitted_at TEXT NOT NULL,
 lease_until REAL NOT NULL, status TEXT NOT NULL,
 completed_at TEXT, snapshot_json TEXT, grade_json TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS pro_ticker_one_pending_signal_review
 ON pro_ticker_manual_signal_reviews(symbol) WHERE status='pending';'''


class SignalConflict(ValueError):
    pass


def _finite(value):
    return not isinstance(value,bool) and isinstance(value,(int,float)) and math.isfinite(value)


def quote_snapshot(raw, *, now):
    base = {'source':'alpaca_latest_trade','captured_at':datetime.fromtimestamp(now,timezone.utc).isoformat(),
            'status':'unavailable','price':None,'market_timestamp':None,'fresh':False}
    if not isinstance(raw,dict) or not _finite(raw.get('p')) or raw['p']<=0 or not isinstance(raw.get('t'),str):
        return base
    try:
        stamp=datetime.fromisoformat(raw['t'].replace('Z','+00:00'))
        if stamp.tzinfo is None: return base
        age=now-stamp.timestamp()
    except (ValueError,OverflowError): return base
    if age<0: return base
    return {**base,'status':'captured','price':raw['p'],'market_timestamp':raw['t'],
            'age_seconds':age,'fresh':age<=REFERENCE_MAX_AGE_SECONDS}


def technical_grade(raw, symbol):
    base={'version':VERSION,'status':'unavailable','grade':None,'score':None,'confidence':None,
          'confidence_kind':'heuristic_not_win_probability','training_eligible':False,
          'analysis_market_timestamp':None,
          'limitations':['Daily technical analysis, not a verified intraday Pro Ticker signal.',
                         'Indicator as-of time and market regime are unverified.',
                         'No automatic execution or model training is authorized by this grade.']}
    if not isinstance(raw,dict) or raw.get('symbol')!=symbol or raw.get('signal') not in {'BUY','HOLD','SELL'}:
        return base
    score,confidence=raw.get('score'),raw.get('confidence')
    if not _finite(score) or not float(score).is_integer() or not -6<=score<=6 or not _finite(confidence) or not 0<=confidence<=100:
        return base
    expected='BUY' if score>=3 else 'SELL' if score<=-3 else 'HOLD'
    if raw['signal']!=expected:return base
    indicators=raw.get('indicators')
    keys=('sma_20','sma_50','rsi','five_day_momentum','volume_ratio')
    if not isinstance(indicators,dict) or any(not _finite(indicators.get(k)) for k in keys[:-1]): return base
    if indicators['sma_20']<=0 or indicators['sma_50']<=0 or not 0<=indicators['rsi']<=100:return base
    if indicators.get('volume_ratio') is not None and (not _finite(indicators['volume_ratio']) or indicators['volume_ratio']<0):return base
    reasons=raw.get('reason')
    if not isinstance(reasons,list) or len(reasons)>30 or any(not isinstance(r,str) or len(r)>1000 for r in reasons):return base
    return {**base,'status':'graded','grade':'A' if score>=4 else 'B' if score>=3 else 'D' if score<=-3 else 'C',
            'score':int(score),'score_scale':'legacy_daily_minus6_to_plus6','signal':expected,
            'confidence':confidence,'indicators':{k:indicators.get(k) for k in keys},'reasons':reasons}


def _record(row,now):
    value=dict(row)
    value.pop('lease_until',None)
    if value['status']=='pending' and row['lease_until']<=now:value['status']='interrupted'
    snapshot=value.pop('snapshot_json');grade=value.pop('grade_json')
    value['market_snapshot']=json.loads(snapshot) if snapshot else None
    value['entry_grade']=json.loads(grade) if grade else None
    return value


def list_manual_signals():
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN')
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_manual_signal_reviews'").fetchone()
        rows=conn.execute('SELECT * FROM pro_ticker_manual_signal_reviews ORDER BY submitted_at DESC,request_id DESC LIMIT 100').fetchall() if exists else []
        count=conn.execute('SELECT COUNT(*) FROM pro_ticker_manual_signal_reviews').fetchone()[0] if exists else 0
        from pro_ticker_signal_learning import outcomes_for_signals
        outcomes=outcomes_for_signals(conn,[r['request_id'] for r in rows])
        from pro_ticker_signal_proposals import proposals_for_signals
        proposals=proposals_for_signals(conn,[r['request_id'] for r in rows])
        from pro_ticker_order_intents import intents_for_proposals
        intents=intents_for_proposals(conn,[p['request_id'] for p in proposals.values()])
    from pro_ticker_manual_scanner import saved_entries
    manual_entries=saved_entries()
    return {'paper':True,'orders_submitted':0,'automatic_execution_enabled':False,
            'signals':[{**_record(r,time.time()),'forward_outcome':outcomes.get(r['request_id']),'manual_entry':manual_entries.get(r['request_id']),'proposal':proposals.get(r['request_id']),'order_intent':intents.get(proposals.get(r['request_id'],{}).get('request_id'))} for r in rows],'total_count':count,'has_more':count>len(rows)}


def submit_manual_signal(payload, *, quote_fetcher, analyzer, clock=time.time, research_collector=None):
    if not isinstance(payload,dict) or set(payload)!={'symbol','request_id'}:raise ValueError('Provide symbol and request ID only.')
    symbols=parse_manual_symbols(payload['symbol'])
    if len(symbols)!=1:raise ValueError('One stock is required.')
    symbol=symbols[0]
    try:
        request_id=str(uuid.UUID(payload['request_id']))
        if request_id!=payload['request_id']:raise ValueError('Canonical UUID required.')
    except (ValueError,TypeError,AttributeError):raise ValueError('Canonical UUID required.') from None
    now=clock()
    with closing(database.get_connection()) as conn:
        conn.executescript(_SCHEMA);conn.execute('BEGIN IMMEDIATE')
        conn.execute("UPDATE pro_ticker_manual_signal_reviews SET status='interrupted' WHERE status='pending' AND lease_until<=?",(now,))
        existing=conn.execute('SELECT * FROM pro_ticker_manual_signal_reviews WHERE request_id=?',(request_id,)).fetchone()
        if existing:
            if existing['symbol']!=symbol:raise SignalConflict('Request ID belongs to a different stock.')
            conn.commit()
            return {**list_result(existing,now),'duplicate':True}
        if conn.execute("SELECT 1 FROM pro_ticker_manual_signal_reviews WHERE symbol=? AND status='pending'",(symbol,)).fetchone():
            raise SignalConflict('This stock is already being graded. Refresh its saved status.')
        conn.execute("INSERT INTO pro_ticker_manual_signal_reviews(request_id,symbol,submitted_at,lease_until,status) VALUES (?,?,?,?,'pending')",
                     (request_id,symbol,datetime.fromtimestamp(now,timezone.utc).isoformat(),now+300))
        conn.commit()
    # Snapshot the entry reference BEFORE the slower daily analysis. No later
    # outcome, article high or reported move is allowed into the entry grade.
    try: snapshot=quote_snapshot(quote_fetcher(symbol),now=clock())
    except Exception: snapshot=quote_snapshot(None,now=clock())
    try: grade=technical_grade(analyzer(symbol),symbol)
    except Exception: grade=technical_grade(None,symbol)
    if research_collector is not None:
        try:
            grade['research_context'] = research_collector(symbol, snapshot)
        except Exception:
            grade['research_context'] = {'status': 'unavailable', 'reason': 'Research evidence collection failed.', 'research_only': True}
    finished=clock()
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("UPDATE pro_ticker_manual_signal_reviews SET status=?,completed_at=?,snapshot_json=?,grade_json=? WHERE request_id=? AND status='pending' AND lease_until>?",
            (grade['status'],datetime.fromtimestamp(finished,timezone.utc).isoformat(),json.dumps(snapshot,allow_nan=False),json.dumps(grade,allow_nan=False),request_id,finished))
        conn.commit()
        row=conn.execute('SELECT * FROM pro_ticker_manual_signal_reviews WHERE request_id=?',(request_id,)).fetchone()
    return {**list_result(row,finished),'duplicate':False}


def list_result(row,now):
    from pro_ticker_signal_learning import outcomes_for_signals
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN')
        outcome=outcomes_for_signals(conn,[row['request_id']]).get(row['request_id'])
        from pro_ticker_signal_proposals import proposals_for_signals
        proposal=proposals_for_signals(conn,[row['request_id']]).get(row['request_id'])
        from pro_ticker_order_intents import intents_for_proposals
        intent=intents_for_proposals(conn,[proposal['request_id']]).get(proposal['request_id']) if proposal else None
    from pro_ticker_manual_scanner import saved_entries
    manual_entry=saved_entries().get(row['request_id'])
    return {'paper':True,'orders_submitted':0,'automatic_execution_enabled':False,
            'signal':{**_record(row,now),'manual_entry':manual_entry,'forward_outcome':outcome,'proposal':proposal,'order_intent':intent},'execution_blocked_reason':'broker_execution_and_outcome_learning_not_connected'}


def build_manual_signal_router(*,require_session,quote_fetcher,analyzer,execution_context=None,research_collector=None,manual_entry_context=None):
    from starlette.concurrency import run_in_threadpool
    router=APIRouter()
    @router.get('/auto-trader/pro-ticker/manual-entry-status')
    def manual_entry_status(request:Request):
        require_session(request)
        from pro_ticker_manual_scanner import status
        context=manual_entry_context() if manual_entry_context is not None else None
        enabled=bool(context is not None and context.enabled() is True and context.paper_base_url=='https://paper-api.alpaca.markets')
        from fastapi.responses import JSONResponse
        return JSONResponse(status(enabled=enabled,premarket_enabled=enabled and context.premarket_enabled() is True,quote_feed=context.quote_feed if context else 'iex'),headers={'Cache-Control':'no-store'})

    @router.post('/auto-trader/pro-ticker/manual-signals/buy-and-scan')
    async def buy_and_scan(request:Request):
        require_session(request)
        require_same_origin(request)
        if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':raise HTTPException(415,'Expected JSON.')
        body=await request.body()
        if len(body)>2048:raise HTTPException(413,'Request too large.')
        from pro_ticker_manual_scanner import submit_entry,EntryBlocked
        context=manual_entry_context() if manual_entry_context is not None else None
        try:
            if context is None:raise EntryBlocked('Manual PAPER mode is not configured.')
            context.validate()
        except EntryBlocked:raise HTTPException(403,'Manual PAPER buying and scanner management are disabled.') from None
        try:
            payload=json.loads(body)
            result=await run_in_threadpool(submit_manual_signal,payload,quote_fetcher=quote_fetcher,analyzer=analyzer,research_collector=research_collector)
            entry=await run_in_threadpool(submit_entry,payload['request_id'],result['signal']['symbol'],context=context)
            return {'paper':True,'kind':'owner_manual_scanner_v1','live_trading':False,
                    'signal':{**result['signal'],'manual_entry':entry},'entry':entry}
        except EntryBlocked as error:raise HTTPException(409,str(error)) from None
        except SignalConflict:raise HTTPException(409,'Existing review or order is active; refresh saved status.') from None
        except ValueError:raise HTTPException(422,'Provide one symbol and canonical request ID only.') from None
        except Exception:raise HTTPException(503,'Unable to confirm the PAPER entry. Refresh the same request; never create a second buy to recover.') from None

    @router.post('/auto-trader/pro-ticker/manual-signals/proposal')
    async def proposal(request:Request):
        require_session(request)
        require_same_origin(request)
        if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':raise HTTPException(415,'Expected JSON.')
        body=await request.body()
        if len(body)>2048:raise HTTPException(413,'Request too large.')
        from pro_ticker_signal_proposals import save_signal_proposal,ProposalConflict
        try:return await run_in_threadpool(save_signal_proposal,json.loads(body),quote_fetcher=quote_fetcher)
        except ProposalConflict:raise HTTPException(409,'A draft already exists for this review or request ID. Refresh saved status.') from None
        except ValueError:raise HTTPException(422,'A saved BUY grade, valid sizing, fresh server quote and sufficient virtual budget within risk limits are required.') from None
        except Exception:raise HTTPException(503,'Draft unavailable. Refresh saved status before retrying.') from None
    @router.get('/auto-trader/pro-ticker/manual-signals/learning')
    def learning(request:Request):
        require_session(request)
        from pro_ticker_signal_learning import learning_summary
        try:return learning_summary()
        except Exception:raise HTTPException(503,'Forward research summary unavailable.') from None
    @router.post('/auto-trader/pro-ticker/manual-signals/{request_id}/forward-review')
    async def forward(request_id:str,request:Request):
        require_session(request)
        require_same_origin(request)
        if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':raise HTTPException(415,'Expected JSON.')
        body=await request.body()
        if len(body)>2048:raise HTTPException(413,'Request too large.')
        from pro_ticker_signal_learning import evaluate_forward_outcome,ForwardNotDue
        try:
            if json.loads(body)!={}:raise ValueError('No outcome data accepted from browser.')
            return await run_in_threadpool(evaluate_forward_outcome,request_id,quote_fetcher=quote_fetcher)
        except ForwardNotDue:raise HTTPException(409,'Forward quote must be at least 24 hours after grading completed.') from None
        except ValueError:raise HTTPException(422,'Completed grade, fresh reference and a valid fresh forward quote are required. No browser-supplied outcome is accepted.') from None
        except Exception:raise HTTPException(503,'Forward research evaluation unavailable. Refresh saved status before retrying.') from None
    @router.get('/auto-trader/pro-ticker/manual-signals/{request_id}/research-context')
    def saved_context(request_id:str,request:Request):
        require_session(request)
        from pro_ticker_signal_proposals import _uuid
        from pro_ticker_research_evidence import research_report
        from fastapi.responses import JSONResponse
        try:_uuid(request_id)
        except ValueError:raise HTTPException(422,'Canonical review ID required.') from None
        try:
            with closing(database.get_connection()) as conn:
                exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_manual_signal_reviews'").fetchone()
                row=conn.execute('SELECT grade_json FROM pro_ticker_manual_signal_reviews WHERE request_id=?',(request_id,)).fetchone() if exists else None
            context=json.loads(row['grade_json']).get('research_context') if row and row['grade_json'] else None
            if not isinstance(context,dict) or 'evidence' not in context:
                raise HTTPException(404,'Saved research evidence unavailable for this review.')
            result=research_report(context['evidence'])
            if result!=context['report']:raise ValueError('Saved evidence/report differs.')
            return JSONResponse({'paper':True,'report':result,'evidence':context['evidence'],'replayed':True},headers={'Cache-Control':'no-store'})
        except HTTPException:raise
        except Exception:raise HTTPException(503,'Saved research evidence could not be verified.') from None
    @router.get('/auto-trader/pro-ticker/manual-signals')
    def history(request:Request):
        require_session(request)
        try:return list_manual_signals()
        except Exception:raise HTTPException(503,'Manual signal history unavailable.') from None
    @router.post('/auto-trader/pro-ticker/manual-signals')
    async def submit(request:Request):
        require_session(request)
        require_same_origin(request)
        if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':raise HTTPException(415,'Expected JSON.')
        body=await request.body()
        if len(body)>2048:raise HTTPException(413,'Request too large.')
        try:return await run_in_threadpool(submit_manual_signal,json.loads(body),quote_fetcher=quote_fetcher,analyzer=analyzer,research_collector=research_collector)
        except SignalConflict:raise HTTPException(409,'Stock review already pending or request ID reused. Refresh saved status.') from None
        except ValueError:raise HTTPException(422,'Provide one valid symbol and canonical request ID.') from None
        except Exception:raise HTTPException(503,'Signal review unavailable. Refresh saved status before retrying.') from None
    from pro_ticker_order_intents import build_order_intent_router
    router.include_router(build_order_intent_router(require_session=require_session,quote_fetcher=quote_fetcher))
    from pro_ticker_research_dataset import build_research_dataset_router
    router.include_router(build_research_dataset_router(require_session=require_session))
    from pro_ticker_execution_routes import build_paper_execution_router
    router.include_router(build_paper_execution_router(require_session=require_session,context=execution_context))
    from pro_ticker_market_scanner import build_market_scanner_router
    router.include_router(build_market_scanner_router(require_session=require_session))
    return router
