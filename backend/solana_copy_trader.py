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

CASH_MINTS = {'So11111111111111111111111111111111111111112',
              'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
              'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'}
DEFAULTS = {'enabled': False, 'trade_usd': 50, 'paper_capital_usd': 100000, 'poll_seconds': 10}
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
    return (json.loads(row[0]), row[1]) if row else (initial(), 0)


def regular_metrics():
    data = bot.public()
    equity = data['paper_equity_usd']
    return {'equity_usd': equity, 'net_usd': equity-data['config']['paper_capital_usd'] if equity is not None else None}


def metrics(state, now):
    complete = all(p.get('marked_at') is not None and 0 <= now-p['marked_at'] <= 120 and p.get('mark_usd') is not None for p in state['positions'].values())
    equity = state['cash_usd'] + sum(p.get('mark_usd') or 0 for p in state['positions'].values()) if complete else None
    return {'equity_usd': equity, 'net_usd': equity-state['config']['paper_capital_usd'] if equity is not None else None}


def public():
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
        totals = conn.execute("SELECT COUNT(*),COALESCE(SUM(json_extract(details_json,'$.pnl_usd')),0),COALESCE(SUM(json_extract(details_json,'$.pnl_usd')>0),0) FROM solana_copy_closes").fetchone() if exists else (0,0,0)
    return {'paper': True, 'live_execution_enabled': False, 'provider_configured': configured(),
            'config': state['config'], 'revision': state['settings_revision'], 'health': state['health'],
            'error': state['error'], 'started_at': state['started_at'], 'last_cycle': state['last_cycle'],
            'cash_usd': state['cash_usd'], **own, 'positions': [{**p,'mark_fresh':p.get('marked_at') is not None and 0<=now-p['marked_at']<=120 and p.get('mark_usd') is not None} for p in state['positions'].values()],
            'traders': state['traders'], 'events': events, 'closed': closed,
            'closed_count': totals[0], 'realized_pnl_usd': totals[1], 'wins': totals[2],
            'comparison': {'since': baseline['at'] if baseline else None, 'copy_change_usd':own_delta, 'copy_return_percent': own_return,
                           'regular_return_percent': regular_return, 'regular_change_usd': delta},
            'history': state['history'], 'server_time': now}


def configure(changes, expected_revision):
    if not isinstance(changes, dict) or not changes or not set(changes) <= set(DEFAULTS) or type(expected_revision) is not int:
        raise ValueError('Known settings and revision required.')
    for key, value in changes.items():
        if key == 'enabled':
            if type(value) is not bool: raise ValueError('Boolean required.')
            if value and not configured(): raise DataUnavailable('Connect BIRDEYE_API_KEY on the server before starting. Wallet ranking and trade-history access are required.')
        else:
            value = bot.num(value)
            if value < 0 or key == 'paper_capital_usd' and value == 0: raise ValueError('Use nonnegative numbers and positive virtual capital.')
            changes[key] = value
    state, revision = load()
    if state['settings_revision'] != expected_revision: raise bot.Conflict('Settings changed. Refresh before saving.')
    added = changes.get('paper_capital_usd', state['config']['paper_capital_usd'])-state['config']['paper_capital_usd']
    if added < 0: raise ValueError('Virtual capital can only increase.')
    state['cash_usd'] += added
    if changes.get('enabled') and not state['config']['enabled']:
        held={p['wallet'] for p in state['positions'].values()}
        for wallet in state['cursors']:
            if wallet not in held: state['cursors'][wallet]={'at':time.time()}
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
            if response.status_code in (401,403): raise DataUnavailable('Wallet-data key or plan does not allow this endpoint. Check Birdeye ranking, PnL and trader-history access.')
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

    def leaders(self):
        rows = self.request('/wallet/v2/leaderboard', params={'sort_by':'realized_pnl','interval':'7d','pnl_method':'wac','min_realized_pnl':0,'limit':20})
        if not isinstance(rows, list): raise DataUnavailable('Unsupported leaderboard response.')
        selected = []
        ranked=[]
        for row in rows[:20]:
            try:
                if isinstance(row,dict):ranked.append((bot.num(row.get('realized_pnl',0)),row))
            except (ValueError,TypeError):pass
        for _,row in sorted(ranked,key=lambda item:item[0],reverse=True):
            try:
                address = bot.mint(row['owner'])
                if bot.num(row['realized_pnl']) <= 0 or address in {r['wallet'] for r in selected}: continue
                details = self.request('/wallet/v2/pnl/details', payload={'wallet':address,'duration':'all','position_scope':'cumulative','pnl_method':'wac','sort_by':'last_trade','limit':1})
                summary = details['summary']; counts = summary['counts']; pnl = bot.num(summary['pnl']['realized_profit_usd']); win = bot.num(counts['win_rate'])
                if pnl <= 0 or not .5 < win <= 1 or bot.num(counts['total_sell']) < 20: continue
                selected.append({'wallet':address,'seven_day_realized_usd':bot.num(row['realized_pnl']), 'historical_realized_usd':pnl,'win_rate_percent':win*100,'sell_count':counts['total_sell']})
            except DataUnavailable: raise
            except (ValueError, KeyError, TypeError): continue
            if len(selected) == 5: break
        return selected

    def meme(self, address):
        data=self.request('/defi/v3/token/meme/detail/single',params={'address':address})
        if not isinstance(data,dict) or data.get('address')!=address: raise DataUnavailable('Meme classification unavailable for this token.')
        info=data.get('meme_info')
        return isinstance(info,dict) and bool(info.get('source') or info.get('platform_id'))

    def trades(self, wallet, cursor, now):
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
                result.append({'id':hashlib.sha256(raw_id.encode()).hexdigest(),'wallet':wallet,'mint':address,'side':side,'source_at':timestamp,'tx_hash':tx,'instruction':str(row['ins_index'])+':'+str(row['inner_ins_index'])})
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
            result[(address,pair)]={'price':price,'pool':pair,'symbol':str(row['baseToken'].get('symbol') or address[:6])[:80],'at':received}
        except (ValueError,KeyError,TypeError): continue
    return result


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
    return {**p,'id':str(uuid.uuid4()),'closed_at':now,'exit_price_usd':quote['price'],'proceeds_usd':proceeds,'pnl_usd':proceeds-p['cost_usd'],'reason':reason}


