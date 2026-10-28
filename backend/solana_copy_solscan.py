"""Read-only Solscan swap source. One frozen provider per enrolled wallet."""
from collections import deque
from threading import Lock
import hashlib
import json
import time
import requests
import solana_profile
import solana_meme_bot as bot

_calls=deque()
_lock=Lock()
_cooldown=0


def configured():
    try:return bool(solana_profile.solscan_api_key())
    except solana_profile.Unavailable:return False


def request(params):
    global _cooldown
    from solana_copy_trader import DataUnavailable
    try:key=solana_profile.solscan_api_key()
    except solana_profile.Unavailable:raise DataUnavailable('Replace the Solscan key in Profile.') from None
    if not key:raise DataUnavailable('Add a Solscan Pro API key in Profile for enrolled Solscan wallets.')
    with _lock:
        now=time.monotonic()
        while _calls and now-_calls[0]>=60:_calls.popleft()
        if now<_cooldown or len(_calls)>=30:raise DataUnavailable('Solscan request budget cooling down.')
        _calls.append(now)
    try:
        response=requests.get('https://pro-api.solscan.io/v2.0/account/defi/activities',params=params,
                              headers={'token':key,'accept':'application/json'},timeout=4,allow_redirects=False)
        if response.status_code in (401,403):raise DataUnavailable('Solscan key or plan does not allow wallet swap history.')
        if response.status_code==429:
            with _lock:_cooldown=time.monotonic()+60
            raise DataUnavailable('Solscan rate limited; retrying after cooldown.')
        response.raise_for_status()
        if len(response.content)>2_000_000:raise ValueError()
        data=response.json()
        if not isinstance(data,dict) or data.get('success') is not True or not isinstance(data.get('data'),list) or len(data['data'])>100:raise ValueError()
        return data['data']
    except DataUnavailable:raise
    except Exception:raise DataUnavailable('Solscan swap response unavailable or unsupported.') from None


def normalized(rows,wallet,after,before):
    grouped={}
    for row in rows:
        if not isinstance(row,dict):continue
        try:
            tx=row['trans_id'];at=bot.num(row['block_time'])
            if row.get('from_address')!=wallet or not after<=at<=before or not isinstance(tx,str) or not 32<=len(tx)<=128 or any(c not in bot.ALPHABET for c in tx):continue
            if row.get('activity_type') not in ('ACTIVITY_TOKEN_SWAP','ACTIVITY_AGG_TOKEN_SWAP'):continue
            # Collapse exact overlapping-page records before deciding ambiguity.
            grouped.setdefault(tx,{})[json.dumps(row,sort_keys=True)]=row
        except (KeyError,TypeError,ValueError):continue
    result=[]
    for tx,items in grouped.items():
        values=list(items.values());aggregate=[r for r in values if r['activity_type']=='ACTIVITY_AGG_TOKEN_SWAP']
        choices=aggregate or values
        if len(choices)!=1:continue  # No invented net direction for multiple independent swaps.
        row=choices[0];routes=row.get('routers')
        if isinstance(routes,dict):routes=[routes]
        if not isinstance(routes,list) or len(routes)!=1 or not isinstance(routes[0],dict):continue
        route=routes[0]
        try:
            sent=bot.mint(route['token1']);received=bot.mint(route['token2'])
            if sent==received or bot.num(route['amount1'])<=0 or bot.num(route['amount2'])<=0:continue
            # A stable provider event key, not an invented instruction index.
            event=hashlib.sha256(json.dumps([tx,sent,received],separators=(',',':')).encode()).hexdigest()
            result.append({'owner':wallet,'tx_hash':tx,'block_unix_time':row['block_time'],
                           'ins_index':'solscan:'+event,'inner_ins_index':'aggregate','source':'solscan',
                           'base':{'address':received,'type_swap':'to'},'quote':{'address':sent,'type_swap':'from'}})
        except (KeyError,TypeError,ValueError):continue
    return result


def trades(wallet,cursor,now):
    from solana_copy_trader import DataUnavailable
    bot.mint(wallet)
    window=cursor.get('solscan_window') or {'after':int(cursor['at'])-2,'before':int(now),'page':1}
    rows=request({'address':wallet,'activity_type[]':['ACTIVITY_TOKEN_SWAP','ACTIVITY_AGG_TOKEN_SWAP'],
                  'from_time':window['after'],'to_time':window['before'],'page':window['page'],
                  'page_size':100,'sort_by':'block_time','sort_order':'asc'})
    compact=[]
    for row in rows:
        if not isinstance(row,dict):continue
        value={k:row.get(k) for k in ('trans_id','block_time','from_address','activity_type')}
        routes=row.get('routers');is_list=isinstance(routes,list)
        routes=routes if is_list else [routes]
        if len(routes)>1:continue
        value['routers']=[{k:r.get(k) for k in ('token1','token2','amount1','amount2')} for r in routes if isinstance(r,dict)]
        compact.append(value)
    gathered=cursor.get('solscan_rows',[])+compact
    if len(gathered)>10000:raise DataUnavailable('Solscan history overflow; wallet has a data gap.')
    if len(rows)==100:
        return [],{**cursor,'solscan_window':{**window,'page':window['page']+1},'solscan_rows':gathered}
    return normalized(gathered,wallet,window['after'],window['before']),{'at':window['before'],'provider':'solscan'}
