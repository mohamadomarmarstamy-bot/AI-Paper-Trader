"""Isolated, owner-activated live copy ledger. Disabled by default.

Only fresh admitted PAPER source buys are entry candidates; PAPER balances and
positions are never imported. Orders are persisted before broadcast and all
uncertain submissions block further signing until reconciled.
"""
import asyncio
from contextlib import closing
from decimal import Decimal,ROUND_CEILING
import hashlib
import json
import time
import uuid
import database
from solana_meme_bot import Conflict
import solana_real_execution as execution

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


def initial():return {'enabled':False,'wallet':'','wallet_revision':None,'budget_revision':None,'started_at':None,
    'event_cursor':0,'positions':{},'cooldowns':{},'realized_lamports':0,'last_cycle':None,'error':None,
    'policy':dict(DEFAULT_POLICY),'last_order_at':0}


def initialize(conn):
    conn.executescript(SCHEMA)
    conn.execute('INSERT OR IGNORE INTO solana_real_run VALUES(1,0,?,NULL,0)',(json.dumps(initial()),));conn.commit()


def load():
    with closing(database.get_connection()) as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_real_run'").fetchone()
        row=conn.execute('SELECT * FROM solana_real_run WHERE id=1').fetchone() if exists else None
    return (json.loads(row['state_json']),row['revision']) if row else (initial(),0)


def orders(pending=False):
    with closing(database.get_connection()) as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_real_orders'").fetchone()
        if not exists:return []
        rows=conn.execute('SELECT details_json FROM solana_real_orders '+("WHERE status IN ('preparing','signed','submitted','unknown') " if pending else '')+'ORDER BY created_at DESC LIMIT 50').fetchall()
    return [json.loads(r[0]) for r in rows]


def guard_wallet_change():
    state,_=load()
    if state['enabled'] or state['positions'] or orders(True):raise Conflict('Pause real copying and finish every live position and pending transaction before changing wallets.')


