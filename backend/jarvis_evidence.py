"""Observed diagnosis and prospective signal scouting, never simulated profit."""
import ast
import json
import math
import time

import jarvis_local_lab as local
import jarvis_codex_feed as codex

SCHEMA='''CREATE TABLE IF NOT EXISTS brain_incident_evidence(
 incident TEXT PRIMARY KEY,first_at REAL NOT NULL,last_at REAL NOT NULL,
 initial TEXT NOT NULL,latest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS brain_scout_samples(
 proposal TEXT NOT NULL,mint TEXT NOT NULL,pair TEXT NOT NULL,at REAL NOT NULL,
 complete REAL NOT NULL,matched REAL NOT NULL,comparable REAL NOT NULL,covered REAL NOT NULL,
 PRIMARY KEY(proposal,mint,pair));
CREATE INDEX IF NOT EXISTS brain_scout_time ON brain_scout_samples(at);'''


def diagnose(snapshot):
    coverage=snapshot.get('chart_coverage',{})
    reasons=coverage.get('reasons',{})
    provider=coverage.get('provider',{})
    facts=[]
    if snapshot.get('queue',{}).get('oldest_wait',0)>=120:facts.append('exit_checks_overdue')
    age=snapshot.get('cycle_age')
    if 'cycle_age' in snapshot and (age is None or age>=180):facts.append('worker_heartbeat_delayed')
    if reasons.get('rate_limited',0):facts.append('primary_rate_limit_observed')
    lag=provider.get('median_returned_lag_seconds')
    if reasons.get('stale_candles',0) or (type(lag) in (int,float) and math.isfinite(lag) and lag>180):
        facts.append('provider_returned_stale_candles')
    if reasons.get('zero_volume_baseline',0):facts.append('volume_ratio_undefined_zero_baseline')
    if reasons.get('pool_warming',0):facts.append('some_pools_lack_completed_history')
    allowance=snapshot.get('fallback_allowance',{})
    if allowance.get('remaining_daily_cu',16)<16 or allowance.get('remaining_monthly_cu',16)<16:
        facts.append('fallback_allowance_exhausted')
    if coverage.get('eligible',0)>0 and reasons.get('complete',0)==0:facts.append('no_complete_candidate_indicators')
    return {'observed_facts':facts,'interpretation':'Observed constraints, not a proven root cause. Missing/stale data is not evidence that a strategy is unprofitable.'}


def record(conn,incident,now,snapshot):
    data=json.dumps({'metrics':snapshot,'diagnosis':diagnose(snapshot)})
    conn.execute('INSERT INTO brain_incident_evidence VALUES (?,?,?,?,?) ON CONFLICT(incident) DO UPDATE SET last_at=excluded.last_at,latest=excluded.latest',
        (incident,now,now,data,data))


def incident(conn,ident):
    row=conn.execute('SELECT * FROM brain_incident_evidence WHERE incident=?',(ident,)).fetchone()
    if not row:return None
    return {'first_at':row['first_at'],'last_at':row['last_at'],'before':json.loads(row['initial']),'latest':json.loads(row['latest'])}


def fields(rule):
    return sorted({n.id for n in ast.walk(local.parse(rule['entry'])) if isinstance(n,ast.Name)})


def scout(api,now):
    with api.connection() as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        state,_=api.read(conn)
        if not state['enabled']:return
        due=conn.execute("SELECT due FROM research_tasks WHERE name='brain-scout'").fetchone()
        if due and due['due']>now:return
        conn.execute("INSERT OR REPLACE INTO research_tasks VALUES ('brain-scout',?)",(now+60,))
        proposals=conn.execute("SELECT id,created,data FROM brain_proposals WHERE kind='strategy' AND status='waiting_slot' ORDER BY created LIMIT 3").fetchall()
        rows=[r for r in state['watch'].values() if not r.get('monitor_only') and 0<=now-r['last']<=90
              and 0<=now-r['first']<=900 and now>=r.get('retry',0)]
        active=list(state.get('local_rules',{}).values())
        for row in rows:
            values=local.observations(row,api.entry_evidence(conn,row,now))
            known=[codex.possible(r['entry'],values) for r in active]
            covered=any(v is True for v in known)
            comparable=covered or bool(known and all(v is False for v in known))
            for proposal in proposals:
                # Prospective only: do not score already-known discovery episodes.
                if row['first']<proposal['created']:continue
                rule=json.loads(proposal['data'])['rule']
                complete=all(type(values.get(f)) in (int,float) and math.isfinite(values[f]) for f in fields(rule))
                matched=bool(complete and local.run(rule['entry'],values))
                conn.execute('''INSERT INTO brain_scout_samples VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(proposal,mint,pair) DO UPDATE SET at=excluded.at,
                    complete=max(complete,excluded.complete),matched=max(matched,excluded.matched),
                    comparable=max(comparable,excluded.comparable),covered=max(covered,excluded.covered)''',
                    (proposal['id'],row['mint'],row['pair'],now,now if complete else 0,now if matched else 0,now if matched and comparable else 0,now if matched and covered else 0))
        conn.execute('DELETE FROM brain_scout_samples WHERE at<?',(now-86400,))


def scouting(conn,now=None):
    now=time.time() if now is None else now
    cutoff=now-86400
    result=[]
    for row in conn.execute("SELECT id,created,data,status FROM brain_proposals WHERE kind='strategy' AND status='waiting_slot' ORDER BY created LIMIT 3"):
        sample=dict(conn.execute('''SELECT count(*) AS distinct_coin_pools,COALESCE(sum(complete>0 AND complete>=?),0) AS complete,
            COALESCE(sum(matched>0 AND matched>=?),0) AS matched,COALESCE(sum(comparable>0 AND comparable>=?),0) AS comparable_matches,
            COALESCE(sum(covered>0 AND covered>=?),0) AS matches_covered_by_active FROM brain_scout_samples WHERE proposal=? AND at>=?''',(cutoff,cutoff,cutoff,cutoff,row['id'],cutoff)).fetchone())
        rule=json.loads(row['data'])['rule']
        ratio=sample['matches_covered_by_active']/sample['comparable_matches'] if sample['comparable_matches'] else None
        result.append({'id':row['id'],'name':rule['name'],'required_fields':fields(rule),**sample,
            'measured_signal_overlap':ratio,'data_ready':sample['complete']>=10 and sample['matched']>=2,
            'scope':'Distinct coin/pools ever observed complete or matching during the last 24h, not current signals, entries, fills, or profit. Unknown active indicators do not establish novelty.'})
    return result


def choose(conn,rows,now=None):
    scores={r['id']:r for r in scouting(conn,now)}
    ready=[row for row in rows if scores.get(row['id'],{}).get('data_ready')]
    if not ready:return rows[0] if rows else None
    # Feasibility is a gate; maximize neither trade frequency nor proxy profits.
    return min(ready,key=lambda row:row['created'])


def scouting_status(api,now):
    with api.connection() as conn:return scouting(conn,now)
