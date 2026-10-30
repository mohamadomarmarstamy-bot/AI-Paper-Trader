"""Independent, durable inactivity email monitor for the two PAPER crypto bots."""
import asyncio
from contextlib import closing
from datetime import datetime
import json
import os
import time
import uuid
from zoneinfo import ZoneInfo
import requests
import database

INTERVAL=1800
LABELS={'copy':'Crypto copy trader','auto':'Automatic crypto trader'}
SCHEMA='''CREATE TABLE IF NOT EXISTS crypto_idle_monitor(
 bot TEXT PRIMARY KEY,enabled INTEGER NOT NULL,anchor REAL NOT NULL,last_trade REAL,
 alerted INTEGER NOT NULL DEFAULT 0,checked_at REAL NOT NULL,event_id TEXT);
CREATE TABLE IF NOT EXISTS crypto_idle_mail(
 id TEXT PRIMARY KEY,bot TEXT NOT NULL,event_json TEXT NOT NULL,created_at REAL NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
 first_attempt REAL,next_attempt REAL NOT NULL DEFAULT 0,lease_until REAL NOT NULL DEFAULT 0,
 lease_token TEXT,provider_id TEXT,last_error TEXT,accepted_at REAL);'''


def configured():
    return all(os.getenv(k,'').strip() for k in ('RESEND_API_KEY','HEALTH_ALERT_EMAIL_FROM','HEALTH_ALERT_EMAIL_TO'))


def latest_trade(state,copy_bot=False,conn=None):
    times=[p.get('opened_at') for p in state['positions'].values()]
    times += [p.get('closed_at') for p in state.get('closed',[])]
    times += [p.get('opened_at') for p in state.get('closed',[])]
    if copy_bot and conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_copy_events'").fetchone():
        buy=conn.execute("SELECT MAX(observed_at) FROM solana_copy_events WHERE json_extract(details_json,'$.status')='paper_buy'").fetchone()[0]
        sell=conn.execute('SELECT MAX(closed_at) FROM solana_copy_closes').fetchone()[0]
        times += [buy,sell]
    return max((t for t in times if isinstance(t,(float,int)) and t>0),default=None)


def inspect(now=None):
    """Read committed trades, independently of scans; one alert per idle episode."""
    now=time.time() if now is None else now
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA);conn.execute('BEGIN IMMEDIATE')
        for bot,table in (('copy','solana_copy_state'),('auto','solana_meme_paper_state')):
            exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone()
            saved=conn.execute('SELECT state_json FROM '+table+' WHERE id=1').fetchone() if exists else None
            previous=conn.execute('SELECT * FROM crypto_idle_monitor WHERE bot=?',(bot,)).fetchone()
            try:
                state=json.loads(saved[0]) if saved else {'config':{'enabled':False},'positions':{}}
                enabled=bool(state['config']['enabled']);trade=latest_trade(state,bot=='copy',conn)
            except (ValueError,TypeError,KeyError,AttributeError):
                enabled=bool(previous and previous['enabled']);trade=None
                state={'positions':{},'health':'state_unavailable','error':'Saved bot activity could not be read; inspect the trading worker.'}
            anchor=previous['anchor'] if previous else (trade or now)
            last=previous['last_trade'] if previous else trade
            alerted=previous['alerted'] if previous else 0
            event_id=previous['event_id'] if previous else None
            resumed=enabled and ((previous is not None and not previous['enabled']) or state.get('inactivity_enabled_at',0)>anchor)
            advanced=trade is not None and (last is None or trade>last)
            if not enabled or resumed or advanced:
                conn.execute("UPDATE crypto_idle_mail SET state='cancelled',lease_until=0,lease_token=NULL WHERE bot=? AND state='pending'",(bot,))
                anchor=(now if resumed or not enabled else trade);alerted=0;event_id=None
            if trade is not None:last=max(last or 0,trade)
            if enabled and not alerted and now-anchor>=INTERVAL:
                event_id=str(uuid.uuid4())
                event={'bot':bot,'since':anchor,'last_trade':last,'observed_at':now,'health':state.get('health'),
                       'error':state.get('error'),'last_cycle':state.get('last_cycle'),'open_holdings':len(state['positions'])}
                conn.execute('INSERT INTO crypto_idle_mail(id,bot,event_json,created_at) VALUES(?,?,?,?)',(event_id,bot,json.dumps(event,allow_nan=False),now));alerted=1
            conn.execute('INSERT OR REPLACE INTO crypto_idle_monitor VALUES(?,?,?,?,?,?,?)',(bot,int(enabled),anchor,last,alerted,now,event_id))
        conn.commit()


