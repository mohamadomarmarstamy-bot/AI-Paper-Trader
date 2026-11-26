"""Bounded first-observation replay. All price evidence comes from completed scanner batches."""
import json
import sqlite3

from day_trader_strategy import stamp
from friday_research import COHORT

VERSION = 'phase-momentum-v1/replay-1'
SCHEMA = """
CREATE TABLE IF NOT EXISTS friday_replays(
 phase_start REAL, phase TEXT, bucket INTEGER, symbol TEXT, decision_at REAL,
 scanned_at REAL, reason TEXT, evidence TEXT, ask REAL, outcome TEXT, net REAL, exit_quote_at REAL,
 PRIMARY KEY(phase_start,bucket,symbol));
CREATE INDEX IF NOT EXISTS friday_replay_pending ON friday_replays(outcome,decision_at);
"""


def verified_alert(db, symbol, now):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='alerts'").fetchone():
        return None
    row = db.execute("""SELECT published,received,reviewed FROM
        (SELECT * FROM alerts ORDER BY rowid DESC LIMIT 32)
        WHERE symbol=? AND reviewed BETWEEN ? AND ? ORDER BY reviewed DESC LIMIT 1""",
        (symbol, now-90, now)).fetchone()
    if row:
        try:
            if stamp(row[0]) <= stamp(row[1]) <= row[2] <= now:
                return dict(published=row[0],first_seen=row[1],reviewed=row[2])
        except (ValueError,TypeError,OverflowError):
            pass
    return None


def record(db, phase, start, end, now, batch, quotes, details, cycle_reason):
    db.execute("UPDATE friday_replays SET outcome='unmeasurable_path' WHERE outcome='pending' AND decision_at < ?", (now-75,))
    db.execute('DELETE FROM friday_replays WHERE decision_at < ?', (now-7*86400,))
    if start is None or not batch or phase not in ('pre_market','after_hours','regular_session'):
        return
    scanned, snapshots = batch
    if not start <= scanned <= now < end:
        return
    for symbol, (q,qt) in quotes.items():
        for row in db.execute("""SELECT bucket,decision_at,reason,ask FROM friday_replays
                WHERE phase_start=? AND symbol=? AND outcome='pending' AND decision_at BETWEEN ? AND ?""",
                (start,symbol,now-75,qt-60)).fetchall():
            bucket,decision,reason,ask = row
            if not decision+60 <= qt <= decision+75 or scanned <= decision:
                continue
            net=q['bid']*.999-ask*1.001-.01
            outcome = ('missed_qualified_move' if net>0 else 'qualified_skip_no_positive_move') if reason=='phase_daily_cap' else 'measured'
            db.execute('UPDATE friday_replays SET outcome=?,net=?,exit_quote_at=? WHERE phase_start=? AND bucket=? AND symbol=?',
                       (outcome,net,qt,start,bucket,symbol))
    bucket=int(scanned//60)
    if db.execute('SELECT 1 FROM friday_replays WHERE phase_start=? AND bucket=? LIMIT 1',(start,bucket)).fetchone():
        return
    # Include the fixed cohort even when no symbol moved or a symbol was absent.
    for symbol in sorted(COHORT):
        reason,chart=details.get(symbol,(cycle_reason,None))
        raw=snapshots.get(symbol)
        raw=raw if isinstance(raw,dict) else {}
        quote=raw.get('latestQuote'); minute=raw.get('minuteBar'); trade=raw.get('latestTrade')
        def reported_at(field):
            value=field.get('t') if isinstance(field,dict) else None
            return value[:64] if isinstance(value,str) else None
        usable=symbol in quotes
        original_reason=reason
        if now-scanned>15:
            reason='late_scanner_batch'
        elif symbol not in snapshots:
            reason='scanner_coverage_gap'
        elif not usable:
            reason='missing_or_stale_phase_evidence'
        evidence=dict(version='friday-breakout-v1/replay-1' if phase=='regular_session' else VERSION,decision_gate=original_reason,quote_at=reported_at(quote),candle_at=reported_at(minute),
                      trade_at=reported_at(trade),chart=chart,phase_end=end,feed='iex',
                      alert=verified_alert(db,symbol,now))
        if usable:
            evidence['decision_quote']=quotes[symbol][0]
        ask=quotes[symbol][0]['ask'] if usable else None
        outcome='pending' if ask and now+75<end else 'unmeasurable_path'
        db.execute('INSERT INTO friday_replays VALUES(?,?,?,?,?,?,?,?,?,?,NULL,NULL)',
                   (start,phase,bucket,symbol,now,scanned,reason,json.dumps(evidence),ask,outcome))
    db.execute('DELETE FROM friday_replays WHERE rowid IN (SELECT rowid FROM friday_replays ORDER BY decision_at DESC,rowid DESC LIMIT -1 OFFSET 2048)')


def summary(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='friday_replays'").fetchone():
        return []
    rows=db.execute("""SELECT symbol,phase,decision_at,scanned_at,reason,outcome,net,exit_quote_at,evidence
        FROM friday_replays ORDER BY decision_at DESC,symbol LIMIT 24""")
    return [{**dict(r),'evidence':json.loads(r['evidence'])} for r in rows]
