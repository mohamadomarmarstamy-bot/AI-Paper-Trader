"""Isolated, owner-activated live copy ledger. Disabled by default.

Verified RPC source buys enter independently of PAPER simulation, with admitted
PAPER events retained as a fallback. PAPER balances and positions are never imported. Orders are persisted before broadcast and all
uncertain submissions block further signing until reconciled.
"""
import asyncio
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal,ROUND_CEILING
import hashlib
import json
import time
import uuid
import database
from solana_execution_timing import measure, PROFILE
from solana_parallel_reads import read_many
from solana_meme_bot import Conflict
import solana_real_execution as execution
from solana_live_source_policy import paper_only
from solana_live_hours import entry_allowed
from solana_token_age import require_age, AgeUnavailable, MINIMUM_SECONDS

SCHEMA='''CREATE TABLE IF NOT EXISTS solana_real_run(
 id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,state_json TEXT NOT NULL,
 lease_owner TEXT,lease_until REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS solana_real_orders(
 id TEXT PRIMARY KEY,created_at REAL NOT NULL,status TEXT NOT NULL,details_json TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS solana_real_one_pending ON solana_real_orders((1))
 WHERE status IN ('preparing','signed','submitted','unknown');
CREATE TABLE IF NOT EXISTS solana_real_decisions(
 source_id TEXT PRIMARY KEY,observed_at REAL NOT NULL,reason TEXT NOT NULL);'''
DEFAULT_POLICY={'slippage_bps':100,'max_price_impact_percent':5,'max_priority_fee_lamports':100000}
PENDING=('preparing','signed','submitted','unknown')
_execution_loop = None
_source_wakeup = None


def notify_source_events():
    """Thread-safe hint only; the existing ledger/lease still admits every order."""
    loop,event=_execution_loop,_source_wakeup
    if loop is not None and event is not None:
        try:loop.call_soon_threadsafe(event.set)
        except RuntimeError:pass  # Loop shut down; persisted events remain available.


def initial():return {'enabled':False,'wallet':'','wallet_revision':None,'budget_revision':None,'started_at':None,
    'event_cursor':0,'live_source_cursor':0,'positions':{},'cooldowns':{},'realized_lamports':0,'last_cycle':None,'error':None,
    'policy':dict(DEFAULT_POLICY),'last_order_at':0,'balance_adjustments':[]}


def initialize(conn):
    conn.executescript(SCHEMA)
    conn.execute('INSERT OR IGNORE INTO solana_real_run VALUES(1,0,?,NULL,0)',(json.dumps(initial()),));conn.commit()


def load():
    with closing(database.get_connection()) as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_real_run'").fetchone()
        row=conn.execute('SELECT * FROM solana_real_run WHERE id=1').fetchone() if exists else None
    state=json.loads(row['state_json']) if row else initial()
    state.setdefault('live_source_cursor',0)
    return state,row['revision'] if row else 0


def orders(pending=False):
    with closing(database.get_connection()) as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_real_orders'").fetchone()
        if not exists:return []
        rows=conn.execute('SELECT details_json FROM solana_real_orders '+("WHERE status IN ('preparing','signed','submitted','unknown') " if pending else '')+'ORDER BY created_at DESC LIMIT 50').fetchall()
    return [json.loads(r[0]) for r in rows]


def accounting_pending(state):
    return bool(state.get('balance_adjustments') or any(p.get('balance_observation') or p.get('external_change') for p in state['positions'].values()))


def guard_wallet_change():
    state,_=load()
    if state['enabled'] or state['positions'] or state.get('balance_adjustments') or orders(True):raise Conflict('Pause real copying and finish every live position and pending transaction before changing wallets.')


def public():
    state,revision=load();pending=orders(True)
    latest=orders()
    with closing(database.get_connection()) as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_real_orders'").fetchone()
        count=conn.execute("SELECT COUNT(*) FROM solana_real_orders WHERE status='confirmed'").fetchone()[0] if exists else 0
        if exists:
            from solana_activity_details import enrich_orders
            latest=enrich_orders(conn,latest,state)
    latest=[{k:v for k,v in r.items() if k in ('id','side','mint','status','created_at','reason','signature','result','error','source_wallet','source_entry','entry_path','realized_pnl_lamports','timing_ms')} for r in latest]
    return {'revision':revision,'enabled':state['enabled'],'wallet':state['wallet'],'last_cycle':state['last_cycle'],'confirmed_trade_count':count,
            'live_notifications':__import__('solana_wallet_stream').public(),
            'started_at':state['started_at'],
            'minimum_token_age_seconds':MINIMUM_SECONDS,'accounting_reviews':state.get('accounting_reviews',[]),
            'balance_adjustments':state.get('balance_adjustments',[]),'accounting_complete':not accounting_pending(state),'error':state['error'],'positions':list(state['positions'].values()),'pending_count':len(pending),
            'exit_alerts':__import__('solana_exit_alerts').summary(),
            'orders':latest,'realized_sol':state['realized_lamports']/10**9,'policy':state['policy'],
            'basis':'Actual finalized wallet balance changes, including transaction fees and account rent. SOL accounting; dollar P/L is not inferred from PAPER.'}


