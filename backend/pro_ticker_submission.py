"""Explicit owner PAPER dispatch. A committed attempt is never dispatched twice."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_CEILING
import json
import time
import uuid
import database
from pro_ticker_broker_admission import PaperEvidenceContext, review_paper_admission, _ledger, _number
from pro_ticker_order_intents import IntentConflict, _audit, _record
from pro_ticker_signal_proposals import _uuid
from pro_ticker_submission_recovery import recover_paper_submission


@dataclass(frozen=True)
class PaperSubmissionContext(PaperEvidenceContext):
    # Server composition only; never enabled by browser data or original keys.
    owner_submission_enabled: bool = False

    def submission_gateway(self):
        if self.owner_submission_enabled is not True:
            raise ValueError('Owner PAPER submission is disabled.')
        gateway = self.gateway()
        if not callable(getattr(gateway, 'submit_bracket', None)):
            raise ValueError('Dedicated PAPER bracket transport required.')
        return gateway


def bracket_request(intent):
    """Limit cost to the saved reservation; use broker-native protective exits."""
    plan = intent['plan']; shares = plan['shares']
    price = Decimal(plan['allocation_cents']) / (100 * shares)
    quantum = Decimal('.01') if price >= 1 else Decimal('.0001')
    price = price.quantize(quantum, rounding=ROUND_DOWN)
    stop = _number(plan['stop_price']); target = _number(plan['take_profit_price'])
    # Round sell levels upward: never widen the saved stop or lower its target.
    stop=stop.quantize(Decimal('.01') if stop>=1 else Decimal('.0001'),rounding=ROUND_CEILING)
    target=target.quantize(Decimal('.01') if target>=1 else Decimal('.0001'),rounding=ROUND_CEILING)
    if not 0 < stop < price < target:raise ValueError('Invalid bracket levels.')
    if (price-stop)*shares*100 > plan['estimated_stop_risk_cents']:
        raise ValueError('Bracket exceeds approved stop-risk estimate.')
    return {'symbol':plan['symbol'], 'qty':str(shares), 'side':'buy', 'type':'limit',
            'limit_price':str(price), 'time_in_force':'gtc', 'extended_hours':False,
            'client_order_id':intent['client_order_id'], 'order_class':'bracket',
            'stop_loss':{'stop_price':str(stop)}, 'take_profit':{'limit_price':str(target)}}


def submit_paper_intent(intent_id, payload, *, context, clock=None):
    intent_id = _uuid(intent_id)
    if not isinstance(payload, dict) or set(payload) != {'confirmed'} or payload['confirmed'] is not True:
        raise ValueError('Explicit owner PAPER order confirmation required.')
    if not isinstance(context, PaperSubmissionContext):raise ValueError('Dedicated submission context required.')
    gateway = context.submission_gateway();clock = clock or time.time
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN');initial, _, _ = _ledger(conn, intent_id)
    if initial['attempt_started_at'] is not None:
        return {**recover_paper_submission(intent_id,context=context),'dispatch_attempted':False}
    if initial['status'] != 'prepared':raise IntentConflict('Prepared intent required.')
    request = bracket_request(initial)
    review = review_paper_admission(intent_id, {'request_id':str(uuid.uuid4())},context=context,clock=clock)
    if review['checks_passed'] is not True or review['expired'] or not review['ledger_unchanged']:
        raise IntentConflict('Fresh broker admission checks did not pass.')
    # BEGIN IMMEDIATE is the dispatch claim. Provider work happens outside this
    # transaction. Cancel/settings/reservations or another claim invalidate it.
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE');current, _, digest = _ledger(conn,intent_id)
        evidence = conn.execute('SELECT * FROM pro_ticker_admission_reviews WHERE id=?',(review['review_id'],)).fetchone()
        if current['status'] != 'prepared' or current['attempt_started_at'] is not None:
            raise IntentConflict('Another request already claimed this intent. Use recovery.')
        if not evidence or evidence['ledger_digest'] != digest or clock() >= evidence['expires_at']:
            raise IntentConflict('Admission expired or ledger changed; no order dispatched.')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_strategy_positions'").fetchone():
            if conn.execute('SELECT 1 FROM pro_ticker_strategy_positions WHERE symbol=? AND shares>0',(current['plan']['symbol'],)).fetchone():
                raise IntentConflict('Strategy shares appeared during admission.')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_strategy_fill_events'").fetchone():
            if conn.execute('SELECT 1 FROM pro_ticker_strategy_fill_events WHERE symbol=? AND recorded_at>=?',(current['plan']['symbol'],current['created_at'])).fetchone():
                raise IntentConflict('New strategy fill evidence requires reconciliation.')
        # One unresolved dispatched entry at a time across the strategy. This
        # also prevents parallel intents from racing the same broker snapshot.
        if conn.execute("SELECT 1 FROM pro_ticker_order_intents WHERE id!=? AND attempt_started_at IS NOT NULL AND status IN ('submitting','uncertain')",(intent_id,)).fetchone():
            raise IntentConflict('Resolve the existing submission attempt first.')
        if bracket_request(current) != request:raise IntentConflict('Order plan changed.')
        now = datetime.fromtimestamp(clock(),timezone.utc).isoformat()
        conn.execute("UPDATE pro_ticker_order_intents SET status='submitting',attempt_started_at=?,updated_at=? WHERE id=?",(now,now,intent_id))
        _audit(conn,intent_id,'owner_paper_submission_started',now,{'admission_review_id':review['review_id'],'request':request,'paper':True})
        conn.commit()
    # Never retry POST, even for 429/5xx, invalid JSON or a lost confirmation.
    # Recovery performs a separate account-verified GET by the saved client ID.
    try:gateway.submit_bracket(request)
    except Exception:pass
    try:return {**recover_paper_submission(intent_id,context=context),'dispatch_attempted':True}
    except Exception:
        with closing(database.get_connection()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("UPDATE pro_ticker_order_intents SET status='uncertain' WHERE id=? AND status='submitting'",(intent_id,))
            value=_record(conn,conn.execute('SELECT * FROM pro_ticker_order_intents WHERE id=?',(intent_id,)).fetchone());conn.commit()
        return {'paper':True,'resolved':False,'dispatch_attempted':True,'order_resubmitted':False,
                'budget_released':False,'protection_verified':False,'intent':value,
                'reason':'Submission outcome uncertain. Funds held. Use existing-order recovery; never send another buy.'}
