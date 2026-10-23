"""Restartable observation worker. Only existing attempted orders are eligible.

No dispatch, cancel, close, or budget-release capability is used here. SQLite
claims coordinate workers; cumulative reconciliation remains idempotent if a
process dies after posting a fill and before saving its observation.
"""
import asyncio
from contextlib import closing
import logging
import os
import time
import uuid
import database
from pro_ticker_position_monitor import monitor_paper_position

_SCHEMA = '''CREATE TABLE IF NOT EXISTS pro_ticker_monitor_observations (
 intent_id TEXT PRIMARY KEY, checked_at REAL NOT NULL DEFAULT 0,
 lease_until REAL NOT NULL DEFAULT 0, lease_owner TEXT,
 state TEXT NOT NULL DEFAULT 'pending', protection_verified INTEGER NOT NULL DEFAULT 0);
'''


def monitor_enabled(context, environ=None):
    env = os.environ if environ is None else environ
    if env.get('PRO_TICKER_POSITION_MONITOR_ENABLED') != 'true' or context is None:
        return False
    try:
        context.validate()
    except (ValueError, TypeError):
        return False
    return True


def _claim(now, owner):
    with closing(database.get_connection()) as conn:
        conn.executescript(_SCHEMA)
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_order_intents'").fetchone():
            return None
        row = conn.execute('''SELECT i.id FROM pro_ticker_order_intents i
            JOIN pro_ticker_paper_reservations r ON r.id=i.reservation_id
            LEFT JOIN pro_ticker_monitor_observations m ON m.intent_id=i.id
            WHERE i.attempt_started_at IS NOT NULL AND i.status!='canceled'
              AND r.status IN ('reserved','submitted','filled')
              AND COALESCE(m.lease_until,0)<=?
              AND COALESCE(m.checked_at,0)<=?
            ORDER BY COALESCE(m.checked_at,0),i.created_at,i.id LIMIT 1''', (now, now-30)).fetchone()
        if row is None:
            return None
        conn.execute('''INSERT INTO pro_ticker_monitor_observations(intent_id,lease_until,lease_owner)
            VALUES (?,?,?) ON CONFLICT(intent_id) DO UPDATE SET
            lease_until=excluded.lease_until,lease_owner=excluded.lease_owner''', (row['id'],now+300,owner))
        conn.commit()
        return row['id']


def run_monitor_batch(*, context, limit=8, clock=time.time):
    context.validate()
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('Bounded monitor batch required.')
    counts = {'observed':0,'errors':0}
    for _ in range(limit):
        owner = str(uuid.uuid4())
        intent_id = _claim(clock(), owner)
        if intent_id is None:
            break
        protected = False
        try:
            result = monitor_paper_position(intent_id, context=context)
            protected = result['protection_verified'] is True
            state = 'closed' if result['closed'] else 'protected' if protected else 'needs_review'
            counts['observed'] += 1
        except Exception:
            # Never persist exception text: upstream failures may contain secrets.
            state = 'unavailable'
            counts['errors'] += 1
        with closing(database.get_connection()) as conn:
            conn.execute('''UPDATE pro_ticker_monitor_observations
                SET checked_at=?,lease_until=0,lease_owner=NULL,state=?,protection_verified=?
                WHERE intent_id=? AND lease_owner=?''', (clock(),state,int(protected),intent_id,owner))
            conn.commit()
    return counts


def monitor_snapshot(*, context):
    now = time.time()
    with closing(database.get_connection()) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_monitor_observations'").fetchone()
        rows = [dict(row) for row in conn.execute('''SELECT intent_id,checked_at,state,protection_verified
            FROM pro_ticker_monitor_observations ORDER BY checked_at DESC,intent_id LIMIT 200''')] if exists else []
    for row in rows:
        row['stale'] = now-row['checked_at'] > 120
        row['protection_verified'] = bool(row['protection_verified']) and not row['stale']
    return {'paper':True,'enabled':monitor_enabled(context),'order_submitted':False,
            'budget_released':False,'observations':rows}


async def monitor_loop(context):
    while True:
        try:
            await asyncio.to_thread(run_monitor_batch, context=context)
        except Exception:
            logging.getLogger(__name__).warning('PAPER monitoring unavailable; retrying without broker mutations.')
        await asyncio.sleep(30)


def start_monitor(context):
    return asyncio.create_task(monitor_loop(context)) if monitor_enabled(context) else None


async def stop_monitor(task):
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