def activate(expected_revision,wallet_revision,budget_revision,acknowledgement,provider=None):
    import solana_real_copy as link
    import solana_real_budget as budget
    import solana_copy_trader as paper
    if any(type(n) is not int for n in (expected_revision,wallet_revision,budget_revision)) or acknowledgement!='ENABLE REAL SOLANA COPY TRADING':raise ValueError('Explicit live-trading acknowledgement and current revisions required.')
    saved=link.wallet();cfg=budget.settings();flags=execution.configuration(saved['wallet_address'])
    if not all(flags[k] for k in ('release_allowed','signer_configured','signer_matches','jupiter_configured')):raise Conflict('Complete server-side signing and swap setup before activation.')
    if saved['revision']!=wallet_revision or not saved['verified_at'] or cfg['revision']!=budget_revision:raise Conflict('Wallet or buy settings changed. Refresh before activation.')
    field='buy_usd' if cfg['config']['buy_mode']=='fixed_usd' else 'buy_percent'
    if not cfg['config'].get(field):raise ValueError('Set the real buy size first.')
    source=paper.load()[0]
    if not source['config']['enabled']:raise Conflict('The paper source copier must be running to provide admitted signals.')
    from solana_copy_health import summary as source_health, live_source_health
    from solana_copy_exit_settings import live_stop
    if live_source_health(source_health(source,time.time()),live_stop(source['config']),time.time())['level']!='ok':raise Conflict('The source wallet feed needs current successful checks before live activation.')
    provider=provider or execution.Provider();provider.network()
    if provider.balance(saved['wallet_address'])<=int(Decimal(str(cfg['config']['fee_reserve_sol']))*10**9):raise Conflict('No native SOL is available above the fee reserve.')
    # Read-only entitlement/route probe: never sign or submit this sample quote.
    provider.build(saved['wallet_address'],budget.SOL,'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',1_000_000,DEFAULT_POLICY)
    with closing(database.get_connection()) as conn:
        initialize(conn);conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM solana_real_run WHERE id=1').fetchone();state=json.loads(row['state_json'])
        if state.get('balance_adjustments') or any(p.get('balance_observation') or p.get('external_change') for p in state['positions'].values()):raise Conflict('External wallet changes require accounting review before restarting.')
        if row['revision']!=expected_revision or row['lease_until']>time.time():raise Conflict('Real copier changed or is busy. Refresh before activation.')
        if state['wallet'] and state['wallet']!=saved['wallet_address'] and (state['positions'] or orders(True)):raise Conflict('Previous wallet has unresolved live holdings.')
        if state['wallet']!=saved['wallet_address']:state=initial()
        # Old source rows are never replayed on first start or resume.
        cursor=state['event_cursor'] if state['positions'] or orders(True) else conn.execute('SELECT COALESCE(MAX(rowid),0) FROM solana_copy_events').fetchone()[0]
        from solana_live_source import latest as latest_live_source
        direct_cursor=state.get('live_source_cursor',0) if state['positions'] or orders(True) else latest_live_source(conn)
        state.update(live_source_cursor=direct_cursor,enabled=True,wallet=saved['wallet_address'],wallet_revision=wallet_revision,budget_revision=budget_revision,
            started_at=time.time(),event_cursor=cursor,error=None)
        conn.execute('UPDATE solana_real_run SET revision=revision+1,state_json=? WHERE id=1',(json.dumps(state,allow_nan=False),));conn.commit()
    return public()


def pause(expected_revision):
    if type(expected_revision) is not int or expected_revision<0:raise ValueError('Real copier revision required.')
    with closing(database.get_connection()) as conn:
        initialize(conn);conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM solana_real_run WHERE id=1').fetchone()
        if row['revision']<expected_revision:raise Conflict('Real copier revision is unavailable.')
        state=json.loads(row['state_json']);state['enabled']=False
        conn.execute('UPDATE solana_real_run SET revision=revision+1,state_json=? WHERE id=1',(json.dumps(state,allow_nan=False),));conn.commit()
    return public()


def queue_close(ids,pause_new_buys):
    if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(i,str) or len(i)>100 for i in ids) or type(pause_new_buys) is not bool:raise ValueError('Exact live position IDs required.')
    with closing(database.get_connection()) as conn:
        initialize(conn);conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM solana_real_run WHERE id=1').fetchone();state=json.loads(row['state_json'])
        for ident in set(ids):
            if ident not in state['positions']:raise Conflict('A live position changed. Refresh before closing.')
            state['positions'][ident]['pending_reason']='manual'
        if pause_new_buys:state['enabled']=False
        conn.execute('UPDATE solana_real_run SET revision=revision+1,state_json=? WHERE id=1',(json.dumps(state,allow_nan=False),));conn.commit()
    return public()


def save_order(order):
    with closing(database.get_connection()) as conn:
        changed=conn.execute("INSERT INTO solana_real_orders VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,details_json=excluded.details_json WHERE solana_real_orders.status IN ('preparing','signed','submitted','unknown')",
            (order['id'],order['created_at'],order['status'],json.dumps(order,allow_nan=False)))
        if changed.rowcount!=1:raise Conflict('Terminal transaction cannot be reopened by an older worker.')
        conn.commit()


def save_signed(order,lease_owner):
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE');row=conn.execute('SELECT lease_owner,lease_until FROM solana_real_run WHERE id=1').fetchone()
        if row['lease_owner']!=lease_owner or row['lease_until']<=time.time():raise Conflict('Worker lease expired before storing signed bytes.')
        changed=conn.execute("UPDATE solana_real_orders SET status='signed',details_json=? WHERE id=? AND status='preparing'",(json.dumps(order,allow_nan=False),order['id']))
        if changed.rowcount!=1:raise Conflict('Order preparation was superseded; signed bytes must not be broadcast.')
        conn.commit()


