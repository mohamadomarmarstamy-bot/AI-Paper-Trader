"""Strategy diversity and prospective, matched-coin research evidence.

Comparison is descriptive, not randomized causal evidence or promotion authority.
"""
import ast
import collections
import copy
import json


def shape(expression):
    tree=ast.parse(expression,mode='eval')
    for node in ast.walk(tree):
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):node.value=0
    return ast.dump(tree,include_attributes=False)


def features(rule):
    return sorted({n.id for n in ast.walk(ast.parse(rule['entry'],mode='eval')) if isinstance(n,ast.Name)})


def family(rule):
    names=set(features(rule))
    if 'rsi' in names:return 'rsi'
    if 'buys_m5' in names or 'sells_m5' in names:return 'order_pressure'
    if 'volume_ratio' in names:return 'relative_volume'
    if 'volume_m5_usd' in names:return 'absolute_volume'
    return 'liquidity_age'


def trials(conn):
    return [{'id':r['id'],'created':r['created'],'status':r['status'],'rule':json.loads(r['rule']),
             'report':json.loads(r['report'])} for r in conn.execute('SELECT * FROM local_strategies ORDER BY created')]


def specification(conn,rule,now,*,state=None):
    active=[r for r in trials(conn) if r['status'] in ('testing','proven')]
    clones=[r for r in active if shape(r['rule']['entry'])==shape(rule['entry'])]
    for row in conn.execute("SELECT data FROM brain_proposals WHERE kind='strategy' AND status='waiting_slot'"):
        if shape(json.loads(row['data'])['rule']['entry'])==shape(rule['entry']):clones.append(row)
    def changes(r):return sum(ast.dump(ast.parse(r['rule'][k],mode='eval'))!=ast.dump(ast.parse(rule[k],mode='eval')) for k in ('entry','exit'))
    state=state or {}
    current=state.get('strategy') or {}
    current_version=current.get('version') if not state.get('entry_guard') else None
    current_id=(current.get('local_rule') or {}).get('id') if current_version else None
    control=next((r for r in active if r['id']==current_id),None)
    if control is None and active and not current_version:
        control=min(active,key=lambda r:(changes(r),r['created']))
    changed=[] if control is None else [k for k in ('entry','exit') if ast.dump(ast.parse(control['rule'][k],mode='eval'))!=ast.dump(ast.parse(rule[k],mode='eval'))]
    return {'family':family(rule),'required_fields':features(rule),'similar_active_or_queued':len(clones),
        'admission':'diversity_blocked' if len(clones)>=2 else 'waiting_slot','frozen_at':now,
        'control_id':control['id'] if control else None,'control_rule':copy.deepcopy(control['rule']) if control else None,
        'changed_expressions':changed,'design':'single_expression_change' if len(changed)==1 else 'distinct_hypothesis',
        'comparison_scope':'Fresh same-size shared-coin closes only; observational, not causal.',
        'paired_gate':True,'control_version':current_version,
        'control_slot':next((r['slot'] for r in conn.execute('SELECT slot FROM local_strategies WHERE id=?',
                            (control['id'],))),None) if control else None}


