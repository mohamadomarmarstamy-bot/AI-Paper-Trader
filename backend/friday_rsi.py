"""Friday-only prospective paired study; never participates in order admission."""
import json
import math

RULE = 'friday-breakout-rsi14-55-v1'
SCHEMA = """CREATE TABLE IF NOT EXISTS friday_rsi_shadow(
 chart_id TEXT PRIMARY KEY, rule TEXT NOT NULL, frozen REAL NOT NULL,
 decision TEXT NOT NULL, evidence TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS friday_rsi_recent ON friday_rsi_shadow(rule,frozen DESC);"""


def measure(rows, opened, frozen, as_of):
    """Wilder RSI seeded with 14 changes, then smoothed from the regular open."""
    closes = [r for r in rows if opened <= r['t'] and r['t']+60 <= frozen]
    info = {'rule':RULE,'bars':len(closes),'as_of':as_of,'rsi':None}
    if not closes or closes[0]['t'] != opened or any(b['t']-a['t'] != 60 for a,b in zip(closes,closes[1:])):
        return {**info,'decision':'missing_or_gapped'}
    if closes[-1]['t']+60 != as_of or not 0 <= frozen-as_of <= 75:
        return {**info,'decision':'stale_or_unaligned'}
    if len(closes)<15:
        return {**info,'decision':'insufficient_warmup'}
    changes=[b['c']-a['c'] for a,b in zip(closes,closes[1:])]
    gain=sum(max(v,0) for v in changes[:14])/14
    loss=sum(max(-v,0) for v in changes[:14])/14
    for change in changes[14:]:
        gain=(13*gain+max(change,0))/14
        loss=(13*loss+max(-change,0))/14
    if gain+loss==0:
        return {**info,'decision':'flat_undefined'}
    value=100*gain/(gain+loss)
    return {**info,'rsi':value,'decision':'confirm' if value>=55 else 'filter'}


def freeze(db,chart_id,now,chart):
    evidence=chart.get('friday_rsi') or {}
    if not isinstance(evidence,dict) or evidence.get('rule')!=RULE or evidence.get('as_of')!=chart.get('bar_as_of'):
        evidence={'rule':RULE,'decision':'missing_at_entry','rsi':None}
    decision=evidence.get('decision')
    if decision not in ('filter','confirm','missing_at_entry','missing_or_gapped','stale_or_unaligned','insufficient_warmup','flat_undefined'):
        decision='invalid_evidence'
    value=evidence.get('rsi')
    if decision in ('filter','confirm') and not (
            type(value) in (float,int) and math.isfinite(value) and 0<=value<=100
            and (value>=55)==(decision=='confirm') and type(evidence.get('bars')) is int and evidence['bars']>=15):
        decision='invalid_evidence'
    db.execute('INSERT OR IGNORE INTO friday_rsi_shadow VALUES(?,?,?,?,?)',
               (chart_id,RULE,now,decision,json.dumps(evidence)))


def summary(db):
    result={'rule':RULE,'admissions':0,'missing':0,'pending':0,'matched':0,'confirmed':0,
            'blocked_winners':0,'filtered_false_moves':0,'filtered_flat':0,'baseline_net':None,'rsi_net':None,'missing_reasons':{}}
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='friday_rsi_shadow'").fetchone():
        return result
    baseline=shadow=0.
    for decision,outcome in db.execute("""SELECT s.decision,t.outcome FROM friday_rsi_shadow s
        JOIN friday_trials t ON t.chart_id=s.chart_id WHERE s.rule=? ORDER BY s.frozen DESC,s.chart_id LIMIT 256""",(RULE,)):
        result['admissions']+=1
        if decision not in ('confirm','filter'):
            result['missing']+=1
            result['missing_reasons'][decision]=result['missing_reasons'].get(decision,0)+1
            continue
        if not outcome:
            result['pending']+=1
            continue
        # Existing verified, immutable forward outcomes have a 10 bps/side base scenario.
        pnl=next(s['modeled_pnl_usd'] for s in json.loads(outcome)['scenarios'] if s['bps_per_side']==10)
        result['matched']+=1
        baseline+=pnl
        if decision=='confirm':
            result['confirmed']+=1
            shadow+=pnl
        else:
            key='blocked_winners' if pnl>0 else 'filtered_false_moves' if pnl<0 else 'filtered_flat'
            result[key]+=1
    if result['matched']:
        result.update(baseline_net=round(baseline,4),rsi_net=round(shadow,4))
    return result