def schedule_failed_sell_retry(state, order, now):
    """Two fast retries after safe pre-broadcast rejection or finalized failure.

    Pending/unknown transactions must settle before another order can be made.
    """
    if order.get('side') != 'sell' or order.get('status') not in ('failed','rejected'): return
    p = state['positions'].get(order.get('position_id'))
    if p is None: return
    count = p.get('fast_failed_sell_retries', 0)
    if count < 2:
        p['fast_failed_sell_retries'] = count + 1
        p['exit_retry_at'] = now + 2
    else:
        p.pop('exit_retry_at',None)


def exit_attempt_ready(position, reason, now):
    # A full protective exit must not wait for an earlier half-sale cooldown.
    if position.get('last_exit_reason')=='profit_take_half' and reason!='profit_take_half':
        return True
    return now >= position.get('exit_retry_at',position['last_exit_attempt_at']+30)


def trailing_profit_trigger(position, value, fee_lamports):
    """Post-trim 20% trail on fresh executable proceeds per token, net cost floor.

    Keep the peak per raw token so subsequent half-sales cannot lower it merely
    by reducing quantity. Caller supplies only a successful current exit quote.
    """
    if not position['stages']: return False
    quantity = position['quantity_raw']
    unit = Decimal(value + fee_lamports) / Decimal(quantity)
    peak = max(Decimal(str(position.get('trailing_peak_lamports_per_raw', unit))), unit)
    position['trailing_peak_lamports_per_raw'] = str(peak)
    floor = max(position['cost_lamports'], int((peak * quantity * Decimal('0.8') - fee_lamports).to_integral_value(rounding=ROUND_CEILING)))
    position['trailing_floor_lamports'] = floor
    return value <= floor


def post_receipt(state,order,result,now):
    """Runs in the same DB transaction as the order's terminal state."""
    if result['state']=='failed':
        state['realized_lamports']-=result['fee_lamports']
        if order.get('position_id') in state['positions']:state['positions'][order['position_id']]['realized_lamports']-=result['fee_lamports']
        return
    delta=result['native_delta'];quantity=abs(result['token_delta'])
    if order['side']=='buy':
        state['positions'][order['id']]={'id':order['id'],'mint':order['mint'],'source_wallet':order['source_wallet'],
            'quantity_raw':quantity,'original_quantity_raw':quantity,'cost_lamports':-delta,'original_cost_lamports':-delta,
            'realized_lamports':0,'opened_slot':result.get('slot',0),'opened_at':now,'source_at':order['source_at'],'decimals':order['prepared']['decimals'],
            'token_program':order['prepared'].get('token_program',execution.TOKEN),'stages':0,
            'next_gain_percent':order['protection']['profit_take_first_percent'],'protection':order['protection'],
            'pending_reason':None,'marked_at':None,'exit_value_lamports':None,'last_exit_attempt_at':0}
    else:
        p=state['positions'].get(order['position_id'])
        if not p or quantity>p['quantity_raw']:raise execution.Unavailable('Exit ledger quantity requires manual reconciliation.')
        cost=p['cost_lamports'] if quantity==p['quantity_raw'] else p['cost_lamports']*quantity//p['quantity_raw']
        pnl=delta-cost;state['realized_lamports']+=pnl;p['realized_lamports']+=pnl
        p.pop('exit_retry_at',None);p.pop('fast_failed_sell_retries',None)
        p['quantity_raw']-=quantity;p['cost_lamports']-=cost;p['pending_reason']=None
        p['balance_checked_slot']=result.get('slot',p.get('balance_checked_slot',p.get('opened_slot',0)))
        if order['reason']=='profit_take_half':p['stages']+=1;p['next_gain_percent']*=2
        if not p['quantity_raw']:
            if order['reason'] in ('stop_loss','profit_protection'):state['cooldowns'][p['mint']]=now+p['protection']['stop_loss_cooldown_seconds']
            del state['positions'][p['id']]


def commit_cycle(state,owner,order=None):
    """Merge current pause state; never let an in-flight cycle undo owner pause."""
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE');row=conn.execute('SELECT * FROM solana_real_run WHERE id=1').fetchone()
        if row['lease_owner']!=owner or row['lease_until']<=time.time():raise Conflict('Real worker lease expired; reconcile stored orders before continuing.')
        current=json.loads(row['state_json'])
        if not current['enabled']:state['enabled']=False
        # A close requested while RPC work was in flight must survive this cycle.
        for ident,p in current['positions'].items():
            if ident in state['positions'] and p.get('pending_reason')=='manual':state['positions'][ident]['pending_reason']='manual'
        external_orders=state.get('_external_orders',[])
        for external in external_orders:
            conn.execute('INSERT INTO solana_real_orders VALUES(?,?,?,?)',(external['id'],external['created_at'],'confirmed',json.dumps(external,allow_nan=False)))
        persisted={k:v for k,v in state.items() if k!='_external_orders'}
        conn.execute('UPDATE solana_real_run SET revision=revision+1,state_json=?,lease_owner=NULL,lease_until=0 WHERE id=1',(json.dumps(persisted,allow_nan=False),))
        if order:conn.execute('UPDATE solana_real_orders SET status=?,details_json=? WHERE id=?',(order['status'],json.dumps(order,allow_nan=False),order['id']))
        conn.commit()


def source_events(cursor):
    with closing(database.get_connection()) as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_copy_events'").fetchone()
        rows=conn.execute('SELECT rowid,details_json FROM solana_copy_events WHERE rowid>? ORDER BY rowid LIMIT 500',(cursor,)).fetchall() if exists else []
    return [(r[0],json.loads(r[1])) for r in rows]


def decide(event,reason,now):
    with closing(database.get_connection()) as conn:
        conn.execute('INSERT OR IGNORE INTO solana_real_decisions VALUES(?,?,?)',(event['id'],now,reason));conn.commit()


