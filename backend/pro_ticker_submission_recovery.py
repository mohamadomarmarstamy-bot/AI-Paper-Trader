"""Recover an existing owned PAPER order by client ID; NEVER resubmit an order."""
from contextlib import closing
from datetime import datetime,timezone
import database
from pro_ticker_signal_proposals import _uuid
from pro_ticker_order_intents import _record,_audit,IntentConflict
from pro_ticker_fill_verification import assess_paper_order_fill


def recover_paper_submission(intent_id, *, context):
    intent_id=_uuid(intent_id);context.validate()
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN')
        row=conn.execute('SELECT * FROM pro_ticker_order_intents WHERE id=?',(intent_id,)).fetchone()
        if not row or row['status']=='canceled' or row['attempt_started_at'] is None:
            raise IntentConflict('A recorded submission attempt is required. No new order will be placed.')
        initial=_record(conn,row)
        if initial['status'] in ('closed','broker_terminal_unfilled'):raise IntentConflict('Terminal intent requires its existing reconciliation history.')
    gateway=context.gateway()
    account=gateway.read_account()
    if not isinstance(account,dict) or account.get('id')!=context.expected_account_id:raise ValueError('Dedicated PAPER identity required.')
    order=gateway.find_order(initial['client_order_id'])
    if order is not None:
        if not isinstance(order,dict) or order.get('client_order_id')!=initial['client_order_id']:
            raise ValueError('Recovered order client identity mismatch.')
        broker_id=_uuid(order.get('id'))
        fill=assess_paper_order_fill(expected_order_id=broker_id,expected_symbol=initial['plan']['symbol'],
            expected_side='buy',expected_shares=initial['plan']['shares'],broker_order=order)
        if fill['verified'] is not True:raise ValueError('Recovered PAPER order ownership/fills invalid.')
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        current=conn.execute('SELECT * FROM pro_ticker_order_intents WHERE id=?',(intent_id,)).fetchone()
        if not current or current['status']=='canceled' or current['attempt_started_at']!=row['attempt_started_at']:
            raise IntentConflict('Intent changed during recovery.')
        public=_record(conn,current)
        if public['status'] in ('closed','broker_terminal_unfilled'):raise IntentConflict('Intent became terminal during recovery.')
        if public['reservation_id']!=initial['reservation_id'] or public['client_order_id']!=initial['client_order_id'] or public['plan']!=initial['plan']:
            raise IntentConflict('Reservation or plan changed during recovery.')
        now=datetime.now(timezone.utc).isoformat()
        if order is None:
            # A 404 is NOT evidence of an unsubmitted order: dispatch could be
            # delayed or visibility incomplete. Preserve funds and NEVER POST.
            if public['linked_broker_order_id'] is not None:raise IntentConflict('Previously linked order is unavailable; preserve its evidence.')
            if current['status']!='uncertain':
                conn.execute("UPDATE pro_ticker_order_intents SET status='uncertain',updated_at=? WHERE id=?",(now,intent_id))
                _audit(conn,intent_id,'broker_lookup_unresolved',now,{'no_order_resubmitted':True,'budget_preserved':True})
            result=_record(conn,conn.execute('SELECT * FROM pro_ticker_order_intents WHERE id=?',(intent_id,)).fetchone());conn.commit()
            return {'paper':True,'resolved':False,'order_resubmitted':False,'budget_released':False,
                    'protection_verified':False,'intent':result,
                    'reason':'No matching order found. Funds remain reserved; absence does not authorize another buy or a budget release.'}
        if public['linked_broker_order_id'] not in (None,broker_id):raise IntentConflict('Conflicting persisted broker order ID.')
        if conn.execute('SELECT 1 FROM pro_ticker_paper_reservations WHERE broker_order_id=? AND id!=?',(broker_id,initial['reservation_id'])).fetchone():
            raise IntentConflict('Broker order belongs to another reservation.')
        duplicate=current['broker_order_id']==broker_id and current['status']=='submitted'
        if public['linked_broker_order_id'] is None:
            changed=conn.execute("UPDATE pro_ticker_paper_reservations SET broker_order_id=?,status='submitted',updated_at=? WHERE id=? AND status='reserved' AND broker_order_id IS NULL",(broker_id,now,initial['reservation_id'])).rowcount
            if changed!=1:raise IntentConflict('Reservation is no longer recoverable.')
        conn.execute("UPDATE pro_ticker_order_intents SET broker_order_id=?,status='submitted',updated_at=? WHERE id=?",(broker_id,now,intent_id))
        if not duplicate:_audit(conn,intent_id,'broker_order_recovered',now,{'broker_order_id':broker_id,'client_order_id':initial['client_order_id'],'no_order_resubmitted':True})
        conn.commit()
    # This existing cumulative bridge is idempotent. If accounting is interrupted
    # after linking, the next explicit recovery still fetches and posts it again.
    from pro_ticker_verified_fill_bridge import account_linked_paper_entry_snapshot
    from pro_ticker_budget_ledger import record_verified_paper_order_state
    posting=account_linked_paper_entry_snapshot(initial['reservation_id'],order)
    if fill['filled_shares']>0 and posting.get('stale_snapshot') is not True:
        record_verified_paper_order_state(initial['reservation_id'],broker_id,fill['broker_status'],filled_quantity=fill['filled_shares'])
    # Zero-fill terminal orders stay reserved here: this service cannot turn a
    # broker rejection/cancellation into automatic virtual budget release.
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN');final=_record(conn,conn.execute('SELECT * FROM pro_ticker_order_intents WHERE id=?',(intent_id,)).fetchone())
    return {'paper':True,'resolved':True,'duplicate':duplicate,'order_resubmitted':False,
            'budget_released':False,'protection_verified':False,'intent':final,
            'posted_entry_shares':posting['delta_shares'],'broker_status':fill['broker_status'],'stale_snapshot':posting.get('stale_snapshot') is True,
            'reason':'Existing owned PAPER order recovered and cumulative entry fills reconciled. No order resubmitted; protective orders still require independent verification.'}
