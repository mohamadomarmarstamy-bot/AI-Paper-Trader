"""Isolated USD-denominated Solana paper simulator. No wallet or swap execution."""
import asyncio
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from collections import deque
from threading import Lock
from time import monotonic
from decimal import Decimal
import json
import math
import time
import uuid
import requests
import database
import solana_meme_learning as learner
import solana_meme_mail as learning_mail

MAX_SCAN_TOKENS = 20
SCAN_SECONDS = 10
DISCOVERY_BATCH_SCANS = 6

VERSION = 'solana_momentum_paper_v1'
TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
DEFAULTS = {'enabled':False, 'discover':True, 'watchlist':[], 'trade_usd':50,
            'max_positions':3, 'stop_percent':8, 'target_percent':20,
            'trail_arm_percent':12, 'trail_percent':6, 'max_hold_minutes':30,
            'daily_loss_usd':50, 'minimum_liquidity_usd':100000, 'minimum_m5_volume_usd':20000,
            'confirmation_enabled':True,'confirmation_seconds':45,
            'confirmation_min_percent':.5,'confirmation_max_percent':8,
            'entry_mode':'filtered','auto_learning':False,'paper_capital_usd':1000,'learning_email_enabled':True,'scan_batch_size':20,'scan_interval_seconds':10}
RULES = {'minimum_liquidity_usd':100000, 'minimum_m5_volume_usd':20000,
         'minimum_m5_transactions':40, 'minimum_buy_sell_ratio':1.2,
         'minimum_pair_age_minutes':30, 'maximum_pair_age_days':30,
         'm5_change_range_percent':[2,20], 'confirmation_change_range_percent':[.5,8],
         'maximum_single_account_percent':35, 'maximum_top10_accounts_percent':70,
         'modeled_cost_bps_per_side':100, 'modeled_network_usd_per_side':.10,
         'cycle_seconds':SCAN_SECONDS, 'cooldown_minutes':60}
LIMITATIONS = [
    'PAPER simulation only: no wallet, signing key, swap submission, or real funds.',
    'DEX Screener prices are provider observations, not executable quotes; market timestamps are unavailable.',
    'Discovery uses latest token profiles, a biased limited list; promotion is not a buy signal.',
    'Filtered mode checks legacy SPL mints. Exploration/learned modes do not check token permissions or concentration; results are unverified simulations.',
    'Largest token accounts are not unique holders; pool vaults are not excluded. Concentration can cause false rejections.',
    'Mint/freeze checks do not establish safety, locked liquidity, sellability, or absence of insider/wash trading.',
    'Costs are assumptions. Real slippage, priority fees, MEV, taxes, liquidity loss and failed exits can be worse.',
    'Stops are sampled every 10 seconds and cannot guarantee a price. Missing observations leave exits unresolved.',
    'Daily loss brake uses marked paper equity since UTC midnight; unknown marks block new entries.',
]
SCHEMA = '''CREATE TABLE IF NOT EXISTS solana_meme_paper_state (
 id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
 state_json TEXT NOT NULL, lease_owner TEXT, lease_until REAL NOT NULL DEFAULT 0);'''


class Conflict(ValueError):
    pass


def mint(value):
    if not isinstance(value,str) or not 32<=len(value)<=44 or any(c not in ALPHABET for c in value):
        raise ValueError('A Solana mint address is required, not a ticker.')
    n = 0
    for c in value:
        n = n*58+ALPHABET.index(c)
    size = (n.bit_length()+7)//8 + len(value)-len(value.lstrip('1'))
    if size!=32:
        raise ValueError('A 32-byte Solana mint address is required.')
    return value


def num(value):
    if isinstance(value,bool):
        raise ValueError('Finite numeric evidence required.')
    n = float(value)
    if not math.isfinite(n):
        raise ValueError('Finite numeric evidence required.')
    return n


def initial():
    return {'config':deepcopy(DEFAULTS),'cash_usd':1000.,'positions':{},'closed':[],
            'observations':{},'candidates':[],'cooldowns':{},'last_cycle':None,
            'health':'paused','day':None,'day_equity':None,'error':None}


def load():
    with closing(database.get_connection()) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_meme_paper_state'").fetchone():
            return initial(),0
        row=conn.execute('SELECT * FROM solana_meme_paper_state WHERE id=1').fetchone()
    if not row:return initial(),0
    state=json.loads(row['state_json'])
    state['config']={**deepcopy(DEFAULTS),**state['config']}
    return state,row['revision']


def initialize(conn):
    conn.executescript(SCHEMA+learner.SCHEMA)
    conn.execute('INSERT OR IGNORE INTO solana_meme_paper_state(id,revision,state_json) VALUES (1,0,?)',
                 (json.dumps(initial()),))
    conn.commit()


