"""Automatic observational paper audit. Never changes strategies or execution."""
import ast
import json

import jarvis_local_lab as local

SCHEMA = '''CREATE TABLE IF NOT EXISTS jarvis_audit_samples(
 bucket INTEGER PRIMARY KEY, at REAL NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jarvis_audit_latest(
 id INTEGER PRIMARY KEY CHECK(id=1), at REAL NOT NULL, data TEXT NOT NULL);'''


# Match the observed templates structurally. Each must be a direct conjunct:
# when gain > 15 holds, the first alternative (gain >= 15) already holds.
_FIXED_OR_TRAILING = ast.dump(ast.parse(
    'gain >= 15 or (peak >= 10 and gain <= peak - 5)', mode='eval').body)
_GAIN_GUARD = ast.dump(ast.parse('gain > 15', mode='eval').body)


def redundant_trailing_branch(expression):
    if not isinstance(expression, str):
        return False
    try:
        root = local.parse(expression, exit_rule=True)
    except (ValueError, OverflowError, RecursionError):
        return False
    if not isinstance(root, ast.BoolOp) or not isinstance(root.op, ast.And):
        return False
    terms = {ast.dump(term) for term in root.values}
    return _FIXED_OR_TRAILING in terms and _GAIN_GUARD in terms


def sample(conn, state, entries, now):
    bucket=int(now)//300
    if conn.execute('SELECT 1 FROM jarvis_audit_samples WHERE bucket=?',(bucket,)).fetchone():
        return False
    slots={}
    for slot, entry in entries.items():
        rule=state.get('local_rules',{}).get(slot)
        if not rule:continue
        slots[rule['id']]={'slot':slot,'eligible':entry.get('fresh_eligible_coins',0),
            'complete':entry.get('complete_observations',0),'matching':entry.get('matching_coins',0),
            'missing':entry.get('missing_observations',{}),'reason':entry['label']}
    conn.execute('INSERT INTO jarvis_audit_samples VALUES (?,?,?)',(bucket,now,json.dumps(slots)))
    conn.execute('DELETE FROM jarvis_audit_samples WHERE at<?',(now-30*86400,))
    return True


