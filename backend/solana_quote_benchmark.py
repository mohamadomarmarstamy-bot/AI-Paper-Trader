"""Background quote comparison. Never signs, simulates, submits or changes settings."""
import asyncio
from contextlib import closing
from decimal import Decimal
import json
import os
import time

import database
from solana_jupiter import build_quote, Deferred, RateLimited, Unavailable

SCHEMA='''CREATE TABLE IF NOT EXISTS solana_quote_benchmark_run(
 id INTEGER PRIMARY KEY CHECK(id=1),next_at REAL NOT NULL,lease_until REAL NOT NULL,sequence INTEGER NOT NULL);
INSERT OR IGNORE INTO solana_quote_benchmark_run VALUES(1,0,0,0);
CREATE TABLE IF NOT EXISTS solana_quote_benchmark(
 id INTEGER PRIMARY KEY,observed_at REAL NOT NULL,details_json TEXT NOT NULL);'''


def load_context(conn):
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    row=conn.execute('SELECT state_json FROM solana_real_run WHERE id=1').fetchone() if 'solana_real_run' in tables else None
    run=json.loads(row[0]) if row else {}
    return tables,run


def idle(owner,now):
    with closing(database.get_connection()) as conn:
        tables,run=load_context(conn)
        if run.get('wallet')!=owner or run.get('positions'):return False
        if 'solana_real_orders' in tables and conn.execute("SELECT 1 FROM solana_real_orders WHERE status IN ('preparing','signed','submitted','unknown') OR created_at>? LIMIT 1",(now-30,)).fetchone():return False
        if 'solana_live_source_events' in tables and conn.execute('SELECT 1 FROM solana_live_source_events WHERE observed_at>? LIMIT 1',(now-30,)).fetchone():return False
        return True


def candidates(conn,owner,now):
    rows=conn.execute("SELECT details_json FROM solana_real_orders WHERE status='confirmed' AND created_at>? ORDER BY created_at DESC LIMIT 30",(now-86400,)).fetchall()
    result=[];seen=set()
    for row in rows:
        o=json.loads(row[0]);p=o.get('prepared') or {};amount=p.get('input_amount')
        if o.get('wallet')!=owner or o.get('side') not in ('buy','sell') or type(amount) is not int or amount<=0:continue
        mint=o.get('mint');side=o['side'];key=(mint,side,amount)
        if not mint or key in seen:continue
        seen.add(key);result.append({'wallet':owner,'mint':mint,'side':side,'amount_raw':str(amount),'basis_order_id':o['id']})
    return result


def tick(*,quote=build_quote,clock=time.time,pause=time.sleep):
    if not os.getenv('JUPITER_API_KEY'):return
    now=clock()
    with closing(database.get_connection()) as conn:
        tables,run=load_context(conn);owner=run.get('wallet')
        if not owner or 'solana_real_orders' not in tables:return
    if not idle(owner,now):return
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA)
        # Persist cadence before making requests. Restarts and concurrent callers
        # cannot create a burst of duplicate research traffic.
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('UPDATE solana_quote_benchmark_run SET next_at=?,lease_until=?,sequence=sequence+1 WHERE id=1 AND next_at<=? AND lease_until<=?',(now+60,now+45,now,now)).rowcount!=1:return
        sequence=conn.execute('SELECT sequence FROM solana_quote_benchmark_run').fetchone()[0];conn.commit()
        choices=candidates(conn,owner,now)
    if not choices:return
    selected=choices[(sequence-1)%len(choices)]
    mode_order=('standard','fast') if sequence%2 else ('fast','standard')
    sample={**selected,'observed_at':now,'first_mode':mode_order[0],'results':{},'comparable':False,
            'quote_policy':{k:run['policy'][k] for k in ('slippage_bps','max_price_impact_percent','max_priority_fee_lamports')}}
    policy=dict(run['policy'])
    from solana_real_budget import SOL
    input_mint,output_mint=(SOL,selected['mint']) if selected['side']=='buy' else (selected['mint'],SOL)
    for mode in mode_order:
        if not idle(owner,clock()):
            sample['results'][mode]={'status':'deferred'};break
        try:
            q=quote(owner,input_mint,output_mint,int(selected['amount_raw']),policy,mode=mode,research=True)
            sample['results'][mode]={'status':'ok','request_ms':q['_request_ms'],'out_raw':q['outAmount'],
                                     'minimum_out_raw':q['otherAmountThreshold'],'at':q['_observed_at']}
        except RateLimited:
            sample['results'][mode]={'status':'rate_limited'};break
        except Deferred:
            sample['results'][mode]={'status':'deferred'};break
        except Unavailable:
            sample['results'][mode]={'status':'unavailable'}
        if mode==mode_order[0]:
            # At most two requests per minute. Avoid a burst on free-tier keys.
            pause(1.1)
    standard=sample['results'].get('standard',{});fast=sample['results'].get('fast',{})
    if standard.get('status')==fast.get('status')=='ok' and abs(standard['at']-fast['at'])<=5:
        sample['comparable']=True
        sample['fast_latency_change_ms']=fast['request_ms']-standard['request_ms']
        sample['fast_minimum_output_change_percent']=float((Decimal(fast['minimum_out_raw'])/Decimal(standard['minimum_out_raw'])-1)*100)
    with closing(database.get_connection()) as conn:
        conn.execute('INSERT INTO solana_quote_benchmark(observed_at,details_json) VALUES(?,?)',(clock(),json.dumps(sample,allow_nan=False)))
        conn.execute('DELETE FROM solana_quote_benchmark WHERE id NOT IN (SELECT id FROM solana_quote_benchmark ORDER BY id DESC LIMIT 2000)')
        conn.execute('UPDATE solana_quote_benchmark_run SET lease_until=0,next_at=MAX(next_at,?) WHERE id=1',(clock()+60,));conn.commit()


