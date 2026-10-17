"""Bounded operator-triggered public research discovery. No broker or email access."""
from contextlib import closing
from datetime import datetime, timezone
import json
import re
import time
import uuid
import database

_SCHEMA = '''CREATE TABLE IF NOT EXISTS pro_ticker_workspace_discovery_runs (
 id TEXT PRIMARY KEY, status TEXT NOT NULL,
 started_at TEXT NOT NULL, finished_at TEXT, lease_until REAL NOT NULL,
 result_json TEXT NOT NULL DEFAULT '{}');
CREATE UNIQUE INDEX IF NOT EXISTS pro_ticker_workspace_one_running_discovery
 ON pro_ticker_workspace_discovery_runs(status) WHERE status='running';'''


def _stamp(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat()


def _public(row, now):
    if row is None:
        return {'paper': True, 'status': 'not_run', 'automatic_discovery_enabled': False,
                'order_execution_enabled': False, 'run': None}
    status = row['status']
    # Observation never mutates a run; expired work is marked interrupted at retry.
    if status == 'running' and row['lease_until'] <= now:
        status = 'interrupted'
    return {'paper': True, 'status': status, 'automatic_discovery_enabled': False,
            'order_execution_enabled': False, 'run': {
                'id': row['id'], 'started_at': row['started_at'], 'finished_at': row['finished_at'],
                **json.loads(row['result_json'])}}


def discovery_status():
    with closing(database.get_connection()) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_workspace_discovery_runs'").fetchone()
        row = conn.execute('SELECT * FROM pro_ticker_workspace_discovery_runs ORDER BY started_at DESC,id DESC LIMIT 1').fetchone() if exists else None
        return _public(row, time.time())


def run_discovery(*, discover=None, collect=None, clock=time.time):
    """One page, at most five new articles, no automatic background scheduler.

    A committed lease prevents overlapping work across processes. A worker crash
    leaves visible interrupted evidence; a later explicit retry gets a new run.
    Successfully stored articles survive an interruption and are skipped on retry.
    """
    now = clock(); run_id = str(uuid.uuid4())
    with closing(database.get_connection()) as conn:
        conn.executescript(_SCHEMA)
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("UPDATE pro_ticker_workspace_discovery_runs SET status='interrupted',finished_at=? WHERE status='running' AND lease_until<=?", (_stamp(now),now))
        active = conn.execute("SELECT * FROM pro_ticker_workspace_discovery_runs WHERE status='running'").fetchone()
        if active is not None:
            conn.commit()
            return {**_public(active,now), 'started': False}
        conn.execute("INSERT INTO pro_ticker_workspace_discovery_runs(id,status,started_at,lease_until) VALUES (?,'running',?,?)", (run_id,_stamp(now),now+600))
        conn.commit()
    result = {'discovered': 0, 'saved': 0, 'skipped': 0, 'failed': 0, 'repeated_symbols': 0, 'candidates': [],
              'source': 'public_pro_ticker_articles', 'market_data_verified': False,
              'reason': 'Public article discovery only; no current market evaluation, email or orders.'}
    status = 'failed'
    try:
        if discover is None or collect is None:
            from pro_ticker_collector import discover_pro_ticker_articles, collect_pro_ticker_article
            discover = discover or discover_pro_ticker_articles
            collect = collect or collect_pro_ticker_article
        # The legacy discovery function inspects article bodies before returning
        # links. Bound that work too, not only the later collection loop.
        items = discover(start_page=1,max_pages=1,max_articles=5)
        if not isinstance(items,list):
            raise ValueError('Invalid discovery results.')
        result['discovered'] = len(items)
        with closing(database.get_connection()) as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_research'").fetchone()
            urls = [item.get('article_url') for item in items[:20] if isinstance(item,dict) and isinstance(item.get('article_url'),str)]
            placeholders = ','.join('?' for _ in urls)
            known = {row[0] for row in conn.execute(f'SELECT article_url FROM pro_ticker_research WHERE article_url IN ({placeholders})',urls)} if exists and urls else set()
        seen_urls = set(); seen_symbols = set(); attempted = 0
        for item in items[:20]:
            if attempted >= 5 or clock() - now >= 120:
                result['reason'] = 'Bounded discovery window reached; run again explicitly for more research.'
                break
            url = item.get('article_url') if isinstance(item,dict) else None
            if not isinstance(url,str) or len(url)>2048 or url in known or url in seen_urls:
                result['skipped'] += 1
                continue
            seen_urls.add(url); attempted += 1
            try:
                record = collect(url)
                symbol = record.get('symbol') if isinstance(record,dict) else None
                if not isinstance(symbol,str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}',symbol):
                    raise ValueError('Missing research symbol.')
                result['saved'] += 1
                if symbol in seen_symbols:
                    result['repeated_symbols'] += 1
                    continue
                seen_symbols.add(symbol)
                result['candidates'].append({'symbol':symbol,'reason':'new_public_research_saved'})
            except Exception:
                result['failed'] += 1
        status = 'partial' if result['failed'] and result['saved'] else 'failed' if result['failed'] else 'completed' if items else 'no_results'
        if not items:
            result['reason'] = 'No articles returned. The source may be empty or unavailable; this does not prove scanner health.'
    except Exception:
        result['reason'] = 'Public research discovery unavailable. No broker requests or orders were made.'
    finished = clock()
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        # A worker cannot overwrite a lease already expired/replaced by a retry.
        changed = conn.execute("UPDATE pro_ticker_workspace_discovery_runs SET status=?,finished_at=?,result_json=? WHERE id=? AND status='running' AND lease_until>?",
            (status,_stamp(finished),json.dumps(result),run_id,finished)).rowcount
        conn.commit()
        row = conn.execute('SELECT * FROM pro_ticker_workspace_discovery_runs WHERE id=?',(run_id,)).fetchone()
    return {**_public(row,finished), 'started': True, 'result_recorded': changed == 1}
