"""Persisted PAPER evidence reviews. Passing checks never authorizes execution."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
import hashlib
import json
import re
import time
from typing import Callable
import database
from pro_ticker_signal_proposals import _uuid
from pro_ticker_order_intents import IntentConflict,_record
from pro_ticker_settings import read_settings
from pro_ticker_paper_gateway import PAPER_URL

ADMISSION_REFERENCE_MAX_AGE_SECONDS = 30

_SCHEMA='''CREATE TABLE IF NOT EXISTS pro_ticker_admission_reviews (
 id TEXT PRIMARY KEY,intent_id TEXT NOT NULL,created_at REAL NOT NULL,
 expires_at REAL NOT NULL,ledger_digest TEXT NOT NULL,review_json TEXT NOT NULL);'''


@dataclass(frozen=True)
class PaperEvidenceContext:
    expected_account_id:str
    gateway_factory:Callable
    dedicated_account_confirmed:bool=False

    def validate(self):
        _uuid(self.expected_account_id)
        if self.dedicated_account_confirmed is not True or not callable(self.gateway_factory):
            raise ValueError('Separately approved dedicated PAPER account required.')
        return self

    def gateway(self):
        self.validate();gateway=self.gateway_factory()
        if getattr(gateway,'paper',None) is not True or getattr(gateway,'base_url',None)!=PAPER_URL or getattr(gateway,'account_id',None)!=self.expected_account_id:
            raise ValueError('Dedicated PAPER gateway identity required.')
        return gateway


def execution_status(context=None):
    configured=False
    if context is not None:
        try:context.validate();configured=True
        except Exception:pass
    return {'paper':True,'admission_review_enabled':configured,'recovery_enabled':configured,'protection_review_enabled':configured,
            'owner_submission_enabled':configured and getattr(context,'owner_submission_enabled',False) is True,
            'owner_management_enabled':configured and getattr(context,'owner_management_enabled',False) is True,
            'execution_enabled':False,'automatic_execution_enabled':False,'automatic_release_enabled':False,
            'account_verified':False,'reason':'Read-only evidence/recovery configured. No broker submission path is enabled.' if configured else 'Dedicated PAPER gateway is not configured or approved. Original trader credentials are never reused.'}


def _number(value):
    if isinstance(value,bool) or not isinstance(value,(str,int,float)) or len(str(value))>40:raise ValueError('Invalid number.')
    result=Decimal(str(value))
    if not result.is_finite() or abs(result)>Decimal('1000000000000'):raise ValueError('Invalid number.')
    return result


def _age(stamp,now):
    if not isinstance(stamp,str):raise ValueError('Missing market time.')
    parsed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
    if parsed.tzinfo is None:raise ValueError('Timezone required.')
    return now-parsed.timestamp()


def _symbols(rows, *, orders=False):
    if not isinstance(rows,list) or len(rows)>10000 or orders and len(rows)>=500:
        raise ValueError('Incomplete collision snapshot.')
    result=set();count=0
    def visit(row,depth=0):
        nonlocal count
        count+=1
        if count>10000 or depth>2 or not isinstance(row,dict):raise ValueError('Malformed broker collision evidence.')
        symbol=row.get('symbol')
        if not isinstance(symbol,str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}',symbol):raise ValueError('Invalid broker symbol.')
        if orders:
            if not isinstance(row.get('id'),str) or not row['id'] or _number(row.get('qty'))<0:raise ValueError('Invalid open order.')
            result.add(symbol)
            legs=row.get('legs')
            if legs is not None:
                if not isinstance(legs,list):raise ValueError('Incomplete nested orders.')
                for leg in legs:visit(leg,depth+1)
        elif _number(row.get('qty'))!=0:result.add(symbol)
    for row in rows:visit(row)
    return result


def _ledger(conn,intent_id):
    row=conn.execute('SELECT * FROM pro_ticker_order_intents WHERE id=?',(intent_id,)).fetchone()
    if row is None:raise ValueError('Saved intent required.')
    public=_record(conn,row)
    settings=read_settings(conn)
    committed=conn.execute("SELECT COALESCE(SUM(notional_cents),0) FROM pro_ticker_paper_reservations WHERE status IN ('reserved','submitted','filled')").fetchone()[0]
    content={'intent':public,'settings':settings,'committed_cents':committed}
    digest=hashlib.sha256(json.dumps(content,sort_keys=True,allow_nan=False).encode()).hexdigest()
    return public,settings,digest


def _review_result(row,now,current_digest):
    return {**json.loads(row['review_json']),'expired':now>=row['expires_at'],
            'ledger_unchanged':current_digest==row['ledger_digest']}


def review_paper_admission(intent_id,payload, *, context,clock=None):
    intent_id=_uuid(intent_id)
    if not isinstance(payload,dict) or set(payload)!={'request_id'}:raise ValueError('Review request ID only.')
    request_id=_uuid(payload['request_id']);context.validate();clock=clock or time.time
    started=clock()
    with closing(database.get_connection()) as conn:
        conn.executescript(_SCHEMA);conn.execute('BEGIN')
        intent,_,digest=_ledger(conn,intent_id)
        existing=conn.execute('SELECT * FROM pro_ticker_admission_reviews WHERE id=?',(request_id,)).fetchone()
        if existing:
            if existing['intent_id']!=intent_id:raise IntentConflict('Review request belongs to another intent.')
            return {**_review_result(existing,started,digest),'duplicate':True}
        if intent['status']!='prepared' or intent['attempt_started_at'] is not None:
            raise IntentConflict('Only a never-submitted prepared intent can be reviewed.')
    snapshot=context.gateway().read_snapshot(intent['plan']['symbol'])
    finished=clock()
    if not isinstance(snapshot,dict):raise ValueError('Complete broker evidence required.')
    account=snapshot.get('account');symbol=intent['plan']['symbol'];checks=[]
    def check(code,fn):
        try:passed=fn() is True
        except (ValueError,TypeError,KeyError,InvalidOperation,OverflowError,AttributeError):passed=False
        checks.append({'code':code,'passed':passed})
    check('dedicated_paper_account',lambda:isinstance(account,dict) and account.get('id')==context.expected_account_id)
    check('account_active_and_unblocked',lambda:account['status']=='ACTIVE' and account['currency']=='USD' and all(account.get(k) is False for k in ('trading_blocked','account_blocked','trade_suspended_by_user')))
    check('broker_buying_power',lambda:_number(account['buying_power'])*100>=intent['plan']['allocation_cents'])
    check('regular_market_open',lambda:snapshot['clock']['is_open'] is True and 0<=_age(snapshot['clock']['timestamp'],finished)<=30)
    check('active_tradable_equity',lambda:snapshot['asset']['symbol']==symbol and snapshot['asset']['status']=='active' and snapshot['asset']['class']=='us_equity' and snapshot['asset']['tradable'] is True)
    check('broker_position_clear',lambda:symbol not in _symbols(snapshot['positions']))
    check('broker_orders_clear',lambda:symbol not in _symbols(snapshot['orders'],orders=True))
    check('snapshot_fetch_fresh',lambda:0<=finished-started<=30)
    from pro_ticker_manual_signals import quote_snapshot
    quote=quote_snapshot(snapshot.get('quote'),now=finished)
    check('current_trade_fresh',lambda:quote.get('fresh') is True and quote['age_seconds']<=ADMISSION_REFERENCE_MAX_AGE_SECONDS)
    check('entry_within_reserved_ceiling',lambda:_number(quote['price'])*intent['plan']['shares']*100<=intent['plan']['allocation_cents'])
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE');current,settings,current_digest=_ledger(conn,intent_id)
        check('ledger_unchanged_during_review',lambda:current_digest==digest)
        check('current_allocation_limit',lambda:current['plan']['allocation_cents']<=settings['settings']['max_allocation_cents'])
        check('current_stop_risk_limit',lambda:current['plan']['estimated_stop_risk_cents']<=settings['settings']['max_stop_risk_cents'])
        now=clock();check('fresh_at_review_commit',lambda:0<=now-started<=30)
        # Repeated concurrent requests share one immutable review, even if one
        # provider later reports a more favorable price or buying-power figure.
        prior=conn.execute('SELECT * FROM pro_ticker_admission_reviews WHERE id=?',(request_id,)).fetchone()
        if prior:
            if prior['intent_id']!=intent_id:raise IntentConflict('Review ID conflict.')
            return {**_review_result(prior,now,current_digest),'duplicate':True}
        result={'paper':True,'preview_only':True,'review_id':request_id,'intent_id':intent_id,
                'execution_enabled':False,'order_submitted':False,'automatic_release_enabled':False,
                'eligible_for_manual_submission':False,'checks_passed':all(c['passed'] for c in checks),
                'checks':checks,'submission_blockers':['submission_not_enabled','protective_orders_not_verified'],
                'account_fingerprint':hashlib.sha256(context.expected_account_id.encode()).hexdigest()[:12],
                'reviewed_at':datetime.fromtimestamp(now,timezone.utc).isoformat(),'expires_at':now+30,
                'reference_price':quote['price'],'reference_market_timestamp':quote['market_timestamp'],
                'reason':'Read-only broker evidence review. It cannot submit an order, authorize execution, or verify protective orders.'}
        conn.execute('INSERT INTO pro_ticker_admission_reviews VALUES (?,?,?,?,?,?)',(request_id,intent_id,now,now+30,current_digest,json.dumps(result,allow_nan=False)))
        conn.commit()
        return {**result,'expired':False,'ledger_unchanged':True,'duplicate':False}
