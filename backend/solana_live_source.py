"""Verified source observations, committed independently of PAPER simulation."""
from contextlib import closing
from copy import deepcopy
import json
import time

import database

TABLE = 'solana_live_source_events'
SCHEMA = '''CREATE TABLE IF NOT EXISTS solana_live_source_events(
 id TEXT PRIMARY KEY, observed_at REAL NOT NULL, details_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS solana_live_source_health(
 wallet TEXT PRIMARY KEY, checked_at REAL NOT NULL, complete INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS solana_live_source_sell_lookup ON solana_live_source_events(
 json_extract(details_json,'$.side'),json_extract(details_json,'$.wallet'),
 json_extract(details_json,'$.mint'),CAST(json_extract(details_json,'$.source_at') AS INTEGER));'''


def initialize(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def interested(wallet, source, run):
    from solana_live_source_policy import paper_only
    return (any(p['source_wallet']==wallet for p in run['positions'].values()) or
            run['enabled'] and source['config']['enabled'] and
            any(t['wallet']==wallet and t.get('active',True) for t in source['traders']) and
            not paper_only(wallet,source['traders']))


def publish(wallet, source, run, rows, cursor, detected_at):
    """Only a successful supported RPC decoder may call this internal boundary.

    Incomplete history stays buffered in the existing source cursor. Neither a
    websocket notification nor a chart price can create these observations.
    """
    if database._paper_trial_path.get() is not None or not interested(wallet,source,run):return
    from solana_copy_trader import signals
    previous=source['cursors'].get(wallet,{})
    if previous.get('provider')!='rpc':return
    coverage=cursor.get('rpc_coverage',{})
    complete='window' not in cursor and bool(coverage) and not any(coverage.get(k) for k in ('error','queued','history_truncated'))
    events=previous.get('pending',[])+[{**s,'detected_at':detected_at} for s in signals(rows,wallet)] if complete else []
    events=[e for e in events if e.get('source')=='solana_rpc_pump' and e.get('wallet')==wallet]
    with closing(database.get_connection()) as conn:
        initialize(conn)
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('INSERT INTO solana_live_source_health VALUES(?,?,?) ON CONFLICT(wallet) DO UPDATE SET checked_at=excluded.checked_at,complete=excluded.complete',
                     (wallet,detected_at,int(complete)))
        for event in events:
            row={**deepcopy(event),'status':'verified_source','observed_at':detected_at,
                 'feed_saved_at':time.time(),'entry_path':'verified_source'}
            conn.execute('INSERT OR IGNORE INTO solana_live_source_events VALUES(?,?,?)',
                         (row['id'],detected_at,json.dumps(row,allow_nan=False)))
        conn.commit()
    if events:
        from solana_real_worker import notify_source_events
        notify_source_events()


def failed(wallet, source, run, checked_at):
    if database._paper_trial_path.get() is not None or not interested(wallet,source,run):return
    with closing(database.get_connection()) as conn:
        initialize(conn)
        conn.execute('INSERT INTO solana_live_source_health VALUES(?,?,0) ON CONFLICT(wallet) DO UPDATE SET checked_at=excluded.checked_at,complete=0',
                     (wallet,checked_at))
        conn.commit()


def ready(wallet, now):
    with closing(database.get_connection()) as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_live_source_health'").fetchone()
        row=conn.execute('SELECT checked_at,complete FROM solana_live_source_health WHERE wallet=?',(wallet,)).fetchone() if exists else None
    return bool(row and row['complete'] and 0<=now-row['checked_at']<=60)


def events(cursor):
    with closing(database.get_connection()) as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_live_source_events'").fetchone()
        rows=conn.execute('SELECT rowid,details_json FROM solana_live_source_events WHERE rowid>? ORDER BY rowid LIMIT 500',(cursor,)).fetchall() if exists else []
    return [(r[0],json.loads(r[1])) for r in rows]


def latest(conn):
    exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_live_source_events'").fetchone()
    return conn.execute('SELECT COALESCE(MAX(rowid),0) FROM solana_live_source_events').fetchone()[0] if exists else 0


def sold(conn, order):
    exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_live_source_events'").fetchone()
    return bool(exists and conn.execute("SELECT 1 FROM solana_live_source_events WHERE json_extract(details_json,'$.side')='sell' AND json_extract(details_json,'$.wallet')=? AND json_extract(details_json,'$.mint')=? AND CAST(json_extract(details_json,'$.source_at') AS INTEGER)>=? LIMIT 1",
                                       (order['source_wallet'],order['mint'],int(order['source_at']))).fetchone())