def public():
    state,revision=load()
    positions=[];value=state['cash_usd'];complete=True
    for address,p in state['positions'].items():
        fresh=p.get('mark_available',True) and p.get('marked_at') is not None and 0<=time.time()-p['marked_at']<=120
        mark=p.get('marked_value_usd') if fresh else None
        if mark is None:complete=False
        else:value+=mark
        positions.append({'mint':address,**p,'position_id':position_reference(address,p),'mark_fresh':fresh,'current_modeled_value_usd':mark})
    with closing(database.get_connection()) as conn:learning_summary=learner.summary(conn)
    return {'learning_email_configured':learning_mail.configured(),'learning_summary':learning_summary,'version':VERSION,'paper':True,'live_execution_enabled':False,'revision':state.get('settings_revision',0),
            **{k:v for k,v in state.items() if k not in ('positions','observations','cooldowns','discovery_batch','discovery_uses','discovery_offset','settings_revision','unavailable_until','watch_batch','watch_uses','watch_offset')},
            'positions':positions,'paper_equity_usd':round(value,4) if complete else None,
            'realized_pnl_usd':round(sum(p['pnl_usd'] for p in state['closed']),4),
            'server_time':time.time(),'monitoring_count':len(state['positions']),'scan_budget_minimum_seconds':effective_cycle_seconds(state['config'],len(state['positions'])),
            'unrealized_pnl_usd':round(sum(p['current_modeled_value_usd']-p['cost_usd'] for p in positions),4) if complete else None,
            'discovery_cooldown_count':sum(t>time.time() for t in state.get('unavailable_until',{}).values()),
            'realized_scope':'Latest 200 completed paper trades; older events may be excluded.',
            'rules':{**RULES,'cycle_seconds':state['config']['scan_interval_seconds'],'scan_batch_size':state['config']['scan_batch_size'],'confirmation_enabled':state['config']['confirmation_enabled'],'confirmation_seconds':state['config']['confirmation_seconds'],'confirmation_change_range_percent':[state['config']['confirmation_min_percent'],state['config']['confirmation_max_percent']],**{k:state['config'][k] for k in ('minimum_liquidity_usd','minimum_m5_volume_usd')}},'limitations':LIMITATIONS}


def configure(changes,expected_revision):
    if (not isinstance(changes,dict) or not changes or not set(changes)<=set(DEFAULTS)
            or isinstance(expected_revision,bool) or not isinstance(expected_revision,int)):
        raise ValueError('Known configuration changes and revision required.')
    for key,value in changes.items():
        if key in ('enabled','discover','confirmation_enabled','auto_learning','learning_email_enabled'):
            if type(value) is not bool:raise ValueError('Boolean control required.')
        elif key=='entry_mode':
            if value not in ('filtered','explore','learned'):raise ValueError('Known paper entry mode required.')
        elif key=='watchlist':
            if not isinstance(value,list) or len(value)>MAX_SCAN_TOKENS or len(set(value))!=len(value):raise ValueError('At most 20 distinct mint addresses.')
            for address in value:mint(address)
        else:
            limits={'trade_usd':(5,200),'max_positions':(1,1000),'stop_percent':(3,20),
                    'target_percent':(5,100),'trail_arm_percent':(5,50),'trail_percent':(2,20),
                    'max_hold_minutes':(5,120),'daily_loss_usd':(10,100),
                    'minimum_liquidity_usd':(0,1000000000),'minimum_m5_volume_usd':(0,1000000000),
                    'scan_batch_size':(1,200),'scan_interval_seconds':(5,60),'paper_capital_usd':(1000,1000000000),'confirmation_seconds':(10,180),'confirmation_min_percent':(0,20),'confirmation_max_percent':(.1,100)}
            low,high=limits[key]
            if not low<=num(value)<=high or key in ('max_positions','max_hold_minutes','confirmation_seconds','scan_batch_size','scan_interval_seconds') and num(value)%1:
                raise ValueError('Setting outside paper experiment bounds.')
    state,revision=load()
    if state.get('settings_revision',0)!=expected_revision:raise Conflict('Settings changed. Refresh before saving.')
    audit_before={k:deepcopy(state['config'][k]) for k in changes if changes[k]!=state['config'][k]}
    if 'paper_capital_usd' in changes:
        added=changes['paper_capital_usd']-state['config']['paper_capital_usd']
        if added<0:raise ValueError('Virtual capital can only increase; existing results are preserved.')
        if added:
            state['cash_usd']+=added
            if state.get('day_equity') is not None:state['day_equity']+=added
            state['funding_events']=(state.get('funding_events',[])+[{'at':time.time(),'amount_usd':added,'capital_usd':changes['paper_capital_usd']}])[-100:]
    entry_keys=('entry_mode','minimum_liquidity_usd','minimum_m5_volume_usd',*learner.POLICY_KEYS)
    manual_entry_change=any(k in changes and changes[k]!=state['config'][k] for k in entry_keys)
    state['config'].update(changes)
    if manual_entry_change:state.setdefault('learning',{})['active_version']='manual-'+str(uuid.uuid4())
    state['settings_revision']=state.get('settings_revision',0)+1
    if state['config']['trail_percent']>=state['config']['trail_arm_percent']:
        raise ValueError('Trailing distance must be below its activation percentage.')
    if state['config']['confirmation_min_percent']>=state['config']['confirmation_max_percent']:
        raise ValueError('Minimum confirmation rise must be below its maximum.')
    state['health']='starting' if state['config']['enabled'] else 'paused_entries_monitoring_exits'
    with closing(database.get_connection()) as conn:
        initialize(conn)
        count=conn.execute('UPDATE solana_meme_paper_state SET state_json=?,revision=revision+1 WHERE id=1 AND revision=?',
                           (json.dumps(state,allow_nan=False),revision)).rowcount
        if count!=1:raise Conflict('Settings changed. Refresh before saving.')
        if audit_before:
            audit={'id':str(uuid.uuid4()),'at':time.time(),'actor':'You','type':'Manual update','before':audit_before,'after':{k:deepcopy(state['config'][k]) for k in audit_before}}
            conn.execute('INSERT INTO solana_meme_manual_changes VALUES (?,?,?)',(audit['id'],audit['at'],json.dumps(audit,allow_nan=False)))
        conn.commit()
    notify_worker()
    return public()


