"""Owner-approved virtual allocations. No broker submission path exists here."""
from contextlib import closing
from datetime import datetime, timezone
import json
import time
import uuid
from fastapi import APIRouter, Request, HTTPException
from starlette.concurrency import run_in_threadpool
import database
from pro_ticker_budget_ledger import _init, reserve_budget_in_transaction
from pro_ticker_signal_proposals import _uuid
from pro_ticker_order_linking import pro_ticker_client_order_id

_SCHEMA = '''CREATE TABLE IF NOT EXISTS pro_ticker_order_intents (
 id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL UNIQUE, reservation_id TEXT NOT NULL UNIQUE,
 client_order_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL CHECK(status IN ('prepared','canceled','submitting','submitted','uncertain')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, plan_json TEXT NOT NULL,
 attempt_started_at TEXT, broker_order_id TEXT);
CREATE TABLE IF NOT EXISTS pro_ticker_order_intent_events (
 id INTEGER PRIMARY KEY, intent_id TEXT NOT NULL, action TEXT NOT NULL,
 recorded_at TEXT NOT NULL, actor TEXT NOT NULL, evidence_json TEXT NOT NULL);'''


class IntentConflict(ValueError):
    pass


def _record(conn,row):
    value=dict(row);plan=json.loads(value.pop('plan_json'))
    reservation=conn.execute('SELECT * FROM pro_ticker_paper_reservations WHERE id=?',(row['reservation_id'],)).fetchone()
    if not reservation or reservation['symbol']!=plan['symbol'] or reservation['notional_cents']!=plan['allocation_cents'] or reservation['expected_shares']!=plan['shares'] or row['client_order_id']!=pro_ticker_client_order_id(reservation['id']):
        raise IntentConflict('Intent and reservation evidence differ.')
    status=row['status'];linked=bool(reservation['broker_order_id'])
    if row['broker_order_id'] is not None and row['broker_order_id']!=reservation['broker_order_id']:
        raise IntentConflict('Intent broker identity differs from reservation.')
    if status=='submitted' and not linked:raise IntentConflict('Submitted intent lacks broker ownership link.')
    if status=='prepared' and row['attempt_started_at'] is not None:status='uncertain'
    if status=='canceled':
        if reservation['status']!='released' or linked or row['attempt_started_at'] is not None or row['broker_order_id'] is not None:
            raise IntentConflict('Canceled intent has conflicting submission evidence.')
    elif linked:
        if reservation['status'] in ('submitted','filled'):status='broker_linked'
        elif reservation['status']=='failed':status='broker_terminal_unfilled'
        elif reservation['status']=='released':
            exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_budget_release_events'").fetchone()
            if not exists or not conn.execute('SELECT 1 FROM pro_ticker_budget_release_events WHERE reservation_id=? AND released_cents=?',(row['reservation_id'],plan['allocation_cents'])).fetchone():
                raise IntentConflict('Released linked intent lacks manual release evidence.')
            status='closed'
        else:raise IntentConflict('Linked intent has inconsistent reservation state.')
    elif reservation['status']!='reserved':
        raise IntentConflict('Unsubmitted intent has inconsistent reservation state.')
    reserved=reservation['status'] in ('reserved','submitted','filled')
    audit=[{**dict(event),'evidence':json.loads(event['evidence_json'])} for event in conn.execute('SELECT id,action,recorded_at,actor,evidence_json FROM pro_ticker_order_intent_events WHERE intent_id=? ORDER BY id',(row['id'],))]
    for event in audit:event.pop('evidence_json')
    return {**value,'intent_status':row['status'],'status':status,'plan':plan,'paper':True,
            'reservation_status':reservation['status'],'linked_broker_order_id':reservation['broker_order_id'],'audit_events':audit,
            'order_submitted': True if linked else None if status in ('submitting','uncertain') else False,
            'execution_enabled':False,'budget_reserved':reserved,'automatic_release_enabled':False,
            'virtual_funds_returned_cents':plan['allocation_cents'] if status in ('canceled','closed') else 0,
            'reason':'State reconciled with the reservation ledger. Broker dispatch requires separate server configuration and explicit owner approval. Returned virtual funds are not trading profit.'}


def intents_for_proposals(conn, ids):
    if not ids or not conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_order_intents'").fetchone():
        return {}
    rows=conn.execute('SELECT * FROM pro_ticker_order_intents WHERE proposal_id IN ('+','.join('?' for _ in ids)+')', ids)
    return {r['proposal_id']: _record(conn,r) for r in rows}


def list_order_intents():
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN')
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_order_intents'").fetchone()
        rows=conn.execute('SELECT * FROM pro_ticker_order_intents ORDER BY created_at DESC,id DESC LIMIT 200').fetchall() if exists else []
        count=conn.execute('SELECT COUNT(*) FROM pro_ticker_order_intents').fetchone()[0] if exists else 0
        records=[_record(conn,r) for r in rows]
    return {'paper':True,'execution_enabled':False,'automatic_release_enabled':False,
            'intents':records,'total_count':count,'has_more':count>len(rows)}


