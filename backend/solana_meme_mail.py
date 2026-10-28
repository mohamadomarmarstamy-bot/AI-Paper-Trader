"""Durable, provider-idempotent email delivery for committed PAPER setting changes."""
from contextlib import closing
import json
import os
import time
import requests
import database


def configured():
    return all(os.getenv(k,'').strip() for k in ('RESEND_API_KEY','HEALTH_ALERT_EMAIL_TO','HEALTH_ALERT_EMAIL_FROM'))


def deliver_one(clock=time.time,post=None):
    now=clock()
    if not configured():return False
    with closing(database.get_connection()) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_meme_learning_mail'").fetchone():return False
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute("SELECT * FROM solana_meme_learning_mail WHERE status='pending' AND next_attempt<=? AND lease_until<=? ORDER BY rowid LIMIT 1",(now,now)).fetchone()
        if not row:conn.commit();return False
        if row['first_attempt'] is not None and now-row['first_attempt']>=23*3600:
            conn.execute("UPDATE solana_meme_learning_mail SET status='attention',error='Retry window expired; delivery is uncertain' WHERE id=?",(row['id'],));conn.commit();return False
        event=json.loads(row['payload_json'])
        # Freeze the complete payload on first attempt so the provider idempotency key is stable.
        if 'email_payload' not in event:
            before=event['before'];after=event['after']
            event['email_payload']={'from':os.environ['HEALTH_ALERT_EMAIL_FROM'].strip(),'to':[os.environ['HEALTH_ALERT_EMAIL_TO'].strip()],
                'subject':f"PAPER meme bot: settings change #{event['change_number']}",
                'text':f"{event['type']}\nChange #{event['change_number']}\nLiquidity minimum: ${before['minimum_liquidity_usd']:,.2f} -> ${after['minimum_liquidity_usd']:,.2f}\nFive-minute volume minimum: ${before['minimum_m5_volume_usd']:,.2f} -> ${after['minimum_m5_volume_usd']:,.2f}\nEntry mode: {before['entry_mode']} -> {after['entry_mode']}\nEvidence: {json.dumps(event['evidence'],sort_keys=True)}\nPosition size and exit/loss limits were not changed.\nPAPER simulation only. Observed selection results are not proof of future profitability.\nReference: {event['id']}"}
        conn.execute('UPDATE solana_meme_learning_mail SET payload_json=?,lease_until=?,attempts=attempts+1,first_attempt=COALESCE(first_attempt,?) WHERE id=?',(json.dumps(event),now+60,now,row['id']));conn.commit()
    try:
        response=(post or requests.post)('https://api.resend.com/emails',headers={'Authorization':'Bearer '+os.environ['RESEND_API_KEY'],'Idempotency-Key':'meme-learning/'+row['id'],'Content-Type':'application/json'},json=event['email_payload'],timeout=8)
        response.raise_for_status();provider_id=response.json().get('id')
        if not provider_id:raise ValueError('Provider acceptance unavailable')
        status='accepted';error=None
    except Exception:
        provider_id=None;status='pending';error='Email provider request failed or acceptance uncertain; retry scheduled'
    with closing(database.get_connection()) as conn:
        conn.execute('UPDATE solana_meme_learning_mail SET status=?,provider_id=?,error=?,lease_until=0,next_attempt=? WHERE id=?',(status,provider_id,error,now+min(3600,60*2**min(row['attempts'],5)),row['id']));conn.commit()
    return status=='accepted'