_request_lock=Lock()
_request_times={}
_worker_loop=None
_worker_wake=None


def notify_worker():
    if _worker_loop is not None and _worker_wake is not None:
        try:_worker_loop.call_soon_threadsafe(_worker_wake.set)
        except RuntimeError:pass


def take_dex_budget(profiles=False,clock=monotonic):
    now=clock();buckets=[('dex',240)]+([('profiles',50)] if profiles else [])
    with _request_lock:
        for key,limit in buckets:
            q=_request_times.setdefault(key,deque())
            while q and now-q[0]>=60:q.popleft()
            if len(q)>=limit:raise ValueError('Provider request budget cooling down.')
        for key,_ in buckets:_request_times[key].append(now)


class ReadOnlyProviders:
    """Fixed hosts and read-only JSON-RPC methods; no client-supplied URLs."""
    def _json(self,response):
        response.raise_for_status()
        if len(response.content)>2_000_000:raise ValueError('Provider response too large.')
        return response.json()
    def profiles(self):
        take_dex_budget(profiles=True)
        data=self._json(requests.get('https://api.dexscreener.com/token-profiles/latest/v1',timeout=4))
        rows=data if isinstance(data,list) else [data]
        result=[]
        for row in rows[:100]:
            if row.get('chainId')=='solana':
                try:result.append(mint(row.get('tokenAddress')))
                except ValueError:pass
        return list(dict.fromkeys(result))
    def pairs(self,addresses):
        if not 1<=len(addresses)<=MAX_SCAN_TOKENS:raise ValueError('Bounded mint batch required.')
        for address in addresses:mint(address)
        take_dex_budget()
        data=self._json(requests.get('https://api.dexscreener.com/tokens/v1/solana/'+','.join(addresses),timeout=4))
        if not isinstance(data,list) or len(data)>500:raise ValueError('Pair evidence unavailable.')
        return data
    def token_pairs(self,address):
        mint(address)
        take_dex_budget()
        data=self._json(requests.get('https://api.dexscreener.com/token-pairs/v1/solana/'+address,timeout=3))
        if not isinstance(data,list) or len(data)>500:raise ValueError('Pair evidence unavailable.')
        return data

    def rpc(self,method,params):
        if method not in ('getAccountInfo','getTokenLargestAccounts'):raise ValueError('Read-only RPC method required.')
        mint(params[0])
        data=self._json(requests.post('https://api.mainnet-beta.solana.com',json={
            'jsonrpc':'2.0','id':1,'method':method,'params':params},timeout=4))
        if data.get('error') or 'result' not in data:raise ValueError('On-chain evidence unavailable.')
        return data['result']
    def safety(self,address):
        account=self.rpc('getAccountInfo',[address,{'encoding':'jsonParsed','commitment':'finalized'}])
        value=account['value']
        if not value or value.get('owner')!=TOKEN_PROGRAM or value.get('executable') is not False:
            raise ValueError('Unsupported mint program; only legacy SPL tokens are supported.')
        parsed=value['data']['parsed']
        info=parsed['info']
        if parsed.get('type')!='mint' or info.get('isInitialized') is not True:
            raise ValueError('Initialized SPL mint required.')
        if 'mintAuthority' not in info or 'freezeAuthority' not in info:
            raise ValueError('Authority evidence unavailable.')
        if info['mintAuthority'] is not None or info['freezeAuthority'] is not None:
            raise ValueError('Active mint or freeze authority.')
        supply=Decimal(info['supply'])
        if not supply.is_finite() or supply<=0:raise ValueError('Supply unavailable.')
        largest=self.rpc('getTokenLargestAccounts',[address,{'commitment':'finalized'}])
        rows=largest['value']
        if not isinstance(rows,list) or not 1<=len(rows)<=20:raise ValueError('Concentration evidence unavailable.')
        amounts=[Decimal(row['amount']) for row in rows]
        if any(not n.is_finite() or n<0 for n in amounts) or sum(amounts)>supply:
            raise ValueError('Invalid token-account concentration evidence.')
        amounts.sort(reverse=True)
        single=float(amounts[0]/supply*100);top10=float(sum(amounts[:10])/supply*100)
        if single>35 or top10>70:raise ValueError('Token-account concentration exceeds the experimental limits.')
        return {'mint_authority':None,'freeze_authority':None,'largest_account_percent':single,
                'top10_accounts_percent':top10,'checked_at':time.time(),
                'slot':largest['context']['slot'],'basis':'token accounts including pool vaults; not unique holders'}


