"""Prospective, isolated USD PAPER copy experiment. No wallet or swap submission."""
import asyncio
from collections import deque
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from threading import Lock
import time
import uuid

import requests
import database
import solana_meme_bot as bot
import solana_copy_solscan as solscan

CASH_MINTS = {'So11111111111111111111111111111111111111112',
              'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
              'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'}
DEFAULTS = {'enabled': False, 'trade_usd': 50, 'paper_capital_usd': 100000, 'poll_seconds': 10,'auto_discover':True,'max_active_traders':12,
            'minimum_trader_hold_seconds':60,'reentry_cooldown_seconds':1800}
SCHEMA = '''CREATE TABLE IF NOT EXISTS solana_copy_state (
 id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, state_json TEXT NOT NULL,
 lease_owner TEXT, lease_until REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS solana_copy_events (
 id TEXT PRIMARY KEY, observed_at REAL NOT NULL, details_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS solana_copy_closes (
 id TEXT PRIMARY KEY, closed_at REAL NOT NULL, details_json TEXT NOT NULL);'''
_lock = Lock()
_calls = deque()
_cooling_until = 0


class DataUnavailable(ValueError):
    pass


def configured():
    import solana_profile
    try: return bool(solana_profile.api_key())
    except solana_profile.Unavailable: return False


def initial():
    return {'config': deepcopy(DEFAULTS), 'cash_usd': 100000., 'positions': {}, 'traders': [],
            'cursors': {}, 'token_types': {}, 'started_at': None, 'last_cycle': None, 'health': 'paused',
            'error': None, 'comparison_baseline': None, 'copy_start_capital': None, 'history': [], 'settings_revision': 0}


def initialize(conn):
    conn.executescript(SCHEMA)
    conn.execute('INSERT OR IGNORE INTO solana_copy_state(id,revision,state_json) VALUES(1,0,?)',
                 (json.dumps(initial()),))
    conn.commit()


def load():
    with closing(database.get_connection()) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_copy_state'").fetchone():
            return initial(), 0
        row = conn.execute('SELECT state_json,revision FROM solana_copy_state WHERE id=1').fetchone()
    if not row:return initial(),0
    state=json.loads(row[0]);state['config']={**DEFAULTS,**state['config']}
    return state,row[1]


def regular_metrics():
    data = bot.public()
    equity = data['paper_equity_usd']
    return {'equity_usd': equity, 'net_usd': equity-data['config']['paper_capital_usd'] if equity is not None else None}


def metrics(state, now):
    fresh=[p for p in state['positions'].values() if p.get('marked_at') is not None and 0<=now-p['marked_at']<=120 and p.get('mark_usd') is not None]
    missing=len(state['positions'])-len(fresh)
    subtotal=state['cash_usd']+sum(p['mark_usd'] for p in fresh)
    equity=subtotal if not missing else None
    return {'equity_usd':equity,'net_usd':equity-state['config']['paper_capital_usd'] if equity is not None else None,
            'valuation':{'complete':not missing,'unpriced_holdings':missing,'priced_holdings':len(fresh),'cash_and_priced_holdings_usd':subtotal}}


