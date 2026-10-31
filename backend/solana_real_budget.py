"""Owner live-sizing preferences and read-only SOL funding preview. No orders."""
from contextlib import closing
from copy import deepcopy
from decimal import Decimal,ROUND_CEILING
import json
import math
from threading import Lock
import time
import database
from solana_meme_bot import Conflict

SOL='So11111111111111111111111111111111111111112'
STABLES={'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v','Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'}
DEFAULTS={'buy_mode':'fixed_usd','buy_usd':None,'buy_percent':None,'fee_reserve_sol':0.01}
SCHEMA='''CREATE TABLE IF NOT EXISTS solana_real_copy_budget(
 id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,config_json TEXT NOT NULL);'''
_lock=Lock();_cache={}


def settings():
    with closing(database.get_connection()) as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_real_copy_budget'").fetchone()
        row=conn.execute('SELECT * FROM solana_real_copy_budget WHERE id=1').fetchone() if exists else None
    return {'revision':row['revision'] if row else 0,'config':{**DEFAULTS,**(json.loads(row['config_json']) if row else {})},
            'budget_basis':'available_native_sol','execution_enabled':False}


def number(value,nullable=False):
    if value is None and nullable:return None
    if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('Finite nonnegative numbers required.')
    return value


def configure(changes,revision):
    if type(revision) is not int or revision<0 or not isinstance(changes,dict) or not changes or set(changes)-set(DEFAULTS):raise ValueError('Buy settings and revision required.')
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA)
        conn.execute('INSERT OR IGNORE INTO solana_real_copy_budget VALUES(1,0,?)',(json.dumps(DEFAULTS),));conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM solana_real_copy_budget WHERE id=1').fetchone()
        if row['revision']!=revision:raise Conflict('Real buy settings changed. Reload before saving.')
        config={**DEFAULTS,**json.loads(row['config_json']),**changes}
        if config['buy_mode'] not in ('fixed_usd','balance_percent'):raise ValueError('Choose dollars or balance percentage.')
        for field in ('buy_usd','buy_percent'):number(config[field],nullable=True)
        if config['buy_percent'] is not None and config['buy_percent']>100:raise ValueError('Balance percentage cannot exceed 100%.')
        selected=config['buy_usd' if config['buy_mode']=='fixed_usd' else 'buy_percent']
        if selected is None or selected<=0:raise ValueError('Enter a positive buy amount.')
        reserve=Decimal(str(number(config['fee_reserve_sol'])))*10**9
        if reserve!=reserve.to_integral_value():raise ValueError('SOL reserve supports up to nine decimals.')
        conn.execute('UPDATE solana_real_copy_budget SET revision=revision+1,config_json=? WHERE id=1',(json.dumps(config,allow_nan=False),));conn.commit()
    return settings()


def sol_price():
    from solana_meme_bot import ReadOnlyProviders
    candidates=[]
    for row in ReadOnlyProviders().pairs([SOL]):
        try:
            if row['chainId']!='solana' or row['baseToken']['address']!=SOL or row['quoteToken']['address'] not in STABLES:continue
            price=float(row['priceUsd']);liquidity=float(row['liquidity']['usd']);volume=float(row['volume']['m5'])
            trades=row['txns']['m5']['buys']+row['txns']['m5']['sells']
            if not all(math.isfinite(v) and v>0 for v in (price,liquidity,volume)) or trades<=0:continue
            candidates.append((liquidity,row['pairAddress'],price))
        except (KeyError,ValueError,TypeError):continue
    if not candidates:raise ValueError('Current SOL reference price unavailable.')
    _,pool,price=max(candidates)
    return {'usd':price,'pool':pool,'observed_at':time.time(),'source':'Dexscreener · active SOL/stablecoin pool'}


