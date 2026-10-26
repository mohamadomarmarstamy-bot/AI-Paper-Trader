"""Observe owned active bracket exits; never place/cancel/replace any order."""
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
import time
import database
from pro_ticker_signal_proposals import _uuid
from pro_ticker_broker_admission import _ledger,_number,_age
from pro_ticker_order_intents import IntentConflict
from pro_ticker_fill_verification import assess_paper_order_fill

_SCHEMA='''CREATE TABLE IF NOT EXISTS pro_ticker_protection_reviews (
 id TEXT PRIMARY KEY,intent_id TEXT NOT NULL,created_at REAL NOT NULL,
 expires_at REAL NOT NULL,ledger_digest TEXT NOT NULL,review_json TEXT NOT NULL);'''


def _state(conn,intent_id):
    intent,_,base_digest=_ledger(conn,intent_id)
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    position=conn.execute('SELECT shares FROM pro_ticker_strategy_positions WHERE symbol=?',(intent['plan']['symbol'],)).fetchone() if 'pro_ticker_strategy_positions' in tables else None
    fills=conn.execute('SELECT broker_order_id,symbol,side,shares,recorded_at FROM pro_ticker_strategy_fill_events WHERE (symbol=? AND recorded_at>=?) OR broker_order_id=? ORDER BY event_id',
        (intent['plan']['symbol'],intent['created_at'],intent['linked_broker_order_id'])).fetchall() if 'pro_ticker_strategy_fill_events' in tables else []
    state={'base':base_digest,'position_shares':position['shares'] if position else None,'fills':[dict(r) for r in fills]}
    digest=hashlib.sha256(json.dumps(state,sort_keys=True,allow_nan=False).encode()).hexdigest()
    return intent,state,digest


def _result(row,now,digest):
    result=json.loads(row['review_json']);expired=now>=row['expires_at'];unchanged=digest==row['ledger_digest']
    # A historical observation must never keep claiming currently verified
    # protection after expiry or a ledger/fill/position change.
    return {**result,'expired':expired,'ledger_unchanged':unchanged,
            'protection_orders_verified':result['checks_passed'] and not expired and unchanged}