def public():
    from solana_copy_setup import connection_status
    state, _ = load(); now = time.time(); own = metrics(state, now)
    regular = regular_metrics(); baseline = state.get('comparison_baseline')
    delta = regular['net_usd']-baseline['net_usd'] if baseline and regular['net_usd'] is not None else None
    own_delta = own['net_usd']-baseline['copy_net_usd'] if baseline and own['net_usd'] is not None else None
    own_return = 100*own_delta/baseline['copy_equity_usd'] if own_delta is not None else None
    regular_return = 100*delta/baseline['equity_usd'] if delta is not None and baseline['equity_usd'] > 0 else None
    with closing(database.get_connection()) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_copy_events'").fetchone()
        events = [json.loads(r[0]) for r in conn.execute('SELECT details_json FROM solana_copy_events ORDER BY rowid DESC LIMIT 100')] if exists else []
        closed = [json.loads(r[0]) for r in conn.execute('SELECT details_json FROM solana_copy_closes ORDER BY rowid DESC LIMIT 100')] if exists else []
        totals = conn.execute("SELECT COUNT(*),COALESCE(SUM(json_extract(details_json,'$.pnl_usd')),0),COALESCE(SUM(json_extract(details_json,'$.pnl_usd')>0),0),COALESCE(SUM(json_extract(details_json,'$.cost_usd')),0),COALESCE(SUM(json_extract(details_json,'$.cost_usd')>0),0) FROM solana_copy_closes").fetchone() if exists else (0,0,0,0,0)
    return {'paper': True, 'live_execution_enabled': False, 'provider_configured': configured(),'connection_checks':connection_status(),
            'solscan_configured':solscan.configured(),'discovery':{k:v for k,v in state.get('discovery',{}).items() if k!='pending'},'last_polled':state.get('last_polled',{}),
            'config': state['config'], 'revision': state['settings_revision'], 'health': state['health'],
            'error': state['error'], 'started_at': state['started_at'], 'last_cycle': state['last_cycle'],
            'public_rpc_enabled':state.get('public_rpc_enabled',False),'public_discovery':state.get('public_discovery',{}),
            'entry_protection':{'one_open_position_per_coin':True,'reentry_cooldown_seconds':state['config']['reentry_cooldown_seconds']},'discovery_error':state.get('discovery_error'),
            'rpc_coverage':{w:c.get('rpc_coverage') for w,c in state['cursors'].items() if c.get('provider')=='rpc'},
            'cash_usd': state['cash_usd'], **own, 'positions': [{**p,'mark_fresh':p.get('marked_at') is not None and 0<=now-p['marked_at']<=120 and p.get('mark_usd') is not None} for p in state['positions'].values()],
            'traders': state['traders'], 'events': events, 'closed': closed,
            'closed_count': totals[0], 'realized_pnl_usd': totals[1], 'wins': totals[2],
            'closed_cost_usd':totals[3] if totals[4]==totals[0] else None,
            'closed_return_percent':100*totals[1]/totals[3] if totals[0] and totals[4]==totals[0] and totals[3]>0 else None,
            'average_closed_pnl_usd':totals[1]/totals[0] if totals[0] else None,
            'comparison': {'since': baseline['at'] if baseline else None, 'copy_change_usd':own_delta, 'copy_return_percent': own_return,
                           'regular_return_percent': regular_return, 'regular_change_usd': delta},
            'history': state['history'], 'server_time': now}


def configure(changes, expected_revision):
    if not isinstance(changes, dict) or not changes or not set(changes) <= set(DEFAULTS) or type(expected_revision) is not int:
        raise ValueError('Known settings and revision required.')
    for key, value in changes.items():
        if key in ('enabled','auto_discover'):
            if type(value) is not bool: raise ValueError('Boolean required.')
            # An explicit start may wait for a key saved later in Profile.
        else:
            value = bot.num(value)
            if value < 0 or key == 'paper_capital_usd' and value == 0: raise ValueError('Use nonnegative numbers and positive virtual capital.')
            if key=='max_active_traders' and (value<1 or value!=int(value)):raise ValueError('Maximum active traders must be a positive whole number.')
            changes[key] = value
    state, revision = load()
    if state['settings_revision'] != expected_revision: raise bot.Conflict('Settings changed. Refresh before saving.')
    added = changes.get('paper_capital_usd', state['config']['paper_capital_usd'])-state['config']['paper_capital_usd']
    if added < 0: raise ValueError('Virtual capital can only increase.')
    state['cash_usd'] += added
    if changes.get('enabled') and not state['config']['enabled']:
        if state.get('started_at'):state['buy_resume_at']=time.time()
        held={p['wallet'] for p in state['positions'].values()}
        for wallet in state['cursors']:
            if wallet not in held: state['cursors'][wallet]={'at':time.time(),'provider':state['cursors'][wallet].get('provider','birdeye')}
    state['config'].update(changes); state['settings_revision'] += 1
    state['health'] = 'starting' if state['config']['enabled'] else 'paused_entries_monitoring_sells'
    with closing(database.get_connection()) as conn:
        initialize(conn)
        if conn.execute('UPDATE solana_copy_state SET state_json=?,revision=revision+1 WHERE id=1 AND revision=?', (json.dumps(state, allow_nan=False), revision)).rowcount != 1:
            raise bot.Conflict('State changed. Refresh and retry.')
        conn.commit()
    return public()