def public(conn):
    from solana_copy_performance import stats
    tables,run=load_context(conn)
    rows=[json.loads(r[0]) for r in conn.execute('SELECT details_json FROM solana_quote_benchmark ORDER BY id DESC LIMIT 2000')] if 'solana_quote_benchmark' in tables else []
    rows=[r for r in rows if r.get('wallet')==run.get('wallet')]
    pairs=[r for r in rows if r.get('comparable')]
    from solana_jupiter import _budget
    capacity=_budget.status()
    pending='solana_real_orders' in tables and conn.execute("SELECT 1 FROM solana_real_orders WHERE status IN ('preparing','signed','submitted','unknown') LIMIT 1").fetchone()
    if not os.getenv('JUPITER_API_KEY'):sampling='Jupiter key needed'
    elif run.get('positions'):sampling='Waiting for a flat wallet'
    elif pending:sampling='Live orders have priority'
    elif capacity['cooldown_seconds']:sampling='Jupiter cooldown active'
    elif capacity['recent_live_request'] or capacity['live_waiters'] or capacity['active_requests']:sampling='Waiting for idle quote capacity'
    elif 'solana_real_orders' not in tables or not candidates(conn,run.get('wallet'),time.time()):sampling='Waiting for a recent recorded trade'
    else:sampling='Sampling when the source feed and wallet are quiet'

    modes=[]
    for mode in ('standard','fast'):
        results=[r['results'][mode] for r in rows if mode in r.get('results',{})]
        attempted=[r for r in results if r['status']!='deferred']
        success=[r for r in attempted if r['status']=='ok']
        modes.append({'mode':mode,'attempts':len(attempted),'successful':len(success),
                      'rate_limited':sum(r['status']=='rate_limited' for r in attempted),
                      'unavailable':sum(r['status']=='unavailable' for r in attempted),
                      'latency_ms':stats([r['request_ms'] for r in success]),
                      'paired_latency_ms':stats([r['results'][mode]['request_ms'] for r in pairs])})
    return {'live_mode':'standard','sampling_status':sampling,'capacity':capacity,'samples':len(rows),'comparable_pairs':len(pairs),'modes':modes,
        'latency_change_ms':stats([r['fast_latency_change_ms'] for r in pairs]),
        'minimum_output_change_percent':stats([r['fast_minimum_output_change_percent'] for r in pairs]),
        'last_sample_at':rows[0]['observed_at'] if rows else None,
        'basis':'Read-only quotes at the last recorded real trade sizes. Two requests per minute at most, only while live holdings and pending orders are absent. Request order alternates; sequential quotes can reflect market movement. Pairs over five seconds apart are excluded. Quotes do not establish fill quality or profit. Live routing stays normal.',
        'recent':[{k:r[k] for k in ('observed_at','mint','side','amount_raw','first_mode','comparable','results')} for r in rows[:10]]}


async def worker():
    while True:
        try:await asyncio.to_thread(tick)
        except asyncio.CancelledError:raise
        except Exception:
            import logging
            logging.getLogger(__name__).warning('Quote benchmark deferred; live execution is unchanged')
        await asyncio.sleep(15)
