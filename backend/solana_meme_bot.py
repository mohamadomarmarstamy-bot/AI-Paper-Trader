"""Isolated USD-denominated Solana paper simulator. No wallet or swap execution."""
import asyncio
from contextlib import closing
from copy import deepcopy
from decimal import Decimal
import json
import math
import time
import uuid
import requests
import database
import solana_meme_learning as learner

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
            'entry_mode':'filtered','auto_learning':False}
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
        positions.append({'mint':address,**p,'mark_fresh':fresh,'current_modeled_value_usd':mark})
    with closing(database.get_connection()) as conn:learning_summary=learner.summary(conn)
    return {'learning_summary':learning_summary,'version':VERSION,'paper':True,'live_execution_enabled':False,'revision':state.get('settings_revision',0),
            **{k:v for k,v in state.items() if k not in ('positions','observations','cooldowns','discovery_batch','discovery_uses','discovery_offset','settings_revision','unavailable_until')},
            'positions':positions,'paper_equity_usd':round(value,4) if complete else None,
            'realized_pnl_usd':round(sum(p['pnl_usd'] for p in state['closed']),4),
            'server_time':time.time(),
            'unrealized_pnl_usd':round(sum(p['current_modeled_value_usd']-p['cost_usd'] for p in positions),4) if complete else None,
            'discovery_cooldown_count':sum(t>time.time() for t in state.get('unavailable_until',{}).values()),
            'realized_scope':'Latest 200 completed paper trades; older events may be excluded.',
            'rules':{**RULES,'confirmation_enabled':state['config']['confirmation_enabled'],'confirmation_seconds':state['config']['confirmation_seconds'],'confirmation_change_range_percent':[state['config']['confirmation_min_percent'],state['config']['confirmation_max_percent']],**{k:state['config'][k] for k in ('minimum_liquidity_usd','minimum_m5_volume_usd')}},'limitations':LIMITATIONS}


def configure(changes,expected_revision):
    if (not isinstance(changes,dict) or not changes or not set(changes)<=set(DEFAULTS)
            or isinstance(expected_revision,bool) or not isinstance(expected_revision,int)):
        raise ValueError('Known configuration changes and revision required.')
    for key,value in changes.items():
        if key in ('enabled','discover','confirmation_enabled','auto_learning'):
            if type(value) is not bool:raise ValueError('Boolean control required.')
        elif key=='entry_mode':
            if value not in ('filtered','explore','learned'):raise ValueError('Known paper entry mode required.')
        elif key=='watchlist':
            if not isinstance(value,list) or len(value)>MAX_SCAN_TOKENS or len(set(value))!=len(value):raise ValueError('At most 20 distinct mint addresses.')
            for address in value:mint(address)
        else:
            limits={'trade_usd':(5,200),'max_positions':(1,5),'stop_percent':(3,20),
                    'target_percent':(5,100),'trail_arm_percent':(5,50),'trail_percent':(2,20),
                    'max_hold_minutes':(5,120),'daily_loss_usd':(10,100),
                    'minimum_liquidity_usd':(0,1000000000),'minimum_m5_volume_usd':(0,1000000000),
                    'confirmation_seconds':(10,180),'confirmation_min_percent':(0,20),'confirmation_max_percent':(.1,100)}
            low,high=limits[key]
            if not low<=num(value)<=high or key in ('max_positions','max_hold_minutes','confirmation_seconds') and num(value)%1:
                raise ValueError('Setting outside paper experiment bounds.')
    state,revision=load()
    if state.get('settings_revision',0)!=expected_revision:raise Conflict('Settings changed. Refresh before saving.')
    state['config'].update(changes)
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
        conn.commit()
    return public()


class ReadOnlyProviders:
    """Fixed hosts and read-only JSON-RPC methods; no client-supplied URLs."""
    def _json(self,response):
        response.raise_for_status()
        if len(response.content)>2_000_000:raise ValueError('Provider response too large.')
        return response.json()
    def profiles(self):
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
        data=self._json(requests.get('https://api.dexscreener.com/tokens/v1/solana/'+','.join(addresses),timeout=4))
        if not isinstance(data,list) or len(data)>500:raise ValueError('Pair evidence unavailable.')
        return data
    def token_pairs(self,address):
        mint(address)
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


def select_candidates(state, profiles, now=None):
    """Keep a discovery batch for confirmation, then rotate beyond the first batch."""
    priority=list(dict.fromkeys([*state['positions'],*state['config']['watchlist']]))[:MAX_SCAN_TOKENS]
    slots=MAX_SCAN_TOKENS-len(priority)
    if not slots:return priority
    now=time.time() if now is None else now
    excluded={a for a,until in state.get('unavailable_until',{}).items() if until>now}
    batch=[a for a in state.get('discovery_batch',[]) if a not in priority and a not in excluded][:slots]
    hold_scans=max(DISCOVERY_BATCH_SCANS,math.ceil(state['config']['confirmation_seconds']/SCAN_SECONDS)+2) if state['config']['confirmation_enabled'] else DISCOVERY_BATCH_SCANS
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