def tick(*, provider=None, market=None, clock=time.time):
    now=clock(); state,revision=load(); owner=str(uuid.uuid4())
    with closing(database.get_connection()) as conn:
        initialize(conn)
        if conn.execute('UPDATE solana_copy_state SET lease_owner=?,lease_until=? WHERE id=1 AND lease_until<=?',(owner,now+180,now)).rowcount != 1: raise bot.Conflict('Copy cycle already running.')
        conn.commit()
    events=[]; closes=[]; provider=provider or Birdeye(); market=market or bot.ReadOnlyProviders(); state['error']=None
    try:
        enabled=state['config']['enabled']; incoming=[]
        if enabled and not state['started_at']:
            state['started_at']=now; state['copy_start_capital']=state['config']['paper_capital_usd']
        if state['started_at'] and not state['comparison_baseline']:
            baseline=regular_metrics(); own=metrics(state,now)
            if baseline['net_usd'] is not None and baseline['equity_usd']>0 and own['net_usd'] is not None and own['equity_usd']>0:
                state['comparison_baseline']={**baseline,'copy_net_usd':own['net_usd'],'copy_equity_usd':own['equity_usd'],'at':now}
        if enabled and not state['traders']:
            leaders=provider.leaders()
            enrolled=clock()
            state['traders']=[{**r,'selected_at':enrolled} for r in leaders]
            state['cursors']={r['wallet']:{'at':enrolled} for r in leaders}
            if not leaders: raise DataUnavailable('No ranked wallets meet the historical-profit and win-rate checks.')
        watched={p['wallet'] for p in state['positions'].values()} | ({r['wallet'] for r in state['traders']} if enabled else set())
        for wallet in sorted(watched):
            try:
                rows,cursor=provider.trades(wallet,state['cursors'][wallet],clock())
                gathered=state['cursors'][wallet].get('pending',[])+signals(rows,wallet)
                if 'window' in cursor: cursor['pending']=gathered
                else: incoming.extend(gathered)
                state['cursors'][wallet]=cursor
            except DataUnavailable as error: state['error']=str(error)
        category_errors={}; checks=0
        if enabled:
            for address in dict.fromkeys(r['mint'] for r in incoming if r['side']=='buy' and 0<=clock()-r['source_at']<=90):
                category=state['token_types'].get(address)
                if category and clock()-category['at']<=86400:continue
                if checks>=10 or clock()-now>100:
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
            quote=quotes.get((p['mint'],p['pool']))
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
            elif observed-signal['source_at'] > 90: signal['reason']='Signal arrived more than 90 seconds late.'
            elif latest_sell.get((wallet,mint),-1) >= signal['source_at']: signal['reason']='Trader already sold before detection.'
            elif state['config']['trade_usd'] <= 0: signal['reason']='Trade amount is zero.'
            elif state['cash_usd'] < state['config']['trade_usd']+.1: signal['reason']='Insufficient copy-paper cash.'
            else:
                category=state['token_types'].get(mint)
                if not category or observed-category['at']>86400:
                    signal['reason']=category_errors.get(mint,'Meme category observation unavailable.'); events.append(signal); continue
                if not category['is_meme']:
                    signal['reason']='Provider does not identify this token as a meme coin.'; events.append(signal); continue
                quote=next((q for (a,_),q in quotes.items() if a==mint),None)
                if not quote: signal['reason']='No current positive pool price. No fill invented.'
                else:
                    amount=state['config']['trade_usd']; cost=amount+.1
                    p={'id':signal['id'],'mint':mint,'wallet':wallet,'pool':quote['pool'],'symbol':quote['symbol'],'quantity':amount/(quote['price']*1.01),'cost_usd':cost,'entry_price_usd':quote['price'],'opened_at':observed,'source_at':signal['source_at'],'latency_seconds':signal['latency_seconds'],'tx_hash':signal['tx_hash'],'marked_at':quote['at']}
                    p['mark_usd']=max(0,p['quantity']*quote['price']*.99-.1)
                    state['positions'][p['id']]=p; state['cash_usd']-=cost; signal['status']='paper_buy'; signal['reason']='Copied buy; no liquidity or volume gate.'
            events.append(signal)
        for key,p in list(state['positions'].items()):
            if p.get('pending_sell') and (p['mint'],p['pool']) in quotes:
                closes.append(record_close(state,key,quotes[(p['mint'],p['pool'])],observed,'manual_sell' if p.get('manual_sell') else 'trader_sell'))
        state['health']='running' if enabled else 'paused_entries_monitoring_sells'
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