def promotion_gate(conn,state,rule,slot,now,minimum):
    """Only newer cloud proposals use the stricter comparator; do not relabel existing trials."""
    row=conn.execute("SELECT report FROM brain_proposals WHERE id=? AND kind='strategy'",(rule['id'],)).fetchone()
    spec=(json.loads(row['report']).get('experiment') or {}) if row else {}
    if not spec.get('paired_gate'):return None
    version=spec.get('control_version')
    control_slot='jarvis' if version else spec.get('control_slot')
    control_id=version or spec.get('control_id')
    result={'baseline':control_id,'matched':0,'edge_without_best_usd':None,'passed':False}
    if not control_id or not control_slot:return result
    # A changed live paper policy is a new comparison, never retroactive evidence.
    current=state.get('strategy') or {}
    if version and current.get('version') not in (version,'local-'+rule['id']):return result
    start=max(rule['created'],spec['frozen_at'])
    def first_closes(bot,key,ident):
        out={}
        # Only internal JSON paths are passed. Preserve full loss from unpriced write-offs.
        for trade in conn.execute("""SELECT mint,opened,closed,entry_cost,pnl,id FROM trades
                WHERE bot=? AND opened>=? AND closed<=? AND json_extract(data,?)=?
                ORDER BY closed,id""",(bot,start,now,key,ident)):
            out.setdefault(trade['mint'],trade)
        return out
    candidate=first_closes(slot,'$.policy.local_rule.id',rule['id'])
    control=first_closes(control_slot,'$.policy.version' if version else '$.policy.local_rule.id',control_id)
    pairs=[(a,control[mint]) for mint,a in candidate.items() if mint in control
           and a['entry_cost']==control[mint]['entry_cost'] and abs(a['opened']-control[mint]['opened'])<=900]
    # Fix the cohort by when both closes were knowable. Later lucky trades cannot rewrite it.
    pairs.sort(key=lambda pair:(max(t['closed'] for t in pair),pair[0]['id'],pair[1]['id']))
    pairs=pairs[:minimum]
    result['matched']=len(pairs)
    if len(pairs)<minimum:return result
    edge=[a['pnl']-b['pnl'] for a,b in pairs]
    mid=len(edge)//2
    def without_best(vals):
        return sum(vals)-max(0,max(vals))
    result.update(edge_without_best_usd=without_best(edge)/1e6,
                  passed=all(without_best(part)>0 for part in (edge[:mid],edge[mid:])))
    return result


def comparisons(conn,state,now):
    records={r['id']:r for r in trials(conn)};results=[]
    held={mint for bot,b in state['bots'].items() if bot=='jarvis' or bot.startswith('research-') for mint in b['positions']}
    for row in conn.execute("SELECT id,report FROM brain_proposals WHERE kind='strategy' AND status IN ('testing','proven') ORDER BY created DESC LIMIT 10"):
        spec=json.loads(row['report']).get('experiment');candidate=records.get(row['id'])
        if not spec or not candidate or spec.get('control_id') not in records:continue
        control=records[spec['control_id']];start=max(candidate['created'],spec['frozen_at'])
        groups={candidate['id']:{},control['id']:{}}
        for trade in conn.execute("SELECT mint,pnl,data FROM trades WHERE opened>=? AND closed<=? AND bot LIKE 'research-%'",(start,now)):
            ident=(json.loads(trade['data']).get('policy',{}).get('local_rule') or {}).get('id')
            if ident in groups and trade['mint'] not in held:
                group=groups[ident];group[trade['mint']]=group.get(trade['mint'],0)+trade['pnl']
        a,b=groups[candidate['id']],groups[control['id']];shared=sorted(a.keys() & b.keys())
        results.append({'candidate_id':candidate['id'],'control_id':control['id'],'since':start,'shared_closed_coins':len(shared),
            'candidate_net_usd':sum(a[m] for m in shared)/1e6,'control_net_usd':sum(b[m] for m in shared)/1e6,
            'scope':'Observed matched coins, including recorded costs/write-offs; not randomized. Unmatched and still-held coins excluded. No automatic promotion from this comparison.'})
    return results


def context(conn,state,now):
    history=trials(conn)
    counts=collections.Counter(family(r['rule']) for r in history if r['status'] in ('testing','proven'))
    return {'active_families':dict(counts),'recent_trials':[{'id':r['id'],'family':family(r['rule']),'status':r['status'],
        'required_fields':features(r['rule']),'entry':r['rule']['entry'],'exit':r['rule']['exit'],'report':{k:r['report'][k] for k in ('closed','coins','net','note') if k in r['report']}} for r in history[-20:]],
        'comparisons':comparisons(conn,state,now),'available_hypotheses':['order_pressure','relative_volume','absolute_volume','liquidity_age','rsi'],
        'limitations':'Families describe rule inputs, not proven behavior. Breakouts/pullbacks require unavailable price-history features and must not be invented. All history remains persisted; context is bounded.'}
