"""One bounded paper challenger at a time; no closes, fresh funding or lost history."""
import json

STALL_SECONDS=2*3600
WINDOW_SECONDS=6*3600
SCHEMA='''CREATE TABLE IF NOT EXISTS challenger_rotations(
 id TEXT PRIMARY KEY,slot TEXT NOT NULL,original TEXT NOT NULL,challenger TEXT NOT NULL,
 started REAL NOT NULL,ends REAL NOT NULL,status TEXT NOT NULL,finished REAL);
CREATE UNIQUE INDEX IF NOT EXISTS challenger_one_active ON challenger_rotations((1)) WHERE status IN ('active','draining');'''


def select(conn,state,now,minimum_cash=50_250000):
    from jarvis_local_lab import SLOTS
    if conn.execute("SELECT 1 FROM challenger_rotations WHERE status IN ('active','draining')").fetchone():return None
    if conn.execute('SELECT count(*) FROM challenger_rotations WHERE started>?',(now-86400,)).fetchone()[0]>=2:return None
    protected=(state.get('strategy') or {}).get('local_rule',{}).get('id')
    candidates=[]
    for slot,rule in state.get('local_rules',{}).items():
        if slot not in SLOTS or rule['id']==protected or state['bots'][slot]['positions'] or state['bots'][slot]['cash']<minimum_cash:continue
        candidate=conn.execute("SELECT * FROM local_strategies WHERE id=? AND status='testing'",(rule['id'],)).fetchone()
        if not candidate:continue
        last=conn.execute("SELECT max(closed) FROM trades WHERE bot=? AND json_extract(data,'$.policy.local_rule.id')=?",(slot,rule['id'])).fetchone()[0]
        anchor=max(rule.get('resumed_at',rule['created']),last or 0)
        if now-anchor>=STALL_SECONDS:candidates.append((anchor,slot,rule))
    return min(candidates,key=lambda v:(v[0],v[1])) if candidates else None


def pause(conn,state,choice,challenger,now):
    from jarvis_local_lab import record_event
    _,slot,rule=choice
    conn.execute("UPDATE local_strategies SET status='paused' WHERE id=?",(rule['id'],))
    del state['local_rules'][slot]
    state['bots'][slot]['local_funded']=True
    conn.execute('INSERT INTO challenger_rotations VALUES (?,?,?,?,?,?,?,NULL)',
        (challenger,slot,rule['id'],challenger,now,now+WINDOW_SECONDS,'active'))
    record_event(conn,now,rule['id'],'paused',{'name':rule['name'],'reason':'Bounded challenger test','slot':slot})
    return slot


def advance(conn,state,now):
    from jarvis_local_lab import record_event
    if not state['enabled']:return
    row=conn.execute("SELECT * FROM challenger_rotations WHERE status IN ('active','draining')").fetchone()
    if row is None:return
    candidate=conn.execute('SELECT * FROM local_strategies WHERE id=?',(row['challenger'],)).fetchone()
    if candidate is None:return
    slot=row['slot'];current=state.get('local_rules',{}).get(slot)
    if current and current['id']!=row['challenger']:
        conn.execute("UPDATE challenger_rotations SET status='needs_review',finished=? WHERE id=?",(now,row['id']))
        return
    if candidate['status']=='proven':
        if current:
            current.pop('challenger_ends',None)
            current.pop('entry_paused',None)
        conn.execute("UPDATE challenger_rotations SET status='retained',finished=? WHERE id=?",(now,row['id']))
        return
    if now<row['ends'] and candidate['status'] not in ('failed','inactive'):return
    if current and current['id']==row['challenger']:
        current['entry_paused']=True
    if state['bots'][slot]['positions']:
        conn.execute("UPDATE challenger_rotations SET status='draining' WHERE id=?",(row['id'],))
        return
    original=conn.execute("SELECT * FROM local_strategies WHERE id=? AND status='paused'",(row['original'],)).fetchone()
    if original is None:return
    if candidate['status']=='testing':
        conn.execute("UPDATE local_strategies SET status='paused' WHERE id=?",(row['challenger'],))
        record_event(conn,now,row['challenger'],'paused',{'name':json.loads(candidate['rule'])['name'],'reason':'Challenger test window ended'})
    conn.execute("UPDATE local_strategies SET status='testing' WHERE id=?",(row['original'],))
    rule=json.loads(original['rule'])
    state.setdefault('local_rules',{})[slot]={'id':original['id'],'created':original['created'],'resumed_at':now,**rule}
    conn.execute("UPDATE challenger_rotations SET status='completed',finished=? WHERE id=?",(now,row['id']))
    record_event(conn,now,original['id'],'resumed',{'name':rule['name'],'slot':slot})


def status(conn):
    return {'stall_hours':STALL_SECONDS/3600,'window_hours':WINDOW_SECONDS/3600,
        'runs':[dict(r) for r in conn.execute('SELECT * FROM challenger_rotations ORDER BY started DESC LIMIT 5')]}