class Birdeye:
    def request(self, path, *, params=None, payload=None):
        global _cooling_until
        import solana_profile
        try: key = solana_profile.api_key()
        except solana_profile.Unavailable:
            raise DataUnavailable('Replace the saved API key in Profile.') from None
        if not key: raise DataUnavailable('Add a Birdeye API key in Profile. No wallets or trades are invented.')
        with _lock:
            now = time.monotonic()
            while _calls and now-_calls[0] >= 60: _calls.popleft()
            if now < _cooling_until or len(_calls) >= 45: raise DataUnavailable('Wallet-data request budget cooling down.')
            _calls.append(now)
        kwargs = {'headers': {'X-API-KEY': key, 'x-chain': 'solana'}, 'timeout': 4, 'allow_redirects': False}
        try:
            response = requests.post('https://public-api.birdeye.so'+path, json=payload, **kwargs) if payload is not None else requests.get('https://public-api.birdeye.so'+path, params=params, **kwargs)
            if response.status_code in (401,403): raise DataUnavailable(f'Birdeye {path}: HTTP {response.status_code}; key or plan does not permit this endpoint.')
            if response.status_code == 429:
                with _lock: _cooling_until = time.monotonic()+60
                raise DataUnavailable('Wallet-data provider rate limit; retrying after cooldown.')
            response.raise_for_status()
            if len(response.content) > 2_000_000: raise ValueError()
            data = response.json()
            if not isinstance(data, dict) or data.get('success') is False: raise ValueError()
            return data['data']
        except DataUnavailable: raise
        except Exception: raise DataUnavailable('Wallet-data response unavailable or unsupported. No simulated signal created.') from None

    def qualify(self,row):
        try:
            address=bot.mint(row['address'])
            profit=bot.num(row['realized_pnl'])
            if profit<=0:return None
            data=self.request('/wallet/v2/pnl/details',payload={'wallet':address,'duration':'all','position_scope':'cumulative','pnl_method':'wac','sort_by':'last_trade','limit':1})
            summary=data['summary'];counts=summary['counts']
            pnl=bot.num(summary['pnl']['realized_profit_usd']);win=bot.num(counts['win_rate']);sells=bot.num(counts['total_sell'])
            if pnl<=0 or not .5<win<=1 or sells<20:return None
            return {'wallet':address,'ranking_realized_usd':profit,'ranking_window':row.get('ranking_window','1W'),
                    'seven_day_realized_usd':profit if row.get('ranking_window','1W')=='1W' else None,
                    'historical_realized_usd':pnl,'win_rate_percent':win*100,'sell_count':sells}
        except DataUnavailable:raise
        except (ValueError,KeyError,TypeError):return None

    def discover(self,state,clock):
        import solana_copy_discovery as discovery
        import solana_copy_solscan as solscan
        try:discovery.advance(state,self,clock(),lambda:'rpc' if state.get('public_rpc_enabled') else 'solscan' if solscan.configured() else 'birdeye',clock=clock)
        except DataUnavailable:raise
        except (ValueError,KeyError,TypeError):raise DataUnavailable('Profitable-trader discovery response unsupported.') from None

    def meme(self, address):
        data=self.request('/defi/v3/token/meme/detail/single',params={'address':address})
        if not isinstance(data,dict) or data.get('address')!=address: raise DataUnavailable('Meme classification unavailable for this token.')
        info=data.get('meme_info')
        return isinstance(info,dict) and bool(info.get('source') or info.get('platform_id'))

    def trades(self, wallet, cursor, now):
        if cursor.get('provider')=='rpc':
            import solana_copy_rpc
            return solana_copy_rpc.trades(wallet,cursor,now)
        if cursor.get('provider')=='solscan':
            import solana_copy_solscan as solscan
            return solscan.trades(wallet,cursor,now)
        window = cursor.get('window') or {'after':cursor['at']-2,'before':int(now),'offset':0}
        data = self.request('/trader/txs/seek_by_time', params={'address':wallet,'tx_type':'swap','after_time':int(window['after']), 'before_time':window['before'],'offset':window['offset'],'limit':100,'ui_amount_mode':'raw'})
        if not isinstance(data,dict) or not isinstance(data.get('items'),list) or not isinstance(data.get('hasNext'),bool): raise DataUnavailable('Unsupported wallet history pagination.')
        rows = data['items']
        if len(rows) > 100: raise DataUnavailable('Wallet history page exceeds protocol limit.')
        if data['hasNext']:
            next_window = {**window,'offset':window['offset']+100}
            if next_window['offset'] >= 10000: raise DataUnavailable('Wallet history overflow; comparison has a data gap. Pause and inspect this trader.')
            updated = {**cursor,'window':next_window}
        else: updated = {'at':window['before']}
        return rows, updated