def source_eligible(source,wallet,source_at,started_at):
    trader=next((t for t in source['traders'] if t['wallet']==wallet and t.get('active',True)),None)
    if not source['config']['enabled'] or trader is None or paper_only(wallet,source['traders']):return False
    lower=max(started_at or 0,trader.get('selected_at') or 0,trader.get('buy_resume_at') or 0,source.get('buy_resume_at') or 0)
    return source_at is not None and source_at>=lower


def authorized(state,side,source_at=None,lease_owner=None,source_wallet=None,entry_path=None):
    import solana_real_copy as link
    import solana_real_budget as budget
    current,_=load();saved=link.wallet();flags=execution.configuration(saved['wallet_address'])
    if lease_owner is not None:
        with closing(database.get_connection()) as conn:row=conn.execute('SELECT lease_owner,lease_until FROM solana_real_run WHERE id=1').fetchone()
        if row['lease_owner']!=lease_owner or row['lease_until']<=time.time():raise execution.Unavailable('Worker lease expired before signing.')
    if not all(flags[k] for k in ('release_allowed','signer_configured','signer_matches','jupiter_configured')):raise execution.Unavailable('Server trading setup is unavailable. No new transaction is signed.')
    if saved['wallet_address']!=state['wallet'] or saved['revision']!=state['wallet_revision']:raise execution.Unavailable('Live wallet binding changed. Signing is blocked.')
    if side=='buy' and (accounting_pending(current) or accounting_pending(state) or not current['enabled'] or budget.settings()['revision']!=state['budget_revision']):raise execution.Unavailable('New real buys are paused or buy settings changed.')
    if side=='buy':
        import solana_copy_trader as paper
        source=paper.load()[0]
        if paper_only(source_wallet,source['traders']):raise execution.Unavailable('Source wallet is paper-only; new live buys are disabled.')
        if not source_eligible(source,source_wallet,source_at,state['started_at']):
            raise execution.Unavailable('Source copying paused or source wallet is no longer eligible for this buy.')
        if entry_path=='verified_source':
            from solana_live_source import ready
            if not ready(source_wallet,time.time()):raise execution.Unavailable('Verified source history is incomplete or stale.')
    if side=='buy' and (not entry_allowed(source_wallet=source_wallet) or source_at is not None and not entry_allowed(source_at, source_wallet)):
        raise execution.Unavailable('Daily live entry pause: 2 PM to midnight America/New_York.')
    if side=='buy' and source_at is not None and not 0<=time.time()-source_at<=90:raise execution.Unavailable('Source buy expired before signing or broadcast.')


def begin_broadcast(order,state,lease_owner):
    """Atomically establish that broadcast began before a concurrent pause."""
    if order['side']=='buy' and paper_only(order.get('source_wallet')):
        raise execution.Unavailable('Source wallet is paper-only; new live buys are disabled.')
    if order['side']=='buy':
        from solana_copy_entry import check_quote
        prepared=order['prepared']
        check_quote(order.get('source_entry'),order['mint'],prepared['decimals'],prepared['input_amount'],prepared['minimum_output'])
        try:order['token_age_proof']=require_age(order['mint'])
        except AgeUnavailable as error:raise execution.Unavailable(str(error)) from None
    authorized(state,order['side'],order.get('source_at'),lease_owner,order.get('source_wallet'),order.get('entry_path'))
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM solana_real_run WHERE id=1').fetchone()
        if row['lease_owner']!=lease_owner or row['lease_until']<=time.time():raise execution.Unavailable('Worker lease expired before broadcast.')
        current=json.loads(row['state_json'])
        if not 0<=time.time()-order['prepared'].get('prepared_at',order['created_at'])<=30:raise execution.Unavailable('Unsent prepared swap expired. A new exit quote is required.')
        if order['side']=='sell':
            position=current['positions'].get(order['position_id'])
            if not position or position['quantity_raw']<order['prepared']['input_amount']:raise execution.Unavailable('Live quantity changed before exit broadcast.')
        wallet=conn.execute('SELECT * FROM solana_real_copy_wallet WHERE id=1').fetchone()
        if wallet['wallet_address']!=state['wallet'] or wallet['revision']!=state['wallet_revision']:raise execution.Unavailable('Wallet changed before broadcast.')
        if order['side']=='buy':
            revision=conn.execute('SELECT revision FROM solana_real_copy_budget WHERE id=1').fetchone()[0]
            if not current['enabled'] or revision!=state['budget_revision']:raise execution.Unavailable('New real buy was paused before broadcast.')
            source_row=conn.execute('SELECT state_json FROM solana_copy_state WHERE id=1').fetchone()
            if not source_row or not source_eligible(json.loads(source_row[0]),order['source_wallet'],order['source_at'],state['started_at']):
                raise execution.Unavailable('Source copying paused or source wallet changed before broadcast.')
            # Chain block times have second precision. Treat a same-second sell
            # as superseding the buy, including subsecond provider clock skew.
            sold=conn.execute("SELECT 1 FROM solana_copy_events WHERE observed_at>=? AND json_extract(details_json,'$.side')='sell' AND json_extract(details_json,'$.wallet')=? AND json_extract(details_json,'$.mint')=? AND CAST(json_extract(details_json,'$.source_at') AS INTEGER)>=? LIMIT 1",(int(order['source_at'])-5,order['source_wallet'],order['mint'],int(order['source_at']))).fetchone()
            from solana_live_source import sold as direct_sold
            if sold or direct_sold(conn,order):raise execution.Unavailable('Source sold before live broadcast; buy canceled.')
        order.update(status='unknown',send_started_at=time.time())
        if order['side']=='buy':
            timings=order.setdefault('timing_ms',{})
            timings['source_to_broadcast_ms']=round(max(0,order['send_started_at']-order['source_at'])*1000,3)
            timings['detection_to_broadcast_ms']=round(max(0,order['send_started_at']-order['detected_at'])*1000,3)
        if order['side']=='sell' and order.get('detected_at') is not None:
            order.setdefault('timing_ms',{})['trigger_to_broadcast_ms']=round(max(0,order['send_started_at']-order['detected_at'])*1000,3)
        changed=conn.execute("UPDATE solana_real_orders SET status='unknown',details_json=? WHERE id=? AND status='signed'",(json.dumps(order,allow_nan=False),order['id']))
        if changed.rowcount!=1:raise Conflict('The stored transaction changed before broadcast.')
        conn.commit()


