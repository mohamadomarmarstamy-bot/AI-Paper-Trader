"""Small, prospective PAPER-only filter experiment. No forecasting or broker access."""
import json
import math
import uuid
from statistics import mean

POLICY_KEYS=('trade_usd','stop_percent','target_percent','trail_arm_percent','trail_percent','max_hold_minutes')
SCHEMA='''CREATE TABLE IF NOT EXISTS solana_meme_learning_outcomes (
 id TEXT PRIMARY KEY, closed_at REAL NOT NULL, mint TEXT NOT NULL, pnl_usd REAL NOT NULL,
 details_json TEXT NOT NULL); CREATE INDEX IF NOT EXISTS solana_meme_learning_closed ON solana_meme_learning_outcomes(closed_at);
CREATE TABLE IF NOT EXISTS solana_meme_learning_evaluations (id TEXT PRIMARY KEY, created_at REAL NOT NULL, changed INTEGER NOT NULL, details_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS solana_meme_learning_mail (id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0, first_attempt REAL, provider_id TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS solana_meme_manual_changes (id TEXT PRIMARY KEY,created_at REAL NOT NULL,details_json TEXT NOT NULL);'''


def scope(config):
    return {k:config[k] for k in POLICY_KEYS}


def finite(v):
    return isinstance(v,(float,int)) and not isinstance(v,bool) and math.isfinite(v)


def matches(row,minimums):
    f=row.get('entry_features',{})
    return all(finite(f.get(k)) and f[k]>=v for k,v in minimums.items())


def archive(conn,rows):
    for row in rows:
        conn.execute('INSERT OR IGNORE INTO solana_meme_learning_outcomes VALUES (?,?,?,?,?)',
                     (row['id'],row['closed_at'],row['mint'],row['pnl_usd'],json.dumps(row,allow_nan=False)))


def recent(conn,limit=1000):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_meme_learning_outcomes'").fetchone():return []
    return [json.loads(r[0]) for r in reversed(conn.execute('SELECT details_json FROM solana_meme_learning_outcomes ORDER BY closed_at DESC,id DESC LIMIT ?',(limit,)).fetchall())]