def stamp(at):
    return datetime.fromtimestamp(at,ZoneInfo('America/New_York')).strftime('%b %d, %Y %I:%M:%S %p %Z') if at else 'No recorded paper trade'


def compose(event):
    label=LABELS[event['bot']]
    return f'PAPER alert: {label} — no trades for 30 minutes', '\n'.join([
        f'{label} has made no successful PAPER buy or sell for at least 30 minutes while enabled.',
        f"Quiet period began: {stamp(event['since'])}",f"Last recorded trade: {stamp(event['last_trade'])}",
        f"Checked: {stamp(event['observed_at'])}",f"Last trading cycle: {stamp(event['last_cycle'])}",
        f"Bot status: {event.get('health') or 'unknown'}",f"Data/status error: {event.get('error') or 'None reported'}",
        f"Open holdings: {event['open_holdings']}",
        'A quiet period can mean no eligible trades, paused data access, or a worker problem; it does not by itself prove a malfunction.',
        'One email per quiet period. A successful paper buy/sell re-arms the alert. Paused bots do not alert.',
        'PAPER only. This notification does not change settings or place trades.'])


def summary(bot,now=None):
    now=time.time() if now is None else now
    with closing(database.get_connection()) as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='crypto_idle_monitor'").fetchone()
        row=conn.execute('SELECT * FROM crypto_idle_monitor WHERE bot=?',(bot,)).fetchone() if exists else None
        mail=conn.execute('SELECT state,accepted_at,last_error FROM crypto_idle_mail WHERE bot=? ORDER BY rowid DESC LIMIT 1',(bot,)).fetchone() if exists else None
    return {'enabled':True,'configured':configured(),'threshold_seconds':INTERVAL,'check_interval_seconds':60,
            'monitor':dict(row) if row else None,'latest_email':dict(mail) if mail else None,
            'next_alert_at':row['anchor']+INTERVAL if row and row['enabled'] and not row['alerted'] else None,
            'basis':'Successful PAPER buys or sells; one email per idle episode while enabled. Accepted means provider acceptance, not confirmed inbox delivery.'}


def deliver_one(*,clock=time.time,post=None):
    if not configured():return False
    now=clock();token=str(uuid.uuid4())
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA);conn.execute('BEGIN IMMEDIATE')
        row=conn.execute("SELECT * FROM crypto_idle_mail WHERE state='pending' AND next_attempt<=? AND lease_until<=? ORDER BY rowid LIMIT 1",(now,now)).fetchone()
        if not row:conn.commit();return False
        if row['first_attempt'] is not None and now-row['first_attempt']>=23*3600:
            conn.execute("UPDATE crypto_idle_mail SET state='attention',last_error='Retry window expired; acceptance uncertain',lease_until=0,lease_token=NULL WHERE id=?",(row['id'],));conn.commit();return False
        event=json.loads(row['event_json'])
        if 'email_payload' not in event:
            subject,body=compose(event)
            event['email_payload']={'from':os.environ['HEALTH_ALERT_EMAIL_FROM'].strip(),'to':[os.environ['HEALTH_ALERT_EMAIL_TO'].strip()],'subject':subject,'text':body}
        conn.execute('UPDATE crypto_idle_mail SET event_json=?,attempts=attempts+1,first_attempt=COALESCE(first_attempt,?),lease_until=?,lease_token=? WHERE id=?',(json.dumps(event),now,now+90,token,row['id']));conn.commit()
    try:
        response=(post or requests.post)('https://api.resend.com/emails',headers={'Authorization':'Bearer '+os.environ['RESEND_API_KEY'].strip(),'Content-Type':'application/json','Idempotency-Key':'crypto-idle/'+row['id']},json=event['email_payload'],timeout=10,allow_redirects=False)
        response.raise_for_status();provider_id=response.json().get('id')
        if not isinstance(provider_id,str) or not provider_id:raise ValueError()
        status='accepted';error=None
    except Exception:provider_id=None;status='pending';error='Email request failed or acceptance uncertain; retry scheduled'
    with closing(database.get_connection()) as conn:
        conn.execute("UPDATE crypto_idle_mail SET state=?,provider_id=?,last_error=?,accepted_at=?,next_attempt=?,lease_until=0,lease_token=NULL WHERE id=? AND state='pending' AND lease_token=?",(status,provider_id,error,now if status=='accepted' else None,now+min(3600,60*2**min(row['attempts'],6)),row['id'],token));conn.commit()
    return status=='accepted'


async def worker():
    while True:
        try:
            await asyncio.to_thread(inspect)
            for _ in range(2):await asyncio.to_thread(deliver_one)
        except Exception:pass
        await asyncio.sleep(60)