def reconcile_balances(state,provider,now,position_ids=None):
    """Read-only wallet drift detection, only after all stored swaps settle.

    External withdrawals are not assumed to be sales. Archive the missing cost
    separately and suppress incomplete P/L until the actual proceeds are audited.
    """
    blocked=set()
    def read_balance(item):
        _,p=item
        return provider.finalized_token_balance(state['wallet'],p['mint'],p.get('token_program',execution.TOKEN))
    with closing(read_many([(ident,p) for ident,p in state['positions'].items() if position_ids is None or ident in position_ids],read_balance)) as observations:
        for (ident,p),observed in observations:
            try:
                if isinstance(observed,Exception):raise observed
                actual=execution.uint(observed['quantity']);slot=execution.uint(observed['slot'])
                if slot<max(p.get('opened_slot',0),p.get('balance_checked_slot',0)):raise execution.Unavailable('Finalized wallet balance is older than the last verified holding.')
                if actual==p['quantity_raw']:
                    p.pop('balance_observation',None);p['balance_checked_slot']=slot;continue
                # A finalized manual swap can be accounted before interrupting a
                # running copier. Transfers and ambiguous settlements still pause.
                if actual<p['quantity_raw'] and not p.get('external_change') and sum(x['mint']==p['mint'] for x in state['positions'].values())==1:
                    from solana_external_sales import audit,book,stored_orders
                    removed=p['quantity_raw']-actual
                    cost=p['cost_lamports'] if actual==0 else p['cost_lamports']*removed//p['quantity_raw']
                    start=p.get('balance_checked_slot',p.get('opened_slot',0))
                    adjustment={'position_id':ident,'mint':p['mint'],'quantity_removed_raw':removed,
                        'unreconciled_cost_lamports':cost,'observed_at':now,'start_slot':start,'finalized_slot':slot}
                    try:
                        history=stored_orders()+state.get('_external_orders',[])
                        result=audit(provider,state['wallet'],p['mint'],p.get('token_program',execution.TOKEN),removed,start,slot,history)
                    except execution.Unavailable:pass
                    else:
                        external=book(state,adjustment,result,now)
                        state.setdefault('_external_orders',[]).append(external)
                        if actual==0:del state['positions'][ident]
                        else:
                            p.update(quantity_raw=actual,cost_lamports=p['cost_lamports']-cost,balance_checked_slot=slot,
                                realized_lamports=p['realized_lamports']+result['native_delta']-cost,exit_value_lamports=None)
                            p.pop('balance_observation',None)
                        continue
                blocked.add(ident);p['exit_value_lamports']=None
                state['error']='External wallet balance change detected. New buys paused; affected holdings require reconciliation.'
                first=p.get('balance_observation')
                if not first or first['quantity']!=actual:
                    p['balance_observation']={'quantity':actual,'slot':slot,'at':now};continue
                if now-first['at']<15 or slot<=first['slot']:continue
                # Extra deposited tokens never become bot-owned trading funds.
                if actual>p['quantity_raw']:continue
                removed=p['quantity_raw']-actual
                cost=p['cost_lamports'] if actual==0 else p['cost_lamports']*removed//p['quantity_raw']
                state.setdefault('balance_adjustments',[]).append({'position_id':ident,'mint':p['mint'],
                    'quantity_removed_raw':removed,'unreconciled_cost_lamports':cost,'observed_at':now,
                    'first_slot':first['slot'],'finalized_slot':slot,'proceeds_lamports':None,
                    'start_slot':p.get('balance_checked_slot',p.get('opened_slot',0)),
                    'basis':'External wallet reduction; sale versus transfer and proceeds require transaction review.'})
                if actual==0:del state['positions'][ident]
                else:
                    p.update(quantity_raw=actual,cost_lamports=p['cost_lamports']-cost,pending_reason=None)
                    p.pop('balance_observation',None);p['external_change']=True
            except execution.Unavailable:
                blocked.add(ident);p['exit_value_lamports']=None
                state['error']='Finalized wallet balance unavailable. No new order for the affected holding.'
    if state.get('balance_adjustments'):
        state['error']='External wallet changes require accounting review. P/L is incomplete; new buys remain paused.'
        blocked.update(p['id'] for p in state['positions'].values() if p.get('external_change'))
    return blocked


