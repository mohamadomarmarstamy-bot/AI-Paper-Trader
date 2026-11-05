"""Independent live stop and an explicitly owner-applied paper/live experiment.

Deployment alone does not change either account's settings or open positions.
"""
from contextlib import closing
import json
import time
import database
from solana_meme_bot import Conflict

SCHEMA = '''CREATE TABLE IF NOT EXISTS solana_live_stop_settings (
 id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
 stop_loss_percent REAL NOT NULL, started_at REAL NOT NULL, audit_json TEXT NOT NULL)'''
ACK = 'APPLY PAPER 0 REAL 40 STOP EXPERIMENT'


def settings():
    with closing(database.get_connection()) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_live_stop_settings'").fetchone()
        row = conn.execute('SELECT * FROM solana_live_stop_settings WHERE id=1').fetchone() if exists else None
    return {'revision': row['revision'] if row else 0,
            'stop_loss_percent': row['stop_loss_percent'] if row else None,
            'independent': bool(row), 'started_at': row['started_at'] if row else None}


def live_stop(paper_config):
    """Keep the existing shared rule until the owner explicitly separates it."""
    saved = settings()
    return saved['stop_loss_percent'] if saved['independent'] else paper_config['stop_loss_percent']


def apply_split(paper_revision, live_revision, exit_revision, acknowledgement):
    if any(type(n) is not int or n < 0 for n in (paper_revision, live_revision, exit_revision)) or acknowledgement != ACK:
        raise ValueError('Current revisions and explicit stop experiment acknowledgement required.')
    now = time.time()
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'solana_copy_state', 'solana_real_run', 'solana_real_orders'} <= names:
            raise Conflict('Load both copy accounts before applying the experiment.')
        conn.execute(SCHEMA)
        saved = conn.execute('SELECT * FROM solana_live_stop_settings WHERE id=1').fetchone()
        source_row = conn.execute('SELECT * FROM solana_copy_state WHERE id=1').fetchone()
        live_row = conn.execute('SELECT * FROM solana_real_run WHERE id=1').fetchone()
        if not source_row or not live_row:
            raise Conflict('Load both copy accounts before applying the experiment.')
        source, runtime = json.loads(source_row['state_json']), json.loads(live_row['state_json'])
        if source['settings_revision'] != paper_revision or live_row['revision'] != live_revision or (saved['revision'] if saved else 0) != exit_revision:
            raise Conflict('Copy settings changed. Refresh before applying the experiment.')
        if live_row['lease_until'] > now:
            raise Conflict('A live cycle is running. Refresh and retry after it finishes.')
        if conn.execute("SELECT 1 FROM solana_real_orders WHERE status IN ('preparing','signed','submitted','unknown') LIMIT 1").fetchone():
            raise Conflict('Wait for the pending live transaction to settle before changing stops.')
        audit = {'started_at': now, 'paper_previous_stop': source['config']['stop_loss_percent'],
                 'live_previous_stop': saved['stop_loss_percent'] if saved else source['config']['stop_loss_percent'],
                 'paper_cash_at_start': source['cash_usd'], 'live_realized_lamports_at_start': runtime['realized_lamports'],
                 'paper_existing_positions': list(source['positions']),
                 'live_previous_position_stops': {k: p['protection']['stop_loss_percent'] for k, p in runtime['positions'].items()}}
        source['config']['stop_loss_percent'] = 0
        source['settings_revision'] += 1
        for position in runtime['positions'].values():
            position['protection']['stop_loss_percent'] = 40
            # Re-evaluate an unsent old 20% trigger under the newly selected rule.
            if position.get('pending_reason') == 'stop_loss':
                position['pending_reason'] = None
                position['last_exit_attempt_at'] = 0
        conn.execute('INSERT OR REPLACE INTO solana_live_stop_settings VALUES(1,?,?,?,?)',
                     (exit_revision + 1, 40, now, json.dumps(audit, allow_nan=False)))
        conn.execute('UPDATE solana_copy_state SET state_json=?,revision=revision+1 WHERE id=1',
                     (json.dumps(source, allow_nan=False),))
        conn.execute('UPDATE solana_real_run SET state_json=?,revision=revision+1 WHERE id=1',
                     (json.dumps(runtime, allow_nan=False),))
        conn.commit()
    return settings()