def _audit(conn, intent_id, action, now, evidence):
    conn.execute('INSERT INTO pro_ticker_order_intent_events(intent_id,action,recorded_at,actor,evidence_json) VALUES (?,?,?,?,?)',
                 (intent_id,action,now,'authenticated-app-owner',json.dumps(evidence,allow_nan=False)))


def prepare_order_intent(payload, *, quote_fetcher, clock=None):
    if not isinstance(payload,dict) or set(payload)!={'request_id','proposal_id','confirmed'} or payload['confirmed'] is not True:
        raise ValueError('Explicit allocation confirmation required.')
    intent_id=_uuid(payload['request_id']); proposal_id=_uuid(payload['proposal_id'])
    clock=clock or time.time
    with closing(database.get_connection()) as conn:
        _init(conn);conn.executescript(_SCHEMA)
        def existing():
            row=conn.execute('SELECT * FROM pro_ticker_order_intents WHERE id=? OR proposal_id=?',(intent_id,proposal_id)).fetchone()
            if row:
                if row['id']!=intent_id or row['proposal_id']!=proposal_id:
                    raise IntentConflict('Request or draft already has an allocation intent.')
                return {'paper':True,'duplicate':True,'intent':_record(conn,row)}
        prior=existing()
        if prior:return prior
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_signal_proposals'").fetchone():
            raise ValueError('Saved draft required.')
        draft=conn.execute('SELECT * FROM pro_ticker_signal_proposals WHERE request_id=?',(proposal_id,)).fetchone()
        if draft is None:raise ValueError('Saved draft required.')
        proposal=json.loads(draft['proposal_json']); sizing=json.loads(draft['intent_json'])
        from pro_ticker_manual_signals import quote_snapshot
        raw=quote_fetcher(proposal['symbol'])
        if isinstance(raw,dict) and raw.get('symbol') not in (None,proposal['symbol']):raise ValueError('Quote symbol mismatch.')
        from pro_ticker_workspace import hypothetical_proposal
        # Fetch before the write lock; then validate against current settings and
        # budget on the SAME connection/transaction as the reservation insert.
        conn.execute('BEGIN IMMEDIATE')
        prior=existing()
        if prior:conn.rollback();return prior
        snapshot=quote_snapshot(raw,now=clock())
        if snapshot['fresh'] is not True:raise ValueError('Fresh server quote required.')
        fields={k:sizing[k] for k in ('shares','stop_loss_percent','take_profit_percent')}
        plan=hypothetical_proposal({'symbol':proposal['symbol'],'reference_price':snapshot['price'],**fields},connection=conn)
        if not plan['risk_limits_satisfied'] or not plan['within_virtual_budget']:
            raise ValueError('Current risk or virtual budget limit exceeded.')
        if plan['allocation_cents']>proposal['allocation_cents'] or plan['estimated_stop_risk_cents']>proposal['estimated_stop_risk_cents']:
            raise ValueError('Fresh sizing exceeds the saved draft approval ceiling.')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_strategy_positions'").fetchone():
            if conn.execute('SELECT 1 FROM pro_ticker_strategy_positions WHERE symbol=? AND shares>0',(proposal['symbol'],)).fetchone():
                raise IntentConflict('Strategy position already exists.')
        reservation_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'pro-ticker-order-intent:'+intent_id))
        reserve_budget_in_transaction(conn,reservation_id=reservation_id,symbol=proposal['symbol'],
                                      notional_cents=plan['allocation_cents'],expected_shares=plan['shares'])
        now=datetime.fromtimestamp(clock(),timezone.utc).isoformat()
        plan={**plan,'budget_reserved':True,'hypothetical':True,'market_snapshot':snapshot,
              'reference_price_source':'server_quote_at_allocation','signal_id':proposal['signal_id'],
              'draft_id':proposal_id,'entry_grade':proposal['entry_grade']}
        conn.execute("INSERT INTO pro_ticker_order_intents(id,proposal_id,reservation_id,client_order_id,status,created_at,updated_at,plan_json) VALUES (?,?,?,?,'prepared',?,?,?)",
                     (intent_id,proposal_id,reservation_id,pro_ticker_client_order_id(reservation_id),now,now,json.dumps(plan,allow_nan=False)))
        _audit(conn,intent_id,'virtual_budget_reserved',now,{'reservation_id':reservation_id,'notional_cents':plan['allocation_cents'],'broker_submission_enabled':False})
        if not quote_snapshot(raw,now=clock())['fresh']:raise ValueError('Quote expired while preparing allocation.')
        result=_record(conn,conn.execute('SELECT * FROM pro_ticker_order_intents WHERE id=?',(intent_id,)).fetchone())
        conn.commit()
        return {'paper':True,'duplicate':False,'intent':result}


