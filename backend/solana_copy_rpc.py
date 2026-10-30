"""Key-free, read-only PAPER observation of supported Pump/PumpSwap instructions.

Never submits transactions. IDL account layouts are pinned in the adjacent JSON.
Unsupported/ambiguous transactions are skipped, rather than inferred as swaps.
"""
from collections import deque
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from threading import Lock
import time
import requests

ENDPOINT='https://api.mainnet.solana.com'
PROGRAMS=json.loads(Path(__file__).with_name('solana_copy_pump_instructions.json').read_text())
_lock=Lock();_calls=deque();_cooldown=0


def request(method,params):
    from solana_copy_trader import DataUnavailable
    global _cooldown
    if method not in ('getSignaturesForAddress','getTransaction'):
        raise ValueError('Only read-only wallet history methods are supported.')
    with _lock:
        now=time.monotonic()
        while _calls and now-_calls[0]>=60:_calls.popleft()
        if now<_cooldown or len(_calls)>=100:raise DataUnavailable('Public Solana RPC cooling down; retrying automatically.')
        _calls.append(now)
    try:
        response=requests.post(ENDPOINT,json={'jsonrpc':'2.0','id':1,'method':method,'params':params},timeout=5,allow_redirects=False)
        if response.status_code==429:
            with _lock:_cooldown=time.monotonic()+60
            raise DataUnavailable('Public Solana RPC rate limited; retrying after cooldown.')
        if response.status_code in (401,403):raise DataUnavailable('Public Solana RPC denied access from this server. Wallet connection cannot grant RPC access.')
        if response.status_code!=200:raise DataUnavailable(f'Public Solana RPC {method}: HTTP {response.status_code}; retrying automatically.')
        if len(response.content)>2_000_000:raise ValueError()
        data=response.json()
        if isinstance(data,dict) and method=='getTransaction' and isinstance(data.get('error'),dict) and data['error'].get('code')==-32015:
            return {'unsupported_transaction_version':True}
        if isinstance(data,dict) and isinstance(data.get('error'),dict):
            code=data['error'].get('code')
            if type(code) is int:raise DataUnavailable(f'Public Solana RPC {method}: provider error {code}; retrying automatically.')
        if not isinstance(data,dict) or data.get('error') or 'result' not in data:raise ValueError()
        return data['result']
    except DataUnavailable:raise
    except requests.Timeout:raise DataUnavailable(f'Public Solana RPC {method} timed out; retrying automatically.') from None
    except requests.ConnectionError:raise DataUnavailable(f'Public Solana RPC {method} connection failed; retrying automatically.') from None
    except Exception:raise DataUnavailable('Public Solana RPC history unavailable or unsupported. No copy fill created.') from None


