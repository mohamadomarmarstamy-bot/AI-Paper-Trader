"""Loss-inclusive forward research outcomes, separate from broker P/L and training."""
from contextlib import closing
from datetime import datetime,timezone
from decimal import Decimal
import math
import time
import uuid
import json
import database

HORIZON_SECONDS=86400
KIND='forward_quote_return_not_trade_pnl'
_SCHEMA='''CREATE TABLE IF NOT EXISTS pro_ticker_signal_forward_outcomes (
 request_id TEXT PRIMARY KEY, evaluated_at TEXT NOT NULL, market_timestamp TEXT NOT NULL,
 reference_price REAL NOT NULL, observed_price REAL NOT NULL, elapsed_seconds REAL NOT NULL,
 return_percent REAL NOT NULL);'''


class ForwardNotDue(ValueError):
    pass


def _date(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('Missing timestamp timezone.')
    return result.timestamp()


def _public(row):
    return {**dict(row),'kind':KIND,'training_eligible':False,
            'limitation':'Research reference precedes daily grading; execution and indicator as-of times are unverified. Raw quotes are not adjusted for corporate actions or trading costs.'}


def outcomes_for_signals(conn,ids):
    if not ids or not conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_signal_forward_outcomes'").fetchone():return {}
    placeholders=','.join('?' for _ in ids)
    return {r['request_id']:_public(r) for r in conn.execute(f'SELECT * FROM pro_ticker_signal_forward_outcomes WHERE request_id IN ({placeholders})',ids)}


def evaluate_forward_outcome(request_id,*,quote_fetcher,clock=None):
    from pro_ticker_manual_signals import quote_snapshot
    clock=clock or time.time
    try:
        if str(uuid.UUID(request_id))!=request_id:raise ValueError('Invalid review ID.')
    except (ValueError,TypeError,AttributeError):raise ValueError('Invalid review ID.') from None
    now=clock()
    with closing(database.get_connection()) as conn:
        conn.executescript(_SCHEMA)
        saved=conn.execute('SELECT * FROM pro_ticker_signal_forward_outcomes WHERE request_id=?',(request_id,)).fetchone()
        if saved:return {'paper':True,'duplicate':True,'outcome':_public(saved),'model_updated':False}
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_manual_signal_reviews'").fetchone()
        review=conn.execute('SELECT * FROM pro_ticker_manual_signal_reviews WHERE request_id=?',(request_id,)).fetchone() if exists else None
        if review is None or review['status']!='graded':raise ValueError('Completed entry grade required.')
        reference=json.loads(review['snapshot_json'])
        grade=json.loads(review['grade_json'])
        if reference.get('fresh') is not True or reference.get('status')!='captured' or grade.get('status')!='graded':
            raise ValueError('Fresh reference quote and completed grade required.')
        completed=_date(review['completed_at']);submitted=_date(review['submitted_at'])
        if completed<submitted:raise ValueError('Invalid review chronology.')
        if now<completed+HORIZON_SECONDS:raise ForwardNotDue('At least 24 hours must elapse after grading completes.')
    try:snapshot=quote_snapshot(quote_fetcher(review['symbol']),now=clock())
    except Exception:raise ValueError('A fresh forward quote is unavailable.') from None
    if snapshot['status']!='captured' or snapshot['fresh'] is not True:raise ValueError('Fresh forward quote required.')
    market_at=_date(snapshot['market_timestamp'])
    if market_at<completed+HORIZON_SECONDS:raise ForwardNotDue('The market quote has not reached the forward horizon.')
    baseline=reference['price'];observed=snapshot['price']
    if isinstance(baseline,bool) or not isinstance(baseline,(int,float)) or not math.isfinite(baseline) or baseline<=0:
        raise ValueError('Invalid reference price.')
    percent=float((Decimal(str(observed))/Decimal(str(baseline))-1)*100)
    if not math.isfinite(percent):raise ValueError('Invalid forward return.')
    finished=clock()
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        saved=conn.execute('SELECT * FROM pro_ticker_signal_forward_outcomes WHERE request_id=?',(request_id,)).fetchone()
        duplicate=saved is not None
        if not duplicate:
            # Entry snapshots and grades are immutable through the service.
            conn.execute('INSERT INTO pro_ticker_signal_forward_outcomes VALUES (?,?,?,?,?,?,?)',
                (request_id,datetime.fromtimestamp(finished,timezone.utc).isoformat(),snapshot['market_timestamp'],baseline,observed,market_at-completed,percent))
            saved=conn.execute('SELECT * FROM pro_ticker_signal_forward_outcomes WHERE request_id=?',(request_id,)).fetchone()
        conn.commit()
    return {'paper':True,'duplicate':duplicate,'outcome':_public(saved),'model_updated':False}


def learning_summary():
    with closing(database.get_connection()) as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_manual_signal_reviews'").fetchone()
        total=conn.execute('SELECT COUNT(*) FROM pro_ticker_manual_signal_reviews').fetchone()[0] if exists else 0
        reviews=conn.execute('SELECT request_id,grade_json FROM pro_ticker_manual_signal_reviews ORDER BY submitted_at DESC,request_id DESC LIMIT 500').fetchall() if exists else []
        outcomes=outcomes_for_signals(conn,[r['request_id'] for r in reviews])
    groups={}
    for review in reviews:
        grade=json.loads(review['grade_json']) if review['grade_json'] else None
        outcome=outcomes.get(review['request_id'])
        if not grade or grade.get('status')!='graded' or outcome is None:continue
        key=(grade['version'],grade['grade']);group=groups.setdefault(key,{'version':key[0],'grade':key[1],'samples':0,'positive':0,'negative':0,'flat':0,'sum_return_percent':0})
        value=outcome['return_percent'];group['samples']+=1;group['positive']+=value>0;group['negative']+=value<0;group['flat']+=value==0;group['sum_return_percent']+=value
    rows=[]
    for group in groups.values():
        total_return=group.pop('sum_return_percent');group['mean_forward_return_percent']=total_return/group['samples'];rows.append(group)
    return {'paper':True,'kind':KIND,'model_updated':False,'training_enabled':False,'training_ready_samples':0,
            'review_count':total,'window_count':len(reviews),'has_more':total>len(reviews),
            'evaluated_count':len(outcomes),'unevaluated_count':len(reviews)-len(outcomes),
            'groups':sorted(rows,key=lambda r:(r['version'],r['grade'])),
            'reason':'Descriptive forward research only. No verified execution P/L, point-in-time indicator validation or out-of-sample model evaluation yet. Costs and corporate actions are not modeled; a manually evaluated subset may be biased.'}