def observe(address,now):
    """Thirty-second cache; failures never fall back to a previous balance."""
    from solana_copy_rpc import request
    with _lock:
        cached=_cache.get(address)
        if cached and 0<=now-cached['attempted_at']<30:return deepcopy(cached)
        observation={'address':address,'attempted_at':now,'observed_at':None,'lamports':None,'slot':None,'price':None,'error':None,'price_error':None}
        try:
            result=request('getBalance',[address,{'commitment':'confirmed'}])
            amount=result['value'];slot=result['context']['slot']
            if type(amount) is not int or not 0<=amount<2**64 or type(slot) is not int or slot<0:raise ValueError()
            observation.update(lamports=amount,slot=slot,observed_at=time.time())
        except Exception:observation['error']='Wallet SOL balance unavailable. No funding estimate is used.'
        if observation['lamports'] is not None:
            try:observation['price']=sol_price()
            except Exception:observation['price_error']='SOL dollar estimate unavailable; the SOL balance is still shown.'
        # This single-owner cache is bounded even after wallet changes.
        _cache.clear();_cache[address]=observation
        return deepcopy(observation)


def preview(observation,config,now):
    reserve=int(Decimal(str(config['fee_reserve_sol']))*10**9)
    at=observation.get('observed_at');fresh=at is not None and 0<=now-at<=45
    balance=observation['lamports'] if fresh else None
    spendable=max(0,balance-reserve) if balance is not None else None
    price=observation.get('price');price_fresh=price and 0<=now-price['observed_at']<=45
    usd=Decimal(str(price['usd'])) if price_fresh else None
    requested=None;amount=None;reason='Set a buy amount.'
    if spendable is None:reason=observation.get('error') or 'Wallet balance is stale or unavailable.'
    elif config['buy_mode']=='fixed_usd' and config['buy_usd']:
        if usd:
            requested=int((Decimal(str(config['buy_usd']))/usd*10**9).to_integral_value(rounding=ROUND_CEILING))
        else:reason='A current SOL dollar price is required for dollar-sized buys.'
    elif config['buy_mode']=='balance_percent' and config['buy_percent']:
        requested=int(Decimal(spendable)*Decimal(str(config['buy_percent']))/100)
    if requested is not None:
        if requested<=0:reason='Buy amount rounds below one lamport.'
        elif requested>spendable:reason='Insufficient SOL after the fee reserve. Reduce the buy amount.'
        else:amount=requested;reason='Funding preview fits the available SOL.'
    return {'wallet_address':observation['address'],'balance_sol':balance/10**9 if balance is not None else None,
            'spendable_sol':spendable/10**9 if spendable is not None else None,'fee_reserve_sol':config['fee_reserve_sol'],
            'spendable_usd':float(Decimal(spendable)/10**9*usd) if spendable is not None and usd else None,
            'next_buy_sol':amount/10**9 if amount is not None else None,
            'next_buy_usd':float(Decimal(amount)/10**9*usd) if amount is not None and usd else None,
            'funding_fits':amount is not None,'reason':reason,'balance_observed_at':at,'slot':observation.get('slot'),
            'valid_until':min(at+45,price['observed_at']+45) if at and price_fresh else at+45 if at else now,
            'price':price if price_fresh else None,'error':observation.get('error'),'price_error':observation.get('price_error'),
            'basis':'Native SOL only. Excludes existing tokens, WSOL and USDC. Fee reserve is a buffer, not an exact fee quote. Preview only; no funds are reserved or spent.'}


def public():
    from solana_real_copy import wallet
    saved=wallet();config=settings();now=time.time()
    if saved['wallet_address'] and saved['verified_at']:observation=observe(saved['wallet_address'],now)
    else:observation={'address':'','lamports':None,'observed_at':None,'error':'Link and verify Phantom to read its SOL balance.'}
    result=preview(observation,config['config'],time.time())
    from solana_real_worker import load as live_state
    return {'settings':config,'funding':result,'server_time':time.time(),'live_execution_enabled':live_state()[0]['enabled']}