def normalize_pair(row,address,now,price_only=False):
    if (row.get('chainId')!='solana' or row.get('baseToken',{}).get('address')!=address):
        raise ValueError('Wrong chain or base mint.')
    pair=mint(row['pairAddress']);price=num(row['priceUsd'])
    if price<=0:raise ValueError('Positive price required.')
    if price_only:
        def optional(*keys):
            value=row
            try:
                for key in keys:value=value[key]
                return num(value)
            except (KeyError,TypeError,ValueError):return None
        def nonnegative(*keys):
            value=optional(*keys)
            return value if value is not None and value>=0 else None
        created=optional('pairCreatedAt')
        return {'mint':address,'pair':pair,'symbol':str(row['baseToken'].get('symbol','?'))[:24],
                'price_usd':price,'liquidity_usd':nonnegative('liquidity','usd'),'volume_m5_usd':nonnegative('volume','m5'),
                'buys_m5':nonnegative('txns','m5','buys'),'sells_m5':nonnegative('txns','m5','sells'),
                'change_m5_percent':optional('priceChange','m5'),
                'age_minutes':(now-created/1000)/60 if created is not None and 0<=created/1000<=now else None,
                'observed_at':now,'market_timestamp':None,'source':'dexscreener_provider_observation'}
    liquidity=num(row['liquidity']['usd'])
    created=num(row['pairCreatedAt'])/1000
    buys=num(row['txns']['m5']['buys']);sells=num(row['txns']['m5']['sells'])
    volume=num(row['volume']['m5']);change=num(row['priceChange']['m5'])
    if min(price,liquidity)<=0 or min(buys,sells,volume)<0 or not 0<=created<=now:
        raise ValueError('Invalid pair evidence.')
    return {'mint':address,'pair':pair,'symbol':str(row['baseToken'].get('symbol','?'))[:24],
            'price_usd':price,'liquidity_usd':liquidity,'volume_m5_usd':volume,
            'buys_m5':buys,'sells_m5':sells,'change_m5_percent':change,
            'age_minutes':(now-created)/60,'observed_at':now,
            'market_timestamp':None,'source':'dexscreener_provider_observation'}


def entry_reason(row,previous,now,config=None):
    config=config or DEFAULTS
    if config['entry_mode']=='explore':return None
    if config['entry_mode']=='learned':
        if not learner.matches({'entry_features':row},{'liquidity_usd':config['minimum_liquidity_usd'],'volume_m5_usd':config['minimum_m5_volume_usd']}):return 'Learned liquidity/volume minimums not met or unavailable.'
        return None
    if row['liquidity_usd']<config['minimum_liquidity_usd']:return f"Liquidity below ${config['minimum_liquidity_usd']:,.0f}."
    if not 30<=row['age_minutes']<=30*1440:return 'Pair age outside 30 minutes–30 days.'
    if row['volume_m5_usd']<config['minimum_m5_volume_usd']:return f"Five-minute volume below ${config['minimum_m5_volume_usd']:,.0f}."
    if row['buys_m5']+row['sells_m5']<40:return 'Insufficient recent observed activity.'
    if row['buys_m5']<1.2*max(1,row['sells_m5']):return 'Buy/sell activity confirmation missing.'
    if not 2<=row['change_m5_percent']<=20:return 'Five-minute change outside experimental momentum range.'
    if not config['confirmation_enabled']:return None
    wait=config['confirmation_seconds'];expiry=max(180,wait+60)
    if not previous or previous['pair']!=row['pair'] or not wait<=now-previous['observed_at']<=expiry:
        return f'Waiting for a second observation of the same pool, {wait:g}–{expiry:g} seconds apart.'
    move=(row['price_usd']/previous['price_usd']-1)*100
    if not config['confirmation_min_percent']<=move<=config['confirmation_max_percent']:return f"No confirmed continuation, or price has run too far to chase. Required rise: {config['confirmation_min_percent']:g}%–{config['confirmation_max_percent']:g}%."
    return None


