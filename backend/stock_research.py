"""Prospective paired filter research. GET-only market data; no order submission."""
from collections import defaultdict
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from queue import Queue, Empty, Full
import random
from statistics import mean
import time
import uuid
from zoneinfo import ZoneInfo
import app_controls as controls
import stock_momentum as momentum

ET=ZoneInfo('America/New_York')
HORIZON=900
GRACE=300
NOTIONAL=1000.
MIN_PAIRS=50
MIN_DAYS=5
_queue=Queue(maxsize=1)
_rotation=0
SCHEMA='''CREATE TABLE IF NOT EXISTS stock_shadow_trials(
 id TEXT PRIMARY KEY,created_at REAL NOT NULL,baseline_revision TEXT NOT NULL,
 baseline_json TEXT NOT NULL,changes_json TEXT NOT NULL,origin TEXT NOT NULL,
 baseline_real_mean REAL,state TEXT NOT NULL DEFAULT 'collecting',decision_json TEXT NOT NULL DEFAULT '{}',evaluated_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS stock_shadow_opportunities(
 id TEXT PRIMARY KEY,symbol TEXT NOT NULL,bucket INTEGER NOT NULL,entered_at REAL NOT NULL,
 due_at REAL NOT NULL,ask REAL NOT NULL,bid REAL NOT NULL,features_json TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'open',exit_at REAL,exit_bid REAL,UNIQUE(symbol,bucket));
CREATE TABLE IF NOT EXISTS stock_shadow_decisions(
 trial_id TEXT NOT NULL,opportunity_id TEXT NOT NULL,baseline_accept INTEGER NOT NULL,
 candidate_accept INTEGER NOT NULL,baseline_failures TEXT NOT NULL,candidate_failures TEXT NOT NULL,
 PRIMARY KEY(trial_id,opportunity_id));
CREATE INDEX IF NOT EXISTS stock_shadow_open_idx ON stock_shadow_opportunities(state,due_at);
CREATE TABLE IF NOT EXISTS stock_research_runtime(id INTEGER PRIMARY KEY CHECK(id=1),last_tick REAL,last_error TEXT);
CREATE TABLE IF NOT EXISTS stock_research_sampling(id INTEGER PRIMARY KEY CHECK(id=1),updated_at REAL NOT NULL,
 state TEXT NOT NULL,details_json TEXT NOT NULL);'''


def initialize(conn):
    conn.executescript(SCHEMA)


def sampling_state(state,now,details=None):
    """One bounded latest batch; worker health alone cannot explain zero pairs."""
    with closing(controls.connect()) as conn:
        if details is None:
            conn.execute("INSERT INTO stock_research_sampling VALUES(1,?,?, '{}') ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,state=excluded.state",(now,state))
        else:
            conn.execute('INSERT INTO stock_research_sampling VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,state=excluded.state,details_json=excluded.details_json',
                         (now,state,json.dumps(details,allow_nan=False)))
        conn.commit()


def enqueue_scanner(candidates,criteria):
    payload=(deepcopy(candidates),dict(criteria),time.time())
    try:_queue.get_nowait()
    except Empty:pass
    try:_queue.put_nowait(payload)
    except Full:pass


def propose(conn,snapshot,changes,report,*,now=None,origin='learner'):
    encoded=json.dumps(changes,sort_keys=True,separators=(',',':'))
    existing=conn.execute("SELECT id FROM stock_shadow_trials WHERE baseline_revision=? AND changes_json=? AND origin=? AND state='collecting'",(snapshot['revision'],encoded,origin)).fetchone()
    if existing:return existing['id']
    if origin=='learner':
        pending=conn.execute("SELECT id FROM stock_shadow_trials WHERE baseline_revision=? AND origin='learner' AND state IN ('collecting','supported') ORDER BY created_at LIMIT 1",(snapshot['revision'],)).fetchone()
        if pending:return pending['id']
    # Each learned proposal starts a fresh future cohort; seeded research data
    # from before proposal selection is never recycled into promotion evidence.
    ident=str(uuid.uuid4())
    conn.execute('INSERT INTO stock_shadow_trials(id,created_at,baseline_revision,baseline_json,changes_json,origin,baseline_real_mean) VALUES(?,?,?,?,?,?,?)',
        (ident,now if now is not None else time.time(),snapshot['revision'],json.dumps(snapshot['settings'],sort_keys=True),encoded,origin,report.get('estimated_net_mean_return_percent')))
    return ident