def tick(provider=None):
    import solana_copy_trader as paper
    import solana_real_budget as budget
    state,_=load()
    if state.get('balance_adjustments') and not orders(True):
        from solana_manual_accounting import reconcile_reviewed
        reconcile_reviewed(provider=provider)
        from solana_external_sales import reconcile_pending
        try:reconcile_pending(provider=provider)
        except execution.Unavailable:pass
        state,_=load()
    if not state['enabled'] and not state['positions'] and not orders(True):return
    provider=provider or execution.Provider();owner=str(uuid.uuid4());now=time.time()
    with closing(database.get_connection()) as conn:
        initialize(conn)
        if conn.execute('UPDATE solana_real_run SET lease_owner=?,lease_until=? WHERE id=1 AND lease_until<=?',(owner,now+180,now)).rowcount!=1:raise Conflict('Real copy worker already running.')
        conn.commit()
    terminal=None
    try:
        # Load again after lease acquisition, so a just-completed cycle is visible.
        state,_=load();state['error']=None
        if not execution.release_allowed():
            state['enabled']=False;state['error']='Server execution switch is off. Live exits cannot be submitted while it is off.'
        provider.network()
        pending=orders(True)
        if pending:
            order=pending[0]
            if order['status']=='preparing':
                # Signed bytes are always saved before any broadcast is permitted.
                order.update(status='abandoned',error='Interrupted before a signed transaction was persisted.');terminal=order
            elif not order.get('send_started_at'):
                try:authorized(state,order['side'],order.get('source_at'),owner,order.get('source_wallet'),order.get('entry_path'))
                except execution.Unavailable as error:
                    order.update(status='canceled',error=str(error));terminal=order
                else:
                    try:begin_broadcast(order,state,owner)
                    except execution.Unavailable as error:
                        order.pop('send_started_at',None);order.update(status='canceled',error=str(error));terminal=order
                        state['last_cycle']=time.time();commit_cycle(state,owner,terminal);return
                    try:provider.send(order['prepared']);order['status']='submitted';save_order(order)
                    except execution.Unavailable:pass
            else:
                result=provider.reconcile(order)
                if result['state'] in ('confirmed','failed'):
                    post_receipt(state,order,result,time.time());order.update(status=result['state'],result=result);terminal=order
                    if order['side'] in ('buy','sell'):
                        observed=time.time();timings=order.setdefault('timing_ms',{})
                        timings['broadcast_to_finalized_ms']=round(max(0,observed-order['send_started_at'])*1000,3)
                        if order.get('detected_at') is not None:timings['trigger_to_finalized_ms']=round(max(0,observed-order['detected_at'])*1000,3)
                        if order['side']=='buy':timings['source_to_finalized_ms']=round(max(0,observed-order['source_at'])*1000,3)
                    if result['state']=='failed':
                        schedule_failed_sell_retry(state,order,time.time())
                        order['error']='On-chain swap failed; no fill recorded. '+json.dumps(result.get('execution_error','Network execution failed'),separators=(',',':'))[:300]
                elif result['state']=='expired_unseen':
                    if order.get('first_expired_at') and time.time()-order['first_expired_at']>=15:
                        order.update(status='expired',error='Finalized block height passed expiry; repeated history queries found no transaction.');terminal=order
                    else:order.setdefault('first_expired_at',time.time());save_order(order)
                elif execution.release_allowed() and time.time()-order.get('last_broadcast_at',order['send_started_at'])>=15:
                    # Same signed bytes and signature, never a replacement order.
                    authorized(state,'sell',lease_owner=owner);order['last_broadcast_at']=time.time();save_order(order)
                    try:provider.send(order['prepared'])
                    except execution.Unavailable:pass
            state['last_cycle']=time.time();commit_cycle(state,owner,terminal);return
        source=paper.load()[0];cfg=budget.settings()
        if cfg['revision']!=state['budget_revision']:
            state['enabled']=False;state['error']='Buy settings changed. Restart copying to apply them; existing exits remain monitored.'
        import solana_live_source as direct_source
        events=[('direct',cursor,event) for cursor,event in direct_source.events(state.get('live_source_cursor',0))]
        events += [('paper',cursor,event) for cursor,event in source_events(state['event_cursor'])]
        latest_sell={}
        for _,_,e in events:
            if e['side']=='sell':latest_sell[(e['wallet'],e['mint'])]=max(e['source_at'],latest_sell.get((e['wallet'],e['mint']),0))
        for _,_,e in events:
            if e['side']=='sell':
                for p in state['positions'].values():
                    if p['mint']==e['mint'] and p['source_wallet']==e['wallet'] and e['source_at']>=p['source_at']:p['pending_reason']='source_sell'
        urgent_ids={p['id'] for p in state['positions'].values() if p.get('pending_reason') in ('manual','source_sell') and exit_attempt_ready(p,p['pending_reason'],time.time())}
        blocked_positions=reconcile_balances(state,provider,time.time(),urgent_ids if urgent_ids else None)
        if urgent_ids and not any(p['id'] in urgent_ids-blocked_positions for p in state['positions'].values()):
            # No urgent holding can trade. Complete ordinary accounting before
            # evaluating another exit or allowing any new buy.
            blocked_positions.update(reconcile_balances(state,provider,time.time()))
        # Current swap quotes, not chart marks, trigger live protective exits.
        chosen=None
        eligible=sorted((p for p in state['positions'].values() if p['id'] not in blocked_positions),key=lambda p:p['opened_at'])
        # Explicit exits already have a reason. Their preparation obtains a fresh
        # executable quote; unrelated monitoring requests must not delay them.
        for p in eligible:
            reason=p.get('pending_reason')
            if reason in ('manual','source_sell') and exit_attempt_ready(p,reason,time.time()):
                p.pop('exit_retry_at',None)
                p.update(last_exit_attempt_at=time.time(),last_exit_reason=reason)
                chosen={'id':str(uuid.uuid4()),'side':'sell','mint':p['mint'],'position_id':p['id'],
                        'quantity':p['quantity_raw'],'reason':reason,'detected_at':time.time(),'timing_ms':{}}
                break
        def read_quote(p):
            timings={}
            with measure(timings,'quote_ms'):
                q=provider.build(state['wallet'],p['mint'],budget.SOL,p['quantity_raw'],{**state['policy'],'reserve_lamports':0,'_quote_priority':'monitor'})
            return q,timings
        with closing(read_many(eligible if chosen is None else [],read_quote)) as quotes:
            for p,result in quotes:
                try:
                    if isinstance(result,Exception):raise result
                    q,quote_timing=result
                    value=max(0,execution.uint(q['otherAmountThreshold'])-5000-state['policy']['max_priority_fee_lamports'])
                    p.update(marked_at=time.time(),exit_value_lamports=value)
                    gain=100*(value-p['cost_lamports'])/p['cost_lamports'];rules=p['protection'];reason=p['pending_reason'];quantity=p['quantity_raw']
                    trailing_exit=trailing_profit_trigger(p,value,5000+state['policy']['max_priority_fee_lamports'])
                    if reason not in ('manual','source_sell'):
                        if rules['stop_loss_percent']>0 and gain<=-rules['stop_loss_percent']:reason='stop_loss'
                        elif trailing_exit:reason='profit_protection'
                        elif reason=='profit_take_half' and gain<p['next_gain_percent']:reason=None;p['pending_reason']=None
                    if reason=='profit_take_half':quantity=max(1,quantity//2)
                    if not reason and rules['profit_take_enabled'] and gain>=p['next_gain_percent'] and quantity>=2:
                        reason='profit_take_half';quantity//=2
                    if reason and exit_attempt_ready(p,reason,time.time()):
                        p.pop('exit_retry_at',None)
                        p['pending_reason']=reason;p['last_exit_attempt_at']=time.time();p['last_exit_reason']=reason
                        chosen={'id':str(uuid.uuid4()),'side':'sell','mint':p['mint'],'position_id':p['id'],'quantity':quantity,'reason':reason,'detected_at':time.time(),'timing_ms':quote_timing};break
                except execution.Unavailable:
                    p['exit_value_lamports']=None;state['error']='A live exit quote is unavailable. Holdings remain unpriced and exits are retried.'
                if time.time()-now>100:break
        for channel,cursor,e in events:
            state['live_source_cursor' if channel=='direct' else 'event_cursor']=cursor
            if e['side']!='buy':continue
            reason=None
            if paper_only(e['wallet'],source['traders']):reason='Source wallet is paper-only; new live buys are disabled.'
            elif not entry_allowed(source_wallet=e['wallet']) or not entry_allowed(e['source_at'], e['wallet']):reason='Daily live entry pause: 2 PM to midnight America/New_York.'
            elif not state['enabled']:reason='Real buys paused.'
            elif accounting_pending(state):reason='External wallet accounting is pending; new buys wait for finalized receipts.'
            elif channel=='direct' and (e.get('status')!='verified_source' or e.get('source')!='solana_rpc_pump'):reason='Source receipt is not a supported verified swap.'
            elif channel=='paper' and e.get('status')!='paper_buy':reason='Source buy was not admitted by the paper copy rules.'
            elif not any(t['wallet']==e['wallet'] and t.get('active',True) for t in source['traders']):reason='Source wallet is no longer actively followed.'
            elif not source_eligible(source,e['wallet'],e['source_at'],state['started_at']):reason='Outside the prospective source follow window or source copying is paused.'
            elif e['source_at']<state['started_at'] or not 0<=time.time()-e['source_at']<=90:reason='Outside the fresh live entry window.'
            elif latest_sell.get((e['wallet'],e['mint']),0)>=e['source_at']:reason='Source already sold before live detection.'
            elif not source['config']['enabled']:reason='Source copying is paused.'
            elif channel=='direct' and not direct_source.ready(e['wallet'],time.time()):reason='Verified source history is incomplete or stale.'
            elif channel=='paper' and (time.time()-(source.get('last_cycle') or 0)>60 or source.get('error')):reason='Source feed is not current.'
            elif any(p['mint']==e['mint'] for p in state['positions'].values()):reason='One live position per coin; repeated buys skipped.'
            elif any(p.get('exit_value_lamports') is None or time.time()-(p.get('marked_at') or 0)>60 for p in state['positions'].values()):reason='An existing live holding lacks a current exit quote.'
            elif state['cooldowns'].get(e['mint'],0)>time.time():reason='Protective-exit cooldown.'
            elif chosen:reason='Another live order has priority.'
            if not reason:
                with closing(database.get_connection()) as conn:
                    known=conn.execute('SELECT 1 FROM solana_real_decisions WHERE source_id=?',(e['id'],)).fetchone()
                if known:continue
                chosen={'id':'buy:'+e['id'],'side':'buy','mint':e['mint'],'source_wallet':e['wallet'],'source_at':e['source_at'],'reason':'copy_buy',
                    'entry_path':'verified_source' if channel=='direct' else 'paper_admitted','source_entry':e.get('source_entry'),'detected_at':e.get('detected_at',e.get('observed_at',time.time())),
                    'timing_ms':{'source_to_detection_ms':round(max(0,e.get('detected_at',e.get('observed_at',time.time()))-e['source_at'])*1000,3),
                                 'feed_to_execution_ms':round(max(0,time.time()-e.get('feed_saved_at',e.get('observed_at',time.time())))*1000,3),
                                 'source_processing_ms':round(max(0,e.get('feed_saved_at',e.get('observed_at',time.time()))-e.get('detected_at',e.get('observed_at',time.time())))*1000,3)},
                    'protection':{**{k:source['config'][k] for k in ('profit_take_enabled','profit_take_first_percent','stop_loss_cooldown_seconds')},
                                  'stop_loss_percent':__import__('solana_copy_exit_settings').live_stop(source['config'])}}
                reason='Selected fresh admitted copy signal.'
            decide(e,reason,time.time())
        if chosen and execution.release_allowed() and time.time()-now<110:
            order={**chosen,'execution_profile':PROFILE,'wallet':state['wallet'],'created_at':time.time(),'status':'preparing'}
            save_order(order)
            try:
                policy={**state['policy'],'reserve_lamports':int(Decimal(str(cfg['config']['fee_reserve_sol']))*10**9)}
                if chosen['side']=='buy':
                    policy['source_entry']=chosen.get('source_entry')
                    try:order['token_age_proof']=require_age(chosen['mint'])
                    except AgeUnavailable as error:raise execution.Unavailable(str(error)) from None
                    with measure(order.setdefault('timing_ms',{}),'funding_checks_ms'):
                        with ThreadPoolExecutor(max_workers=3) as reads:
                            balance=reads.submit(provider.balance,state['wallet'])
                            rent=reads.submit(provider.entry_rent,chosen['mint'])
                            price_read=reads.submit(budget.sol_price) if cfg['config']['buy_mode']=='fixed_usd' else None
                            available=balance.result()-policy['reserve_lamports']-5000-policy['max_priority_fee_lamports']-rent.result()
                            price=Decimal(str(price_read.result()['usd'])) if price_read is not None else None
                    if cfg['config']['buy_mode']=='fixed_usd':
                        amount=int((Decimal(str(cfg['config']['buy_usd']))/price*10**9).to_integral_value(rounding=ROUND_CEILING))
                    else:amount=int(Decimal(max(0,available))*Decimal(str(cfg['config']['buy_percent']))/100)
                    if not 0<amount<=available:raise execution.Unavailable('Buy size does not fit the current wallet after fees and reserve.')
                else:amount=chosen['quantity']
                quote=None
                if chosen['side']=='sell' and chosen['reason']=='profit_take_half':
                    # The actual half-size executable quote must meet the same
                    # profit target as the whole-position monitoring estimate.
                    position=state['positions'][chosen['position_id']]
                    with measure(order.setdefault('timing_ms',{}),'quote_ms'):
                        quote=provider.build(state['wallet'],chosen['mint'],budget.SOL,amount,policy)
                    allocated_cost=position['cost_lamports']*amount//position['quantity_raw']
                    target=int((Decimal(allocated_cost)*(100+Decimal(str(position['next_gain_percent'])))/100).to_integral_value(rounding=ROUND_CEILING))
                    policy['minimum_sell_output_lamports']=target+5000+state['policy']['max_priority_fee_lamports']
                    if execution.uint(quote['otherAmountThreshold'])-5000-policy['max_priority_fee_lamports']<target:
                        position['pending_reason']=None
                        raise execution.Unavailable('Profit trim deferred: current half-sale proceeds after fees do not meet the profit target.')
                kwargs={'quote':quote} if quote is not None else {}
                with measure(order.setdefault('timing_ms',{}),'prepare_ms'):
                    prepared=provider.prepare(state['wallet'],chosen['side'],chosen['mint'],amount,policy,lambda:authorized(state,chosen['side'],chosen.get('source_at'),owner,chosen.get('source_wallet'),chosen.get('entry_path')),**kwargs)
                order['timing_ms'].update(prepared.get('timing_ms',{}))
                order.update(prepared=prepared,status='signed',signature=prepared['signature']);save_signed(order,owner)
                begin_broadcast(order,state,owner)
                try:
                    with measure(order.setdefault('timing_ms',{}),'submit_ms'):provider.send(prepared)
                    order['status']='submitted';save_order(order)
                except execution.Unavailable:
                    save_order(order)
                    state['error']='Broadcast outcome unknown. Further orders wait for reconciliation.'
            except execution.Unavailable as error:
                if not order.get('send_started_at'):
                    order.update(status='rejected',error=str(error));save_order(order)
                    schedule_failed_sell_retry(state,order,time.time())
                raise
            state['last_order_at']=time.time()
        state['last_cycle']=time.time();commit_cycle(state,owner)
    except Exception as error:
        state['last_cycle']=time.time();state['error']=str(error) if isinstance(error,(execution.Unavailable,Conflict)) else 'Real worker error. Stored orders must be reconciled before new submissions.'
        try:commit_cycle(state,owner,terminal)
        except Conflict:pass


def cycle_delay():
    """Faster reconciliation while a transaction is pending, no overlapping ticks."""
    if orders(True): return 1
    positions=load()[0]['positions']
    if any(p.get('exit_retry_at') is not None for p in positions.values()):return 1
    return 2 if positions else 5


async def execution_loop():
    global _execution_loop,_source_wakeup
    _execution_loop=asyncio.get_running_loop();_source_wakeup=asyncio.Event()
    try:
        while True:
            _source_wakeup.clear()
            try:await asyncio.to_thread(tick)
            except Exception:pass
            delay=await asyncio.to_thread(cycle_delay)
            try:await asyncio.wait_for(_source_wakeup.wait(),timeout=delay)
            except asyncio.TimeoutError:pass
    finally:
        _execution_loop=None;_source_wakeup=None


async def worker():
    import solana_exit_alerts
    await asyncio.gather(execution_loop(), solana_exit_alerts.worker())