def signals(rows, wallet):
    result = []
    for row in rows:
        try:
            if not isinstance(row,dict) or row.get('owner') != wallet: continue
            timestamp = bot.num(row['block_unix_time']); tx = row['tx_hash']
            if not isinstance(tx,str) or not 32 <= len(tx) <= 128: continue
            base, quote = row['base'], row['quote']
            if {base['type_swap'],quote['type_swap']} != {'from','to'} or base['address'] == quote['address']: continue
            for leg in (base,quote):
                address = bot.mint(leg['address'])
                if address in CASH_MINTS: continue
                side = 'buy' if leg['type_swap'] == 'to' else 'sell'
                raw_id = json.dumps([wallet,tx,row['ins_index'],row['inner_ins_index'],address,side],separators=(',',':'))
                result.append({'id':hashlib.sha256(raw_id.encode()).hexdigest(),'wallet':wallet,'mint':address,'side':side,'source_at':timestamp,'tx_hash':tx,'instruction':str(row['ins_index'])+':'+str(row['inner_ins_index']),'source':row.get('source','birdeye'),
                    'source_position_before':row.get('source_position_before')})
        except (KeyError, TypeError, ValueError): continue
    return sorted(result,key=lambda r:(r['source_at'],r['tx_hash'],r['instruction'],r['side']=='buy'))


def prices(addresses, provider, clock):
    raw=[]; addresses=list(dict.fromkeys(addresses))
    batches=[addresses[i:i+20] for i in range(0,len(addresses),20)]
    def fetch(batch):
        try:
            rows=provider.pairs(batch);received=clock()
            return [(row,received) for row in rows]
        except Exception:return []
    if batches:
        with ThreadPoolExecutor(max_workers=4) as pool:raw=[row for rows in pool.map(fetch,batches) for row in rows]
    result={}
    for row,received in raw:
        try:
            if row['chainId'] != 'solana': continue
            address=bot.mint(row['baseToken']['address']); pair=bot.mint(row['pairAddress']); price=bot.num(row['priceUsd'])
            if address not in addresses or price <= 0: continue
            result[(address,pair)]={'price':price,'pool':pair,'symbol':str(row['baseToken'].get('symbol') or address[:6])[:80],'at':received,
                'quote_mint':(row.get('quoteToken') or {}).get('address'),
                'liquidity_usd':(row.get('liquidity') or {}).get('usd'),
                'volume_m5_usd':(row.get('volume') or {}).get('m5'),
                'trades_m5':sum((row.get('txns') or {}).get('m5',{}).get(k,0) for k in ('buys','sells')),
                'inactive_pool':row.get('liquidity','unknown') is None and (row.get('volume') or {}).get('m5')==0}
        except (ValueError,KeyError,TypeError): continue
    return result


