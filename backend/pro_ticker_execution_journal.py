"""Owned broker FILL prices/times, separate from share postings and averages."""
from contextlib import closing
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
import re
import time
import database
from pro_ticker_signal_proposals import _uuid
from pro_ticker_order_intents import _record,IntentConflict
from pro_ticker_broker_admission import _number,_age
from pro_ticker_fill_verification import assess_paper_order_fill

SCHEMA='''CREATE TABLE IF NOT EXISTS pro_ticker_execution_journal (
 activity_id TEXT PRIMARY KEY,intent_id TEXT NOT NULL,reservation_id TEXT NOT NULL,
 broker_order_id TEXT NOT NULL,symbol TEXT NOT NULL,side TEXT NOT NULL,
 shares INTEGER NOT NULL,price TEXT NOT NULL,executed_at TEXT NOT NULL,
 recorded_at TEXT NOT NULL,account_fingerprint TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS pro_ticker_execution_sync (
 intent_id TEXT PRIMARY KEY,recorded_at TEXT NOT NULL,coverage_json TEXT NOT NULL);'''


def _ownership(conn,intent_id):
    row=conn.execute('SELECT * FROM pro_ticker_order_intents WHERE id=?',(intent_id,)).fetchone()
    if row is None:raise ValueError('Saved intent required.')
    intent=_record(conn,row)
    if intent['status'] not in ('broker_linked','closed'):raise IntentConflict('Owned broker entry required.')
    orders={intent['linked_broker_order_id']:{'side':'buy','client_order_id':intent['client_order_id'],'expected_shares':intent['plan']['shares']}}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_paper_exit_links'").fetchone():
        for link in conn.execute('SELECT * FROM pro_ticker_paper_exit_links WHERE reservation_id=?',(intent['reservation_id'],)):
            if link['symbol']!=intent['plan']['symbol'] or link['broker_order_id'] in orders:raise IntentConflict('Ambiguous order ownership.')
            orders[link['broker_order_id']]={'side':'sell','client_order_id':link['client_order_id'],'expected_shares':link['expected_shares']}
    if len(orders)>20:raise IntentConflict('Too many linked orders for bounded execution sync.')
    digest=hashlib.sha256(json.dumps({'orders':orders,'reservation_id':intent['reservation_id'],'plan':intent['plan']},sort_keys=True).encode()).hexdigest()
    return intent,orders,digest