def seed(conn,snapshot,now):
    config=snapshot['settings']
    keys=[('stock_min_time_volume_ratio' if config['stock_intraday_enabled'] else 'stock_min_volume_ratio',1.1),
          ('stock_max_vwap_distance_percent',.9),('stock_max_float_millions',.9)]
    for key,factor in keys:
        if not config[key] or (key=='stock_max_vwap_distance_percent' and not config['stock_intraday_enabled']):continue
        if key=='stock_max_float_millions' and config['stock_min_float_millions']>config[key]*factor:continue
        encoded=json.dumps({key:round(config[key]*factor,6)},sort_keys=True,separators=(',',':'))
        if conn.execute("SELECT 1 FROM stock_shadow_trials WHERE baseline_revision=? AND changes_json=? AND origin='seed'",(snapshot['revision'],encoded)).fetchone():continue
        propose(conn,snapshot,json.loads(encoded),{},now=now,origin='seed')


def fresh_quote(raw,now):
    try:
        bid=momentum.number(raw['bp']);ask=momentum.number(raw['ap'])
        at=datetime.fromisoformat(str(raw['t']).replace('Z','+00:00'))
        if at.tzinfo is None:return None
        stamp=at.timestamp()
        if bid is None or ask is None or not 0<bid<=ask or not 0<=now-stamp<=30:return None
        if (momentum.number(raw.get('bs')) or 0)<=0 or (momentum.number(raw.get('as')) or 0)<=0:return None
        return {'bid':bid,'ask':ask,'at':stamp,'spread_percent':(ask-bid)/((bid+ask)/2)*100}
    except (KeyError,TypeError,ValueError,OverflowError):return None


def quotes(request,symbols,now,*,clock=None):
    result={}
    symbols=sorted(set(symbols))
    for i in range(0,len(symbols),30):
        try:
            payload=request('GET','/v2/stocks/quotes/latest',params={'symbols':','.join(symbols[i:i+30]),'feed':'iex'})
            for symbol,raw in (payload.get('quotes') or {}).items():
                parsed=fresh_quote(raw,clock() if clock else now)
                if symbol in symbols and parsed:result[symbol]=parsed
        except Exception:continue
    return result


