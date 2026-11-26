"""Prospective matched paper observations from the existing worker and chart cache."""
import json
import math

import jarvis_research as research

SCHEMA = """
CREATE TABLE IF NOT EXISTS fallback_scores(
 id INTEGER PRIMARY KEY, started REAL NOT NULL, mint TEXT NOT NULL, pair TEXT NOT NULL,
 episode REAL NOT NULL, baseline INTEGER NOT NULL, reserved INTEGER NOT NULL DEFAULT 0,
 applied INTEGER NOT NULL DEFAULT 0, finished REAL);
CREATE INDEX IF NOT EXISTS fallback_score_time ON fallback_scores(started);
CREATE TABLE IF NOT EXISTS fallback_checks(
 score INTEGER NOT NULL, phase INTEGER NOT NULL, at REAL NOT NULL, readiness INTEGER,
 PRIMARY KEY(score,phase));
"""
# The last two windows lie beyond the original chart's freshness lifetime.
WINDOWS = (60, 240, 360)
FIELDS = {'candles': 1, 'rsi': 2, 'volume_ratio': 4, 'complete': 7}


def readiness(conn, row, now):
    found = conn.execute('SELECT evidence FROM research WHERE mint=? AND pair=?',
                         (row['mint'],row['pair'])).fetchone()
    chart = (json.loads(found[0]).get('chart') or {}) if found else {}
    if not research.chart_is_fresh(chart, now):
        return 0
    mask = FIELDS['candles']
    for field in ('rsi', 'volume_ratio'):
        value = chart['value'].get(field)
        if type(value) in (int, float) and math.isfinite(value):
            mask |= FIELDS[field]
    return mask


def start(conn, row, now):
    return conn.execute('INSERT INTO fallback_scores(started,mint,pair,episode,baseline) VALUES (?,?,?,?,?)',
                        (now,row['mint'],row['pair'],row['first'],readiness(conn,row,now))).lastrowid


def sample(conn, state, score, now, phase, window):
    row = state['watch'].get(score['mint'])
    comparable = (state['enabled'] and row and row['pair'] == score['pair']
                  and row['first'] == score['episode'] and not row.get('monitor_only')
                  and 0 <= now-row['last'] <= 90 and now >= row.get('retry',0)
                  and any(rule['created'] <= row['first'] for rule in state.get('local_rules',{}).values())
                  and 0 <= now-row['first'] <= window)
    mask = readiness(conn,row,now) if comparable else None
    conn.execute('INSERT OR IGNORE INTO fallback_checks VALUES (?,?,?,?)',(score['id'],phase,now,mask))


def finish(conn, state, ident, now, applied, window):
    if ident is None:
        return
    conn.execute('UPDATE fallback_scores SET finished=?,applied=? WHERE id=?',(now,int(applied),ident))
    score = conn.execute('SELECT * FROM fallback_scores WHERE id=?',(ident,)).fetchone()
    sample(conn,state,score,now,0,window)


def observe(conn, state, now, window):
    # An overdue/missing checkpoint remains unknown. Never backfill from a later cache.
    for score in conn.execute('SELECT * FROM fallback_scores WHERE finished BETWEEN ? AND ?',
                              (now-max(WINDOWS)-60,now-min(WINDOWS))):
        for phase in WINDOWS:
            if phase <= now-score['finished'] < phase+60:
                sample(conn,state,score,now,phase,window)
    cutoff = now-30*86400
    conn.execute('DELETE FROM fallback_checks WHERE score IN (SELECT id FROM fallback_scores WHERE started<?)',(cutoff,))
    conn.execute('DELETE FROM fallback_scores WHERE started<?',(cutoff,))


def status(conn, now):
    rows = conn.execute('SELECT * FROM fallback_scores WHERE started BETWEEN ? AND ? ORDER BY started DESC,id DESC',
                        (now-86400,now)).fetchall()
    checks = {}
    for check in conn.execute('SELECT c.* FROM fallback_checks c JOIN fallback_scores s ON s.id=c.score WHERE s.started BETWEEN ? AND ?',
                              (now-86400,now)):
        checks.setdefault(check['score'],{})[check['phase']] = check['readiness']
    # One latest attempt per exact coin and pool. Costs include all attempts, including repeats.
    seen = set()
    stages = [0,*WINDOWS]
    counts = {field:[0]*(1+len(stages)) for field in FIELDS}
    immediate = {field:[0,0] for field in FIELDS}
    matched = post = durable = lost = applied = 0
    for row in rows:
        key = row['mint'],row['pair']
        if key in seen:
            continue
        seen.add(key)
        samples = checks.get(row['id'],{})
        if samples.get(0) is None:
            continue
        post += 1
        applied += row['applied']
        for field,mask in FIELDS.items():
            immediate[field][0] += (row['baseline'] & mask) == mask
            immediate[field][1] += (samples[0] & mask) == mask
        gained = row['applied'] and row['baseline'] != 7 and samples[0] == 7
        lost += int(bool(gained and any(samples.get(stage) not in (None, 7) for stage in WINDOWS)))
        if any(samples.get(stage) is None for stage in stages):
            continue
        matched += 1
        series = [row['baseline'],*(samples[stage] for stage in stages)]
        for field,mask in FIELDS.items():
            for i,value in enumerate(series):
                counts[field][i] += (value & mask) == mask
        durable += int(gained and all(value == 7 for value in series[2:]))
    verdict = 'retry_measurement'
    if lost or (matched >= 3 and durable == 0):
        verdict = 'change_approach'
    elif durable >= 3 and durable == len(seen):
        verdict = 'sustained_in_measured_window'
    review = conn.execute("SELECT created FROM brain_mail WHERE id LIKE '%/provider-review' ORDER BY created DESC LIMIT 1").fetchone()
    return dict(provider_review_at=review[0] if review else None,attempts=len(rows),pools=len(seen),post_matched=post,applied=applied,matched=matched,
                immediate=immediate,series=counts,durable=durable,recurrences=lost,verdict=verdict,
                reserved_cu=sum(row['reserved'] for row in rows),billed_cu=None)