def cancel_prepared_intent(intent_id, payload):
    intent_id=_uuid(intent_id)
    if not isinstance(payload,dict) or payload!={'confirmed':True} or payload['confirmed'] is not True:
        raise ValueError('Explicit cancellation confirmation required.')
    with closing(database.get_connection()) as conn:
        _init(conn);conn.executescript(_SCHEMA);conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM pro_ticker_order_intents WHERE id=?',(intent_id,)).fetchone()
        if row is None:raise ValueError('Unknown order intent.')
        if row['status']=='canceled':return {'paper':True,'duplicate':True,'intent':_record(conn,row)}
        if row['status']!='prepared' or row['attempt_started_at'] is not None or row['broker_order_id'] is not None:
            raise IntentConflict('Submission evidence requires independent broker reconciliation; funds remain reserved.')
        reservation=conn.execute('SELECT * FROM pro_ticker_paper_reservations WHERE id=?',(row['reservation_id'],)).fetchone()
        plan=json.loads(row['plan_json'])
        if not reservation or reservation['status']!='reserved' or reservation['broker_order_id'] is not None or reservation['symbol']!=plan['symbol'] or reservation['notional_cents']!=plan['allocation_cents'] or reservation['expected_shares']!=plan['shares']:
            raise IntentConflict('Reservation evidence differs; funds remain reserved.')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_strategy_positions'").fetchone():
            if conn.execute('SELECT 1 FROM pro_ticker_strategy_positions WHERE symbol=? AND shares>0',(plan['symbol'],)).fetchone():
                raise IntentConflict('Strategy shares require independent reconciliation.')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_strategy_fill_events'").fetchone():
            if conn.execute("SELECT 1 FROM pro_ticker_strategy_fill_events WHERE symbol=? AND recorded_at>=?",(plan['symbol'],row['created_at'])).fetchone():
                raise IntentConflict('New fill evidence requires independent reconciliation.')
        now=datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE pro_ticker_paper_reservations SET status='released',updated_at=?,note='Owner canceled never-submitted Pro Ticker intent' WHERE id=?",(now,row['reservation_id']))
        conn.execute("UPDATE pro_ticker_order_intents SET status='canceled',updated_at=? WHERE id=?",(now,intent_id))
        _audit(conn,intent_id,'owner_canceled_never_submitted',now,{'reservation_id':row['reservation_id'],'virtual_funds_returned_cents':plan['allocation_cents'],'trading_profit':False,'submission_attempt_started':False})
        result=_record(conn,conn.execute('SELECT * FROM pro_ticker_order_intents WHERE id=?',(intent_id,)).fetchone());conn.commit()
        return {'paper':True,'duplicate':False,'intent':result}


def build_order_intent_router(*, require_session, quote_fetcher):
    router=APIRouter();path='/auto-trader/pro-ticker/order-intents'
    async def body(request):
        require_session(request)
        if request.headers.get('origin')!=str(request.base_url).rstrip('/'):raise HTTPException(403,'Same-origin request required.')
        if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':raise HTTPException(415,'Expected JSON.')
        raw=await request.body()
        if len(raw)>2048:raise HTTPException(413,'Request too large.')
        try:return json.loads(raw)
        except ValueError:raise HTTPException(422,'Valid JSON required.') from None
    @router.get(path)
    def history(request:Request):
        require_session(request)
        try:return list_order_intents()
        except Exception:raise HTTPException(503,'Intent history unavailable.') from None
    @router.post(path)
    async def prepare(request:Request):
        payload=await body(request)
        try:return await run_in_threadpool(prepare_order_intent,payload,quote_fetcher=quote_fetcher)
        except IntentConflict:raise HTTPException(409,'Intent or strategy position already exists. Refresh saved state.') from None
        except ValueError:raise HTTPException(422,'Confirmed saved draft, fresh quote and sufficient budget within current and saved limits are required.') from None
        except Exception:raise HTTPException(503,'Allocation unconfirmed. Refresh history before retrying unchanged intent.') from None
    @router.post(path+'/{intent_id}/cancel')
    async def cancel(intent_id:str,request:Request):
        payload=await body(request)
        try:return await run_in_threadpool(cancel_prepared_intent,intent_id,payload)
        except IntentConflict:raise HTTPException(409,'Cannot prove this allocation was never submitted. Funds remain reserved for independent reconciliation.') from None
        except ValueError:raise HTTPException(422,'Known intent and explicit confirmation required.') from None
        except Exception:raise HTTPException(503,'Cancellation unconfirmed. Refresh saved state before retrying.') from None
    return router
