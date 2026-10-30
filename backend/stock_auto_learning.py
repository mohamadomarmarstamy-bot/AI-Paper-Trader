"""Bounded, audited 25-close PAPER parameter experiments; no broker access."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import json
from statistics import mean,median
import uuid
import app_controls as controls
from stock_momentum import number

BATCH_SIZE=25
SCHEMA='''CREATE TABLE IF NOT EXISTS stock_entry_evidence(order_id TEXT PRIMARY KEY,symbol TEXT NOT NULL,
 captured_at REAL NOT NULL,settings_revision TEXT NOT NULL,features_json TEXT NOT NULL,settings_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stock_learning_state(id INTEGER PRIMARY KEY CHECK(id=1),cursor INTEGER NOT NULL,status TEXT NOT NULL,last_error TEXT);
CREATE TABLE IF NOT EXISTS stock_learning_batches(id TEXT PRIMARY KEY,cursor_after INTEGER UNIQUE NOT NULL,
 created_at TEXT NOT NULL,report_json TEXT NOT NULL,change_id TEXT);'''


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


def outcomes(conn,cursor,limit):
    if not all(conn.execute('SELECT 1 FROM sqlite_master WHERE name=?',(name,)).fetchone() for name in ('learning_outcomes','trade_book')):return []
    rows=conn.execute('''SELECT lo.id,lo.symbol,lo.trade_book_id,lo.entry_price,lo.entry_score,lo.spread_percent,
      lo.realized_profit_loss,lo.realized_return_percent,e.features_json,e.settings_json
      FROM learning_outcomes lo JOIN trade_book t ON t.id=lo.trade_book_id
      LEFT JOIN stock_entry_evidence e ON e.order_id=t.entry_order_id
      WHERE lo.id>? AND t.status='CLOSED' AND t.entry_reason='auto_trader_entry'
      ORDER BY lo.id LIMIT ?''',(cursor,limit)).fetchall()
    result=[]
    for row in rows:
        item=dict(row)
        item['features']=json.loads(item.pop('features_json')) if item['features_json'] else {
            'price':item['entry_price'],'score':item['entry_score'],'spread_percent':item['spread_percent']}
        item['entry_settings']=json.loads(item.pop('settings_json')) if item['settings_json'] else None
        result.append(item)
    return result


def plan(rows,settings):
    """Select one small tightening using training rows and a later validation slice."""
    valid=[r for r in rows if number(r.get('realized_return_percent')) is not None and number(r.get('realized_profit_loss')) is not None]
    winners=[r for r in valid if r['realized_profit_loss']>0];losers=[r for r in valid if r['realized_profit_loss']<0]
    report={'closed_count':len(rows),'wins':len(winners),'losses':len(losers),
            'realized_pnl_usd':round(sum(r['realized_profit_loss'] for r in valid),4),
            'basis':'25 recorded completed PAPER trades. Small observational sample; recorded returns do not fully model fees or execution costs.',
            'comparisons':[],'changed':False}
    train=valid[:17];validation=valid[17:];options=[]
    specifications=[('price','stock_min_price_usd',True),('price','stock_max_price_usd',False),
        ('float_shares','stock_max_float_millions',False),('volume_ratio','stock_min_volume_ratio',True),
        ('average_volume','stock_min_average_volume',True),('spread_percent','stock_max_spread_percent',False),
        ('day_change_percent','stock_min_day_change_percent',True),('atr_percent','stock_max_atr_percent',False)]
    for feature,key,lower in specifications:
        factor=1e6 if feature=='float_shares' else 1
        win=[number(r['features'].get(feature)) for r in winners];lose=[number(r['features'].get(feature)) for r in losers]
        win=[v/factor for v in win if v is not None];lose=[v/factor for v in lose if v is not None]
        report['comparisons'].append({'feature':feature,'winner_count':len(win),'loser_count':len(lose),
            'winner_median':median(win) if win else None,'loser_median':median(lose) if lose else None,
            'units':'millions of shares' if factor>1 else feature})
        if not settings['stock_filters_enabled'] or not settings[key] or len(win)<3 or len(lose)<3:continue
        # Train/validate only comparable current exit policies. Old/missing
        # snapshots can inform risk reduction, but cannot justify filter fitting.
        if any(not r['entry_settings'] or any(r['entry_settings'].get(k)!=settings[k] for k in ('stop_loss_percent','take_profit_percent')) for r in valid):continue
        training_wins=[number(r['features'].get(feature)) for r in train if r['realized_profit_loss']>0]
        training_losses=[number(r['features'].get(feature)) for r in train if r['realized_profit_loss']<0]
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
        improvement=mean(r['realized_return_percent'] for r in held)-mean(r['realized_return_percent'] for r in validation)
        if improvement<=.05 or mean(r['realized_return_percent'] for r in held)<=0:continue
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
    if len(valid)==BATCH_SIZE and report['realized_pnl_usd']<0 and settings['position_size_percent']>.1:
        size=round(max(.1,settings['position_size_percent']*.9),6)
        report.update(reason='Losing 25-trade batch without validated filter improvement: reduce future position size by 10%.',changed=True)
        return {'position_size_percent':size},report
    report['reason']='No validated filter improvement; retain settings.' if report['realized_pnl_usd']>=0 else 'Position-size floor reached; no unsubstantiated filter change.'
    return {},report


def review():
    with closing(controls.connect()) as conn:
        initialize(conn);conn.execute('BEGIN IMMEDIATE')
        current=controls._snapshot(conn);state=conn.execute('SELECT * FROM stock_learning_state WHERE id=1').fetchone()
        if not current['settings']['stock_auto_adjust_enabled']:
            conn.execute("UPDATE stock_learning_state SET cursor=?,status='Automatic adjustment paused',last_error=NULL WHERE id=1",(latest_id(conn),));conn.commit();return None
        rows=outcomes(conn,state['cursor'],BATCH_SIZE)
        if len(rows)<BATCH_SIZE:
            conn.execute('UPDATE stock_learning_state SET status=?,last_error=NULL WHERE id=1',(f"Next automatic review: {len(rows)}/25 completed stock trades",));conn.commit();return None
        changes,report=plan(rows,current['settings']);change_id=None
        if changes:
            receipt=controls._apply(conn,current,controls.validate(changes),str(uuid.uuid4()),'stock_auto_25')
            change_id=receipt['receipt']['id'];report.update(before=receipt['receipt']['before'],after=receipt['receipt']['after'])
        report.update(outcome_ids=[r['id'] for r in rows],symbols=[r['symbol'] for r in rows])
        conn.execute('INSERT INTO stock_learning_batches VALUES(?,?,?,?,?)',
            (str(uuid.uuid4()),rows[-1]['id'],datetime.now(timezone.utc).isoformat(),json.dumps(report,allow_nan=False),change_id))
        conn.execute('UPDATE stock_learning_state SET cursor=?,status=?,last_error=NULL WHERE id=1',
            (rows[-1]['id'],'Applied automatic settings change' if changes else report['reason']))
        conn.commit()
    return report


def status():
    with closing(controls.connect()) as conn:
        initialize(conn);state=dict(conn.execute('SELECT * FROM stock_learning_state WHERE id=1').fetchone())
        rows=conn.execute('SELECT created_at,report_json,change_id FROM stock_learning_batches ORDER BY rowid DESC LIMIT 10').fetchall()
        return {**state,'batch_size':BATCH_SIZE,'pending_closed_count':len(outcomes(conn,state['cursor'],BATCH_SIZE)),
                'reviews':[{**json.loads(r['report_json']),'created_at':r['created_at'],'change_id':r['change_id']} for r in rows]}


async def worker(invalidate):
    while True:
        try:
            result=await asyncio.to_thread(review)
            if result and result['changed']:invalidate()
        except Exception:
            try:
                with closing(controls.connect()) as conn:
                    initialize(conn);conn.execute("UPDATE stock_learning_state SET last_error='Automatic review failed; retrying. No uncommitted change was applied.' WHERE id=1");conn.commit()
            except Exception:pass
        await asyncio.sleep(30)