def sync_execution_journal(intent_id,*,context,clock=time.time):
    intent_id=_uuid(intent_id);context.validate()
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA);conn.execute('BEGIN');intent,orders,digest=_ownership(conn,intent_id)
    gateway=context.gateway();account=gateway.read_account()
    if not isinstance(account,dict) or account.get('id')!=context.expected_account_id:raise ValueError('Dedicated PAPER identity required.')
    records=[];coverage=[];activity_ids=set();started=clock()
    earliest=datetime.fromisoformat((intent['attempt_started_at'] or intent['created_at']).replace('Z','+00:00'))
    for id,owned in orders.items():
        order=gateway.read_order(id)
        if not isinstance(order,dict) or order.get('client_order_id')!=owned['client_order_id']:raise ValueError('Broker client ownership mismatch.')
        fill=assess_paper_order_fill(expected_order_id=id,expected_symbol=intent['plan']['symbol'],expected_side=owned['side'],expected_shares=owned['expected_shares'],broker_order=order)
        if not fill['verified']:raise ValueError('Owned order fill snapshot required.')
        activities=gateway.read_fill_activities(id)
        if not isinstance(activities,list) or len(activities)>500:raise ValueError('Bounded execution activities required.')
        total=0;last_time=None
        for activity in activities:
            if not isinstance(activity,dict) or activity.get('activity_type')!='FILL' or activity.get('type') not in ('fill','partial_fill') or activity.get('order_id')!=id or activity.get('symbol')!=intent['plan']['symbol'] or activity.get('side')!=owned['side']:raise ValueError('Execution activity ownership mismatch.')
            activity_id=activity.get('id')
            if not isinstance(activity_id,str) or not re.fullmatch(r'[A-Za-z0-9:_\-]{1,180}',activity_id) or activity_id in activity_ids:raise ValueError('Unique execution activity ID required.')
            qty=_number(activity.get('qty'));price=_number(activity.get('price'))
            if qty!=qty.to_integral_value() or not 0<qty<=owned['expected_shares'] or not Decimal('.000001')<=price<=1000000:raise ValueError('Invalid execution price or quantity.')
            stamp=activity.get('transaction_time')
            if _age(stamp,clock())<0:raise ValueError('Execution time is in the future.')
            moment=datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(timezone.utc)
            if moment<earliest or last_time and moment<last_time:raise ValueError('Execution activity ordering invalid.')
            last_time=moment;total+=int(qty)
            if _number(activity.get('cum_qty'))!=total:raise ValueError('Execution coverage has gaps or overlaps.')
            records.append({'activity_id':activity_id,'broker_order_id':id,'symbol':intent['plan']['symbol'],'side':owned['side'],'shares':int(qty),'price':format(price.normalize(),'f'),'executed_at':moment.isoformat()});activity_ids.add(activity_id)
        if total!=fill['filled_shares']:raise IntentConflict('Execution activities lag or differ from broker fills. Retry later.')
        coverage.append({'broker_order_id':id,'side':owned['side'],'shares':total,'terminal':fill['terminal']})
    if not 0<=clock()-started<=90:raise ValueError('Execution sync exceeded its evidence window.')
    now=datetime.fromtimestamp(clock(),timezone.utc).isoformat();fingerprint=hashlib.sha256(context.expected_account_id.encode()).hexdigest()[:12]
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE');_,_,current_digest=_ownership(conn,intent_id)
        if current_digest!=digest:raise IntentConflict('Order ownership changed during execution sync.')
        for record in records:
            previous=conn.execute('SELECT * FROM pro_ticker_execution_journal WHERE activity_id=?',(record['activity_id'],)).fetchone()
            if previous:
                if previous['intent_id']!=intent_id or previous['reservation_id']!=intent['reservation_id'] or previous['account_fingerprint']!=fingerprint or any(previous[k]!=v for k,v in record.items()):raise IntentConflict('Immutable execution activity changed.')
            else:
                conn.execute('INSERT INTO pro_ticker_execution_journal VALUES (?,?,?,?,?,?,?,?,?,?,?)',(record['activity_id'],intent_id,intent['reservation_id'],record['broker_order_id'],record['symbol'],record['side'],record['shares'],record['price'],record['executed_at'],now,fingerprint))
        for item in coverage:
            qty=conn.execute('SELECT COALESCE(SUM(shares),0) FROM pro_ticker_execution_journal WHERE intent_id=? AND broker_order_id=?',(intent_id,item['broker_order_id'])).fetchone()[0]
            posted=conn.execute('SELECT COALESCE(SUM(shares),0) FROM pro_ticker_strategy_fill_events WHERE broker_order_id=?',(item['broker_order_id'],)).fetchone()[0]
            if qty!=item['shares'] or posted!=qty:raise IntentConflict('Reconcile share accounting before priced execution sync.')
        conn.execute('INSERT INTO pro_ticker_execution_sync VALUES (?,?,?) ON CONFLICT(intent_id) DO UPDATE SET recorded_at=excluded.recorded_at,coverage_json=excluded.coverage_json',(intent_id,now,json.dumps(coverage,sort_keys=True)));conn.commit()
    return {'paper':True,'order_submitted':False,'budget_released':False,'intent_id':intent_id,'execution_count':len(records),'coverage_complete':True,'net_pnl':None,'reason':'Broker FILL prices/times preserved. No orders, share postings, budget release, fees or model updates.'}


def gross_execution_metrics(outcomes):
    """Closed, fully covered trade results only; no account equity or cost claims."""
    profit=sum((value for value in outcomes if value>0),Decimal(0))
    loss=-sum((value for value in outcomes if value<0),Decimal(0))
    total=sum(outcomes,Decimal(0))
    return {'gross_profit_usd':str(profit) if outcomes else None,
            'gross_loss_usd':str(loss) if outcomes else None,
            'gross_profit_factor':str((profit/loss).quantize(Decimal('.000001'))) if loss else None,
            'average_gross_result_usd':str((total/len(outcomes)).quantize(Decimal('.000001'))) if outcomes else None}


