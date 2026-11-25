"""100 distinct closed-coin checkpoints for current Jarvis/research activity.

Descriptive learning evidence only. Per-strategy promotion and risk gates remain
independent; pooled performance never earns promotion.
"""
import json
import time

INTERVAL=100
SCHEMA='''CREATE TABLE IF NOT EXISTS jarvis_learning_coins(mint TEXT PRIMARY KEY,checkpoint INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS jarvis_learning_checkpoints(id INTEGER PRIMARY KEY,at REAL NOT NULL,report TEXT NOT NULL);'''


def pending(conn,state,now):
    held={mint for bot,b in state['bots'].items() if bot=='jarvis' or bot.startswith('research-') for mint in b['positions']}
    return [r['mint'] for r in conn.execute("""SELECT mint,min(id) AS first_id FROM trades
        WHERE (bot='jarvis' OR bot LIKE 'research-%') AND closed<=? AND mint NOT IN (SELECT mint FROM jarvis_learning_coins)
        GROUP BY mint ORDER BY first_id""",(now,)) if r['mint'] not in held]


def review(conn,state,now):
    coins=pending(conn,state,now)
    while len(coins)>=INTERVAL:
        batch=coins[:INTERVAL];coins=coins[INTERVAL:]
        marks=','.join('?' for _ in batch)
        rows=conn.execute("SELECT bot,pnl,data FROM trades WHERE (bot='jarvis' OR bot LIKE 'research-%') AND closed<=? AND mint IN ("+marks+")",[now,*batch]).fetchall()
        groups={}
        for row in rows:
            value=json.loads(row['data']);version=(value.get('policy',{}).get('local_rule') or {}).get('id') or value.get('policy',{}).get('version') or 'unversioned'
            group=groups.setdefault(row['bot']+'/'+str(version),{'closed':0,'net_usd':0,'wins':0,'losses':0,'owner_closeouts':0})
            group['closed']+=1;group['net_usd']+=row['pnl'];group['wins']+=row['pnl']>0;group['losses']+=row['pnl']<0;group['owner_closeouts']+=bool(value.get('owner_closeout'))
        for group in groups.values():group['net_usd']/=1e6
        ident=conn.execute('SELECT COALESCE(max(id),0)+1 FROM jarvis_learning_checkpoints').fetchone()[0]
        report={'distinct_closed_coins':INTERVAL,'closed_positions':len(rows),'by_strategy':groups,
            'scope':'Snapshot of recorded closes at this checkpoint, including fees and write-offs. Multiple strategies can share a coin. This is learning evidence, not pooled promotion proof.'}
        conn.execute('INSERT INTO jarvis_learning_checkpoints VALUES (?,?,?)',(ident,now,json.dumps(report)))
        conn.executemany('INSERT INTO jarvis_learning_coins VALUES (?,?)',[(mint,ident) for mint in batch])
        conn.execute("INSERT INTO research_tasks VALUES ('brain-strategy',?) ON CONFLICT(name) DO UPDATE SET due=min(due,excluded.due)",(now,))
        conn.execute('INSERT INTO local_strategy_events(at,strategy,kind,detail) VALUES (?,?,?,?)',
            (now,'learning/'+str(ident),'learning',json.dumps({'name':f'Checkpoint {ident}: 100 distinct closed coins'})))


def status(conn,state,now=None):
    now=time.time() if now is None else now
    reviewed=conn.execute('SELECT count(*) FROM jarvis_learning_coins').fetchone()[0]
    latest=conn.execute('SELECT * FROM jarvis_learning_checkpoints ORDER BY id DESC LIMIT 1').fetchone()
    waiting=len(pending(conn,state,now))
    return {'interval_coins':INTERVAL,'reviewed_coins':reviewed,'pending_closed_coins':waiting,'next_checkpoint':reviewed+INTERVAL,
        'latest':None if latest is None else {'id':latest['id'],'at':latest['at'],'report':json.loads(latest['report'])}}
