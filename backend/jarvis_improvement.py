"""Persistent paper improvement goals and independent regression follow-ups."""
import json

SCHEMA='''CREATE TABLE IF NOT EXISTS improvement_tasks(
 kind TEXT PRIMARY KEY,priority INTEGER NOT NULL,status TEXT NOT NULL,opened REAL NOT NULL,
 updated REAL NOT NULL,healthy_since REAL,last_seen REAL NOT NULL,goal TEXT NOT NULL,
 evidence TEXT NOT NULL,incident TEXT,recurrences INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS improvement_events(id INTEGER PRIMARY KEY,at REAL NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS improvement_rechecks(run TEXT PRIMARY KEY,checked REAL NOT NULL,status TEXT NOT NULL,evidence TEXT NOT NULL);
'''
GOALS={
 'exit_backlog':(100,'Keep overdue regular exit checks below 120 seconds while preserving existing exit rules.'),
 'worker_stalled':(95,'Restore a paper heartbeat younger than 45 seconds without weakening safety gates.'),
 'engine_error':(90,'Clear the observed engine error and retain a current heartbeat and monitored exits.'),
 'chart_data_gap':(80,'Increase fresh complete same-pool indicators; never label missing or stale data valid.'),
 'data_coverage':(70,'Measure and reduce persistent missing-indicator coverage using existing provider allowances.'),
}


def transition(conn,kind,status,now):
    row=conn.execute('SELECT status FROM improvement_tasks WHERE kind=?',(kind,)).fetchone()
    if row and row['status']==status:return
    conn.execute('UPDATE improvement_tasks SET status=?,updated=? WHERE kind=?',(status,now,kind))
    conn.execute('INSERT INTO improvement_events(at,kind,status) VALUES (?,?,?)',(now,kind,status))


def observe(conn,problems,snapshot,now):
    observed=set(problems)
    coverage=snapshot.get('chart_coverage',{});eligible=coverage.get('eligible',0)
    if eligible>=10 and coverage.get('reasons',{}).get('complete',0)/eligible<.2:observed.add('data_coverage')
    for kind,(priority,goal) in GOALS.items():
        row=conn.execute('SELECT * FROM improvement_tasks WHERE kind=?',(kind,)).fetchone()
        if kind in observed:
            evidence={'cycle_age':snapshot.get('cycle_age'),'oldest_quote_wait':snapshot.get('queue',{}).get('oldest_wait'),
                'eligible':eligible,'complete':coverage.get('reasons',{}).get('complete'),
                'observed_facts':snapshot.get('diagnosis',{}).get('observed_facts',[])}
            if row is None:
                conn.execute('INSERT INTO improvement_tasks VALUES (?,?,?,?,?,NULL,?,?,?,NULL,0)',
                    (kind,priority,'queued',now,now,now,goal,json.dumps({'baseline':evidence,'latest':evidence})))
                conn.execute("INSERT INTO improvement_events(at,kind,status) VALUES (?,?,'queued')",(now,kind))
            else:
                recurrent=row['status']=='observed_recovery'
                if recurrent:
                    transition(conn,kind,'queued',now)
                    conn.execute('UPDATE improvement_tasks SET recurrences=recurrences+1,opened=? WHERE kind=?',(now,kind))
                conn.execute('UPDATE improvement_tasks SET evidence=?,last_seen=?,updated=?,healthy_since=NULL WHERE kind=?',
                             (json.dumps({'baseline':evidence if recurrent else json.loads(row['evidence']).get('baseline',evidence),'latest':evidence}),now,now,kind))
            incident=conn.execute('SELECT id FROM brain_incidents WHERE kind=? AND resolved IS NULL',(kind,)).fetchone()
            if incident:conn.execute('UPDATE improvement_tasks SET incident=? WHERE kind=?',(incident['id'],kind))
        elif row and row['status']!='observed_recovery':
            # Missing diagnostics or an intentional pause do not establish recovery.
            if not snapshot.get('paper_enabled') or snapshot.get('cycle_age') is None or snapshot['cycle_age']>45:continue
            since=row['healthy_since'] if row['healthy_since'] is not None and now-row['updated']<=90 else now
            conn.execute('UPDATE improvement_tasks SET healthy_since=?,updated=? WHERE kind=?',(since,now,kind))
            if now-since>=120:transition(conn,kind,'observed_recovery',now)


def selected(conn):
    return next((r for r in status(conn)['tasks'] if r['status'] in ('queued','investigating','measuring')),None)


def status(conn):
    return {'scope':'Paper-only goals. Recovery is observed, not proof a code change caused it.',
        'tasks':[dict(r)|{'evidence':json.loads(r['evidence'])} for r in conn.execute('SELECT * FROM improvement_tasks ORDER BY CASE WHEN status=\'observed_recovery\' THEN 1 ELSE 0 END,priority DESC,opened')],
        'rechecks':[dict(r)|{'evidence':json.loads(r['evidence'])} for r in conn.execute('SELECT * FROM improvement_rechecks ORDER BY checked DESC LIMIT 5')]}


def recheck(api,now):
    import jarvis_repair as repair
    import jarvis_repair_policy as policy
    with api.connection() as conn:
        due=conn.execute("SELECT due FROM research_tasks WHERE name='improvement-recheck'").fetchone()
        if due and due['due']>now:return
    actual=repair.health(api,now)
    with api.connection() as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        due=conn.execute("SELECT due FROM research_tasks WHERE name='improvement-recheck'").fetchone()
        if due and due['due']>now:return
        conn.execute("INSERT OR REPLACE INTO research_tasks VALUES ('improvement-recheck',?)",(now+3600,))
        for row in conn.execute("SELECT id,report FROM repair_runs WHERE status='deployed' ORDER BY started DESC LIMIT 10").fetchall():
            report=json.loads(row['report'])
            if not actual['paper_enabled']:outcome='owner_paused_unverified'
            elif policy.goal_met(report.get('incident_kind'),report.get('baseline',{}),actual):outcome='recovery_still_observed'
            else:outcome='metric_recurrence'
            evidence={'original_kind':report.get('incident_kind'),'same_release':actual['source_sha']==report.get('commit'),
                      'cycle_age':actual['cycle_age'],'oldest_quote_wait':actual['oldest_quote_wait'],
                      'complete_candidates':actual['complete_candidates'],'engine_error_present':actual['engine_error_present']}
            conn.execute('INSERT OR REPLACE INTO improvement_rechecks VALUES (?,?,?,?)',(row['id'],now,outcome,json.dumps(evidence)))