def holding_quote(position,quotes,now):
    """Recover fungible-token marks from an observed active same-mint pool.

    A price-only alternate is insufficient: require a known cash quote, positive
    published liquidity, and trading in the last five minutes. No stale marks,
    entry price, symbol matching or another mint can supply a liquidation mark.
    """
    exact=quotes.get((position['mint'],position['pool']))
    if exact and not exact.get('inactive_pool') and 0<=now-exact['at']<=45:return exact
    candidates=[]
    for (address,pair),quote in quotes.items():
        if address!=position['mint'] or pair==position['pool'] or quote.get('quote_mint') not in CASH_MINTS:continue
        try:
            if bot.num(quote.get('liquidity_usd'))<=0 or bot.num(quote.get('volume_m5_usd'))<=0 or bot.num(quote.get('trades_m5'))<=0:continue
            if not 0<=now-quote['at']<=45:continue
            candidates.append(quote)
        except (ValueError,TypeError,KeyError):continue
    return max(candidates,key=lambda q:(float(q['liquidity_usd']),q['pool']),default=None)


def queue_exits(ids, pause, expected_revision):
    if not isinstance(ids,list) or not ids or any(not isinstance(i,str) or len(i)!=64 for i in ids) or type(pause) is not bool or type(expected_revision) is not int:
        raise ValueError('Exact copied position identities required.')
    state,revision=load()
    if state['settings_revision']!=expected_revision: raise bot.Conflict('Settings changed. Refresh before selling.')
    for key in set(ids):
        if key in state['positions']:
            state['positions'][key]['pending_sell']='manual'
            state['positions'][key]['manual_sell']=True
    if pause: state['config']['enabled']=False; state['settings_revision']+=1
    with closing(database.get_connection()) as conn:
        initialize(conn)
        if conn.execute('UPDATE solana_copy_state SET state_json=?,revision=revision+1 WHERE id=1 AND revision=?',(json.dumps(state,allow_nan=False),revision)).rowcount!=1:
            raise bot.Conflict('Copy cycle changed. Refresh before retrying.')
        conn.commit()
    return public()


def record_close(state, key, quote, now, reason):
    p=state['positions'].pop(key); proceeds=max(0,p['quantity']*quote['price']*.99-.1)
    state['cash_usd']+=proceeds
    state.setdefault('mint_cooldowns',{})[p['mint']]=now+state['config']['reentry_cooldown_seconds']
    return {**p,'id':str(uuid.uuid4()),'closed_at':now,'exit_price_usd':quote['price'],'proceeds_usd':proceeds,'pnl_usd':proceeds-p['cost_usd'],'reason':reason}