def effective_cycle_seconds(config,position_count):
    # Reserve budget for every held batch, discovery, profile lookup and four recoveries.
    calls=math.ceil((position_count+config['scan_batch_size'])/MAX_SCAN_TOKENS)+5
    return max(config['scan_interval_seconds'],math.ceil(calls*60/240))


def batch_hold_cycles(config):
    interval=config['scan_interval_seconds']
    return max(2,math.ceil(60/interval),math.ceil(config['confirmation_seconds']/interval)+2 if config['confirmation_enabled'] else 0)


def select_watchlist(state):
    watch=list(dict.fromkeys(state['config']['watchlist']));size=state['config']['scan_batch_size']
    if len(watch)<=size:return watch
    batch=[a for a in state.get('watch_batch',[]) if a in watch][:size]
    if len(batch)!=size or state.get('watch_uses',batch_hold_cycles(state['config']))>=batch_hold_cycles(state['config']):
        offset=state.get('watch_offset',0)%len(watch);batch=(watch[offset:]+watch[:offset])[:size]
        state['watch_offset']=(offset+size)%len(watch);state['watch_uses']=0
    state['watch_batch']=batch;state['watch_uses']=state.get('watch_uses',0)+1
    return batch


def select_candidates(state, profiles, now=None):
    """Keep a discovery batch for confirmation, then rotate beyond the first batch."""
    priority=select_watchlist(state)
    slots=state['config']['scan_batch_size']-len(priority)
    if not state['config']['discover']:return priority
    if not slots:return priority
    now=time.time() if now is None else now
    excluded={a for a,until in state.get('unavailable_until',{}).items() if until>now}
    batch=[a for a in state.get('discovery_batch',[]) if a not in priority and a not in excluded][:slots]
    hold_scans=batch_hold_cycles(state['config'])
    if state.get('discovery_uses',hold_scans)>=hold_scans or not batch:
        pool=list(dict.fromkeys(a for a in profiles if a not in priority and a not in excluded))
        offset=state.get('discovery_offset',0)%len(pool) if pool else 0
        batch=(pool[offset:]+pool[:offset])[:slots]
        state['discovery_offset']=(offset+len(batch))%len(pool) if pool else 0
        state['discovery_uses']=0
    if len(batch)<slots:
        batch+=list(dict.fromkeys(a for a in profiles if a not in priority and a not in excluded and a not in batch))[:slots-len(batch)]
    state['discovery_batch']=batch
    state['discovery_uses']=state.get('discovery_uses',0)+1
    return priority+batch


def position_reference(address,position):
    return position.get('trade_id') or f"{address}:{position['opened_at']}"


def manual_close(positions,pause_new_buys,*,providers=None,clock=time.time):
    if type(pause_new_buys) is not bool or not isinstance(positions,list) or not 1<=len(positions)<=1000 or (not pause_new_buys and len(positions)!=1):
        raise ValueError('Select one position or sell-all with entries paused.')
    targets={}
    for row in positions:
        if not isinstance(row,dict) or set(row)!={'mint','position_id'}:raise ValueError('Position identity required.')
        address=mint(row['mint']);ref=row['position_id']
        if address in targets or not isinstance(ref,str) or not 1<=len(ref)<=128:raise ValueError('Distinct position identities required.')
        targets[address]=ref
    return tick(providers=providers,clock=clock,force=True,manual_exits=targets,manual_pause=pause_new_buys)