def build(conn, now):
    observations={}
    for row in conn.execute('SELECT data FROM jarvis_audit_samples WHERE at>? ORDER BY at',(now-86400,)):
        for ident,s in json.loads(row['data']).items():
            x=observations.setdefault(ident,{'samples':0,'eligible_observations':0,'complete_observations':0,'matching_observations':0,'missing_fields':{},'capacity_samples':0,'data_wait_samples':0})
            x['samples']+=1
            for source,target in [('eligible','eligible_observations'),('complete','complete_observations'),('matching','matching_observations')]:x[target]+=s[source]
            for field,count in s['missing'].items():x['missing_fields'][field]=x['missing_fields'].get(field,0)+count
            x['capacity_samples']+=('position limit' in s['reason'] or 'capacity' in s['reason'] or 'urgent exits' in s['reason'])
            x['data_wait_samples']+=s['eligible']>s['complete']
    groups={}
    for row in conn.execute("SELECT bot,mint,pnl,data FROM trades WHERE bot LIKE 'research-%' ORDER BY closed,id"):
        data=json.loads(row['data']);ident=data.get('policy',{}).get('local_rule',{}).get('id')
        if not ident:continue
        g=groups.setdefault(ident,{'coins':{},'closed':0,'owner_closeouts':0,'owner_closeout_net':0,'writeoffs':0,'waits':[],'signal_delays':[]})
        if data.get('owner_closeout'):
            g['owner_closeouts']+=1;g['owner_closeout_net']+=row['pnl']/1e6
            continue
        g['closed']+=1
        g['coins'][row['mint']]=g['coins'].get(row['mint'],0)+row['pnl']/1e6
        g['writeoffs']+=bool(data.get('paper_writeoff'))
        if isinstance(data.get('exit_quote_wait'),(int,float)):g['waits'].append(data['exit_quote_wait'])
        if isinstance(data.get('signal_to_close_seconds'),(int,float)):g['signal_delays'].append(data['signal_to_close_seconds'])
    strategies=[]
    redundant_exits=[]
    for candidate in conn.execute('SELECT id,slot,status,rule FROM local_strategies ORDER BY created'):
        rule=json.loads(candidate['rule'])
        if candidate['status'] in ('testing','proven') and redundant_trailing_branch(rule.get('exit')):
            redundant_exits.append(candidate['id'])
        ident=candidate['id'];g=groups.get(ident,{})
        values=list(g.get('coins',{}).values());net=sum(values);best=max(values,default=0)
        gains=sum(v for v in values if v>0);losses=-sum(v for v in values if v<0)
        flags=[]
        obs=observations.get(ident,{})
        if obs.get('eligible_observations',0)>obs.get('complete_observations',0):flags.append('missing_data_observed')
        if obs.get('capacity_samples',0):flags.append('capacity_constraints_observed')
        waits=g.get('waits',[])
        if any(w>=90 for w in waits):flags.append('delayed_exit_checks_observed')
        if net>0 and net-max(best,0)<=0:flags.append('positive_result_depends_on_best_coin')
        if len(values)<100:flags.append('small_sample')
        strategies.append({'id':ident,'slot':candidate['slot'],'name':rule['name'],'status':candidate['status'],
            'closed':g.get('closed',0),'distinct_coins':len(values),'net':net,'net_without_best_coin':net-max(best,0),
            'profit_factor':gains/losses if losses else None,'writeoffs':g.get('writeoffs',0),
            'owner_closeouts':g.get('owner_closeouts',0),'owner_closeout_net':g.get('owner_closeout_net',0),
            'quote_wait_measured':len(waits),'quote_wait_over_90s':sum(w>=90 for w in waits),
            'mean_quote_wait':sum(waits)/len(waits) if waits else None,
            'signal_delay_measured':len(g.get('signal_delays',[])),
            'observations':obs,'flags':flags})
    return {'at':now,'observation_window_hours':24,'sample_interval_seconds':300,'strategies':strategies,
        'limitations':['Observations are repeated five-minute snapshots, not unique opportunities or executed orders.',
            'Historical missing decision data cannot be reconstructed reliably.',
            'Delayed checks and losses are associations, not proof that delays caused losses.',
            'Owner closeouts are reported separately from strategy results. No-route write-offs remain losses.',
            'One hundred coins is a checkpoint, not evidence of a durable edge.'],
        'fixed_gain_guard_redundant_trailing':redundant_exits, 'automatic_actions':False}


def refresh(conn, state, entries, now):
    if not sample(conn,state,entries,now):return
    report=build(conn,now)
    conn.execute('INSERT OR REPLACE INTO jarvis_audit_latest VALUES (1,?,?)',(now,json.dumps(report)))


def latest(conn):
    row=conn.execute('SELECT at,data FROM jarvis_audit_latest WHERE id=1').fetchone()
    return json.loads(row['data']) if row else None


def brief(report):
    if not report:return 'Automated audit is collecting its first observations.'
    active=[s for s in report['strategies'] if s['status'] in ('testing','proven')]
    count=lambda flag:sum(flag in s['flags'] for s in active)
    redundant=len(report.get('fixed_gain_guard_redundant_trailing',[]))
    warning=(f' In {redundant} active paper rules, the fixed-gain guard prevents the trailing branch from triggering independently.' if redundant else '')
    return ('Automated audit: '+str(len(active))+' active strategies; '+str(count('missing_data_observed'))+
        ' with missing-data observations; '+str(count('delayed_exit_checks_observed'))+
        ' with measured late exit checks; '+str(count('positive_result_depends_on_best_coin'))+
        ' positive results dependent on their best coin. These are diagnostic flags, not causal findings.'+warning)
