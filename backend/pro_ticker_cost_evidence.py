"""Immutable dedicated PAPER cash evidence; unlinked cash is never trade profit."""
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
import time
import uuid
import database
from pro_ticker_execution_journal import _ownership
from pro_ticker_signal_proposals import _uuid
from pro_ticker_broker_admission import _number

VERSION='pro_ticker_cash_evidence_v1'
SCHEMA='''CREATE TABLE IF NOT EXISTS pro_ticker_cash_activities (
 activity_id TEXT PRIMARY KEY, account_fingerprint TEXT NOT NULL,
 captured_at TEXT NOT NULL, evidence_json TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS pro_ticker_cash_syncs (
 id TEXT PRIMARY KEY, captured_at TEXT NOT NULL, account_fingerprint TEXT NOT NULL,
 activity_count INTEGER NOT NULL, evidence_digest TEXT NOT NULL);'''
COST_TYPES={'FEE','PTC','PTR','DIVFEE'}
FLOW_TYPES={'CSD','CSW','ACATC','TRANS'}


def normalize_cash_activity(raw, *, expected_account_id):
    if not isinstance(raw,dict) or raw.get('account_id',expected_account_id)!=expected_account_id:
        raise ValueError('Dedicated cash-activity identity required.')
    activity_id=raw.get('id');kind=raw.get('activity_type')
    if not isinstance(activity_id,str) or not re.fullmatch(r'[A-Za-z0-9:_\-]{1,180}',activity_id):
        raise ValueError('Stable cash activity ID required.')
    if not isinstance(kind,str) or not re.fullmatch(r'[A-Z][A-Z0-9]{0,15}',kind) or kind=='FILL':
        raise ValueError('Non-trade activity required.')
    if raw.get('currency') not in (None,'USD'):raise ValueError('USD cash evidence required.')
    if kind=='CFEE':raise ValueError('Crypto-denominated fees are not verified USD cash evidence.')
    amount=_number(raw.get('net_amount'))
    stamp=raw.get('date')
    if not isinstance(stamp,str):raise ValueError('Reported effective date required.')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',stamp):date.fromisoformat(stamp)
    else:
        parsed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
        if parsed.tzinfo is None:raise ValueError('Effective timestamp requires timezone.')
    symbol=raw.get('symbol')
    if symbol is not None and (not isinstance(symbol,str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}',symbol)):
        raise ValueError('Invalid cash activity symbol.')
    order_id=raw.get('order_id')
    if order_id is not None:order_id=_uuid(order_id)
    category='reported_order_cost' if kind in COST_TYPES else 'external_cash_flow' if kind in FLOW_TYPES else 'income' if kind.startswith(('DIV','INT')) else 'other_adjustment'
    return {'activity_id':activity_id,'activity_type':kind,'effective_date':stamp,'net_amount_usd':format((amount if amount else Decimal(0)).normalize(),'f'),
            'symbol':symbol,'broker_order_id':order_id,'category':category}


def _attribution(conn, record):
    order=record['broker_order_id']
    if order is None:return None
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ids=set()
    if 'pro_ticker_order_intents' in tables:
        ids.update(r[0] for r in conn.execute('SELECT id FROM pro_ticker_order_intents WHERE broker_order_id=?',(order,)))
        if 'pro_ticker_paper_exit_links' in tables:
            ids.update(r[0] for r in conn.execute('SELECT i.id FROM pro_ticker_order_intents i JOIN pro_ticker_paper_exit_links e ON e.reservation_id=i.reservation_id WHERE e.broker_order_id=?',(order,)))
    if not ids:return None
    if len(ids)!=1:raise ValueError('Ambiguous cash activity ownership.')
    intent_id=ids.pop();intent,orders,_=_ownership(conn,intent_id)
    if order not in orders or record['symbol'] not in (None,intent['plan']['symbol']):
        raise ValueError('Cash activity attribution conflicts with order ownership.')
    return intent_id


def sync_cash_evidence(*, context, clock=time.time):
    context.validate();started=clock();gateway=context.gateway();account=gateway.read_account()
    if not isinstance(account,dict) or account.get('id')!=context.expected_account_id or account.get('currency')!='USD':
        raise ValueError('Dedicated USD PAPER account required.')
    raw=gateway.read_nontrade_activities()
    if not isinstance(raw,list) or len(raw)>500:raise ValueError('Complete bounded cash history required.')
    records=[normalize_cash_activity(row,expected_account_id=context.expected_account_id) for row in raw]
    if len({r['activity_id'] for r in records})!=len(records):raise ValueError('Duplicate cash activity IDs.')
    fingerprint=hashlib.sha256(context.expected_account_id.encode()).hexdigest()[:12]
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA);conn.execute('BEGIN IMMEDIATE')
        if not 0<=clock()-started<=30:raise ValueError('Cash evidence expired before commit.')
        identities={r[0] for r in conn.execute('SELECT DISTINCT account_fingerprint FROM pro_ticker_cash_syncs')}
        if identities and identities!={fingerprint}:raise ValueError('Cash history belongs to a different dedicated account.')
        now=datetime.fromtimestamp(clock(),timezone.utc).isoformat()
        existing={r['activity_id']:r for r in conn.execute('SELECT * FROM pro_ticker_cash_activities')}
        # A complete new history cannot silently erase a previously observed row.
        if set(existing)-{r['activity_id'] for r in records}:raise ValueError('Previously saved cash evidence disappeared.')
        added=0
        for record in records:
            serialized=json.dumps(record,sort_keys=True,allow_nan=False)
            previous=existing.get(record['activity_id'])
            if previous:
                if previous['account_fingerprint']!=fingerprint or previous['evidence_json']!=serialized:
                    raise ValueError('Immutable cash evidence changed; independent correction review required.')
            else:
                conn.execute('INSERT INTO pro_ticker_cash_activities VALUES (?,?,?,?)',(record['activity_id'],fingerprint,now,serialized));added+=1
            # Reject contradictory known ownership now; never accept browser links.
            _attribution(conn,record)
        digest=hashlib.sha256(json.dumps(records,sort_keys=True,allow_nan=False).encode()).hexdigest()
        if not 0<=clock()-started<=30:raise ValueError('Cash evidence expired during accounting checks.')
        conn.execute('INSERT INTO pro_ticker_cash_syncs VALUES (?,?,?,?,?)',(str(uuid.uuid4()),now,fingerprint,len(records),digest));conn.commit()
    return {'version':VERSION,'paper':True,'order_submitted':False,'budget_released':False,
            'new_activity_count':added,'observed_activity_count':len(records),'net_pnl_usd':None,
            'reason':'Broker cash records saved. A complete fetched window is not proof that all future fees or corrections have posted.'}


