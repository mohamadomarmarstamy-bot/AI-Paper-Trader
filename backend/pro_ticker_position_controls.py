"""Explicit owner management of one owned PAPER intent. No automatic writes.

Claims and dispatch markers precede transport calls. Lost responses are recovered
by saved client identity; no POST or DELETE is automatically replayed. No virtual
allocation is returned here. One close attempt per intent is deliberately bounded;
rejected/canceled partial closes require review rather than another blind sell.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import time
import uuid
import database
from pro_ticker_signal_proposals import _uuid
from pro_ticker_order_intents import _record, IntentConflict
from pro_ticker_fill_verification import assess_paper_order_fill
from pro_ticker_broker_admission import _number, _age, _symbols

SCHEMA = '''CREATE TABLE IF NOT EXISTS pro_ticker_position_actions (
 intent_id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL, account_fingerprint TEXT NOT NULL,
 action_id TEXT NOT NULL UNIQUE, mode TEXT NOT NULL, attempts_json TEXT NOT NULL DEFAULT '[]',
 close_shares INTEGER, close_broker_order_id TEXT, lease_owner TEXT,
 lease_until REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);'''


def _fingerprint(context):
    return hashlib.sha256(context.expected_account_id.encode()).hexdigest()


def saved_action(intent_id):
    with closing(database.get_connection()) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_position_actions'").fetchone():return None
        row=conn.execute('SELECT * FROM pro_ticker_position_actions WHERE intent_id=?',(intent_id,)).fetchone()
        return dict(row) if row else None


def _claim(intent, mode, context):
    owner=str(uuid.uuid4());now=time.time()
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA);conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM pro_ticker_position_actions WHERE intent_id=?',(intent['id'],)).fetchone()
        if row:
            if row['reservation_id']!=intent['reservation_id'] or row['account_fingerprint']!=_fingerprint(context):raise IntentConflict('Management account or reservation changed.')
            if row['lease_until']>now:raise IntentConflict('Another management request is active. Inspect existing state.')
            if row['mode']=='close' and mode!='close':raise IntentConflict('Close already requested; recover that operation.')
            conn.execute('UPDATE pro_ticker_position_actions SET mode=?,lease_owner=?,lease_until=?,updated_at=? WHERE intent_id=?',(mode,owner,now+300,datetime.now(timezone.utc).isoformat(),intent['id']))
        else:
            conn.execute('''INSERT INTO pro_ticker_position_actions
                (intent_id,reservation_id,account_fingerprint,action_id,mode,lease_owner,lease_until,updated_at)
                VALUES (?,?,?,?,?,?,?,?)''',(intent['id'],intent['reservation_id'],_fingerprint(context),str(uuid.uuid4()),mode,owner,now+300,datetime.now(timezone.utc).isoformat()))
        conn.commit()
    return owner


def _mark(intent_id, owner, label, *, shares=None):
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM pro_ticker_position_actions WHERE intent_id=?',(intent_id,)).fetchone()
        if row is None or row['lease_owner']!=owner or row['lease_until']<=time.time():raise IntentConflict('Management claim expired.')
        attempts=json.loads(row['attempts_json'])
        if label in attempts:return False
        attempts.append(label)
        conn.execute('UPDATE pro_ticker_position_actions SET attempts_json=?,close_shares=COALESCE(?,close_shares),updated_at=? WHERE intent_id=?',
                     (json.dumps(attempts),shares,datetime.now(timezone.utc).isoformat(),intent_id));conn.commit()
    return True


def _parent(intent_id, context):
    from pro_ticker_submission_recovery import recover_paper_submission
    result=recover_paper_submission(intent_id,context=context)
    if not result['resolved']:raise IntentConflict('Entry submission is unresolved; no broker mutation is allowed.')
    intent=result['intent'];gateway=context.gateway();account=gateway.read_account()
    if not isinstance(account,dict) or account.get('id')!=context.expected_account_id:raise ValueError('Dedicated PAPER identity required.')
    order=gateway.read_order(intent['linked_broker_order_id'])
    if not isinstance(order,dict) or order.get('client_order_id')!=intent['client_order_id'] or order.get('order_class')!='bracket':raise ValueError('Owned bracket parent required.')
    fill=assess_paper_order_fill(expected_order_id=intent['linked_broker_order_id'],expected_symbol=intent['plan']['symbol'],expected_side='buy',expected_shares=intent['plan']['shares'],broker_order=order)
    if not fill['verified']:raise ValueError('Owned entry snapshot required.')
    from pro_ticker_verified_fill_bridge import account_linked_paper_entry_snapshot
    from pro_ticker_budget_ledger import record_verified_paper_order_state
    posted=account_linked_paper_entry_snapshot(intent['reservation_id'],order)
    if posted.get('stale_snapshot'):raise IntentConflict('Entry snapshot is stale.')
    if fill['filled_shares']:
        record_verified_paper_order_state(intent['reservation_id'],order['id'],fill['broker_status'],filled_quantity=fill['filled_shares'])
    return intent,gateway,order,fill


def owned_manual_exit(intent, *, context):
    """Independent recovery for monitor/release accounting; never dispatch."""
    row=saved_action(intent['id'])
    if not row or 'close' not in json.loads(row['attempts_json']):return None
    if row['reservation_id']!=intent['reservation_id'] or row['account_fingerprint']!=_fingerprint(context) or not isinstance(row['close_shares'],int) or not 0<row['close_shares']<=intent['plan']['shares']:
        raise IntentConflict('Saved close ownership differs.')
    gateway=context.gateway();account=gateway.read_account()
    if not isinstance(account,dict) or account.get('id')!=context.expected_account_id:raise ValueError('Dedicated PAPER identity required.')
    client='ptx-'+row['action_id'];order=gateway.find_exit_order(client)
    if order is None:
        if row['close_broker_order_id']:raise IntentConflict('Previously linked close is unavailable.')
        return None
    if not isinstance(order,dict) or order.get('client_order_id')!=client or order.get('type')!='market' or order.get('time_in_force')!='day':raise ValueError('Owned manual close evidence required.')
    fill=assess_paper_order_fill(expected_order_id=_uuid(order.get('id')),expected_symbol=intent['plan']['symbol'],expected_side='sell',expected_shares=row['close_shares'],broker_order=order)
    if not fill['verified']:raise ValueError('Manual close quantities or status differ.')
    from pro_ticker_exit_links import link_verified_paper_exit
    link_verified_paper_exit(reservation_id=intent['reservation_id'],symbol=intent['plan']['symbol'],expected_shares=row['close_shares'],broker_order=order,exit_id=row['action_id'])
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        current=conn.execute('SELECT * FROM pro_ticker_position_actions WHERE intent_id=?',(intent['id'],)).fetchone()
        if not current or current['action_id']!=row['action_id'] or current['close_shares']!=row['close_shares'] or current['close_broker_order_id'] not in (None,order['id']):raise IntentConflict('Close identity changed.')
        conn.execute('UPDATE pro_ticker_position_actions SET close_broker_order_id=? WHERE intent_id=?',(order['id'],intent['id']));conn.commit()
    return order


def _remaining(intent):
    with closing(database.get_connection()) as conn:
        entry=conn.execute("SELECT COALESCE(SUM(shares),0) FROM pro_ticker_strategy_fill_events WHERE broker_order_id=? AND side='buy'",(intent['linked_broker_order_id'],)).fetchone()[0]
        exits=0
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_paper_exit_links'").fetchone():
            exits=conn.execute('''SELECT COALESCE(SUM(f.shares),0) FROM pro_ticker_strategy_fill_events f
                JOIN pro_ticker_paper_exit_links e ON e.broker_order_id=f.broker_order_id
                WHERE e.reservation_id=? AND f.side='sell' ''',(intent['reservation_id'],)).fetchone()[0]
    if exits>entry:raise IntentConflict('Exit shares exceed owned entry.')
    return entry-exits


def _position_qty(gateway, symbol):
    positions=gateway.read_positions();_symbols(positions)
    matched=[p for p in positions if p['symbol']==symbol]
    if len(matched)>1:raise ValueError('Duplicate broker position.')
    qty=_number(matched[0]['qty']) if matched else 0
    if qty<0 or qty!=int(qty):raise IntentConflict('Whole-share long broker position required.')
    return int(qty)


def _regular_session(gateway):
    clock=gateway.read_clock()
    if not isinstance(clock,dict) or clock.get('is_open') is not True or not 0<=_age(clock.get('timestamp'),time.time())<=10:
        raise IntentConflict('Fresh regular-session clock required before changing protection or closing.')


def inspect_position_management(intent_id, *, context):
    intent_id=_uuid(intent_id);context.validate()
    intent,gateway,parent,fill=_parent(intent_id,context)
    row=saved_action(intent_id);remaining=_remaining(intent);closed=False;close_status=None;protected=False
    if fill['filled_shares'] and fill['terminal'] and isinstance(parent.get('legs'),list) and len(parent['legs'])==2:
        from pro_ticker_position_monitor import monitor_paper_position
        result=monitor_paper_position(intent_id,context=context)
        remaining=_remaining(intent);protected=result['protection_verified']
        closed=result['closed'] and _position_qty(gateway,intent['plan']['symbol'])==0
        if row and row['close_broker_order_id']:
            close_status=gateway.read_order(row['close_broker_order_id']).get('status')
    attempted=bool(row and 'close' in json.loads(row['attempts_json']))
    if row:
        # The monitor may have just recovered the saved close order.
        row=saved_action(intent_id)
        if row['close_broker_order_id']:close_status=gateway.read_order(row['close_broker_order_id']).get('status')
    state='closed' if closed else 'close_unconfirmed' if attempted and not row['close_broker_order_id'] else 'close_'+close_status if close_status else 'entry_terminal' if fill['terminal'] else 'entry_open'
    return {'paper':True,'budget_released':False,'automatic_release_enabled':False,'intent_id':intent_id,
            'state':state,'entry_status':fill['broker_status'],'entry_filled_shares':fill['filled_shares'],
            'entry_cancellation_confirmed':fill['broker_status']=='canceled','position_closed':closed,
            'remaining_shares':remaining,'close_dispatch_attempted':attempted,'close_order_status':close_status,
            'protection_verified':protected,'reason':'Broker-confirmed snapshots only. Cancellation does not close existing shares. Funds remain held until separate owner release approval.'}


def manage_paper_position(intent_id,payload,*,context):
    if not isinstance(payload,dict) or set(payload)!={'action','confirmed'} or payload['confirmed'] is not True or payload['action'] not in ('cancel_entry','close'):
        raise ValueError('Explicit owner management confirmation required.')
    context.validate()
    if getattr(context,'owner_management_enabled',False) is not True:raise ValueError('Owner PAPER management disabled.')
    intent_id=_uuid(intent_id);intent,gateway,parent,fill=_parent(intent_id,context)
    owner=_claim(intent,payload['action'],context)
    try:
        if payload['action']=='close':_regular_session(gateway)
        if not fill['terminal']:
            if _mark(intent_id,owner,'cancel:'+parent['id']):
                gateway.cancel_owned_order(parent['id'])
            # Independently refetch/account; never infer cancel success from DELETE.
            intent,gateway,parent,fill=_parent(intent_id,context)
        if payload['action']=='cancel_entry' or not fill['terminal']:
            return inspect_position_management(intent_id,context=context)
        if fill['filled_shares']==0:
            if _position_qty(gateway,intent['plan']['symbol'])!=0:raise IntentConflict('Unattributed broker shares require review.')
            return inspect_position_management(intent_id,context=context)
        from pro_ticker_position_monitor import monitor_paper_position
        result=monitor_paper_position(intent_id,context=context)
        if not result['resolved'] or len(result['legs'])<2:raise IntentConflict('Complete bracket exit evidence required.')
        # Recovering a previous close only reads its saved identity and fills.
        row=saved_action(intent_id)
        if 'close' in json.loads(row['attempts_json']):return inspect_position_management(intent_id,context=context)
        from pro_ticker_exit_links import get_pro_ticker_exit_link
        for leg in result['legs']:
            link=get_pro_ticker_exit_link(leg['id']);order=gateway.read_order(leg['id'])
            if not link or link['reservation_id']!=intent['reservation_id'] or order.get('client_order_id')!=link['client_order_id']:raise ValueError('Owned exit link required.')
            reviewed=assess_paper_order_fill(expected_order_id=leg['id'],expected_symbol=intent['plan']['symbol'],expected_side='sell',expected_shares=link['expected_shares'],broker_order=order)
            if not reviewed['verified']:raise ValueError('Fresh owned exit required.')
            if not reviewed['terminal']:
                _regular_session(gateway)
                if _mark(intent_id,owner,'cancel:'+leg['id']):gateway.cancel_owned_order(leg['id'])
                fresh=gateway.read_order(leg['id'])
                if not isinstance(fresh,dict) or fresh.get('client_order_id')!=link['client_order_id']:raise ValueError('Cancel confirmation ownership differs.')
                confirmed=assess_paper_order_fill(expected_order_id=leg['id'],expected_symbol=intent['plan']['symbol'],expected_side='sell',expected_shares=link['expected_shares'],broker_order=fresh)
                if not confirmed['verified']:raise ValueError('Cancel confirmation evidence invalid.')
                if not confirmed['terminal']:return inspect_position_management(intent_id,context=context)
        # Every old exit is terminal. Reconcile last fills before sizing any sell.
        monitor_paper_position(intent_id,context=context)
        shares=_remaining(intent)
        if _position_qty(gateway,intent['plan']['symbol'])!=shares:raise IntentConflict('Broker and owned shares differ; no sell submitted.')
        if not shares:return inspect_position_management(intent_id,context=context)
        if intent['plan']['symbol'] in _symbols(gateway.read_open_orders(),orders=True):raise IntentConflict('Open symbol orders prevent a new close.')
        _regular_session(gateway)
        if _mark(intent_id,owner,'close',shares=shares):
            gateway.submit_owned_close({'symbol':intent['plan']['symbol'],'qty':str(shares),'side':'sell','type':'market','time_in_force':'day','extended_hours':False,'client_order_id':'ptx-'+row['action_id'],'position_intent':'sell_to_close'})
        return inspect_position_management(intent_id,context=context)
    finally:
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE pro_ticker_position_actions SET lease_owner=NULL,lease_until=0 WHERE intent_id=? AND lease_owner=?',(intent_id,owner));conn.commit()