def decode58(value):
    from solana_meme_bot import ALPHABET
    if not isinstance(value,str) or not 1<=len(value)<=2048:raise ValueError()
    n=0
    for char in value:n=n*58+ALPHABET.index(char)
    return b'\0'*(len(value)-len(value.lstrip('1')))+n.to_bytes((n.bit_length()+7)//8,'big')


def parse(transaction,wallet,signature):
    """One successful known instruction, signed actor and matching net token flow."""
    from solana_meme_bot import mint,num
    from solana_copy_trader import CASH_MINTS
    try:
        meta=transaction['meta'];message=transaction['transaction']['message']
        if meta['err'] is not None or transaction['transaction']['signatures'][0]!=signature:return []
        keys=message['accountKeys']
        if not any(k['pubkey']==wallet and k['signer'] is True for k in keys):return []
        instructions=list(enumerate(message['instructions']))
        expanded=[(str(i),r) for i,r in instructions]
        for group in meta.get('innerInstructions') or []:
            expanded.extend((str(group['index'])+':'+str(i),r) for i,r in enumerate(group['instructions']))
        matched=[]
        for index,instruction in expanded:
            program=PROGRAMS.get(instruction.get('programId'))
            if not program:continue
            layout=program['instructions'].get(decode58(instruction.get('data'))[:8].hex())
            if not layout:continue
            accounts=instruction['accounts']
            if accounts[layout['user']]!=wallet:continue
            address=mint(accounts[layout['mint']])
            quote=mint(accounts[layout['quote']]) if layout['quote'] is not None else 'So11111111111111111111111111111111111111112'
            if address in CASH_MINTS or quote not in CASH_MINTS:continue
            matched.append((index,address,quote,'buy' if layout['name'].startswith('buy') else 'sell'))
        # Multi-swap transactions cannot establish a single intent reliably.
        if len(matched)!=1:return []
        index,address,quote,side=matched[0]
        balances={};pre_balances={}
        for key,sign in [('preTokenBalances',-1),('postTokenBalances',1)]:
            for row in meta[key]:
                if row.get('owner')!=wallet:continue
                amount=row['uiTokenAmount'];raw=amount['amount'];decimals=amount['decimals']
                if not isinstance(raw,str) or not raw.isdigit() or len(raw)>40 or type(decimals) is not int or not 0<=decimals<=18:return []
                token=mint(row['mint'])
                value=Decimal(raw)/(Decimal(10)**decimals)
                balances[token]=balances.get(token,Decimal(0))+sign*value
                if sign==-1:pre_balances[token]=pre_balances.get(token,Decimal(0))+value
        delta=balances.get(address,Decimal(0))
        if (side=='buy' and delta<=0) or (side=='sell' and delta>=0):return []
        # Ignore unrelated token gifts/transfers and ambiguous multi-asset flows.
        if any(v and a not in CASH_MINTS and a!=address for a,v in balances.items()):return []
        at=num(transaction['blockTime'])
        return [{'owner':wallet,'tx_hash':signature,'ins_index':index,'inner_ins_index':0,'block_unix_time':at,
                 'base':{'address':address,'type_swap':'to' if side=='buy' else 'from'},
                 'quote':{'address':quote,'type_swap':'from' if side=='buy' else 'to'},'source':'solana_rpc_pump','source_position_before':float(pre_balances.get(address,Decimal(0)))}]
    except (ValueError,TypeError,KeyError,IndexError,OverflowError):return []


def trades(wallet,cursor,now):
    from solana_copy_trader import DataUnavailable
    from solana_meme_bot import mint,num
    wallet=mint(wallet);cursor=deepcopy(cursor)
    window=cursor.get('window')
    if window is None or window.get('scan_before'):
        options={'limit':20,'commitment':'confirmed'}
        if cursor.get('latest'):options['until']=cursor['latest']
        if window is not None:options['before']=window['scan_before']
        data=request('getSignaturesForAddress',[wallet,options])
        if not isinstance(data,list) or len(data)>20:raise DataUnavailable('Unsupported public RPC signature list.')
        queue=[]
        for row in data:
            if not isinstance(row,dict) or not isinstance(row.get('signature'),str) or not 64<=len(row['signature'])<=128:
                raise DataUnavailable('Unsupported public RPC signature entry.')
            if row.get('err') is None and row.get('confirmationStatus') in ('confirmed','finalized'):
                if row.get('blockTime') is None:raise DataUnavailable('RPC transaction time not available yet.')
                at=num(row['blockTime'])
                if at>now+5:raise DataUnavailable('RPC transaction time is ahead of this server.')
                if at>=cursor['at']-2:queue.append(row)
        if window is None:
            window={'queue':[],'at':now,'newest':data[0]['signature'] if data else cursor.get('latest'),
                    'seen':0,'unsupported':0,'decoded':0,'truncated':False}
        # A full newest page is not a complete history window. Page backwards
        # to the previous cursor/time boundary before advancing or decoding.
        window['queue']=list(reversed(queue))+window['queue']
        window['seen']+=len(data)
        reached_boundary=any(isinstance(r.get('blockTime'),(int,float)) and r['blockTime']<cursor['at']-2 for r in data)
        window['scan_before']=data[-1]['signature'] if len(data)==20 and not reached_boundary else None
        window['truncated']=bool(window['scan_before'])
        if window['scan_before']:
            coverage={'checked_at':now,'signatures_seen':window['seen'],'decoded_swaps':window['decoded'],
                      'unsupported_transactions':window['unsupported'],'history_truncated':True,'queued':len(window['queue'])}
            return [],{**cursor,'window':window,'rpc_coverage':coverage}
    rows=[]
    for row in window['queue'][:4]:
        tx=request('getTransaction',[row['signature'],{'encoding':'jsonParsed','commitment':'confirmed','maxSupportedTransactionVersion':1}])
        if tx is None:
            if now-row['blockTime']<=180:raise DataUnavailable('RPC confirmed transaction is not available yet; retrying.')
            parsed=[]
        else:parsed=parse(tx,wallet,row['signature'])
        window['decoded']+=len(parsed);window['unsupported']+=not bool(parsed)
        rows.extend(parsed)
    window['queue']=window['queue'][4:]
    coverage={'checked_at':now,'signatures_seen':window['seen'],'decoded_swaps':window['decoded'],
              'unsupported_transactions':window['unsupported'],'history_truncated':window['truncated'],'queued':len(window['queue'])}
    if window['queue']:return rows,{**cursor,'window':window,'rpc_coverage':coverage}
    return rows,{'at':window['at'],'latest':window['newest'],'provider':'rpc','rpc_coverage':coverage}