def review_paper_protection(intent_id,payload, *, context,clock=None):
    intent_id=_uuid(intent_id)
    if not isinstance(payload,dict) or set(payload)!={'request_id'}:raise ValueError('Review request ID only.')
    review_id=_uuid(payload['request_id']);context.validate();clock=clock or time.time;started=clock()
    with closing(database.get_connection()) as conn:
        conn.executescript(_SCHEMA);conn.execute('BEGIN')
        intent,state,digest=_state(conn,intent_id)
        prior=conn.execute('SELECT * FROM pro_ticker_protection_reviews WHERE id=?',(review_id,)).fetchone()
        if prior:
            if prior['intent_id']!=intent_id:raise IntentConflict('Review ID belongs to another intent.')
            return {**_result(prior,started,digest),'duplicate':True}
        if intent['status']!='broker_linked' or not intent['linked_broker_order_id']:
            raise IntentConflict('Owned active broker entry required.')
    gateway=context.gateway();account=gateway.read_account()
    parent=gateway.read_order(intent['linked_broker_order_id'])
    clock_snapshot=gateway.read_clock();positions=gateway.read_positions();trade=gateway.read_trade(intent['plan']['symbol'])
    references=parent.get('legs') if isinstance(parent,dict) else None
    children={}
    if isinstance(references,list) and len(references)==2:
        try:
            ids=[_uuid(leg['id']) for leg in references]
        except (ValueError,KeyError,TypeError):ids=[]
        if len(set(ids))==2 and intent['linked_broker_order_id'] not in ids:
            children={id:gateway.read_order(id) for id in ids}
    finished=clock();checks=[];plan=intent['plan'];symbol=plan['symbol'];shares=plan['shares']
    def check(code,fn):
        try:passed=fn() is True
        except (ValueError,TypeError,KeyError,ArithmeticError,OverflowError,AttributeError):passed=False
        checks.append({'code':code,'passed':passed})
    check('dedicated_paper_account',lambda:isinstance(account,dict) and account.get('id')==context.expected_account_id)
    check('owned_bracket_entry',lambda:isinstance(parent,dict) and parent['id']==intent['linked_broker_order_id'] and parent['client_order_id']==intent['client_order_id'] and parent['order_class']=='bracket' and
          assess_paper_order_fill(expected_order_id=intent['linked_broker_order_id'],expected_symbol=symbol,expected_side='buy',expected_shares=shares,broker_order=parent)['verified'] is True)
    check('entry_fully_filled',lambda:parent['status']=='filled' and _number(parent['filled_qty'])==shares)
    check('entry_fills_accounted_without_exit',lambda:state['position_shares']==shares and bool(state['fills']) and
          all(r['broker_order_id']==intent['linked_broker_order_id'] and r['symbol']==symbol and r['side']=='buy' for r in state['fills']) and sum(r['shares'] for r in state['fills'])==shares)
    def held_shares():
        if not isinstance(positions,list):return False
        by_symbol={}
        for row in positions:
            if not isinstance(row,dict) or not isinstance(row.get('symbol'),str) or row['symbol'] in by_symbol:return False
            qty=_number(row.get('qty'))
            if qty<0 or qty!=qty.to_integral_value():return False
            by_symbol[row['symbol']]=qty
        return by_symbol.get(symbol)==shares
    check('broker_position_matches',held_shares)
    check('regular_market_open',lambda:clock_snapshot['is_open'] is True and 0<=_age(clock_snapshot['timestamp'],finished)<=30)
    check('two_independently_fetched_legs',lambda:len(children)==2 and isinstance(references,list) and len(references)==2 and all(isinstance(leg,dict) for leg in children.values()))
    identity_fields=('id','client_order_id','symbol','side','qty','filled_qty','type','status','stop_price','limit_price')
    check('leg_snapshots_match_parent',lambda:len(children)==2 and all(all(ref.get(k)==children[ref['id']].get(k) for k in identity_fields) for ref in references))
    check('active_owned_sell_legs',lambda:len(children)==2 and all(leg['symbol']==symbol and leg['side']=='sell' and isinstance(leg['client_order_id'],str) and bool(leg['client_order_id']) and
          leg['status'] in ('new','accepted') and _number(leg['qty'])==shares and _number(leg['filled_qty'])==0 for leg in children.values()))
    stop=next((leg for leg in children.values() if isinstance(leg,dict) and leg.get('type')=='stop'),None)
    target=next((leg for leg in children.values() if isinstance(leg,dict) and leg.get('type')=='limit'),None)
    check('stop_and_target_order_types',lambda:stop is not None and target is not None and len(children)==2)
    check('stop_and_target_within_plan',lambda:_number(stop['stop_price'])>=_number(plan['stop_price']) and _number(target['limit_price'])>=_number(plan['take_profit_price']) and _number(stop['stop_price'])<_number(target['limit_price']))
    check('actual_entry_cost_and_stop_risk',lambda:0<_number(parent['filled_avg_price'])*shares*100<=plan['allocation_cents'] and
          max(_number(parent['filled_avg_price'])-_number(stop['stop_price']),0)*shares*100<=plan['estimated_stop_risk_cents'])
    from pro_ticker_manual_signals import quote_snapshot
    quote=quote_snapshot(trade,now=finished)
    check('fresh_trade_between_exit_levels',lambda:quote['fresh'] is True and quote['age_seconds']<=30 and _number(stop['stop_price'])<_number(quote['price'])<_number(target['limit_price']))
    check('bounded_fetch_duration',lambda:0<=finished-started<=30)
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE');_,_,current_digest=_state(conn,intent_id)
        check('ledger_unchanged_during_review',lambda:current_digest==digest)
        now=clock();check('fresh_at_review_commit',lambda:0<=now-started<=30)
        prior=conn.execute('SELECT * FROM pro_ticker_protection_reviews WHERE id=?',(review_id,)).fetchone()
        if prior:
            if prior['intent_id']!=intent_id:raise IntentConflict('Review ID conflict.')
            return {**_result(prior,now,current_digest),'duplicate':True}
        result={'paper':True,'preview_only':True,'review_id':review_id,'intent_id':intent_id,
                'execution_enabled':False,'order_submitted':False,'budget_released':False,'loss_cap_guaranteed':False,
                'checks_passed':all(c['passed'] for c in checks),'checks':checks,
                'reviewed_at':datetime.fromtimestamp(now,timezone.utc).isoformat(),'expires_at':now+30,
                'reason':'Read-only observation of existing bracket orders in the regular session. No order was placed, canceled or replaced. Stops do not guarantee execution price or a loss cap. Partial entries/exits and after-hours coverage are not certified.'}
        conn.execute('INSERT INTO pro_ticker_protection_reviews VALUES (?,?,?,?,?,?)',(review_id,intent_id,now,now+30,current_digest,json.dumps(result,allow_nan=False)))
        row=conn.execute('SELECT * FROM pro_ticker_protection_reviews WHERE id=?',(review_id,)).fetchone();conn.commit()
        return {**_result(row,now,current_digest),'duplicate':False}
