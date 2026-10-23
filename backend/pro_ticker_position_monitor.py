"""Independently reconcile owned PAPER brackets. Never submit or release funds."""
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
import uuid
import database
from pro_ticker_signal_proposals import _uuid
from pro_ticker_order_intents import _record,IntentConflict
from pro_ticker_submission_recovery import recover_paper_submission
from pro_ticker_fill_verification import assess_paper_order_fill
from pro_ticker_broker_admission import _number
from pro_ticker_exit_links import _SCHEMA as EXIT_SCHEMA

SCHEMA='''CREATE TABLE IF NOT EXISTS pro_ticker_bracket_exit_provenance (
 broker_order_id TEXT PRIMARY KEY,intent_id TEXT NOT NULL,parent_order_id TEXT NOT NULL,
 account_fingerprint TEXT NOT NULL,verified_at TEXT NOT NULL,proof_json TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS pro_ticker_bracket_exit_audit (
 id INTEGER PRIMARY KEY,intent_id TEXT NOT NULL,broker_order_id TEXT NOT NULL,
 recorded_at TEXT NOT NULL,old_quantity INTEGER,new_quantity INTEGER NOT NULL,proof_digest TEXT NOT NULL);'''


def monitor_paper_position(intent_id,*,context):
    intent_id=_uuid(intent_id);context.validate()
    recovered=recover_paper_submission(intent_id,context=context)
    base={'paper':True,'order_submitted':False,'budget_released':False,'automatic_release_enabled':False}
    if not recovered['resolved']:
        return {**base,'resolved':False,'closed':False,'protection_verified':False,'intent':recovered['intent'],'reason':recovered['reason'],'legs':[]}
    intent=recovered['intent'];plan=intent['plan'];entry_id=intent['linked_broker_order_id'];gateway=context.gateway()
    account=gateway.read_account()
    if not isinstance(account,dict) or account.get('id')!=context.expected_account_id:raise ValueError('Dedicated PAPER identity required.')
    parent=gateway.read_order(entry_id)
    if not isinstance(parent,dict) or parent.get('client_order_id')!=intent['client_order_id'] or parent.get('order_class')!='bracket':raise ValueError('Owned bracket parent required.')
    parent_fill=assess_paper_order_fill(expected_order_id=entry_id,expected_symbol=plan['symbol'],expected_side='buy',expected_shares=plan['shares'],broker_order=parent)
    if not parent_fill['verified']:raise ValueError('Parent identity or quantities invalid.')
    # Account the separately fetched parent too; a GET may advance since lookup.
    from pro_ticker_verified_fill_bridge import account_linked_paper_entry_snapshot
    from pro_ticker_budget_ledger import record_verified_paper_order_state
    entry_post=account_linked_paper_entry_snapshot(intent['reservation_id'],parent)
    if parent_fill['filled_shares'] and not entry_post.get('stale_snapshot'):
        record_verified_paper_order_state(intent['reservation_id'],entry_id,parent_fill['broker_status'],filled_quantity=parent_fill['filled_shares'])
    if not (parent_fill['filled_shares']>0 and parent_fill['terminal'] and parent_fill['broker_status'] in ('filled','canceled','expired')):
        return {**base,'resolved':True,'closed':False,'protection_verified':False,'intent':intent,'legs':[],
                'reason':'Entry incomplete. Partial bracket entry fills are not certified as protected; funds remain held.'}
    references=parent.get('legs')
    if not isinstance(references,list) or len(references)!=2:raise ValueError('Two bracket exit references required.')
    ids=[_uuid(row.get('id')) if isinstance(row,dict) else None for row in references]
    if None in ids or len(set(ids))!=2 or entry_id in ids:raise ValueError('Distinct bracket child IDs required.')
    children=[gateway.read_order(id) for id in ids];fills=[]
    fields=('id','client_order_id','symbol','side','qty','filled_qty','status','type','stop_price','limit_price')
    for ref,child,id in zip(references,children,ids):
        if not isinstance(child,dict) or any(ref.get(k)!=child.get(k) for k in fields):raise ValueError('Independent bracket child snapshots differ.')
        qty=_number(child.get('qty'))
        if qty!=qty.to_integral_value() or not 0<qty<=plan['shares']:raise ValueError('Invalid bracket child quantity.')
        if not isinstance(child.get('client_order_id'),str) or not child['client_order_id'] or child['client_order_id']==intent['client_order_id']:raise ValueError('Distinct child client identity required.')
        fill=assess_paper_order_fill(expected_order_id=id,expected_symbol=plan['symbol'],expected_side='sell',expected_shares=int(qty),broker_order=child)
        if not fill['verified']:raise ValueError('Bracket exit identity or fill invalid.')
        fills.append(fill)
    if len({child['client_order_id'] for child in children})!=2:raise ValueError('Duplicate child client identity.')
    if sorted(child.get('type','') for child in children)!=['limit','stop']:raise ValueError('Known stop/target exit types required.')
    stop=next(child for child in children if child['type']=='stop');target=next(child for child in children if child['type']=='limit')
    if not (_number(stop['stop_price'])>=_number(plan['stop_price']) and _number(target['limit_price'])>=_number(plan['take_profit_price']) and _number(stop['stop_price'])<_number(target['limit_price'])):
        raise ValueError('Exit levels differ from approved protection.')
    if sum(fill['filled_shares'] for fill in fills)>parent_fill['filled_shares']:raise IntentConflict('Bracket fills exceed the owned entry. Manual review required.')
    from pro_ticker_position_controls import owned_manual_exit, saved_action
    manual_exit=owned_manual_exit(intent,context=context)
    manual_ids=[manual_exit['id']] if manual_exit else []
    now=datetime.now(timezone.utc).isoformat()
    with closing(database.get_connection()) as conn:
        conn.executescript(EXIT_SCHEMA+SCHEMA);conn.execute('BEGIN IMMEDIATE')
        current=_record(conn,conn.execute('SELECT * FROM pro_ticker_order_intents WHERE id=?',(intent_id,)).fetchone())
        if current['status']!='broker_linked' or current['linked_broker_order_id']!=entry_id or current['plan']!=plan:raise IntentConflict('Reservation changed during monitoring.')
        # Never mix a manually attributed exit with a newly inferred bracket.
        links=conn.execute('SELECT * FROM pro_ticker_paper_exit_links WHERE reservation_id=?',(intent['reservation_id'],)).fetchall()
        if any(link['broker_order_id'] not in ids+manual_ids for link in links):raise IntentConflict('Other exit links require independent combined reconciliation.')
        for child,fill in zip(children,fills):
            previous=conn.execute('SELECT * FROM pro_ticker_paper_exit_links WHERE broker_order_id=?',(child['id'],)).fetchone()
            prior_proof=conn.execute('SELECT * FROM pro_ticker_bracket_exit_provenance WHERE broker_order_id=?',(child['id'],)).fetchone()
            if previous and (not prior_proof or previous['reservation_id']!=intent['reservation_id'] or previous['symbol']!=plan['symbol'] or previous['client_order_id']!=child['client_order_id'] or prior_proof['parent_order_id']!=entry_id or prior_proof['intent_id']!=intent_id):
                raise IntentConflict('Existing child attribution differs.')
            qty=int(_number(child['qty']));posted=conn.execute('SELECT COALESCE(SUM(shares),0) FROM pro_ticker_strategy_fill_events WHERE broker_order_id=?',(child['id'],)).fetchone()[0]
            # Broker OCO may shrink the still-unfilled sibling after a partial
            # exit. Only a child with zero posted fills can change quantity.
            if previous and previous['expected_shares']!=qty and posted:
                raise IntentConflict('Filled child quantity changed; manual review required.')
            if posted>fill['filled_shares']:raise IntentConflict('Stale exit snapshot cannot reduce accounted shares.')
            proof=json.dumps({'parent_id':entry_id,'parent_client_id':intent['client_order_id'],'child':{key:child.get(key) for key in fields}},sort_keys=True,allow_nan=False)
            if not previous:
                conn.execute('INSERT INTO pro_ticker_paper_exit_links VALUES (?,?,?,?,?,?)',(intent['reservation_id'],child['id'],child['client_order_id'],plan['symbol'],qty,now))
            elif previous['expected_shares']!=qty:
                conn.execute('UPDATE pro_ticker_paper_exit_links SET expected_shares=? WHERE broker_order_id=?',(qty,child['id']))
            if not previous or previous['expected_shares']!=qty:
                conn.execute('INSERT INTO pro_ticker_bracket_exit_audit(intent_id,broker_order_id,recorded_at,old_quantity,new_quantity,proof_digest) VALUES (?,?,?,?,?,?)',(intent_id,child['id'],now,previous['expected_shares'] if previous else None,qty,hashlib.sha256(proof.encode()).hexdigest()))
            conn.execute('INSERT INTO pro_ticker_bracket_exit_provenance VALUES (?,?,?,?,?,?) ON CONFLICT(broker_order_id) DO UPDATE SET verified_at=excluded.verified_at,proof_json=excluded.proof_json',
                (child['id'],intent_id,entry_id,hashlib.sha256(context.expected_account_id.encode()).hexdigest()[:12],now,proof))
        conn.commit()
    from pro_ticker_verified_exit_fill_bridge import account_linked_paper_exit_snapshot
    posted_exits=sum(account_linked_paper_exit_snapshot(child['id'],child)['delta_shares'] for child in children)
    all_exits=list(children)
    if manual_exit:
        posted_exits+=account_linked_paper_exit_snapshot(manual_exit['id'],manual_exit)['delta_shares']
        all_exits.append(manual_exit)

    from pro_ticker_broker_snapshot_audit import audit_linked_paper_broker_snapshots
    closure=audit_linked_paper_broker_snapshots(intent['reservation_id'],parent,{child['id']:child for child in all_exits})
    protection=False
    if manual_exit is None and parent_fill['filled_shares']==plan['shares'] and parent_fill['broker_status']=='filled' and not any(fill['filled_shares'] for fill in fills):
        from pro_ticker_protection_review import review_paper_protection
        observed=review_paper_protection(intent_id,{'request_id':str(uuid.uuid4())},context=context)
        protection=observed['protection_orders_verified']
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN');intent=_record(conn,conn.execute('SELECT * FROM pro_ticker_order_intents WHERE id=?',(intent_id,)).fetchone())
    action=saved_action(intent_id)
    unresolved_close=bool(action and 'close' in json.loads(action['attempts_json']) and manual_exit is None)
    if manual_exit:
        manual_fill=assess_paper_order_fill(expected_order_id=manual_exit['id'],expected_symbol=plan['symbol'],expected_side='sell',expected_shares=action['close_shares'],broker_order=manual_exit)
        fills.append(manual_fill)
    return {**base,'resolved':True,'closed':closure['verified'] is True and not unresolved_close,'protection_verified':protection,
            'posted_exit_shares':posted_exits,'intent':intent,'legs':[{'id':child['id'],'status':child['status'],'shares':int(_number(child['qty'])),'filled_shares':fill['filled_shares']} for child,fill in zip(all_exits,fills)],
            'reason':'Closure evidence matches linked accounting; owner release approval is still required.' if closure['verified'] else 'Entry/exit accounting refreshed. Partial exits or after-hours protection are not certified. Funds remain held.'}
