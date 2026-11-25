"""Owner's reversible Jarvis-only notification mode and one-time legacy pause."""
from contextlib import closing
import json
import os
import time

import database


def enabled():
    return os.getenv('JARVIS_FOCUS_MODE', '').strip().lower() in ('1','true','yes','on')


def email_allowed(category):
    return not enabled() or category in ('jarvis_hourly', 'jarvis_health', 'jarvis_backlog')


def apply_startup_pause():
    if not enabled():
        return
    # Runs before any trading worker starts. Preserve holdings, pending orders,
    # accounting and exit settings; never issue a buy or a sell from this migration.
    with closing(database.get_connection()) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS owner_focus_actions(name TEXT PRIMARY KEY,applied REAL NOT NULL)')
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute("SELECT 1 FROM owner_focus_actions WHERE name='jarvis-only-v1'").fetchone():
            conn.commit()
            return
        for table,live in (('solana_meme_paper_state',False),('solana_copy_state',False),('solana_real_run',True)):
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():
                continue
            row=conn.execute('SELECT state_json FROM '+table+' WHERE id=1').fetchone()
            if not row:
                continue
            state=json.loads(row[0])
            if live:
                state['enabled']=False
            else:
                state['config']['enabled']=False
                state['settings_revision']=state.get('settings_revision',0)+1
                state['health']='paused_entries_monitoring_exits'
            conn.execute('UPDATE '+table+' SET state_json=?,revision=revision+1 WHERE id=1',
                         (json.dumps(state,allow_nan=False),))
        conn.execute("INSERT INTO owner_focus_actions VALUES ('jarvis-only-v1',?)",(time.time(),))
        conn.commit()
    print('Jarvis focus applied: original meme, paper copy and real copy entries paused; existing exits remain monitored.',flush=True)