def execution_journal_snapshot():
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN')
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_execution_journal'").fetchone()
        rows=[dict(row) for row in conn.execute('SELECT activity_id,intent_id,broker_order_id,symbol,side,shares,price,executed_at,recorded_at FROM pro_ticker_execution_journal ORDER BY executed_at DESC,activity_id DESC LIMIT 500')] if exists else []
        count=conn.execute('SELECT COUNT(*) FROM pro_ticker_execution_journal').fetchone()[0] if exists else 0
        gross=Decimal(0);closed=0;wins=0;losses=0;flat=0;outcomes=[]
        if exists:
            for sync in conn.execute('SELECT * FROM pro_ticker_execution_sync'):
                intent,orders,_=_ownership(conn,sync['intent_id']);coverage=json.loads(sync['coverage_json'])
                if intent['status'] not in ('broker_linked','closed') or {c['broker_order_id'] for c in coverage}!=set(orders) or not all(c['terminal'] for c in coverage):continue
                events=conn.execute('SELECT * FROM pro_ticker_execution_journal WHERE intent_id=?',(intent['id'],)).fetchall()
                buys=sum(r['shares'] for r in events if r['side']=='buy');sells=sum(r['shares'] for r in events if r['side']=='sell')
                if not 0<buys<=intent['plan']['shares'] or sells!=buys or sum(c['shares'] for c in coverage if c['side']=='buy')!=buys:continue
                trade_gross=sum(Decimal(r['price'])*r['shares']*(1 if r['side']=='sell' else -1) for r in events)
                gross+=trade_gross;closed+=1;outcomes.append(trade_gross)
                if trade_gross>0:wins+=1
                elif trade_gross<0:losses+=1
                else:flat+=1
    return {'paper':True,'kind':'owned_broker_fill_activities','rows':rows,'total_count':count,'has_more':count>len(rows),
            'closed_priced_count':closed,'gross_realized_usd':str(gross) if closed else None,
            **gross_execution_metrics(outcomes),
            'gross_wins':wins,'gross_losses':losses,'gross_flat':flat,'gross_win_rate_percent':round(100*wins/closed,2) if closed else None,
            'net_pnl':None,'fees':None,'adjustments':None,'net_pnl_status':'unavailable_cost_evidence','model_updated':False,'reason':'Gross closed covered executions only. Fees, corrections/corporate actions and net performance remain unverified. Research estimates are separate.'}


def prepare_execution_journal_export():
    """Stream the complete activity journal from one SQLite read snapshot.

    The dedicated read connection may move between sequential streaming worker
    threads. It is not reused for writes or by concurrent operations.
    """
    import csv
    import io
    fields=('activity_id','intent_id','broker_order_id','executed_at','recorded_at','symbol','side','shares','price')
    conn=database.get_connection(check_same_thread=False)
    try:
        conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_execution_journal'").fetchone()
        count=conn.execute('SELECT COUNT(*) FROM pro_ticker_execution_journal').fetchone()[0] if exists else 0
    except Exception:
        conn.close();raise
    def chunk(rows, *, header=False):
        out=io.StringIO(newline='');writer=csv.writer(out)
        if header:writer.writerow(fields)
        for row in rows:
            writer.writerow(["'"+row[key] if isinstance(row[key],str) and row[key].startswith(('=','+','-','@')) else row[key] for key in fields])
        return out.getvalue()
    def stream():
        try:
            yield chunk([],header=True)
            if exists:
                cursor=conn.execute('SELECT '+','.join(fields)+' FROM pro_ticker_execution_journal ORDER BY executed_at DESC,activity_id DESC')
                while True:
                    rows=cursor.fetchmany(500)
                    if not rows:break
                    yield chunk(rows)
        finally:conn.close()
    return count,stream(),conn.close