def record_opportunity(conn,trials,symbol,features,quote,now,*,diagnostic=None):
    bucket=int(now//HORIZON)
    if conn.execute('SELECT 1 FROM stock_shadow_opportunities WHERE symbol=? AND bucket=?',(symbol,bucket)).fetchone():
        if diagnostic is not None:diagnostic.update(state='duplicate')
        return None
    decisions=[]
    for trial in trials:
        if now<trial['created_at']:continue
        base=json.loads(trial['baseline_json']);candidate={**base,**json.loads(trial['changes_json'])}
        bf=momentum.evaluate(features,base,now=now);cf=momentum.evaluate(features,candidate,now=now)
        decisions.append((trial['id'],not bf,not cf,bf,cf))
    # Keep failed evidence visible through trial counters only when at least
    # one arm can act; a missing quote is never replaced with a scanner close.
    if diagnostic is not None:
        diagnostic.update(state='accepted' if any(base or candidate for _,base,candidate,_,_ in decisions) else 'filters_rejected',
            baseline_failures=sorted({reason for _,_,_,bf,_ in decisions for reason in bf}),
            candidate_failures=sorted({reason for _,_,_,_,cf in decisions for reason in cf}),
            float_source=features.get('float_source'),intraday_reason=(features.get('intraday') or {}).get('reason'))
    if not any(base or candidate for _,base,candidate,_,_ in decisions):return None
    ident=str(uuid.uuid4())
    conn.execute('INSERT INTO stock_shadow_opportunities(id,symbol,bucket,entered_at,due_at,ask,bid,features_json) VALUES(?,?,?,?,?,?,?,?)',
        (ident,symbol,bucket,now,now+HORIZON,quote['ask'],quote['bid'],json.dumps(features,allow_nan=False)))
    for trial_id,base,candidate,bf,cf in decisions:
        conn.execute('INSERT INTO stock_shadow_decisions VALUES(?,?,?,?,?,?)',(trial_id,ident,int(base),int(candidate),json.dumps(bf),json.dumps(cf)))
    return ident


def settle(conn,observed,now):
    for row in conn.execute("SELECT * FROM stock_shadow_opportunities WHERE state='open' AND due_at<=?",(now,)).fetchall():
        quote=observed.get(row['symbol'])
        if now>row['due_at']+GRACE:
            conn.execute("UPDATE stock_shadow_opportunities SET state='unavailable' WHERE id=?",(row['id'],));continue
        if quote and quote['at']>=row['due_at']:
            conn.execute("UPDATE stock_shadow_opportunities SET state='closed',exit_at=?,exit_bid=? WHERE id=?",(quote['at'],quote['bid'],row['id']))


def trial_rows(conn,ident):
    return [dict(r) for r in conn.execute('''SELECT d.*,o.symbol,o.entered_at,o.exit_at,o.ask,o.exit_bid,o.state
        FROM stock_shadow_decisions d JOIN stock_shadow_opportunities o ON o.id=d.opportunity_id
        WHERE d.trial_id=? ORDER BY o.entered_at,o.id''',(ident,))]


def trial_stats(conn,trial,*,bootstrap=False):
    rows=trial_rows(conn,trial['id']);closed=[r for r in rows if r['state']=='closed']
    missing=sum(r['state']=='unavailable' for r in rows)
    base=json.loads(trial['baseline_json'])
    cost=2*(base['stock_slippage_bps_per_side']+base['stock_fee_bps_per_side'])/100
    paired=[]
    for row in closed:
        net=(row['exit_bid']/row['ask']-1)*100-cost
        paired.append({**row,'base_return':net if row['baseline_accept'] else 0.,
            'candidate_return':net if row['candidate_accept'] else 0.,
            'delta':net*(row['candidate_accept']-row['baseline_accept']),
            'day':datetime.fromtimestamp(row['entered_at'],ET).date().isoformat()})
    days=defaultdict(list)
    for row in paired:days[row['day']].append(row)
    stats={'opportunities':len(rows),'matured_pairs':len(paired),'open':sum(r['state']=='open' for r in rows),
        'unavailable':missing,'unavailable_percent':missing/max(1,len(closed)+missing)*100,
        'different_decisions':sum(r['baseline_accept']!=r['candidate_accept'] for r in paired),
        'sessions':len(days),'symbols':len({r['symbol'] for r in paired}),
        'baseline_selected':sum(r['baseline_accept'] for r in paired),'candidate_selected':sum(r['candidate_accept'] for r in paired),
        'baseline_mean_percent':mean(r['base_return'] for r in paired) if paired else None,
        'candidate_mean_percent':mean(r['candidate_return'] for r in paired) if paired else None,
        'improvement_percent':mean(r['delta'] for r in paired) if paired else None,
        'baseline_shadow_pnl_usd':sum(r['base_return']*NOTIONAL/100 for r in paired),
        'candidate_shadow_pnl_usd':sum(r['candidate_return']*NOTIONAL/100 for r in paired),
        'lower_95_percent':None,'supported':False}
    eligible=len(paired)>=MIN_PAIRS and len(days)>=MIN_DAYS and stats['symbols']>=5 and stats['different_decisions']>=10 and stats['unavailable_percent']<=10
    if bootstrap and eligible:
        # Resample whole ET sessions, preserving within-session dependence.
        # This is exploratory evidence, not a calibrated guarantee or p-value.
        rng=random.Random(trial['id']);groups=list(days.values());estimates=[]
        for _ in range(1000):
            sample=[r for group in rng.choices(groups,k=len(groups)) for r in group]
            estimates.append(mean(r['delta'] for r in sample))
        stats['lower_95_percent']=sorted(estimates)[24]
        stats['supported']=stats['lower_95_percent']>0 and stats['improvement_percent']>.05 and stats['candidate_mean_percent']>0
    return stats


def evaluate_trials(conn,current):
    for trial in conn.execute("SELECT * FROM stock_shadow_trials WHERE state='collecting'").fetchall():
        if trial['baseline_revision']!=current['revision']:
            conn.execute("UPDATE stock_shadow_trials SET state='superseded',decision_json=? WHERE id=?",(json.dumps({'reason':'Saved settings changed; a fresh comparison is required.'}),trial['id']));continue
        stats=trial_stats(conn,trial,bootstrap=True)
        state='supported' if stats['supported'] else 'not_supported' if stats['matured_pairs']>=250 else 'collecting'
        reason='More prospective evidence needed: 50 matured pairs, five sessions, five symbols and 10 differing decisions, with limited missing quotes.'
        if stats['supported']:reason='Candidate improved paired net markouts with a positive session-bootstrap lower bound. This remains simulated, exploratory evidence.'
        elif state=='not_supported':reason='The comparison reached 250 paired markouts without meeting the promotion threshold.'
        stats['reason']=reason
        conn.execute('UPDATE stock_shadow_trials SET state=?,decision_json=?,evaluated_count=? WHERE id=?',
                     (state,json.dumps(stats,allow_nan=False),stats['matured_pairs'],trial['id']))


def promote(conn,current):
    if not current['settings']['stock_parallel_validation_enabled'] or not current['settings']['stock_auto_adjust_enabled']:return None
    if conn.execute("SELECT 1 FROM stock_learning_experiments WHERE state='pending'").fetchone():return None
    trial=conn.execute("SELECT * FROM stock_shadow_trials WHERE state='supported' AND origin='learner' AND baseline_revision=? ORDER BY created_at LIMIT 1",(current['revision'],)).fetchone()
    if not trial or momentum.number(trial['baseline_real_mean']) is None:return None
    changes=controls.validate(json.loads(trial['changes_json']))
    # Only proposed small filter tightenings can be promoted, never exposure,
    # stops, targets, cost assumptions, toggles or manual settings.
    allowed={'stock_min_price_usd':True,'stock_max_price_usd':False,'stock_max_float_millions':False,
        'stock_min_volume_ratio':True,'stock_min_time_volume_ratio':True,'stock_min_average_volume':True,
        'stock_max_spread_percent':False,'stock_min_day_change_percent':True,'stock_max_atr_percent':False,'stock_max_vwap_distance_percent':False}
    if len(changes)!=1:return None
    for key,value in changes.items():
        old=current['settings'].get(key)
        if key not in allowed or not old or not (old<value<=old*1.100001 if allowed[key] else old*.899999<=value<old):return None
    receipt=controls._apply(conn,current,changes,str(uuid.uuid4()),'stock_parallel_promote')
    audit=receipt['receipt']
    conn.execute('INSERT INTO stock_learning_experiments(change_id,created_at,revision_after,settings_json,before_json,after_json,baseline_mean,state) VALUES(?,?,?,?,?,?,?,?)',
        (audit['id'],audit['created_at'],receipt['revision'],json.dumps(receipt['settings']),json.dumps(audit['before']),json.dumps(audit['after']),trial['baseline_real_mean'],'pending'))
    evidence=json.loads(trial['decision_json'])
    conn.execute("UPDATE stock_shadow_trials SET state='promoted',decision_json=? WHERE id=?",(json.dumps({**evidence,'change_id':audit['id'],'before':audit['before'],'after':audit['after']}),trial['id']))
    import stock_learning_mail
    stock_learning_mail.enqueue(conn,'promotion-'+audit['id'],{'kind':'promotion','before':audit['before'],'after':audit['after'],
        'reason':'Prospective comparison supported the learned proposal. Real PAPER-trade follow-up and rollback remain active.',
        'trial_id':trial['id'],'evidence':evidence})
    return receipt


def step(request,*,now=None):
    from stock_auto_learning import initialize as init_learning
    import stock_intraday
    clock=(lambda:now) if now is not None else time.time
    stamp=clock();local=datetime.fromtimestamp(stamp,ET)
    with closing(controls.connect()) as conn:
        init_learning(conn);conn.execute('BEGIN IMMEDIATE');current=controls._snapshot(conn)
        evaluate_trials(conn,current)
        promoted=promote(conn,current)
        if promoted:current=controls._snapshot(conn)
        if current['settings']['stock_parallel_validation_enabled']:seed(conn,current,stamp)
        conn.commit()
        trials=[dict(t) for t in conn.execute("SELECT * FROM stock_shadow_trials WHERE state='collecting' AND baseline_revision=? ORDER BY created_at DESC LIMIT 4",(current['revision'],))]
        active=[dict(r) for r in conn.execute("SELECT id,symbol,due_at FROM stock_shadow_opportunities WHERE state='open'")]
    def finish(state,details=None):
        sampling_state(state,clock(),details);return bool(promoted)
    if local.weekday()>=5 or not 570<=local.hour*60+local.minute<=965:return finish('outside_session')
    observed=quotes(request,[r['symbol'] for r in active if r['due_at']<=stamp<=r['due_at']+GRACE],stamp,clock=clock)
    with closing(controls.connect()) as conn:
        settle(conn,observed,clock());conn.commit()
    if not current['settings']['stock_parallel_validation_enabled']:return finish('paused')
    if not trials:return finish('no_collecting_trials')
    if len(active)>=100:return finish('at_capacity')
    if local.hour*60+local.minute>=940:return finish('late_session')
    try:candidates,criteria,queued_at=_queue.get_nowait()
    except Empty:return finish('waiting_for_scan')
    if not 0<=stamp-queued_at<=180:return finish('stale_scan')
    from entry_quality import evaluate_entry_quality
    details={'at':stamp,'scanner_count':len(candidates),'quality_eligible':0,'already_recorded':0,
             'selected':0,'fresh_quotes':0,'evaluated':0,'recorded':0,'failures':{},'examples':[]}
    def reject(reason):details['failures'][reason]=details['failures'].get(reason,0)+1
    eligible=[]
    for candidate in candidates:
        quality=evaluate_entry_quality(candidate,**criteria)
        if quality['passed'] and isinstance(candidate.get('symbol'),str):eligible.append(candidate)
        else:
            for reason in quality['failed_requirements']:reject(reason)
    # A previously admitted opportunity already has frozen decisions and its
    # exit quote will be sampled separately. Do not spend feature/quote work on
    # it again during the same 15-minute bucket.
    details['quality_eligible']=len(eligible)
    with closing(controls.connect()) as conn:
        recorded={r['symbol'] for r in conn.execute('SELECT symbol FROM stock_shadow_opportunities WHERE bucket=?',(int(stamp//HORIZON),))}
    details['already_recorded']=sum(c['symbol'] in recorded for c in eligible)
    eligible=list({c['symbol']:c for c in eligible if c['symbol'] not in recorded}.values())
    global _rotation
    if not eligible:return finish('already_sampled' if details['already_recorded'] else 'no_quality_candidates',details)
    selected=(eligible+eligible)[_rotation%len(eligible):_rotation%len(eligible)+min(3,len(eligible))];_rotation+=3
    details['selected']=len(selected)
    entry_quotes=quotes(request,[c['symbol'] for c in selected],stamp,clock=clock)
    for candidate in selected:
        symbol=candidate['symbol'];quote=entry_quotes.get(symbol)
        attempt={'symbol':symbol};details['examples'].append(attempt)
        if not quote:
            reject('fresh_entry_quote_unavailable');attempt['state']='fresh_entry_quote_unavailable';continue
        details['fresh_quotes']+=1
        try:
            values=momentum.features(candidate,quote,current['settings'],symbol=symbol,clock=clock,
                intraday_fetch=lambda name:stock_intraday.fetch(name,request,clock=clock))
        except Exception:
            reject('feature_lookup_failed');attempt['state']='feature_lookup_failed';continue
        refreshed=quotes(request,[symbol],clock(),clock=clock).get(symbol)
        if not refreshed:
            reject('refreshed_quote_unavailable');attempt['state']='refreshed_quote_unavailable';continue
        quote=refreshed;entered_at=clock()
        values.update(price=quote['ask'],spread_percent=quote['spread_percent'],captured_at=entered_at,quote_at=quote['at'])
        vwap=momentum.number((values.get('intraday') or {}).get('vwap'))
        if vwap and vwap>0:values['vwap_distance_percent']=(quote['ask']/vwap-1)*100
        with closing(controls.connect()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            # Evidence fetched outside locks; recheck the saved revision before
            # admitting an opportunity to the frozen prospective cohort.
            if controls._snapshot(conn)['revision']==current['revision']:
                details['evaluated']+=1
                opportunity=record_opportunity(conn,trials,symbol,values,quote,entered_at,diagnostic=attempt)
                if opportunity:details['recorded']+=1
                elif attempt.get('state')=='filters_rejected':
                    for reason in attempt.get('baseline_failures',[]):reject(reason)
                elif attempt.get('state')=='duplicate':reject('duplicate_opportunity')
            else:
                reject('settings_changed');attempt['state']='settings_changed'
            conn.commit()
    return finish('sampled' if details['recorded'] else 'no_opportunities_recorded',details)


def status(conn):
    trials=[]
    for row in conn.execute('SELECT * FROM stock_shadow_trials ORDER BY created_at DESC,rowid DESC LIMIT 12'):
        trials.append({'id':row['id'],'created_at':row['created_at'],'origin':row['origin'],'state':row['state'],
            'changes':json.loads(row['changes_json']),'baseline':{k:json.loads(row['baseline_json'])[k] for k in json.loads(row['changes_json'])},
            'stats':trial_stats(conn,row),'decision':json.loads(row['decision_json'])})
    runtime=conn.execute('SELECT * FROM stock_research_runtime WHERE id=1').fetchone()
    sampling=conn.execute('SELECT * FROM stock_research_sampling WHERE id=1').fetchone()
    diagnostic={**dict(sampling),'last_batch':json.loads(sampling['details_json'])} if sampling else None
    if diagnostic:diagnostic.pop('details_json',None)
    return {'runtime':dict(runtime) if runtime else None,'sampling':diagnostic,'trials':trials,'minimum_pairs':MIN_PAIRS,'minimum_sessions':MIN_DAYS,'notional_usd':NOTIONAL,
        'horizon_minutes':15,'basis':'Prospective paired 15-minute quote markouts: entry ask, later bid, plus extra estimated costs. Same opportunity/time for both filters; a rejected opportunity earns zero. Uses the same fixed base scanner-quality gate for both arms; does not reproduce production adaptive/regime/news score adjustments, portfolio capacity, protective exits or executable fills. No orders. Late/missing quotes are excluded and counted. IEX is one venue. Seeded challengers are research-only; learned proposals need independent future evidence before promotion.'}


async def worker(request,invalidate):
    import asyncio
    while True:
        try:
            changed=await asyncio.to_thread(step,request)
            if changed:invalidate()
            with closing(controls.connect()) as conn:
                conn.execute('INSERT INTO stock_research_runtime VALUES(1,?,NULL) ON CONFLICT(id) DO UPDATE SET last_tick=excluded.last_tick,last_error=NULL',(time.time(),));conn.commit()
        except Exception:
            try:
                with closing(controls.connect()) as conn:
                    initialize(conn)
                    conn.execute("INSERT INTO stock_research_runtime VALUES(1,?,'Research data processing failed; retrying without placing orders') ON CONFLICT(id) DO UPDATE SET last_tick=excluded.last_tick,last_error=excluded.last_error",(time.time(),));conn.commit()
            except Exception:pass
        await asyncio.sleep(30)