def tick(*,providers=None,clock=time.time,force=False,manual_exits=None,manual_pause=False):
    state,revision=load();now=clock();config=state['config']
    if not force and not config['enabled'] and not state['positions']:return public()
    pending_outcomes=[]
    owner=str(uuid.uuid4())
    with closing(database.get_connection()) as conn:
        initialize(conn)
        if conn.execute('UPDATE solana_meme_paper_state SET lease_owner=?,lease_until=? WHERE id=1 AND lease_until<=?',
                        (owner,now+90,now)).rowcount!=1:raise Conflict('A scan is already running.')
        conn.commit()
    pause_changed=manual_pause and config['enabled']
    if pause_changed:
        config['enabled']=False;state['settings_revision']=state.get('settings_revision',0)+1
    closed_manual=[]
    targeted={a for a,ref in (manual_exits or {}).items() if a in state['positions'] and position_reference(a,state['positions'][a])==ref}
    providers=providers or ReadOnlyProviders()
    state['error']=None
    try:
        candidates=[]
        if manual_exits is None:
            profiles=[]
            if config['discover'] and (config['enabled'] or force):
                try:profiles=providers.profiles()
                except Exception:state['error']='Discovery unavailable; existing positions/watchlist still monitored.'
            candidates=select_candidates(state,profiles,now) if config['enabled'] or force else select_watchlist(state)
        monitored=list(dict.fromkeys([*(targeted if manual_exits is not None else state['positions']),*candidates]))
        if not monitored:
            state.update(health='paused_entries_monitoring_exits' if not config['enabled'] else 'running' if manual_exits is not None else 'waiting_for_mints',last_cycle=now)
        else:
            batches=[monitored[i:i+MAX_SCAN_TOKENS] for i in range(0,len(monitored),MAX_SCAN_TOKENS)]
            def fetch_batch(batch):
                try:return providers.pairs(batch)
                except Exception:return []
            if len(batches)==1:raw=fetch_batch(batches[0])
            else:
                with ThreadPoolExecutor(max_workers=8) as pool:raw=[r for rows in pool.map(fetch_batch,batches) for r in rows]
            observed=clock()
            if not 0<=observed-now<=45:raise ValueError('Observation request exceeded cycle budget.')
            def group(rows):
                grouped={}
                for r in rows:
                    if isinstance(r,dict) and isinstance(r.get('baseToken'),dict):grouped.setdefault(r['baseToken'].get('address'),[]).append(r)
                return grouped
            grouped=group(raw)
            # Only incomplete/absent pool batches receive a bounded alternate-pool lookup.
            recoveries=0
            for address in monitored:
                matching=[r for r in grouped.get(address,[]) if r.get('chainId')=='solana']
                complete=False
                for r in matching:
                    try:
                        normalized=normalize_pair(r,address,observed,config['entry_mode']!='filtered' or address in state['positions'])
                        if address not in state['positions'] or normalized['pair']==state['positions'][address]['pair']:complete=True
                    except (ValueError,KeyError,TypeError):pass
                if not complete and recoveries<4 and hasattr(providers,'token_pairs'):
                    recoveries+=1
                    try:raw.extend(providers.token_pairs(address))
                    except Exception:pass
            observed=clock()
            if not 0<=observed-now<=45:raise ValueError('Observation request exceeded cycle budget.')
            grouped=group(raw)
            snapshots={};missing={}
            for address in monitored:
                valid=[]
                for row in grouped.get(address,[]):
                    if row.get('chainId')!='solana':continue
                    try:valid.append(normalize_pair(row,address,observed,config['entry_mode']!='filtered' or address in state['positions']))
                    except (ValueError,KeyError,TypeError) as error:
                        fields={'liquidity':'Liquidity data missing.','usd':'Liquidity data missing.',
                                'priceUsd':'Price data missing.','pairCreatedAt':'Pool age missing.',
                                'txns':'Recent activity missing.','volume':'Volume data missing.',
                                'priceChange':'Price-change data missing.','m5':'Five-minute activity missing.'}
                        missing[address]={'symbol':str(row.get('baseToken',{}).get('symbol',''))[:24],
                            'reason':fields.get(error.args[0] if isinstance(error,KeyError) else '', 'Pool metrics incomplete or invalid.')}
                        continue
                # Open positions stay pinned to their original pool, not a new unrelated price.
                if address in state['positions']:
                    valid=[r for r in valid if r['pair']==state['positions'][address]['pair']]
                if valid:snapshots[address]=max(valid,key=lambda r:r['liquidity_usd'] if learner.finite(r['liquidity_usd']) else -1)
            unavailable={a:t for a,t in state.get('unavailable_until',{}).items() if t>observed}
            for address in candidates:
                if address in snapshots:unavailable.pop(address,None)
                else:unavailable[address]=observed+300
            state['unavailable_until']=unavailable
            closed_this_cycle=set()
            for address,p in list(state['positions'].items()):
                if manual_exits is not None and address not in targeted:continue
                row=snapshots.get(address)
                if not row:
                    p['mark_available']=False
                    continue
                p['mark_available']=True
                value=p['quantity']*row['price_usd']*.99-.10
                change=(value/p['cost_usd']-1)*100
                p.update(marked_value_usd=value,marked_at=observed,peak_percent=max(p['peak_percent'],change),low_percent=min(p.get('low_percent',0),change))
                reason=('manual_sell_all' if manual_exits is not None and manual_pause else 'manual_sell' if manual_exits is not None else 'stop_loss' if change<=-p['stop_percent'] else 'take_profit' if change>=p['target_percent']
                        else 'profit_trail' if p['peak_percent']>=p['trail_arm_percent'] and p['peak_percent']-change>=p['trail_percent']
                        else 'liquidity_drop' if learner.finite(row['liquidity_usd']) and learner.finite(p['entry_liquidity_usd']) and row['liquidity_usd']<p['entry_liquidity_usd']*.5
                        else 'maximum_hold' if observed-p['opened_at']>=p['max_hold_minutes']*60 else None)
                if reason:
                    value=max(0,value);state['cash_usd']+=value
                    state['closed'].append({'mint':address,'symbol':p['symbol'],'opened_at':p['opened_at'],
                        'closed_at':observed,'entry_usd':p['cost_usd'],'proceeds_usd':value,
                        'pnl_usd':value-p['cost_usd'],'reason':reason,'exit_source':'manual' if manual_exits is not None else 'automatic',
                        'id':p.get('trade_id',str(uuid.uuid4())),'entry_features':p.get('entry_features',{}),
                        'entry_mode':p.get('entry_mode','filtered'),'exit_policy':p.get('exit_policy',{}),
                        'learning_candidate_id':p.get('learning_candidate_id'),'settings_version':p.get('settings_version','legacy'),'peak_percent':p['peak_percent'],'low_percent':p.get('low_percent',0),
                        'note':f"{'Win' if value>p['cost_usd'] else 'Loss' if value<p['cost_usd'] else 'Flat'}: {reason.replace('_',' ')} after {(observed-p['opened_at'])/60:.1f} min; modeled return {change:.2f}%.",'basis':'modeled provider observation; not a swap fill'})
                    pending_outcomes.append(state['closed'][-1])
                    if manual_exits is not None:closed_manual.append(address)
                    state['closed']=state['closed'][-200:];del state['positions'][address]
                    state['cooldowns'][address]=observed+3600;closed_this_cycle.add(address)
            equity=state['cash_usd']+sum(p.get('marked_value_usd',0) for p in state['positions'].values())
            marks_complete=all(address in snapshots or (manual_exits is not None and address not in targeted and p.get('mark_available',True) and 0<=observed-p.get('marked_at',0)<=120) for address,p in state['positions'].items())
            day=time.strftime('%Y-%m-%d',time.gmtime(observed))
            if state['day']!=day and marks_complete:state.update(day=day,day_equity=equity)
            brake=not marks_complete or state['day']!=day or equity-state['day_equity']<=-config['daily_loss_usd']
            state['candidates']=[];checks=0
            for address in candidates:
                row=snapshots.get(address)
                if not row:
                    state['candidates'].append({'mint':address,'status':'unavailable',**missing.get(address,{'reason':'No matching pool data.'})});continue
                reason=entry_reason(row,state['observations'].get(address),observed,config) if address not in state['positions'] and address not in closed_this_cycle else None
                if address in state['positions']:reason='Existing paper position is being monitored.'
                elif address in closed_this_cycle or state['cooldowns'].get(address,0)>observed:reason='One-hour re-entry cooldown.'
                elif brake:reason='Paper daily loss brake or incomplete position marks blocks new entries.'
                elif not config['enabled']:reason='New paper buys are paused.'
                elif len(state['positions'])>=config['max_positions']:reason='Maximum paper positions reached.'
                elif state['cash_usd']<config['trade_usd']+.10:reason='Insufficient virtual cash.'
                entry_friction=config['trade_usd']+.20-config['trade_usd']*.99/1.01
                if reason is None and equity-state['day_equity']-entry_friction<=-config['daily_loss_usd']:
                    reason='Remaining daily paper-loss budget cannot cover modeled entry/exit friction.'
                safety={'status':'Not checked in PAPER exploration/learned mode'}
                if reason is None and config['entry_mode']=='filtered':
                    if checks>=3:reason='On-chain check budget reached; retry next cycle.'
                    else:
                        checks+=1
                        try:
                            safety=providers.safety(address)
                            if not 0<=clock()-safety['checked_at']<=30:raise ValueError('On-chain evidence is stale.')
                            if clock()-observed>45:raise ValueError('Pool observation is too old for entry.')
                        except ValueError as error:reason=str(error)
                        except Exception:reason='On-chain checks unavailable; no paper entry.'
                if reason is None:
                    amount=config['trade_usd'];cost=amount+.10
                    state['positions'][address]={'symbol':row['symbol'],'pair':row['pair'],'opened_at':observed,
                        'trade_id':str(uuid.uuid4()),'entry_features':{k:row.get(k) for k in ('liquidity_usd','volume_m5_usd','buys_m5','sells_m5','change_m5_percent','age_minutes')},
                        'entry_mode':config['entry_mode'],'exit_policy':learner.scope(config),
                        'learning_candidate_id':None,'settings_version':state.get('learning',{}).get('active_version','legacy'),
                        'quantity':amount/(row['price_usd']*1.01),'cost_usd':cost,'entry_price_usd':row['price_usd'],
                        'entry_liquidity_usd':row['liquidity_usd'],'peak_percent':0.,'safety':safety,
                        **{k:config[k] for k in ('stop_percent','target_percent','trail_arm_percent','trail_percent','max_hold_minutes')}}
                    p=state['positions'][address];p.update(marked_at=observed,marked_value_usd=max(0,p['quantity']*row['price_usd']*.99-.10))
                    state['cash_usd']-=cost;equity-=cost-p['marked_value_usd'];reason='PAPER buy simulated after momentum and on-chain checks.' if config['entry_mode']=='filtered' else 'PAPER exploration buy simulated; token checks not performed.'
                expiry=max(180,config['confirmation_seconds']+60)
                baseline=state['observations'].get(address)
                if not baseline or baseline['pair']!=row['pair'] or observed-baseline['observed_at']>expiry:baseline=row
                confirmation={'confirmation_started_at':baseline['observed_at'],'confirmation_due_at':baseline['observed_at']+config['confirmation_seconds'],'confirmation_expires_at':baseline['observed_at']+expiry} if reason.startswith('Waiting for a second') else {}
                state['candidates'].append({**row,**confirmation,'status':'paper_open' if address in state['positions'] else 'blocked','reason':reason})
                prior=state['observations'].get(address)
                if not prior or prior['pair']!=row['pair'] or observed-prior['observed_at']>expiry:
                    state['observations'][address]=row
            state['observations']={a:r for a,r in state['observations'].items() if observed-r['observed_at']<=max(180,config['confirmation_seconds']+60)}
            state['cooldowns']={a:t for a,t in state['cooldowns'].items() if t>observed}
            state.update(last_cycle=observed,health='entry_brake' if brake else 'running' if config['enabled'] else 'paused_entries_monitoring_exits')
    except Exception:
        for position in state['positions'].values():position['mark_available']=False
        state.update(health='data_unavailable',error='Provider unavailable or rate limited. No new paper entry; unresolved marks are unavailable.',last_cycle=clock())
    history=state.get('equity_history',[])
    recorded=clock()
    if not history or recorded-history[-1]['at']>=60:
        available=all(p.get('mark_available',True) and 0<=recorded-p.get('marked_at',0)<=120 for p in state['positions'].values())
        marked=state['cash_usd']+sum(p.get('marked_value_usd',0) for p in state['positions'].values()) if available else None
        history.append({'at':recorded,'equity_usd':round(marked,4) if marked is not None else None,'capital_usd':config['paper_capital_usd']})
        state['equity_history']=history[-1440:]
    try:
        with closing(database.get_connection()) as conn:
            changed=conn.execute('UPDATE solana_meme_paper_state SET state_json=?,revision=revision+1 WHERE id=1 AND revision=? AND lease_owner=? AND lease_until>?',
                (json.dumps(state,allow_nan=False),revision,owner,clock())).rowcount
            if changed!=1:
                conn.rollback();raise Conflict('Configuration changed during scan; simulated changes discarded.')
            if pause_changed:
                audit={'id':str(uuid.uuid4()),'at':clock(),'actor':'You','type':'Sell all · pause entries','before':{'enabled':True},'after':{'enabled':False}}
                conn.execute('INSERT INTO solana_meme_manual_changes VALUES (?,?,?)',(audit['id'],audit['at'],json.dumps(audit)))
            learner.archive(conn,pending_outcomes)
            learner.process(conn,state,clock())
            conn.execute('UPDATE solana_meme_paper_state SET state_json=? WHERE id=1',(json.dumps(state,allow_nan=False),))
            conn.commit()
    finally:
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE solana_meme_paper_state SET lease_owner=NULL,lease_until=0 WHERE id=1 AND lease_owner=?',(owner,));conn.commit()
    result=public()
    if manual_exits is not None:result['manual_close_result']={'closed':closed_manual,'unresolved':[a for a in state['positions'] if manual_pause or a in targeted],'not_current':[a for a in manual_exits if a not in targeted],'entries_paused':not state['config']['enabled']}
    return result


async def worker():
    global _worker_loop,_worker_wake
    loop=asyncio.get_running_loop();wake=asyncio.Event();_worker_loop=loop;_worker_wake=wake
    try:
        while True:
            wake.clear();started=loop.time()
            try:await asyncio.to_thread(tick)
            except Exception:pass
            try:
                state,_=await asyncio.to_thread(load)
                if state['config']['learning_email_enabled']:await asyncio.to_thread(learning_mail.deliver_one)
            except Exception:state={'config':DEFAULTS}
            delay=max(.1,effective_cycle_seconds(state['config'],len(state.get('positions',{})))-(loop.time()-started))
            try:await asyncio.wait_for(wake.wait(),timeout=delay)
            except asyncio.TimeoutError:pass
    finally:
        if _worker_wake is wake:_worker_loop=None;_worker_wake=None
