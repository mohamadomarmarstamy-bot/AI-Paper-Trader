"""Read-only live-exit observer and durable, idempotent owner email outbox.

This worker never submits transactions and never runs in the execution loop.
"""
import asyncio
from contextlib import closing
import hashlib
import json
import os
import time
import requests
import database

SCHEMA = '''CREATE TABLE IF NOT EXISTS solana_exit_alert_state (
 id INTEGER PRIMARY KEY CHECK(id=1), started_at REAL NOT NULL, seen_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS solana_exit_incidents (
 position_id TEXT PRIMARY KEY, opened_at REAL NOT NULL, resolved_at REAL, mint TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS solana_exit_mail (
 id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
 lease_until REAL NOT NULL DEFAULT 0, first_attempt REAL, provider_id TEXT, error TEXT);'''


def configured():
    return all(os.getenv(k, '').strip() for k in ('RESEND_API_KEY', 'HEALTH_ALERT_EMAIL_FROM', 'HEALTH_ALERT_EMAIL_TO'))


def enqueue(conn, key, kind, order, now):
    ident = hashlib.sha256(key.encode()).hexdigest()
    signature = order.get('signature')
    body = (f"Live Solana exit: {kind}\nToken: {order['mint']}\nOrder: {order['id']}\n"
            f"Reason: {order.get('reason', 'unknown')}\nObserved at Unix time: {now}\n"
            f"Status: {order['status']}\n")
    if signature: body += f"Transaction: https://solscan.io/tx/{signature}\n"
    # Do not transmit arbitrary upstream error strings, which can contain credentials.
    body += ('A finalized sell was verified. This may be a partial sale; check remaining holdings.\n' if kind == 'recovered'
             else 'A sale is not confirmed. The live worker continues its normal reconciliation and exit checks.\n')
    body += 'Open the app /real-copy/status for current status. This email does not initiate a trade.'
    payload = {'subject': f'AI Trader: live exit {kind}', 'text': body}
    conn.execute('INSERT OR IGNORE INTO solana_exit_mail(id,payload_json) VALUES (?,?)', (ident, json.dumps(payload)))


def observe(clock=time.time):
    now = float(clock())
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA)
        conn.execute('BEGIN IMMEDIATE')
        state = conn.execute('SELECT * FROM solana_exit_alert_state WHERE id=1').fetchone()
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_real_orders'").fetchone()
        rows = conn.execute('SELECT details_json FROM solana_real_orders ORDER BY created_at DESC LIMIT 100').fetchall() if exists else []
        orders = sorted((json.loads(r[0]) for r in rows), key=lambda o: o['created_at'])
        if state is None:
            # Baseline old terminal orders; do not send historical failure emails at deployment.
            seen = {o['id']: o['status'] for o in orders if o['status'] not in ('preparing', 'signed', 'submitted', 'unknown')}
            started = now
        else:
            seen = json.loads(state['seen_json']); started = state['started_at']
        for o in orders:
            if o.get('side') != 'sell': continue
            position = o.get('position_id', o['id'])
            incident = conn.execute('SELECT * FROM solana_exit_incidents WHERE position_id=?', (position,)).fetchone()
            changed = seen.get(o['id']) != o['status']
            kind = None
            intentional_deferral = o.get('reason') == 'profit_take_half' and o.get('error', '').startswith('Profit trim deferred:')
            if o['status'] in ('failed', 'rejected', 'expired', 'abandoned', 'canceled') and changed and not intentional_deferral:
                kind = 'failed'
            elif o['status'] in ('signed', 'submitted', 'unknown') and now-o.get('send_started_at', o['created_at']) >= 30:
                kind = 'pending over 30 seconds'
            if kind:
                if incident is None or incident['resolved_at'] is not None:
                    conn.execute('INSERT OR REPLACE INTO solana_exit_incidents VALUES (?,?,NULL,?)', (position, now, o['mint']))
                    incident = {'opened_at': now}
                enqueue(conn, f"{position}:{incident['opened_at']}:{'failed' if kind == 'failed' else 'pending'}", kind, o, now)
            elif o['status'] == 'confirmed' and changed and incident is not None and incident['resolved_at'] is None:
                enqueue(conn, f"{position}:{incident['opened_at']}:recovery", 'recovered', o, now)
                conn.execute('UPDATE solana_exit_incidents SET resolved_at=? WHERE position_id=?', (now, position))
            seen[o['id']] = o['status']
        seen = {o['id']: seen[o['id']] for o in orders if o['id'] in seen}
        conn.execute('INSERT OR REPLACE INTO solana_exit_alert_state VALUES (1,?,?)', (started, json.dumps(seen)))
        conn.commit()


def deliver_one(clock=time.time, post=None):
    now = float(clock())
    if not configured(): return False
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA)
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("SELECT * FROM solana_exit_mail WHERE status='pending' AND next_attempt<=? AND lease_until<=? ORDER BY rowid LIMIT 1", (now, now)).fetchone()
        if not row: conn.commit(); return False
        if row['first_attempt'] is not None and now-row['first_attempt'] >= 23*3600:
            conn.execute("UPDATE solana_exit_mail SET status='attention',error='Email retry window expired; acceptance uncertain' WHERE id=?", (row['id'],)); conn.commit(); return False
        payload = json.loads(row['payload_json'])
        if 'from' not in payload:
            payload.update({'from': os.environ['HEALTH_ALERT_EMAIL_FROM'].strip(), 'to': [os.environ['HEALTH_ALERT_EMAIL_TO'].strip()]})
        conn.execute('UPDATE solana_exit_mail SET payload_json=?,lease_until=?,first_attempt=COALESCE(first_attempt,?),attempts=attempts+1 WHERE id=?', (json.dumps(payload), now+30, now, row['id'])); conn.commit()
    try:
        response = (post or requests.post)('https://api.resend.com/emails', headers={'Authorization': 'Bearer '+os.environ['RESEND_API_KEY'].strip(), 'Idempotency-Key': 'live-exit/'+row['id']}, json=payload, timeout=8, allow_redirects=False)
        response.raise_for_status(); provider_id = response.json().get('id')
        if not provider_id: raise ValueError('Missing acceptance')
        status, error = 'accepted', None
    except Exception:
        provider_id, status, error = None, 'pending', 'Email request failed or acceptance uncertain; retry scheduled'
    with closing(database.get_connection()) as conn:
        conn.execute('UPDATE solana_exit_mail SET status=?,provider_id=?,error=?,lease_until=0,next_attempt=? WHERE id=?', (status, provider_id, error, now+min(300, 10*2**min(row['attempts'], 5)), row['id'])); conn.commit()
    return status == 'accepted'


def summary():
    with closing(database.get_connection()) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_exit_mail'").fetchone()
        counts = dict(conn.execute('SELECT status,COUNT(*) FROM solana_exit_mail GROUP BY status').fetchall()) if exists else {}
        monitor = conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_exit_alert_state'").fetchone()
    return {'configured': configured(), 'monitor_initialized': bool(monitor),
            'pending_warning_seconds': 30, 'delivery_counts': counts,
            'basis': 'Provider acceptance is not confirmed inbox delivery. Alerts observe live sell orders; no trades are initiated.'}


async def observe_loop():
    while True:
        try: await asyncio.to_thread(observe)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Live exit alert monitor failed')
        await asyncio.sleep(2)


async def delivery_loop():
    while True:
        try: await asyncio.to_thread(deliver_one)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Live exit email delivery failed')
        await asyncio.sleep(2)


async def worker():
    await asyncio.gather(observe_loop(), delivery_loop())