def tick(*, provider=None, market=None, clock=time.time):
    now=clock(); state,revision=load(); owner=str(uuid.uuid4())
    with closing(database.get_connection()) as conn:
        initialize(conn)
        if conn.execute('UPDATE solana_copy_state SET lease_owner=?,lease_until=? WHERE id=1 AND lease_until<=?',(owner,now+180,now)).rowcount != 1: raise bot.Conflict('Copy cycle already running.')
        conn.commit()
    events=[]; closes=[]; provider=provider or Birdeye(); market=market or bot.ReadOnlyProviders(); state['error']=None
    try:
        cooldown=state['config']['reentry_cooldown_seconds']
        state['mint_cooldowns']={mint:until for mint,until in state.get('mint_cooldowns',{}).items() if until>now}
        if state.get('entry_guard_version')!=1:
            with closing(database.get_connection()) as conn:
                for row in conn.execute('SELECT closed_at,details_json FROM solana_copy_closes WHERE closed_at>?',(now-cooldown,)):
                    closed=json.loads(row['details_json']);mint=closed['mint']
                    state['mint_cooldowns'][mint]=max(state['mint_cooldowns'].get(mint,0),row['closed_at']+cooldown)
            state['entry_guard_version']=1
        enabled=state['config']['enabled']; incoming=[]
        rpc_wallets={r['wallet'] for r in state['traders'] if state['cursors'].get(r['wallet'],{}).get('provider')=='rpc'}
        connection_missing=enabled and isinstance(provider,Birdeye) and not configured() and not state.get('public_rpc_enabled') and not rpc_wallets
        if connection_missing:
            enabled=False
            state['error']='Automatic PAPER copying is enabled; waiting for a Birdeye data key in Profile.'
        if enabled and not state['started_at']:
            state['started_at']=now; state['copy_start_capital']=state['config']['paper_capital_usd']
        if state['started_at'] and not state['comparison_baseline']:
            baseline=regular_metrics(); own=metrics(state,now)
            if baseline['net_usd'] is not None and baseline['equity_usd']>0 and own['net_usd'] is not None and own['equity_usd']>0:
                state['comparison_baseline']={**baseline,'copy_net_usd':own['net_usd'],'copy_equity_usd':own['equity_usd'],'at':now}
        if enabled and not state['traders'] and not hasattr(provider,'discover'):
            leaders=provider.leaders()
            enrolled=clock()
            state['traders']=[{**r,'selected_at':enrolled} for r in leaders]
            state['cursors']={r['wallet']:{'at':enrolled} for r in leaders}
            if not leaders: raise DataUnavailable('No ranked wallets meet the historical-profit and win-rate checks.')
        watched={p['wallet'] for p in state['positions'].values()} | ({r['wallet'] for r in state['traders'] if r.get('active',True)} if enabled else set())
        last_polled=state.setdefault('last_polled',{})
        # Oldest first prevents a rate-limited large list starving its later wallets.
        for wallet in sorted(watched,key=lambda w:(last_polled.get(w,0),w))[:3]:
            last_polled[wallet]=clock()
            try:
                rows,cursor=provider.trades(wallet,state['cursors'][wallet],clock())
                gathered=state['cursors'][wallet].get('pending',[])+signals(rows,wallet)
                if 'window' in cursor: cursor['pending']=gathered
                else: incoming.extend(gathered)
                cursor['provider']=state['cursors'][wallet].get('provider','birdeye')
                state['cursors'][wallet]=cursor
            except DataUnavailable as error: state['error']=str(error)
        category_errors={}; checks=0
        if enabled:
            for address,is_rpc in dict.fromkeys((r['mint'],r['wallet'] in rpc_wallets and r.get('source')=='solana_rpc_pump') for r in incoming if r['side']=='buy' and 0<=clock()-r['source_at']<=90):
                if is_rpc:
                    state['token_types']['rpc:'+address]={'platform_supported':True,'at':clock(),'basis':'Supported Pump/PumpSwap instruction; token meme status is not independently verified.'}
                    continue
                category=state['token_types'].get(address)
                if category and clock()-category['at']<=86400:continue
                if checks>=2 or clock()-now>100:
                    category_errors[address]='Meme metadata request capacity reached; this signal was not filled.';continue
                checks+=1
                try:state['token_types'][address]={'is_meme':provider.meme(address),'at':clock()}
                except DataUnavailable as error:category_errors[address]=str(error)
        observed=clock()
        if observed-now > 150: raise DataUnavailable('Copy cycle exceeded observation budget.')
        quotes=prices([*(p['mint'] for p in state['positions'].values()),*(s['mint'] for s in incoming)],market,clock)
        observed=clock()
        if observed-now>150:raise DataUnavailable('Copy cycle exceeded observation budget.')
        quotes={key:q for key,q in quotes.items() if 0<=observed-q['at']<=45}
        for p in state['positions'].values():
            quote=holding_quote(p,quotes,observed)
            if quote and quote['pool']!=p['pool']:
                p.setdefault('entry_pool',p['pool'])
                p.setdefault('pool_changes',[]).append({'from':p['pool'],'to':quote['pool'],'at':observed,'reason':'Original pool absent or inactive; active same-mint cash-quoted pool observed.'})
                p['pool']=quote['pool']
            p['mark_usd']=max(0,p['quantity']*quote['price']*.99-.1) if quote else None
            p['marked_at']=quote['at'] if quote else None
        with closing(database.get_connection()) as conn:
            ids=list({r['id'] for r in incoming}); known=set()
            for start in range(0,len(ids),500):
                chunk=ids[start:start+500]
                known.update(r[0] for r in conn.execute('SELECT id FROM solana_copy_events WHERE id IN ('+','.join('?' for _ in chunk)+')',chunk))
        latest_sell={(s['wallet'],s['mint']):max(r['source_at'] for r in incoming if r['wallet']==s['wallet'] and r['mint']==s['mint'] and r['side']=='sell') for s in incoming if s['side']=='sell'}
        for signal in sorted(incoming,key=lambda s:(s['source_at'],s['tx_hash'],s['instruction'],s['side']=='buy')):
            if signal['id'] in known: continue
            known.add(signal['id']); signal={**signal,'observed_at':observed,'latency_seconds':observed-signal['source_at'],'status':'skipped'}
            wallet,mint=signal['wallet'],signal['mint']; selected=next((r['selected_at'] for r in state['traders'] if r['wallet']==wallet),state['started_at'])
            if signal['source_at'] < selected or signal['source_at'] > observed+5:
                signal['reason']='Outside prospective follow window.'
            elif signal['side']=='sell':
                lots=[(key,p) for key,p in state['positions'].items() if p['wallet']==wallet and p['mint']==mint]
                for key,p in lots:
                    p['pending_sell']=signal['id']
                signal['status']='sell_queued' if lots else 'observed'
                signal['reason']='Close copied lots on the next observed price.' if lots else 'No copied holding for this wallet.'
            elif not enabled: signal['reason']='New copied buys paused.'
            elif signal['source_at']<max(state.get('buy_resume_at',0),next((r.get('buy_resume_at',0) for r in state['traders'] if r['wallet']==wallet),0)): signal['reason']='Buy occurred before copying resumed.'
            elif not any(r['wallet']==wallet and r.get('active',True) for r in state['traders']): signal['reason']='Trader no longer passes the historical checks; only exits monitored.'
            elif observed-signal['source_at'] > 90: signal['reason']='Signal arrived more than 90 seconds late.'
            elif latest_sell.get((wallet,mint),-1) >= signal['source_at']: signal['reason']='Trader already sold before detection.'
            elif any(p['mint']==mint for p in state['positions'].values()):signal['reason']='Already holding this coin; repeated adds are not copied.'
            elif state['config']['reentry_cooldown_seconds'] and observed<state.get('mint_cooldowns',{}).get(mint,0):signal['reason']='Coin is cooling down after a copied exit; no immediate re-entry.'
            elif signal.get('source')=='solana_rpc_pump' and isinstance(signal.get('source_position_before'),(float,int)) and signal['source_position_before']>0:signal['reason']='Source wallet already held this coin; add-on purchase skipped.'
            elif state['config']['trade_usd'] <= 0: signal['reason']='Trade amount is zero.'
            elif state['cash_usd'] < state['config']['trade_usd']+.1: signal['reason']='Insufficient copy-paper cash.'
            else:
                category=state['token_types'].get(('rpc:' if wallet in rpc_wallets and signal.get('source')=='solana_rpc_pump' else '')+mint)
                if not category or observed-category['at']>86400:
                    signal['reason']=category_errors.get(mint,'Meme category observation unavailable.'); events.append(signal); continue
                eligible=category.get('platform_supported',False) if wallet in rpc_wallets and signal.get('source')=='solana_rpc_pump' else category.get('is_meme',False)
                if not eligible:
                    signal['reason']='Provider does not identify this token as a meme coin.'; events.append(signal); continue
                quote=next((q for (a,_),q in quotes.items() if a==mint),None)
                if not quote: signal['reason']='No current positive pool price. No fill invented.'
                else:
                    amount=state['config']['trade_usd']; cost=amount+.1
                    p={'id':signal['id'],'mint':mint,'wallet':wallet,'pool':quote['pool'],'symbol':quote['symbol'],'quantity':amount/(quote['price']*1.01),'cost_usd':cost,'entry_price_usd':quote['price'],'opened_at':observed,'source_at':signal['source_at'],'latency_seconds':signal['latency_seconds'],'tx_hash':signal['tx_hash'],'marked_at':quote['at'],'source':signal.get('source','birdeye')}
                    p['mark_usd']=max(0,p['quantity']*quote['price']*.99-.1)
                    state['positions'][p['id']]=p; state['cash_usd']-=cost; signal['status']='paper_buy'; signal['reason']='Copied buy; no liquidity or volume gate.'
            events.append(signal)
        for key,p in list(state['positions'].items()):
            quote=holding_quote(p,quotes,observed)
            if p.get('pending_sell') and quote:
                closes.append(record_close(state,key,quote,observed,'manual_sell' if p.get('manual_sell') else 'trader_sell'))
        if enabled and hasattr(provider,'discover') and now>=state.get('discovery_retry_at',0):
            try:
                provider.discover(state,clock);state['discovery_error']=None
            except DataUnavailable as error:
                state['discovery_error']=str(error);state['discovery_retry_at']=now+600
                if not state.get('public_rpc_enabled') and not rpc_wallets:state['error']=str(error)
        if enabled and state.get('public_rpc_enabled') and state['config']['auto_discover']:
            from solana_copy_public_discovery import advance
            advance(state,clock)
        state['health']='waiting_for_data_key' if connection_missing else 'data_unavailable' if state['error'] else ('running' if state['traders'] else 'searching_profitable_traders') if enabled else 'paused_entries_monitoring_sells'
        state['last_cycle']=observed
        own=metrics(state,observed); regular=regular_metrics(); baseline=state.get('comparison_baseline')
        if baseline and (not state['history'] or observed-state['history'][-1]['at']>=60):
            state['history']=(state['history']+[{'at':observed,'copy_return_percent':100*(own['net_usd']-baseline['copy_net_usd'])/baseline['copy_equity_usd'] if own['net_usd'] is not None else None,'regular_return_percent':100*(regular['net_usd']-baseline['net_usd'])/baseline['equity_usd'] if baseline and regular['net_usd'] is not None else None}])[-2880:]
    except DataUnavailable as error:
        state['error']=str(error); state['health']='data_unavailable'; state['last_cycle']=clock()
    except Exception:
        # Do not commit partially simulated actions after an unexpected error.
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE solana_copy_state SET lease_until=0,lease_owner=NULL WHERE id=1 AND lease_owner=?',(owner,)); conn.commit()
        raise
    with closing(database.get_connection()) as conn:
        if conn.execute('UPDATE solana_copy_state SET state_json=?,revision=revision+1,lease_until=0,lease_owner=NULL WHERE id=1 AND revision=? AND lease_owner=? AND lease_until>?',(json.dumps(state,allow_nan=False),revision,owner,clock())).rowcount != 1:
            conn.rollback()
            conn.execute('UPDATE solana_copy_state SET lease_until=0,lease_owner=NULL WHERE id=1 AND lease_owner=?',(owner,)); conn.commit()
            raise bot.Conflict('Copy settings changed; cycle discarded.')
        for signal in events: conn.execute('INSERT OR IGNORE INTO solana_copy_events VALUES(?,?,?)',(signal['id'],signal['observed_at'],json.dumps(signal,allow_nan=False)))
        for closed in closes: conn.execute('INSERT INTO solana_copy_closes VALUES(?,?,?)',(closed['id'],closed['closed_at'],json.dumps(closed,allow_nan=False)))
        conn.commit()
    return public()


async def worker():
    while True:
        started=time.monotonic()
        try: await asyncio.to_thread(tick)
        except Exception: pass
        state,_=await asyncio.to_thread(load)
        await asyncio.sleep(max(.1,max(10,state['config']['poll_seconds'])-(time.monotonic()-started)))