def summary(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_meme_learning_outcomes'").fetchone():return {'total':0,'wins':0,'losses':0,'notes':[]}
    row=conn.execute('SELECT COUNT(*),COALESCE(SUM(pnl_usd>0),0),COALESCE(SUM(pnl_usd<0),0) FROM solana_meme_learning_outcomes').fetchone()
    result={'total':row[0],'wins':row[1],'losses':row[2],'notes':recent(conn,20)[::-1]}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_meme_learning_evaluations'").fetchone():
        result['evaluations']=[{**json.loads(r[0]),'email_status':r[1]} for r in conn.execute('SELECT e.details_json,m.status FROM solana_meme_learning_evaluations e LEFT JOIN solana_meme_learning_mail m ON m.id=e.id ORDER BY e.rowid DESC LIMIT 20')]
        result['email_pending']=conn.execute("SELECT COUNT(*) FROM solana_meme_learning_mail WHERE status='pending'").fetchone()[0]
    changes=[]
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_meme_learning_evaluations'").fetchone():
        changes += [{**json.loads(r[0]),'actor':'Bot','email_status':r[1]} for r in conn.execute('SELECT e.details_json,m.status FROM solana_meme_learning_evaluations e LEFT JOIN solana_meme_learning_mail m ON m.id=e.id WHERE e.changed=1 ORDER BY e.rowid DESC LIMIT 50')]
        result['settings_change_count']=conn.execute('SELECT COUNT(*) FROM solana_meme_learning_evaluations WHERE changed=1').fetchone()[0]
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_meme_manual_changes'").fetchone():
        changes += [json.loads(r[0]) for r in conn.execute('SELECT details_json FROM solana_meme_manual_changes ORDER BY rowid DESC LIMIT 50')]
        result['settings_change_count']=result.get('settings_change_count',0)+conn.execute('SELECT COUNT(*) FROM solana_meme_manual_changes').fetchone()[0]
    result['settings_changes']=sorted(changes,key=lambda r:r['at'],reverse=True)[:50]
    return result



def usable(row,policy):
    f=row.get('entry_features',{})
    return row.get('exit_source','automatic')=='automatic' and row.get('exit_policy')==policy and finite(row.get('pnl_usd')) and finite(row.get('entry_usd')) and row['entry_usd']>0 and all(finite(f.get(k)) and 0<=f[k]<=1e9 for k in ('liquidity_usd','volume_m5_usd'))


def average(rows):
    return mean(r['pnl_usd']/r['entry_usd']*100 for r in rows) if rows else 0


def evaluate_batch(rows,config):
    usable_rows=[r for r in rows if usable(r,scope(config))]
    if len(usable_rows)<40 or len({r['mint'] for r in usable_rows})<5:return None,{'reason':'Insufficient comparable liquidity/volume evidence','usable':len(usable_rows)}
    train=usable_rows[:30];holdout=usable_rows[30:];winners=[r for r in train if r['pnl_usd']>0]
    if len(winners)<5:return None,{'reason':'Fewer than five training winners','usable':len(usable_rows)}
    options={}
    for k in ('liquidity_usd','volume_m5_usd'):
        values=sorted(r['entry_features'][k] for r in winners)
        options[k]=sorted({0,round(values[len(values)//4],1),round(values[len(values)//2],1)})
    candidates=[]
    for liq in options['liquidity_usd']:
        for vol in options['volume_m5_usd']:
            rule={'liquidity_usd':liq,'volume_m5_usd':vol};selected=[r for r in train if matches(r,rule)]
            if len(selected)>=10 and len({r['mint'] for r in selected})>=3 and average(selected)>0:candidates.append((average(selected),len(selected),rule))
    if not candidates:return None,{'reason':'No positive training candidate','usable':len(usable_rows)}
    _,_,rule=max(candidates,key=lambda c:(c[0],c[1],-c[2]['liquidity_usd'],-c[2]['volume_m5_usd']))
    selected=[r for r in holdout if matches(r,rule)]
    evidence={'reason':'Held-out comparison','usable':len(usable_rows),'training_count':len(train),'validation_count':len(holdout),'selected_count':len(selected),'selected_average_percent':average(selected),'baseline_average_percent':average(holdout)}
    if len(selected)<5 or len({r['mint'] for r in selected})<3 or average(selected)<=0 or average(selected)<=average(holdout):return None,{**evidence,'reason':'Candidate did not improve held-out results'}
    return rule,evidence


def record(conn,event):
    conn.execute('INSERT INTO solana_meme_learning_evaluations VALUES (?,?,?,?)',(event['id'],event['at'],int(event['changed']),json.dumps(event,allow_nan=False)))
    if event['changed']:
        conn.execute('INSERT INTO solana_meme_learning_mail(id,payload_json) VALUES (?,?)',(event['id'],json.dumps(event,allow_nan=False)))


def apply(state,rule,event):
    config=state['config'];before={'entry_mode':config['entry_mode'],'minimum_liquidity_usd':config['minimum_liquidity_usd'],'minimum_m5_volume_usd':config['minimum_m5_volume_usd']}
    after={'entry_mode':'learned','minimum_liquidity_usd':rule['liquidity_usd'],'minimum_m5_volume_usd':rule['volume_m5_usd']}
    event.update(before=before,after=after,changed=before!=after,policy=scope(config))
    if event['changed']:
        config.update(after);state['settings_revision']=state.get('settings_revision',0)+1
        learning=state['learning'];learning['change_count']=learning.get('change_count',0)+1
        event['change_number']=learning['change_count'];event['settings_version']=event['id'];learning['active_version']=event['id']
        learning['changes']=(learning.get('changes',[])+[{'id':event['id'],'number':event['change_number'],'rule':rule,'policy':scope(config)}])[-100:]
        if event['change_number']%10==0:
            learning.setdefault('pending_reviews',[]).append({'number':event['change_number']//10,'changes':learning['changes'][-10:]})


def process(conn,state,now):
    learning=state.setdefault('learning',{});config=state['config']
    learning.pop('proposal',None) # Retire the old one-off 30/20 experiment.
    if not config['auto_learning']:
        learning['status']='Automatic evaluation paused';return
    cursor=learning.get('cycle_cursor',0)
    fetched=conn.execute('SELECT rowid,details_json FROM solana_meme_learning_outcomes WHERE rowid>? ORDER BY rowid LIMIT 5000',(cursor,)).fetchall()
    for offset in range(0,len(fetched)-49,50):
        batch=fetched[offset:offset+50];rows=[json.loads(r[1]) for r in batch];number=learning.get('batch_count',0)+1
        event={'id':f'cycle50-{number}','type':'50-trade evaluation','at':now,'batch':number,'first_sequence':batch[0][0],'last_sequence':batch[-1][0],'closed_count':50,'changed':False}
        rule,evidence=evaluate_batch(rows,config);event['evidence']=evidence
        if rule:apply(state,rule,event)
        record(conn,event);learning.update(cycle_cursor=batch[-1][0],batch_count=number,last_evaluation=event)
    for review in list(learning.get('pending_reviews',[])):
        if any(c['policy']!=scope(config) for c in review['changes']):
            event={'id':f"review10-{review['number']}",'type':'10-change review','at':now,'changed':False,'evidence':{'reason':'Exit/size policy changed; version results are not comparable'}}
            record(conn,event);learning['pending_reviews'].remove(review);learning['last_review']=event;continue
        ids=[c['id'] for c in review['changes']];cohorts={ident:[] for ident in ids}
        # Versions are saved at ENTRY; already-open positions cannot be credited to a new version.
        for r in conn.execute("SELECT details_json FROM solana_meme_learning_outcomes WHERE json_extract(details_json,'$.settings_version') IN ("+','.join('?' for _ in ids)+')',ids):
            row=json.loads(r[0]);ident=row.get('settings_version')
            if usable(row,scope(config)):cohorts[ident].append(row)
        if any(len(cohorts[ident])<10 or len({r['mint'] for r in cohorts[ident]})<3 for ident in ids):
            learning['review_status']=f"10-change review {review['number']} waiting for at least 10 closes per version";continue
        best=max(review['changes'],key=lambda c:(average(cohorts[c['id']]),len(cohorts[c['id']])))
        event={'id':f"review10-{review['number']}",'type':'10-change review','at':now,'changed':False,'evidence':{'versions':[{ 'id':c['id'],'count':len(cohorts[c['id']]),'average_percent':average(cohorts[c['id']])} for c in review['changes']],'best_version':best['id'],'reason':'Observed version cohorts; different market periods are not a causal comparison'}}
        if average(cohorts[best['id']])>0:apply(state,best['rule'],event)
        record(conn,event);learning['pending_reviews'].remove(review);learning['review_status']=f"10-change review {review['number']} complete";learning['last_review']=event
    remaining=conn.execute('SELECT COUNT(*) FROM solana_meme_learning_outcomes WHERE rowid>?',(learning.get('cycle_cursor',0),)).fetchone()[0]
    learning['status']=f"Next evaluation: {remaining}/50 closes · {learning.get('batch_count',0)} batches · {learning.get('change_count',0)} settings changes"
