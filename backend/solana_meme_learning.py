"""Small, prospective PAPER-only filter experiment. No forecasting or broker access."""
import json
import math
import uuid

POLICY_KEYS=('trade_usd','stop_percent','target_percent','trail_arm_percent','trail_percent','max_hold_minutes')
SCHEMA='''CREATE TABLE IF NOT EXISTS solana_meme_learning_outcomes (
 id TEXT PRIMARY KEY, closed_at REAL NOT NULL, mint TEXT NOT NULL, pnl_usd REAL NOT NULL,
 details_json TEXT NOT NULL); CREATE INDEX IF NOT EXISTS solana_meme_learning_closed ON solana_meme_learning_outcomes(closed_at);'''


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
    return {'total':row[0],'wins':row[1],'losses':row[2],'notes':recent(conn,20)[::-1]}


def update(state,rows,now):
    config=state['config'];learning=state.setdefault('learning',{})
    if not config['auto_learning']:
        learning['status']='Automatic learning paused';return
    if config['entry_mode']!='explore':
        learning['status']='Learned filters active' if config['entry_mode']=='learned' else 'Filtered mode · exploration paused';return
    eligible=[r for r in rows if r.get('entry_mode')=='explore' and r.get('exit_policy')==scope(config)
              and finite(r.get('pnl_usd')) and all(finite(r.get('entry_features',{}).get(k)) and 0<=r['entry_features'][k]<=1000000000 for k in ('liquidity_usd','volume_m5_usd'))]
    learning['eligible_outcomes']=len(eligible)
    proposal=learning.get('proposal')
    if proposal and proposal['scope']!=scope(config):learning.pop('proposal');proposal=None
    if proposal:
        future=[r for r in eligible if r.get('learning_candidate_id')==proposal['id'] and r['opened_at']>proposal['created_at']]
        proposal['validation_count']=len(future)
        learning['status']=f"Testing frozen filters · {len(future)}/20 future closes"
        if len(future)<20:return
        # Only the first twenty future outcomes can decide this frozen proposal.
        future=future[:20];selected=[r for r in future if matches(r,proposal['minimums'])];rejected=[r for r in future if not matches(r,proposal['minimums'])]
        avg=lambda rs:sum(r['pnl_usd']/r['entry_usd']*100 for r in rs)/len(rs) if rs else 0
        passed=(len(selected)>=8 and len(rejected)>=5 and len({r['mint'] for r in future})>=5
                and sum(r['pnl_usd']>0 for r in selected)>=4 and avg(selected)>0
                and avg(selected)>avg(future) and avg(selected)>avg(rejected))
        result={**proposal,'accepted':passed,'evaluated_at':now,'selected_count':len(selected),
                'selected_avg_return_percent':avg(selected),'baseline_avg_return_percent':avg(future)}
        learning['last_result']=result;learning.pop('proposal');learning['retrain_after']=now
        if passed:
            config.update(entry_mode='learned',minimum_liquidity_usd=proposal['minimums']['liquidity_usd'],minimum_m5_volume_usd=proposal['minimums']['volume_m5_usd'])
            state['settings_revision']=state.get('settings_revision',0)+1
            learning['status']='Learned filters active';learning['applied_at']=now
        else:learning['status']='Candidate failed forward test · exploration continues'
        return
    if len(eligible)<30 or (learning.get('retrain_after') is not None and sum(r['closed_at']>learning['retrain_after'] for r in eligible)<20) or len({r['mint'] for r in eligible})<10 or sum(r['pnl_usd']>0 for r in eligible)<5 or sum(r['pnl_usd']<0 for r in eligible)<5:
        learning['status']=f'Collecting outcomes · {len(eligible)}/30 minimum, including wins and losses';return
    training=eligible[-100:];winners=[r for r in training if r['pnl_usd']>0]
    # One transparent heuristic is frozen before observing its validation trades.
    minimums={k:round(sorted(r['entry_features'][k] for r in winners)[len(winners)//4],1) for k in ('liquidity_usd','volume_m5_usd')}
    learning['proposal']={'id':str(uuid.uuid4()),'created_at':now,'scope':scope(config),'minimums':minimums,'training_count':len(training),'validation_count':0}
    learning['status']='Testing frozen filters · 0/20 future closes'