def tick(*,providers=None,clock=time.time,force=False):
    state,revision=load();now=clock();config=state['config']
    if not force and not config['enabled'] and not state['positions']:return public()
    pending_outcomes=[]
    with closing(database.get_connection()) as conn:past_outcomes=learner.recent(conn)
    owner=str(uuid.uuid4())
    with closing(database.get_connection()) as conn:
        initialize(conn)
        if conn.execute('UPDATE solana_meme_paper_state SET lease_owner=?,lease_until=? WHERE id=1 AND lease_until<=?',
                        (owner,now+90,now)).rowcount!=1:raise Conflict('A scan is already running.')
        conn.commit()
    providers=providers or ReadOnlyProviders()
    state['error']=None
    try:
        candidates=list(dict.fromkeys([*state['positions'],*config['watchlist']]))[:MAX_SCAN_TOKENS]
        if config['discover'] and (config['enabled'] or force):
            try:candidates=select_candidates(state,providers.profiles(),now)
            except Exception:state['error']='Discovery unavailable; existing positions/watchlist still monitored.'
        if not candidates:
            state.update(health='waiting_for_mints',last_cycle=now)
        else:
            raw=providers.pairs(candidates);observed=clock()
            if not 0<=observed-now<=45:raise ValueError('Observation request exceeded cycle budget.')
            # Only incomplete/absent pool batches receive a bounded alternate-pool lookup.
            recoveries=0
            for address in candidates:
                matching=[r for r in raw if r.get('baseToken',{}).get('address')==address and r.get('chainId')=='solana']
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
            snapshots={};missing={}
            for address in candidates:
                valid=[]
                for row in raw:
                    if row.get('chainId')!='solana' or row.get('baseToken',{}).get('address')!=address:continue
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
                row=snapshots.get(address)
                if not row:
                    p['mark_available']=False
                    continue
                p['mark_available']=True
                value=p['quantity']*row['price_usd']*.99-.10
                change=(value/p['cost_usd']-1)*100
                p.update(marked_value_usd=value,marked_at=observed,peak_percent=max(p['peak_percent'],change),low_percent=min(p.get('low_percent',0),change))
                reason=('stop_loss' if change<=-p['stop_percent'] else 'take_profit' if change>=p['target_percent']
                        else 'profit_trail' if p['peak_percent']>=p['trail_arm_percent'] and p['peak_percent']-change>=p['trail_percent']
                        else 'liquidity_drop' if learner.finite(row['liquidity_usd']) and learner.finite(p['entry_liquidity_usd']) and row['liquidity_usd']<p['entry_liquidity_usd']*.5
                        else 'maximum_hold' if observed-p['opened_at']>=p['max_hold_minutes']*60 else None)
                if reason:
                    value=max(0,value);state['cash_usd']+=value
                    state['closed'].append({'mint':address,'symbol':p['symbol'],'opened_at':p['opened_at'],
                        'closed_at':observed,'entry_usd':p['cost_usd'],'proceeds_usd':value,
                        'pnl_usd':value-p['cost_usd'],'reason':reason,
                        'id':p.get('trade_id',str(uuid.uuid4())),'entry_features':p.get('entry_features',{}),
                        'entry_mode':p.get('entry_mode','filtered'),'exit_policy':p.get('exit_policy',{}),
                        'learning_candidate_id':p.get('learning_candidate_id'),'peak_percent':p['peak_percent'],'low_percent':p.get('low_percent',0),
                        'note':f"{'Win' if value>p['cost_usd'] else 'Loss' if value<p['cost_usd'] else 'Flat'}: {reason.replace('_',' ')} after {(observed-p['opened_at'])/60:.1f} min; modeled return {change:.2f}%.",'basis':'modeled provider observation; not a swap fill'})
                    pending_outcomes.append(state['closed'][-1])
                    state['closed']=state['closed'][-200:];del state['positions'][address]
                    state['cooldowns'][address]=observed+3600;closed_this_cycle.add(address)
            equity=state['cash_usd']+sum(p.get('marked_value_usd',0) for p in state['positions'].values())
            marks_complete=all(address in snapshots for address in state['positions'])
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
                        'learning_candidate_id':state.get('learning',{}).get('proposal',{}).get('id'),
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
    learner.update(state,past_outcomes+pending_outcomes,clock())
    history=state.get('equity_history',[])
    recorded=clock()
    if not history or recorded-history[-1]['at']>=60:
        available=all(p.get('mark_available',True) and 0<=recorded-p.get('marked_at',0)<=120 for p in state['positions'].values())
        marked=state['cash_usd']+sum(p.get('marked_value_usd',0) for p in state['positions'].values()) if available else None
        history.append({'at':recorded,'equity_usd':round(marked,4) if marked is not None else None})
        state['equity_history']=history[-1440:]
    try:
        with closing(database.get_connection()) as conn:
            changed=conn.execute('UPDATE solana_meme_paper_state SET state_json=?,revision=revision+1 WHERE id=1 AND revision=? AND lease_owner=? AND lease_until>?',
                (json.dumps(state,allow_nan=False),revision,owner,clock())).rowcount
            if changed!=1:
                conn.rollback();raise Conflict('Configuration changed during scan; simulated changes discarded.')
            learner.archive(conn,pending_outcomes)
            conn.commit()
    finally:
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE solana_meme_paper_state SET lease_owner=NULL,lease_until=0 WHERE id=1 AND lease_owner=?',(owner,));conn.commit()
    return public()


async def worker():
    while True:
        try:await asyncio.to_thread(tick)
        except Exception:pass
        await asyncio.sleep(SCAN_SECONDS)