def public():
    state,revision=load();pending=orders(True)
    latest=[{k:v for k,v in r.items() if k in ('id','side','mint','status','created_at','reason','signature','result','error')} for r in orders()]
    with closing(database.get_connection()) as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_real_orders'").fetchone()
        count=conn.execute("SELECT COUNT(*) FROM solana_real_orders WHERE status='confirmed'").fetchone()[0] if exists else 0
    return {'revision':revision,'enabled':state['enabled'],'wallet':state['wallet'],'last_cycle':state['last_cycle'],'confirmed_trade_count':count,
            'started_at':state['started_at'],
            'error':state['error'],'positions':list(state['positions'].values()),'pending_count':len(pending),
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
    from solana_copy_health import summary as source_health
    if source_health(source,time.time())['level']!='ok':raise Conflict('The source wallet feed needs current successful checks before live activation.')
    provider=provider or execution.Provider();provider.network()
    if provider.balance(saved['wallet_address'])<=int(Decimal(str(cfg['config']['fee_reserve_sol']))*10**9):raise Conflict('No native SOL is available above the fee reserve.')
    # Read-only entitlement/route probe: never sign or submit this sample quote.
    provider.build(saved['wallet_address'],budget.SOL,'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',1_000_000,DEFAULT_POLICY)
    with closing(database.get_connection()) as conn:
        initialize(conn);conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM solana_real_run WHERE id=1').fetchone();state=json.loads(row['state_json'])
        if row['revision']!=expected_revision or row['lease_until']>time.time():raise Conflict('Real copier changed or is busy. Refresh before activation.')
        if state['wallet'] and state['wallet']!=saved['wallet_address'] and (state['positions'] or orders(True)):raise Conflict('Previous wallet has unresolved live holdings.')
        if state['wallet']!=saved['wallet_address']:state=initial()
        # Old source rows are never replayed on first start or resume.
        cursor=state['event_cursor'] if state['positions'] or orders(True) else conn.execute('SELECT COALESCE(MAX(rowid),0) FROM solana_copy_events').fetchone()[0]
        state.update(enabled=True,wallet=saved['wallet_address'],wallet_revision=wallet_revision,budget_revision=budget_revision,
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
            'realized_lamports':0,'opened_at':now,'source_at':order['source_at'],'decimals':order['prepared']['decimals'],'stages':0,
            'next_gain_percent':order['protection']['profit_take_first_percent'],'protection':order['protection'],
            'pending_reason':None,'marked_at':None,'exit_value_lamports':None,'last_exit_attempt_at':0}
    else:
        p=state['positions'].get(order['position_id'])
        if not p or quantity>p['quantity_raw']:raise execution.Unavailable('Exit ledger quantity requires manual reconciliation.')
        cost=p['cost_lamports'] if quantity==p['quantity_raw'] else p['cost_lamports']*quantity//p['quantity_raw']
        pnl=delta-cost;state['realized_lamports']+=pnl;p['realized_lamports']+=pnl
        p['quantity_raw']-=quantity;p['cost_lamports']-=cost;p['pending_reason']=None
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
        conn.execute('UPDATE solana_real_run SET revision=revision+1,state_json=?,lease_owner=NULL,lease_until=0 WHERE id=1',(json.dumps(state,allow_nan=False),))
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


def authorized(state,side,source_at=None,lease_owner=None):
    import solana_real_copy as link
    import solana_real_budget as budget
    current,_=load();saved=link.wallet();flags=execution.configuration(saved['wallet_address'])
    if lease_owner is not None:
        with closing(database.get_connection()) as conn:row=conn.execute('SELECT lease_owner,lease_until FROM solana_real_run WHERE id=1').fetchone()
        if row['lease_owner']!=lease_owner or row['lease_until']<=time.time():raise execution.Unavailable('Worker lease expired before signing.')
    if not all(flags[k] for k in ('release_allowed','signer_configured','signer_matches','jupiter_configured')):raise execution.Unavailable('Server trading setup is unavailable. No new transaction is signed.')
    if saved['wallet_address']!=state['wallet'] or saved['revision']!=state['wallet_revision']:raise execution.Unavailable('Live wallet binding changed. Signing is blocked.')
    if side=='buy' and (not current['enabled'] or budget.settings()['revision']!=state['budget_revision']):raise execution.Unavailable('New real buys are paused or buy settings changed.')
    if side=='buy' and source_at is not None and not 0<=time.time()-source_at<=90:raise execution.Unavailable('Source buy expired before signing or broadcast.')


def begin_broadcast(order,state,lease_owner):
    """Atomically establish that broadcast began before a concurrent pause."""
    authorized(state,order['side'],order.get('source_at'),lease_owner)
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
            # Chain block times have second precision. Treat a same-second sell
            # as superseding the buy, including subsecond provider clock skew.
            sold=conn.execute("SELECT 1 FROM solana_copy_events WHERE observed_at>=? AND json_extract(details_json,'$.side')='sell' AND json_extract(details_json,'$.wallet')=? AND json_extract(details_json,'$.mint')=? AND CAST(json_extract(details_json,'$.source_at') AS INTEGER)>=? LIMIT 1",(int(order['source_at'])-5,order['source_wallet'],order['mint'],int(order['source_at']))).fetchone()
            if sold:raise execution.Unavailable('Source sold before live broadcast; buy canceled.')
        order.update(status='unknown',send_started_at=time.time())
        changed=conn.execute("UPDATE solana_real_orders SET status='unknown',details_json=? WHERE id=? AND status='signed'",(json.dumps(order,allow_nan=False),order['id']))
        if changed.rowcount!=1:raise Conflict('The stored transaction changed before broadcast.')
        conn.commit()


def tick(provider=None):
    import solana_copy_trader as paper
    import solana_real_budget as budget
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
                try:authorized(state,order['side'],order.get('source_at'),owner)
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
                elif result['state']=='expired_unseen':
                    if order.get('first_expired_at') and time.time()-order['first_expired_at']>=15:
                        order.update(status='expired',error='Finalized block height passed expiry; repeated history queries found no transaction.');terminal=order
                    else:order['first_expired_at']=time.time();save_order(order)
                elif execution.release_allowed() and time.time()-order.get('last_broadcast_at',order['send_started_at'])>=15:
                    # Same signed bytes and signature, never a replacement order.
                    authorized(state,'sell',lease_owner=owner);order['last_broadcast_at']=time.time();save_order(order)
                    try:provider.send(order['prepared'])
                    except execution.Unavailable:pass
            state['last_cycle']=time.time();commit_cycle(state,owner,terminal);return
        source=paper.load()[0];cfg=budget.settings()
        if cfg['revision']!=state['budget_revision']:
            state['enabled']=False;state['error']='Buy settings changed. Restart copying to apply them; existing exits remain monitored.'
        events=source_events(state['event_cursor']);latest_sell={}
        for _,e in events:
            if e['side']=='sell':latest_sell[(e['wallet'],e['mint'])]=max(e['source_at'],latest_sell.get((e['wallet'],e['mint']),0))
        for _,e in events:
            if e['side']=='sell':
                for p in state['positions'].values():
                    if p['mint']==e['mint'] and p['source_wallet']==e['wallet'] and e['source_at']>=p['source_at']:p['pending_reason']='source_sell'
        # Current swap quotes, not chart marks, trigger live protective exits.
        chosen=None
        for p in sorted(state['positions'].values(),key=lambda p:p['opened_at']):
            try:
                q=provider.build(state['wallet'],p['mint'],budget.SOL,p['quantity_raw'],{**state['policy'],'reserve_lamports':0})
                value=max(0,execution.uint(q['otherAmountThreshold'])-5000-state['policy']['max_priority_fee_lamports'])
                p.update(marked_at=time.time(),exit_value_lamports=value)
                gain=100*(value-p['cost_lamports'])/p['cost_lamports'];rules=p['protection'];reason=p['pending_reason'];quantity=p['quantity_raw']
                if reason not in ('manual','source_sell'):
                    if rules['stop_loss_percent']>0 and gain<=-rules['stop_loss_percent']:reason='stop_loss'
                    elif p['stages'] and value<=p['cost_lamports']:reason='profit_protection'
                    elif reason=='profit_take_half' and gain<p['next_gain_percent']:reason=None;p['pending_reason']=None
                if reason=='profit_take_half':quantity=max(1,quantity//2)
                if not reason and rules['profit_take_enabled'] and gain>=p['next_gain_percent'] and quantity>=2:
                    reason='profit_take_half';quantity//=2
                if reason and time.time()-p['last_exit_attempt_at']>=30:
                    p['pending_reason']=reason;p['last_exit_attempt_at']=time.time()
                    chosen={'id':str(uuid.uuid4()),'side':'sell','mint':p['mint'],'position_id':p['id'],'quantity':quantity,'reason':reason};break
            except execution.Unavailable:
                p['exit_value_lamports']=None;state['error']='A live exit quote is unavailable. Holdings remain unpriced and exits are retried.'
            if time.time()-now>100:break
        for cursor,e in events:
            state['event_cursor']=cursor
            if e['side']!='buy':continue
            reason=None
            if not state['enabled']:reason='Real buys paused.'
            elif e.get('status')!='paper_buy':reason='Source buy was not admitted by the paper copy rules.'
            elif not any(t['wallet']==e['wallet'] and t.get('active',True) for t in source['traders']):reason='Source wallet is no longer actively followed.'
            elif e['source_at']<state['started_at'] or not 0<=time.time()-e['source_at']<=90:reason='Outside the fresh live entry window.'
            elif latest_sell.get((e['wallet'],e['mint']),0)>=e['source_at']:reason='Source already sold before live detection.'
            elif not source['config']['enabled'] or time.time()-(source.get('last_cycle') or 0)>60 or source.get('error'):reason='Source feed is not current.'
            elif any(p['mint']==e['mint'] for p in state['positions'].values()):reason='One live position per coin; repeated buys skipped.'
            elif any(p.get('exit_value_lamports') is None or time.time()-(p.get('marked_at') or 0)>60 for p in state['positions'].values()):reason='An existing live holding lacks a current exit quote.'
            elif state['cooldowns'].get(e['mint'],0)>time.time():reason='Protective-exit cooldown.'
            elif chosen:reason='Another live order has priority.'
            if not reason:
                with closing(database.get_connection()) as conn:
                    known=conn.execute('SELECT 1 FROM solana_real_decisions WHERE source_id=?',(e['id'],)).fetchone()
                if known:continue
                chosen={'id':'buy:'+e['id'],'side':'buy','mint':e['mint'],'source_wallet':e['wallet'],'source_at':e['source_at'],'reason':'copy_buy',
                    'protection':{k:source['config'][k] for k in ('stop_loss_percent','profit_take_enabled','profit_take_first_percent','stop_loss_cooldown_seconds')}}
                reason='Selected fresh admitted copy signal.'
            decide(e,reason,time.time())
        if chosen and execution.release_allowed() and time.time()-now<110:
            policy={**state['policy'],'reserve_lamports':int(Decimal(str(cfg['config']['fee_reserve_sol']))*10**9)}
            if chosen['side']=='buy':
                available=provider.balance(state['wallet'])-policy['reserve_lamports']-5000-policy['max_priority_fee_lamports']-2*execution.uint(provider.rpc('getMinimumBalanceForRentExemption',[165]))
                if cfg['config']['buy_mode']=='fixed_usd':
                    price=Decimal(str(budget.sol_price()['usd']));amount=int((Decimal(str(cfg['config']['buy_usd']))/price*10**9).to_integral_value(rounding=ROUND_CEILING))
                else:amount=int(Decimal(max(0,available))*Decimal(str(cfg['config']['buy_percent']))/100)
                if not 0<amount<=available:raise execution.Unavailable('Buy size does not fit the current wallet after fees and reserve.')
            else:amount=chosen['quantity']
            order={**chosen,'wallet':state['wallet'],'created_at':time.time(),'status':'preparing'}
            save_order(order)
            try:
                prepared=provider.prepare(state['wallet'],chosen['side'],chosen['mint'],amount,policy,lambda:authorized(state,chosen['side'],chosen.get('source_at'),owner))
                order.update(prepared=prepared,status='signed',signature=prepared['signature']);save_signed(order,owner)
                begin_broadcast(order,state,owner)
                try:provider.send(prepared);order['status']='submitted';save_order(order)
                except execution.Unavailable:state['error']='Broadcast outcome unknown. Further orders wait for reconciliation.'
            except execution.Unavailable as error:
                if not order.get('send_started_at'):order.update(status='rejected',error=str(error));save_order(order)
                raise
            state['last_order_at']=time.time()
        state['last_cycle']=time.time();commit_cycle(state,owner)
    except Exception as error:
        state['last_cycle']=time.time();state['error']=str(error) if isinstance(error,(execution.Unavailable,Conflict)) else 'Real worker error. Stored orders must be reconciled before new submissions.'
        try:commit_cycle(state,owner,terminal)
        except Conflict:pass


async def worker():
    while True:
        try:await asyncio.to_thread(tick)
        except Exception:pass
        await asyncio.sleep(5)
