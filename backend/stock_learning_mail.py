"""Durable emails for committed stock PAPER reviews; no broker access."""
from contextlib import closing
from datetime import datetime
import json
import os
import time
import uuid
from zoneinfo import ZoneInfo
import requests
import database

SCHEMA='''CREATE TABLE IF NOT EXISTS stock_learning_mail(
 id TEXT PRIMARY KEY,event_json TEXT NOT NULL,created_at REAL NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
 first_attempt REAL,next_attempt REAL NOT NULL DEFAULT 0,lease_until REAL NOT NULL DEFAULT 0,
 lease_token TEXT,provider_id TEXT,last_error TEXT,accepted_at REAL);'''

LABELS={
 'position_size_percent':('Position size','% of equity'),
 'stock_min_price_usd':('Minimum stock price','USD'),
 'stock_max_price_usd':('Maximum stock price','USD'),
 'stock_max_float_millions':('Maximum float','million shares'),
 'stock_min_volume_ratio':('Minimum daily volume ratio','×'),
 'stock_min_time_volume_ratio':('Minimum same-time volume ratio','×'),
 'stock_min_average_volume':('Minimum average daily volume','shares'),
 'stock_max_spread_percent':('Maximum spread','%'),
 'stock_min_day_change_percent':('Minimum daily gain','%'),
 'stock_max_atr_percent':('Additional ATR cap','%'),
 'stock_max_vwap_distance_percent':('Maximum entry above VWAP','%'),
}


def configured():
    return all(os.getenv(key,'').strip() for key in ('RESEND_API_KEY','HEALTH_ALERT_EMAIL_TO','HEALTH_ALERT_EMAIL_FROM'))


def enqueue(conn,ident,event):
    """Must run inside the review transaction. Never sends from this function."""
    conn.execute('INSERT OR IGNORE INTO stock_learning_mail(id,event_json,created_at) VALUES(?,?,?)',
                 (ident,json.dumps(event,allow_nan=False),time.time()))


def changes(before,after):
    lines=[]
    for key,value in after.items():
        label,unit=LABELS.get(key,(key.replace('_',' ').capitalize(),''))
        lines.append(f"- {label}: {before.get(key)} -> {value} {unit}".rstrip())
    return lines


def compose(event):
    rollback=event.get('rollback') or (event if event.get('kind')=='rollback' else None)
    if event.get('kind')=='batch':
        report=event['report']
        title=f"PAPER stocks: 25-trade review #{event['batch_number']}"
        stamp=datetime.fromisoformat(event['reviewed_at']).astimezone(ZoneInfo('America/New_York'))
        lines=[title,f"Reviewed: {stamp:%b %d, %Y %I:%M %p %Z}",
               f"Completed trades: {report['closed_count']} (not necessarily 25 different companies)",
               f"Wins / losses after estimated extra costs: {report['wins']} / {report['losses']}",
               f"Broker-recorded P/L: ${report['realized_pnl_usd']:,.2f}",
               f"Estimated net P/L: ${report['estimated_net_pnl_usd']:,.2f}",
               '', 'Settings changed:' if report.get('after') else 'No new filter or position-size change in this batch.']
        lines+=changes(report.get('before',{}),report.get('after',{}))
        lines+=['Reason: '+report['reason']]
        if report.get('cost_model'):
            model=report['cost_model'];lines.append(f"Extra cost assumptions per side: {model['stock_slippage_bps_per_side']:g} bps slippage + {model['stock_fee_bps_per_side']:g} bps fees.")
        lines+=['Symbols: '+', '.join(dict.fromkeys(report.get('symbols',[]))),
                'Review reference: '+event['id']]
    else:
        title='PAPER stocks: 25-trade follow-up rollback'
        lines=[title,'The filter experiment completed its 25 qualifying follow-up trades.']
    if rollback:
        lines+=['','Automatic filter rollback:']+changes(rollback.get('before',{}),rollback.get('after',{}))
        lines+=['Reason: '+rollback['reason'],
                f"Baseline estimated mean return: {rollback['baseline_net_mean_return_percent']:.3f}%",
                f"Follow-up estimated mean return: {rollback['followup_net_mean_return_percent']:.3f}%"]
    lines+=['','See Settings > 25-trade automatic adjustments and Change history for the saved details.',
            'PAPER simulation only. Estimated costs and small observational samples do not establish future profitability.']
    return title,'\n'.join(lines)


def summary(conn):
    counts={row['state']:row['count'] for row in conn.execute('SELECT state,COUNT(*) AS count FROM stock_learning_mail GROUP BY state')}
    latest=conn.execute('SELECT state,last_error,accepted_at FROM stock_learning_mail ORDER BY rowid DESC LIMIT 1').fetchone()
    return {'enabled':True,'configured':configured(),'pending':counts.get('pending',0),
            'accepted':counts.get('accepted',0),'attention':counts.get('attention',0),
            'latest':dict(latest) if latest else None,
            'basis':'Every new completed 25-trade automatic stock batch. Accepted means the email provider accepted the message, not verified inbox delivery.'}


def deliver_one(*,clock=time.time,post=None):
    if not configured():return False
    now=clock();token=str(uuid.uuid4())
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA);conn.execute('BEGIN IMMEDIATE')
        row=conn.execute("SELECT * FROM stock_learning_mail WHERE state='pending' AND next_attempt<=? AND lease_until<=? ORDER BY rowid LIMIT 1",(now,now)).fetchone()
        if not row:conn.commit();return False
        # Resend remembers idempotency keys for 24h. Never blindly resend an
        # uncertain request after that window; surface it for investigation.
        if row['first_attempt'] is not None and now-row['first_attempt']>=23*3600:
            conn.execute("UPDATE stock_learning_mail SET state='attention',last_error='Retry window expired; provider acceptance is uncertain',lease_until=0,lease_token=NULL WHERE id=?",(row['id'],));conn.commit();return False
        event=json.loads(row['event_json'])
        if 'email_payload' not in event:
            subject,body=compose(event)
            event['email_payload']={'from':os.environ['HEALTH_ALERT_EMAIL_FROM'].strip(),
                'to':[os.environ['HEALTH_ALERT_EMAIL_TO'].strip()],'subject':subject,'text':body}
        conn.execute('UPDATE stock_learning_mail SET event_json=?,attempts=attempts+1,first_attempt=COALESCE(first_attempt,?),lease_until=?,lease_token=? WHERE id=?',
                     (json.dumps(event,allow_nan=False),now,now+90,token,row['id']));conn.commit()
    try:
        response=(post or requests.post)('https://api.resend.com/emails',
            headers={'Authorization':'Bearer '+os.environ['RESEND_API_KEY'].strip(),'Content-Type':'application/json','Idempotency-Key':'stock-review/'+row['id']},
            json=event['email_payload'],timeout=10)
        response.raise_for_status();provider_id=response.json().get('id')
        if not isinstance(provider_id,str) or not provider_id:raise ValueError('Missing provider acceptance')
        state='accepted';error=None
    except Exception:
        provider_id=None;state='pending';error='Email request failed or acceptance uncertain; retry scheduled'
    with closing(database.get_connection()) as conn:
        conn.execute('UPDATE stock_learning_mail SET state=?,provider_id=?,last_error=?,accepted_at=?,next_attempt=?,lease_until=0,lease_token=NULL WHERE id=? AND state=\'pending\' AND lease_token=?',
                     (state,provider_id,error,now if state=='accepted' else None,now+min(3600,60*2**min(row['attempts'],6)),row['id'],token));conn.commit()
    return state=='accepted'
