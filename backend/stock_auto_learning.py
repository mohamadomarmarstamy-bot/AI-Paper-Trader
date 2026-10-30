"""Bounded, audited 25-close PAPER parameter experiments; no broker access."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import json
from statistics import mean,median
import uuid
import time
from queue import Queue, Empty, Full
_observation_queue=Queue(maxsize=1)
import app_controls as controls
from stock_momentum import number
import stock_learning_mail as review_mail

BATCH_SIZE=25
SCHEMA='''CREATE TABLE IF NOT EXISTS stock_entry_evidence(order_id TEXT PRIMARY KEY,symbol TEXT NOT NULL,
 captured_at REAL NOT NULL,settings_revision TEXT NOT NULL,features_json TEXT NOT NULL,settings_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS stock_entry_revision_idx ON stock_entry_evidence(settings_revision,captured_at);
CREATE TABLE IF NOT EXISTS stock_learning_state(id INTEGER PRIMARY KEY CHECK(id=1),cursor INTEGER NOT NULL,status TEXT NOT NULL,last_error TEXT);
CREATE TABLE IF NOT EXISTS stock_learning_batches(id TEXT PRIMARY KEY,cursor_after INTEGER UNIQUE NOT NULL,
 created_at TEXT NOT NULL,report_json TEXT NOT NULL,change_id TEXT);
CREATE TABLE IF NOT EXISTS stock_learning_experiments(change_id TEXT PRIMARY KEY,created_at TEXT NOT NULL,
 revision_after TEXT NOT NULL,settings_json TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,
 baseline_mean REAL NOT NULL,state TEXT NOT NULL,result_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS stock_exit_observations(trade_book_id INTEGER PRIMARY KEY,
 first_at REAL NOT NULL,last_at REAL NOT NULL,samples INTEGER NOT NULL,
 high_return REAL NOT NULL,low_return REAL NOT NULL,shadow_json TEXT NOT NULL);'''+review_mail.SCHEMA


def latest_id(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='learning_outcomes'").fetchone():return 0
    return conn.execute('SELECT COALESCE(MAX(id),0) FROM learning_outcomes').fetchone()[0]


def initialize(conn):
    conn.executescript(SCHEMA)
    conn.execute('INSERT OR IGNORE INTO stock_learning_state VALUES(1,?,?,NULL)',(latest_id(conn),'Waiting for 25 new completed stock trades'))
    conn.commit()


def record_entry(order_id,symbol,features,snapshot):
    if not isinstance(order_id,str) or not order_id or len(order_id)>200:raise ValueError('Broker entry order ID required.')
    with closing(controls.connect()) as conn:
        initialize(conn)
        conn.execute('INSERT OR IGNORE INTO stock_entry_evidence VALUES(?,?,?,?,?,?)',
            (order_id,symbol,features['captured_at'],snapshot['revision'],json.dumps(features,allow_nan=False),json.dumps(snapshot['settings'],allow_nan=False)))
        conn.commit()


def outcomes(conn,cursor,limit,*,revision=None,entered_after=None):
    if not all(conn.execute('SELECT 1 FROM sqlite_master WHERE name=?',(name,)).fetchone() for name in ('learning_outcomes','trade_book')):return []
    rows=conn.execute('''SELECT lo.id,lo.symbol,lo.trade_book_id,lo.entry_price,lo.entry_score,lo.spread_percent,
      lo.realized_profit_loss,lo.realized_return_percent,lo.shares,lo.holding_seconds,lo.exit_reason,
      e.features_json,e.settings_json,x.samples,x.high_return,x.low_return,x.shadow_json
      FROM learning_outcomes lo JOIN trade_book t ON t.id=lo.trade_book_id
      LEFT JOIN stock_entry_evidence e ON e.order_id=t.entry_order_id
      LEFT JOIN stock_exit_observations x ON x.trade_book_id=t.id
      WHERE lo.id>? AND t.status='CLOSED' AND t.entry_reason='auto_trader_entry'
      AND (? IS NULL OR e.settings_revision=?) AND (? IS NULL OR e.captured_at>=?)
      ORDER BY lo.id LIMIT ?''',(cursor,revision,revision,entered_after,entered_after,limit)).fetchall()
    result=[]
    for row in rows:
        item=dict(row)
        item['features']=json.loads(item.pop('features_json')) if item['features_json'] else {
            'price':item['entry_price'],'score':item['entry_score'],'spread_percent':item['spread_percent']}
        item['entry_settings']=json.loads(item.pop('settings_json')) if item['settings_json'] else None
        item['shadow']=json.loads(item.pop('shadow_json')) if item['shadow_json'] else {}
        result.append(item)
    return result


def plan(rows,settings):
    """Select one small tightening using training rows and a later validation slice."""
    closed_count=len(rows)
    rows=cost_adjusted(rows,settings)
    valid=[r for r in rows if number(r.get('realized_return_percent')) is not None and number(r.get('realized_profit_loss')) is not None]
    winners=[r for r in valid if r['estimated_net_pnl_usd']>0];losers=[r for r in valid if r['estimated_net_pnl_usd']<0]
    report={'closed_count':closed_count,'costed_count':len(valid),'wins':len(winners),'losses':len(losers),
            'realized_pnl_usd':round(sum(r['realized_profit_loss'] for r in valid),4),
            'basis':'25 completed PAPER trades; estimated net results subtract configurable incremental slippage and fees from broker-recorded fills. Estimates are not live execution.',
            'estimated_net_pnl_usd':round(sum(r['estimated_net_pnl_usd'] for r in valid),4),
            'estimated_net_mean_return_percent':mean(r['net_return_percent'] for r in valid) if valid else None,
            'cost_model':cost_model(settings),'exit_analysis':exit_analysis(valid,settings),
            'comparisons':[],'changed':False}
    train=valid[:17];validation=valid[17:];options=[]
    specifications=[('price','stock_min_price_usd',True),('price','stock_max_price_usd',False),
        ('float_shares','stock_max_float_millions',False),('volume_ratio','stock_min_volume_ratio',True),
        ('average_volume','stock_min_average_volume',True),('spread_percent','stock_max_spread_percent',False),
        ('day_change_percent','stock_min_day_change_percent',True),('atr_percent','stock_max_atr_percent',False),
        ('time_volume_ratio','stock_min_time_volume_ratio',True),('vwap_distance_percent','stock_max_vwap_distance_percent',False)]
    for feature,key,lower in specifications:
        factor=1e6 if feature=='float_shares' else 1
        win=[number(r['features'].get(feature)) for r in winners];lose=[number(r['features'].get(feature)) for r in losers]
        win=[v/factor for v in win if v is not None];lose=[v/factor for v in lose if v is not None]
        report['comparisons'].append({'feature':feature,'winner_count':len(win),'loser_count':len(lose),
            'winner_median':median(win) if win else None,'loser_median':median(lose) if lose else None,
            'units':'millions of shares' if factor>1 else feature})
        if key=='stock_min_volume_ratio' and settings['stock_intraday_enabled']:continue
        if key in ('stock_min_time_volume_ratio','stock_max_vwap_distance_percent') and not settings['stock_intraday_enabled']:continue
        if not settings['stock_filters_enabled'] or not settings[key] or len(win)<3 or len(lose)<3:continue
        # Train/validate only comparable current exit policies. Old/missing
        # snapshots can inform risk reduction, but cannot justify filter fitting.
        if any(not r['entry_settings'] or any(r['entry_settings'].get(k)!=settings[k] for k in settings if not k.startswith('notify_') and k not in ('pause_new_entries','stock_auto_adjust_enabled','stock_auto_rollback_enabled')) for r in valid):continue
        training_wins=[number(r['features'].get(feature)) for r in train if r['estimated_net_pnl_usd']>0]
        training_losses=[number(r['features'].get(feature)) for r in train if r['estimated_net_pnl_usd']<0]
        tw=[v/factor for v in training_wins if v is not None];tl=[v/factor for v in training_losses if v is not None]
        if len(tw)<3 or len(tl)<3:continue
        if (median(tw)<=median(tl) if lower else median(tw)>=median(tl)):continue
        current=settings[key];proposed=round(current*(1.1 if lower else .9),6)
        if lower:proposed=min(proposed,(median(tw)+median(tl))/2)
        else:proposed=max(proposed,(median(tw)+median(tl))/2)
        proposed=round(proposed,6)
        if (proposed<=current if lower else proposed>=current):continue
        def kept(group):
            return [r for r in group if number(r['features'].get(feature)) is not None and
                    (r['features'][feature]/factor>=proposed if lower else r['features'][feature]/factor<=proposed)]
        retained=kept(train);held=kept(validation)
        if len(retained)<10 or len(held)<4 or len(validation)<8:continue
        improvement=mean(r['net_return_percent'] for r in held)-mean(r['net_return_percent'] for r in validation)
        if improvement<=.05 or mean(r['net_return_percent'] for r in held)<=0:continue
        candidate={key:proposed}
        try:
            controls.validate(candidate)
            merged={**settings,**candidate}
            if merged['stock_max_price_usd'] and merged['stock_min_price_usd']>merged['stock_max_price_usd']:continue
            if merged['stock_max_float_millions'] and merged['stock_min_float_millions']>merged['stock_max_float_millions']:continue
        except ValueError:continue
        options.append((improvement,key,proposed,len(held)))
    if options:
        improvement,key,value,count=max(options)
        report.update(reason='Winner/loser feature contrast improved the later validation slice; apply one bounded PAPER experiment.',
                      validation_improvement_percent=round(improvement,4),validation_retained=count,changed=True)
        return {key:value},report
    # A losing batch still has a concrete, restrained consequence. Never raise
    # exposure or loosen stops/loss limits merely to force a setting change.
    if len(valid)==BATCH_SIZE and report['estimated_net_pnl_usd']<0 and settings['position_size_percent']>.1:
        size=round(max(.1,settings['position_size_percent']*.9),6)
        report.update(reason='Losing cost-adjusted 25-trade batch without validated filter improvement: reduce future position size by 10%.',changed=True)
        return {'position_size_percent':size},report
    report['reason']='No validated filter improvement; retain settings.' if report['estimated_net_pnl_usd']>=0 else 'Position-size floor reached; no unsubstantiated filter change.'
    return {},report


def cost_model(settings):
    return {key:settings[key] for key in ('stock_slippage_bps_per_side','stock_fee_bps_per_side')}


def cost_adjusted(rows,settings):
    cost_percent=2*sum(cost_model(settings).values())/100
    result=[]
    for original in rows:
        row={**original}
        ret=number(row.get('realized_return_percent'));pnl=number(row.get('realized_profit_loss'))
        price=number(row.get('entry_price'));shares=number(row.get('shares'))
        notional=price*shares if price is not None and shares is not None and price>0 and shares>0 else None
        if ret is None or pnl is None or notional is None:continue
        row.update(net_return_percent=ret-cost_percent,estimated_net_pnl_usd=pnl-notional*cost_percent/100)
        result.append(row)
    return result


def enqueue_positions(positions):
    """Copy marks without database/network I/O in the protection loop."""
    copied=([(p.get('symbol'),p.get('current_price')) for p in positions],time.time())
    try:_observation_queue.get_nowait()
    except Empty:pass
    try:_observation_queue.put_nowait(copied)
    except Full:pass


def drain_observations():
    try:positions,stamp=_observation_queue.get_nowait()
    except Empty:return
    observe_positions(positions,now=stamp)


def observe_position(symbol,price,*,now=None):
    """Sample broker marks; never changes an order or position."""
    observe_positions([(symbol,price)],now=now)


def observe_positions(positions,*,now=None):
    stamp=now if now is not None else time.time()
    with closing(controls.connect()) as conn:
        initialize(conn)
        for symbol,price in positions:
            price=number(price)
            if price is None or price<=0:continue
            rows=conn.execute("SELECT id,entry_price FROM trade_book WHERE symbol=? AND status='OPEN' AND entry_reason='auto_trader_entry'",(symbol,)).fetchall()
            for trade in rows:
                if trade['entry_price']<=0:continue
                ret=(price/trade['entry_price']-1)*100
                prior=conn.execute('SELECT * FROM stock_exit_observations WHERE trade_book_id=?',(trade['id'],)).fetchone()
                if prior and stamp<=prior['last_at']:continue
                shadow=json.loads(prior['shadow_json']) if prior else {}
                for stop,target in ((1,2),(1.5,3),(2,4)):
                    key=f'{stop:g}% stop / {target:g}% target'
                    if key not in shadow and (ret<=-stop or ret>=target):shadow[key]=ret
                conn.execute('INSERT INTO stock_exit_observations VALUES(?,?,?,?,?,?,?) ON CONFLICT(trade_book_id) DO UPDATE SET last_at=excluded.last_at,samples=excluded.samples,high_return=excluded.high_return,low_return=excluded.low_return,shadow_json=excluded.shadow_json',
                    (trade['id'],prior['first_at'] if prior else stamp,stamp,(prior['samples'] if prior else 0)+1,
                     max(prior['high_return'] if prior else 0,ret),min(prior['low_return'] if prior else 0,ret),json.dumps(shadow)))
        conn.commit()


def exit_analysis(rows,settings):
    observed=[r for r in rows if (r.get('samples') or 0)>0]
    cost=2*sum(cost_model(settings).values())/100
    return {'observed_trades':len(observed),'total_trades':len(rows),
            'basis':'Sampled broker marks, not complete tick paths. Shadow exits use the observed mark when crossed, otherwise the actual closing return. They do not change orders or establish achievable fills.',
            'median_observed_best_return_percent':median(max(r['high_return'],r['realized_return_percent']) for r in observed) if observed else None,
            'median_observed_worst_return_percent':median(min(r['low_return'],r['realized_return_percent']) for r in observed) if observed else None,
            'median_giveback_percent':median(max(0,r['high_return']-r['realized_return_percent']) for r in observed) if observed else None,
            'actual_net_mean_return_percent':mean(r['net_return_percent'] for r in observed) if observed else None,
            'shadow_exits':[{'policy':key,'trades':len(observed),
                'estimated_net_mean_return_percent':mean(r['shadow'].get(key,r['realized_return_percent'])-cost for r in observed) if observed else None}
                for key in ('1% stop / 2% target','1.5% stop / 3% target','2% stop / 4% target')]}


def experiment_rows(conn,experiment):
    return outcomes(conn,0,BATCH_SIZE,revision=experiment['revision_after'],
                    entered_after=datetime.fromisoformat(experiment['created_at']).timestamp())



def evaluate_experiment(conn,current):
    experiment=conn.execute("SELECT * FROM stock_learning_experiments WHERE state='pending' ORDER BY rowid LIMIT 1").fetchone()
    if not experiment:return None,False
    if current['revision']!=experiment['revision_after']:
        conn.execute("UPDATE stock_learning_experiments SET state='superseded',result_json=? WHERE change_id=?",
            (json.dumps({'reason':'A newer saved settings change superseded this experiment; no automatic rollback.'}),experiment['change_id']))
        return None,False
    rows=experiment_rows(conn,experiment)
    if len(rows)<BATCH_SIZE:return {'state':'pending','completed':len(rows),'required':BATCH_SIZE},True
    adjusted=cost_adjusted(rows,current['settings'])
    if len(adjusted)!=BATCH_SIZE:return {'state':'pending','completed':len(adjusted),'required':BATCH_SIZE},True
    later=mean(r['net_return_percent'] for r in adjusted)
    worse=later<experiment['baseline_mean']-.1
    revert=worse and current['settings']['stock_auto_rollback_enabled']
    result={'baseline_net_mean_return_percent':experiment['baseline_mean'],'followup_net_mean_return_percent':later,
            'outcome_ids':[r['id'] for r in rows], 'reason':'Follow-up mean return worsened by more than 0.1 percentage point; restore the prior filter.' if revert else 'Follow-up review complete; retain filter.' if not worse else 'Performance worsened; automatic rollback is disabled.'}
    if revert:
        receipt=controls._apply(conn,current,controls.validate(json.loads(experiment['before_json'])),str(uuid.uuid4()),'stock_auto_rollback')
        result.update(before=receipt['receipt']['before'],after=receipt['receipt']['after'],change_id=receipt['receipt']['id'])
    conn.execute('UPDATE stock_learning_experiments SET state=?,result_json=? WHERE change_id=?',
                 ('reverted' if revert else 'kept',json.dumps(result,allow_nan=False),experiment['change_id']))
    return {**result,'state':'reverted' if revert else 'kept','changed':revert},True


def review():
    with closing(controls.connect()) as conn:
        initialize(conn);conn.execute('BEGIN IMMEDIATE')
        current=controls._snapshot(conn);state=conn.execute('SELECT * FROM stock_learning_state WHERE id=1').fetchone()
        if not current['settings']['stock_auto_adjust_enabled']:
            conn.execute("UPDATE stock_learning_state SET cursor=?,status='Automatic adjustment paused',last_error=NULL WHERE id=1",(latest_id(conn),));conn.commit();return None
        experiment,locked=evaluate_experiment(conn,current)
        rows=outcomes(conn,state['cursor'],BATCH_SIZE)
        if len(rows)<BATCH_SIZE:
            if experiment and experiment.get('changed'):
                review_mail.enqueue(conn,'rollback-'+experiment['change_id'],{'kind':'rollback',**experiment})
            conn.execute('UPDATE stock_learning_state SET status=?,last_error=NULL WHERE id=1',(f"Next automatic review: {len(rows)}/25 completed stock trades",));conn.commit()
            return experiment if experiment and experiment.get('changed') else None
        changes,report=plan(rows,current['settings']);change_id=None
        if locked:
            changes={};report.update(changed=False,reason='Follow-up experiment review: '+experiment['state']+'. No additional parameter experiment in this batch.')
        if changes:
            receipt=controls._apply(conn,current,controls.validate(changes),str(uuid.uuid4()),'stock_auto_25')
            change_id=receipt['receipt']['id'];report.update(before=receipt['receipt']['before'],after=receipt['receipt']['after'])
            # Position-size cuts are defensive controls, not reversible filter
            # experiments: automatic rollback must never increase exposure.
            if 'position_size_percent' not in changes:
                conn.execute('INSERT INTO stock_learning_experiments(change_id,created_at,revision_after,settings_json,before_json,after_json,baseline_mean,state) VALUES(?,?,?,?,?,?,?,?)',
                    (change_id,receipt['receipt']['created_at'],receipt['revision'],json.dumps(receipt['settings']),
                     json.dumps(receipt['receipt']['before']),json.dumps(receipt['receipt']['after']),report['estimated_net_mean_return_percent'],'pending'))
        report.update(outcome_ids=[r['id'] for r in rows],symbols=[r['symbol'] for r in rows])
        batch_id=str(uuid.uuid4());reviewed_at=datetime.now(timezone.utc).isoformat()
        batch_number=conn.execute('SELECT COUNT(*)+1 FROM stock_learning_batches').fetchone()[0]
        if experiment and experiment.get('changed'):report['rollback']=experiment
        conn.execute('INSERT INTO stock_learning_batches VALUES(?,?,?,?,?)',
            (batch_id,rows[-1]['id'],reviewed_at,json.dumps(report,allow_nan=False),change_id))
        review_mail.enqueue(conn,'batch-'+batch_id,{'id':batch_id,'kind':'batch','batch_number':batch_number,
            'reviewed_at':reviewed_at,'report':report,'rollback':report.get('rollback')})
        conn.execute('UPDATE stock_learning_state SET cursor=?,status=?,last_error=NULL WHERE id=1',
            (rows[-1]['id'],'Applied automatic settings change' if changes else report['reason']))
        conn.commit()
    if experiment and experiment.get('changed'):report['changed']=True
    return report


def status():
    with closing(controls.connect()) as conn:
        initialize(conn);state=dict(conn.execute('SELECT * FROM stock_learning_state WHERE id=1').fetchone())
        rows=conn.execute("SELECT b.created_at,b.report_json,b.change_id,m.state AS email_status FROM stock_learning_batches b LEFT JOIN stock_learning_mail m ON m.id='batch-'||b.id ORDER BY b.rowid DESC LIMIT 10").fetchall()
        experiments=conn.execute('SELECT * FROM stock_learning_experiments ORDER BY rowid DESC LIMIT 10').fetchall()
        return {**state,'email':review_mail.summary(conn),'experiments':[{'change_id':e['change_id'],'created_at':e['created_at'],'state':e['state'],
                'before':json.loads(e['before_json']),'after':json.loads(e['after_json']),
                'followup_count':len(experiment_rows(conn,e)) if e['state']=='pending' else len(json.loads(e['result_json']).get('outcome_ids',[])),
                'result':json.loads(e['result_json'])} for e in experiments],'batch_size':BATCH_SIZE,'pending_closed_count':len(outcomes(conn,state['cursor'],BATCH_SIZE)),
                'reviews':[{**json.loads(r['report_json']),'created_at':r['created_at'],'change_id':r['change_id'],'email_status':r['email_status']} for r in rows]}


async def worker(invalidate):
    while True:
        try:
            await asyncio.to_thread(drain_observations)
        except Exception:pass  # Missing telemetry cannot block protection/reviews.
        try:
            result=await asyncio.to_thread(review)
            if result and result['changed']:invalidate()
        except Exception:
            try:
                with closing(controls.connect()) as conn:
                    initialize(conn);conn.execute("UPDATE stock_learning_state SET last_error='Automatic review failed; retrying. No uncommitted change was applied.' WHERE id=1");conn.commit()
            except Exception:pass
        try:
            await asyncio.to_thread(review_mail.deliver_one)
        except Exception:pass  # A committed review survives provider/network failures.
        await asyncio.sleep(30)
