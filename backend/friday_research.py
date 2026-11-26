"""Friday's on-demand stock research ledger. No broker or provider client."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import time

import database
from fast_mover_discovery import MAX_AGE, finite, timestamp

COHORT = frozenset(("AAPL", "AMD", "MSFT", "NVDA"))
REVIEW_CAP = 2
SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    finished REAL PRIMARY KEY, reviewed REAL NOT NULL, universe INTEGER NOT NULL,
    screened INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    finished REAL NOT NULL REFERENCES scans(finished) ON DELETE CASCADE,
    symbol TEXT NOT NULL, reason TEXT NOT NULL, change REAL, spread REAL, evidence TEXT NOT NULL,
    PRIMARY KEY (finished, symbol)
);
"""


def ledger_path():
    return Path(database.DATABASE_PATH).with_suffix('.friday.sqlite3')


def classify(symbol, row, now):
    if not row:
        return 'missing_snapshot'
    stamps = [timestamp(row.get(key)) for key in ('snapshot_quote_at', 'snapshot_trade_at')]
    if any(t is None or not 0 <= now - t.timestamp() <= MAX_AGE for t in stamps):
        return 'stale_or_invalid_time'
    change, spread, dollars = [finite(row.get(k)) for k in
                               ('percent_change', 'spread_percent', 'session_dollar_volume')]
    if (None in (change, spread, dollars) or row.get('feed') != 'iex'
            or type(row.get('above_session_vwap')) is not bool):
        return 'missing_evidence'
    if change > 15:
        return 'overextended'
    if change < 3 or not row['above_session_vwap']:
        return 'momentum_rule_skip'
    if symbol not in COHORT:
        return 'outside_execution_cohort'
    if not 0 <= spread <= 0.15 or dollars < 5_000_000:
        return 'liquidity_rule_skip'
    return 'review_candidate'


def capture(scan, *, path=None, clock=time.time):
    if scan is None:
        return 'No completed stock scan in this process.'
    finished, universe, symbols, metrics = scan
    now = clock()
    records = []
    for symbol in symbols:
        row = metrics.get(symbol, {})
        records.append([finished, symbol, classify(symbol, row, now),
                        finite(row.get('percent_change')), finite(row.get('spread_percent')), json.dumps(row, sort_keys=True)])
    candidates = sorted((r for r in records if r[2] == 'review_candidate'),
                        key=lambda r: (-r[3], r[4], r[1]))
    for r in candidates[REVIEW_CAP:]:
        r[2] = 'review_cap'
    with closing(sqlite3.connect(path or ledger_path(), timeout=2)) as db, db:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(SCHEMA)
        insert = db.execute('INSERT OR IGNORE INTO scans VALUES(?,?,?,?)',
                            (finished, now, universe, len(symbols)))
        if not insert.rowcount:
            return 'This scan was already recorded.'
        db.executemany('INSERT INTO observations VALUES(?,?,?,?,?,?)', records)
        db.execute('DELETE FROM scans WHERE finished IN (SELECT finished FROM scans ORDER BY finished DESC LIMIT -1 OFFSET 256)')
    return 'Stock scan recorded.'


def latest(path=None):
    target = Path(path or ledger_path())
    if not target.exists():
        return None
    with closing(sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='scans'").fetchone():
            return None
        scan = db.execute('SELECT * FROM scans ORDER BY finished DESC LIMIT 1').fetchone()
        if not scan:
            return None
        reasons = db.execute('SELECT reason, count(*) AS count FROM observations WHERE finished=? GROUP BY reason ORDER BY count DESC, reason', (scan['finished'],))
        candidates = db.execute("SELECT symbol, change, spread FROM observations WHERE finished=? AND reason='review_candidate' ORDER BY change DESC, spread, symbol", (scan['finished'],))
        history = db.execute('SELECT finished, reviewed, screened, universe FROM scans ORDER BY finished DESC LIMIT 20')
        return {**dict(scan), 'history': [dict(r) for r in history], 'reasons': [dict(r) for r in reasons],
                'candidates': [dict(r) for r in candidates]}


def record_authorized_alert(*, event_id, symbol, published_at, received_at,
                            scan, path=None, clock=time.time):
    """Internal adapter for a permission-verified reader. Never accepts alert prose."""
    import re
    if not isinstance(event_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', event_id):
        raise ValueError('Invalid source event identifier')
    if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,9}', symbol):
        raise ValueError('Invalid symbol')
    published, received = timestamp(published_at), timestamp(received_at)
    now = clock()
    if not published or not received or not published.timestamp() <= received.timestamp() <= now:
        raise ValueError('Verified publication and receipt times required')
    reason = 'scanner_coverage'
    scan_at = None
    if now - published.timestamp() > MAX_AGE:
        reason = 'late_observation'
    elif scan and scan[0] <= now and symbol in scan[2]:
        scan_at = scan[0]
        reason = classify(symbol, scan[3].get(symbol, {}), now)
    with closing(sqlite3.connect(path or ledger_path(), timeout=2)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS alerts (
            event_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, published TEXT NOT NULL,
            received TEXT NOT NULL, reviewed REAL NOT NULL, scan_at REAL, reason TEXT NOT NULL)""")
        db.execute('INSERT OR IGNORE INTO alerts VALUES (?,?,?,?,?,?,?)',
                   (event_id, symbol, published_at, received_at, now, scan_at, reason))
        saved = db.execute('SELECT symbol,published,reason FROM alerts WHERE event_id=?',
                           (event_id,)).fetchone()
        if saved[:2] != (symbol, published_at):
            raise ValueError('Conflicting event identifier')
        return saved[2]