def cash_evidence_snapshot(*, connection=None, trades=None):
    def build(conn):
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        saved=[dict(r) for r in conn.execute('SELECT * FROM pro_ticker_cash_activities ORDER BY captured_at,activity_id')] if 'pro_ticker_cash_activities' in tables else []
        latest=conn.execute('SELECT captured_at FROM pro_ticker_cash_syncs ORDER BY captured_at DESC,id DESC LIMIT 1').fetchone() if 'pro_ticker_cash_syncs' in tables else None
        rows=[];by_intent={};totals={}
        for saved_row in saved:
            record=json.loads(saved_row['evidence_json']);intent=_attribution(conn,record)
            row={**record,'captured_at':saved_row['captured_at'],'intent_id':intent,
                 'attribution_status':'owned_order' if intent else 'unattributed'}
            rows.append(row);key=record['category'];totals[key]=totals.get(key,Decimal(0))+Decimal(record['net_amount_usd'])
            if intent and record['activity_type'] in COST_TYPES:
                by_intent[intent]=by_intent.get(intent,Decimal(0))+Decimal(record['net_amount_usd'])
        selected=trades
        if selected is None:
            from pro_ticker_performance_analytics import covered_trades
            selected,_=covered_trades(conn)
        results=[]
        for trade in selected:
            cost=by_intent.get(trade['intent_id'])
            results.append({'intent_id':trade['intent_id'],'gross_pnl_usd':trade['gross_pnl_usd'],
                            'known_linked_cost_cash_usd':str(cost) if cost is not None else None,
                            'gross_after_known_costs_usd':str(Decimal(trade['gross_pnl_usd'])+cost) if cost is not None else None,
                            'net_pnl_usd':None,'cost_coverage_final':False})
        return {'version':VERSION,'paper':True,'order_execution_authorized':False,'model_updated':False,
                'scope':'dedicated_account_cash_history_not_trade_pnl','last_observed_at':latest[0] if latest else None,'total_count':len(rows),'rows':rows,
                'unattributed_count':sum(r['intent_id'] is None for r in rows),
                'category_cash_totals_usd':{k:str(v) for k,v in sorted(totals.items())},'closed_trade_known_costs':results,
                'net_pnl_usd':None,'cost_coverage_final':False,
                'limitations':['Cash flows, income and unlinked adjustments are not strategy trading profit.',
                               'No fee is allocated by matching only a symbol, date or amount.',
                               'Reported cash signs are preserved. Known linked costs are not a certified final net result.',
                               'The activity feed does not certify that later fees, corrections or corporate actions are complete.']}
    if connection is not None:return build(connection)
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN');return build(conn)
