"""One-off accounting repair for Omar's finalized 2026-11-01 Phantom GOON exit.

Defaults to a read-only preview. --apply changes ledger accounting only, creates
an SQLite backup, retains the evidence, and always leaves new buys disabled.
Never imports the execution signer or submits a blockchain transaction.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.request

WALLET='3tmujS2fwJiPHRXc6NskYz1vJHf1Eq9JNvx2547MKut6'
MINT='8buabJcozHuvWi8PEwzPK8gQdNJSJ6A8cAqsoqhBxQfQ'
QUANTITY=338072070401
COST=212834386
EXPECTED_PROCEEDS=99679352
SIGNATURES=(
 '5yvsLX9Yo9kNrqkkXL9AyHYdNu6gqMLswjtMEdu7J2wAPmwYQMX3NRKKt1T5cstCh1utVnf9QL4nBZVDqrTWBLVZ',
 '32LQktuzkoQa6w9gnu2TecRmPMkwuumPKj3sGmG3YQTmfL7Htu5fnWRfBDhZwinJJdbyyNwVp9m1vHWpLkt64nkD',
 '2ZZUgs5wGMy8AgwC3ynx3vjsCP2TjBb8HBpFFd9JMzThr7eQ6gjgNcjoakFAcoaKJFNCy2jLPCzTE34SxuVe8U97',
 '3udeeRpHWQG9qV9wP43zHpoVzhcf4MdrjaMafdcc4fFCHrzPwmTy68BhhHn13PURo6UJkcVZqDmWfedo1bmEctCP',
 '3iSBYgYwzmza3KXwCx9dyJi2F5xMLz1uWaMuV5BoTT58J8GRSqusWBjsti9dF9QKXVRCoaywX6Y7D82ZDDzwuCe2',
)
RPC_METHODS={'getGenesisHash','getSignatureStatuses','getTransaction','getSignaturesForAddress','getTokenAccountsByOwner'}
GENESIS='5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d'
REPAIR_ID=hashlib.sha256(('GOON external exit:'+','.join(SIGNATURES)).encode()).hexdigest()


def require(condition,message):
    if not condition:raise ValueError(message)


def rpc(method,params):
    require(method in RPC_METHODS,'Read-only RPC methods only.')
    from urllib.parse import urlsplit
    url=os.getenv('SOLANA_REAL_RPC_URL','https://api.mainnet-beta.solana.com')
    parsed=urlsplit(url)
    require(parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password and not parsed.fragment,'Invalid HTTPS RPC configuration.')
    q=urllib.request.Request(url,data=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(q,timeout=20) as response:
            raw=response.read(4_000_001)
        require(len(raw)<=4_000_000,'RPC response too large.')
        data=json.loads(raw);require(isinstance(data,dict) and 'result' in data and not data.get('error'),'RPC did not return complete evidence.')
        return data['result']
    except Exception:raise ValueError('Read-only chain verification unavailable; no ledger changes permitted.') from None


def verify_chain(call=rpc):
    require(call('getGenesisHash',[])==GENESIS,'RPC is not Solana mainnet.')
    statuses=call('getSignatureStatuses',[list(SIGNATURES),{'searchTransactionHistory':True}])['value']
    require(len(statuses)==5 and all(s and s.get('confirmationStatus')=='finalized' and s.get('err') is None for s in statuses),'All five evidence transactions must be finalized and successful.')
    proof=[];deltas={};native=0;slots=[]
    for signature in SIGNATURES:
        tx=call('getTransaction',[signature,{'encoding':'jsonParsed','commitment':'finalized','maxSupportedTransactionVersion':1}])
        require(tx is not None and tx['meta']['err'] is None,'Missing or failed finalized transaction.')
        require(tx['transaction']['signatures'][0]==signature,'Evidence signature mismatch.')
        keys=tx['transaction']['message']['accountKeys'];indices=[i for i,k in enumerate(keys) if k['pubkey']==WALLET]
        require(len(indices)==1,'Trading wallet is missing from the evidence transaction.')
        require(1793552926<=tx['blockTime']<=1793552991,'Evidence time is outside the reviewed sale interval.')
        meta=tx['meta'];idx=indices[0];delta=meta['postBalances'][idx]-meta['preBalances'][idx];native+=delta;slots.append(tx['slot'])
        token_delta={}
        for field,sign in [('preTokenBalances',-1),('postTokenBalances',1)]:
            for row in meta[field]:
                if row.get('owner')==WALLET:
                    token_delta[row['mint']]=token_delta.get(row['mint'],0)+sign*int(row['uiTokenAmount']['amount'])
        if token_delta.get(MINT,0)<0:require(keys[idx]['signer'],'The token sale must be signed by the trading wallet.')
        for mint,amount in token_delta.items():deltas[mint]=deltas.get(mint,0)+amount
        proof.append({'signature':signature,'slot':tx['slot'],'block_time':tx['blockTime'],'native_delta_lamports':delta,'fee_lamports':meta['fee'],'token_deltas':token_delta})
    require(deltas.get(MINT)==-QUANTITY,'Evidence does not remove the exact recorded GOON quantity.')
    require(all(amount==0 for mint,amount in deltas.items() if mint!=MINT),'Other token changes require a separate review.')
    require(native==EXPECTED_PROCEEDS,'Net proceeds differ from the reviewed 99,679,352 lamports.')
    # Prevent an omitted incoming/outgoing transaction from changing the result.
    recent=call('getSignaturesForAddress',[WALLET,{'limit':100,'commitment':'finalized'}])
    require(recent and min(r['slot'] for r in recent)<=min(slots),'Recent history does not cover the complete sale interval.')
    interval={r['signature'] for r in recent if min(slots)<=r['slot']<=max(slots)}
    require(interval==set(SIGNATURES),'Additional wallet activity exists in the reviewed interval.')
    accounts=call('getTokenAccountsByOwner',[WALLET,{'mint':MINT},{'encoding':'jsonParsed','commitment':'finalized'}])
    require(all(int(r['account']['data']['parsed']['info']['tokenAmount']['amount'])==0 for r in accounts['value']),'GOON is still held on-chain.')
    return {'repair_id':REPAIR_ID,'wallet':WALLET,'mint':MINT,'quantity_raw':QUANTITY,'net_proceeds_lamports':native,'evidence':proof}


def validate_ledger(conn,proof):
    row=conn.execute('SELECT * FROM solana_real_run WHERE id=1').fetchone();require(row is not None,'Live ledger not found.')
    state=json.loads(row['state_json'])
    require(state['wallet']==WALLET and not state['enabled'],'Exact trading wallet must remain paused.')
    require(not state['positions'],'All bot holdings must be closed before this one-off repair.')
    require(row['lease_until']<=time.time(),'Worker is busy; retry later.')
    pending=conn.execute("SELECT count(*) FROM solana_real_orders WHERE status IN ('preparing','signed','submitted','unknown')").fetchone()[0]
    require(pending==0,'Pending bot order must settle or expire first.')
    require(proof['repair_id']==REPAIR_ID and proof['net_proceeds_lamports']==EXPECTED_PROCEEDS and proof['quantity_raw']==QUANTITY,'Repair proof mismatch.')
    if any(r.get('repair_id')==REPAIR_ID for r in state.get('accounting_repairs',[])):
        return row,state,None
    adjustments=state.get('balance_adjustments',[])
    matches=[a for a in adjustments if a['mint']==MINT and a['quantity_removed_raw']==QUANTITY and a['unreconciled_cost_lamports']==COST and a.get('proceeds_lamports') is None]
    require(len(adjustments)==1 and len(matches)==1,'Expected one exact unresolved GOON balance reduction.')
    return row,state,matches[0]


def repair(database,proof,apply=False):
    path=Path(database).resolve();require(path.is_file(),'Database file does not exist.')
    with closing(sqlite3.connect(f'file:{path}?mode=rw',uri=True,timeout=30)) as conn:
        conn.row_factory=sqlite3.Row
        row,state,adjustment=validate_ledger(conn,proof)
        if adjustment is None:return {'status':'already_applied','buys_paused':True,'repair_id':REPAIR_ID}
        pnl=EXPECTED_PROCEEDS-COST
        preview={'status':'preview','buys_paused':True,'repair_id':REPAIR_ID,'net_proceeds_lamports':EXPECTED_PROCEEDS,
                 'removed_cost_lamports':COST,'realized_pnl_adjustment_lamports':pnl,'realized_before_lamports':state['realized_lamports'],
                 'realized_after_lamports':state['realized_lamports']+pnl,'evidence':proof['evidence']}
        if not apply:return preview
        revision=row['revision'];backup=path.with_name(path.name+'.before_goon_repair_'+str(time.time_ns())+'.sqlite3')
        with closing(sqlite3.connect(backup)) as dest:conn.backup(dest)
        conn.execute('BEGIN IMMEDIATE')
        try:
            row,state,adjustment=validate_ledger(conn,proof)
            require(row['revision']==revision and adjustment is not None,'Ledger changed after preview/backup; rerun verification.')
            audit={**proof,'reviewed_at':time.time(),'removed_cost_lamports':COST,'realized_pnl_adjustment_lamports':pnl,
                   'original_adjustment':adjustment,'basis':'Reviewed five-transaction Phantom sale/settlement bundle; net native wallet change includes fees and account rent.'}
            state.setdefault('accounting_repairs',[]).append(audit)
            state['balance_adjustments'].remove(adjustment)
            state['realized_lamports']+=pnl;state['enabled']=False;state['error']=None
            conn.execute('UPDATE solana_real_run SET revision=revision+1,state_json=? WHERE id=1 AND revision=?',(json.dumps(state,allow_nan=False),revision))
            conn.commit()
        except Exception:conn.rollback();raise
        return {**preview,'status':'applied','backup':str(backup),'accounting_complete':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--database',default=os.getenv('DATABASE_PATH','/data/trader.db'));parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    try:print(json.dumps(repair(args.database,verify_chain(),args.apply),indent=2))
    except Exception as error:raise SystemExit('No repair applied: '+str(error))


if __name__=='__main__':main()
