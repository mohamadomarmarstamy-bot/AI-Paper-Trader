"""Owner-run data checks and explicitly attributed PAPER research seeds."""
from contextlib import closing
from copy import deepcopy
import json
import time
import database

SEEDS = (
    {'name':'Mr. Frog','wallet':'4DdrfiDHpmx55i4SPssxVzS9ZaKLb8qr45NKY9Er9nNh',
     'source_url':'https://kolexplorer.com/kol/mr-frog','reported_realized_usd':377000,
     'reported_win_rate_percent':93.5,'reported_closed_positions':4763},
    {'name':'Numero','wallet':'A3W8psibkTUvjxs4LRscbnjux6TFDXdvD4m4GsGpQ2KJ',
     'source_url':'https://kolexplorer.com/kol/numero','reported_realized_usd':65100,
     'reported_win_rate_percent':61.6,'reported_closed_positions':310},
    {'name':'Iceman','wallet':'DVMkhiQe1D8yenuEgsW44NjRn9LfVQjGEpZcez5x7Mff',
     'source_url':'https://kolexplorer.com/kol/iceman','reported_realized_usd':84600,
     'reported_win_rate_percent':39.8,'reported_closed_positions':1620,'reviewed_on':'2026-10-30',
     'reported_realized_7d_usd':2100,'reported_realized_30d_usd':22800,
     'reported_equal_stake_multiple_90d':1.53,'reported_profitable_weeks':5,'reported_weeks_sample':8,
     'reported_median_hold_seconds':104,'reported_reliability_score':69,
     'copy_source_check':'Recent successful Pump/PumpSwap sell decoded by the same read-only copier on 2026-10-30.',
     'review_note':'Positive reported 7/30/90-day realized P/L and equal-stake history. Low win rate; results depend on larger winners. Historical provider figures do not include copying delays or modeled execution costs.'},
    {'name':'Solkrow','wallet':'ApRnQN2HkbCn7W2WWiT2FEKvuKJp9LugRyAE1a9Hdz1',
     'source_url':'https://kolexplorer.com/kol/solkrow','reported_realized_usd':162800,
     'reported_win_rate_percent':38.9,'reported_sample_trades':1547,'reviewed_on':'2026-10-30',
     'reported_realized_7d_usd':12916.059331935,'reported_realized_30d_usd':42442.618992857,
     'reported_equal_stake_multiple_90d':1.38,'reported_profitable_weeks':5,'reported_weeks_sample':8,
     'reported_median_hold_seconds':162,'reported_reliability_score':66,
     'copy_source_check':'Three successful wallet-signed Pump buys decoded read-only on 2026-10-30; historical sample only, not copied retrospectively.',
     'copy_source_sample_at':1793375009,
     'copy_source_sample_signatures':['2S5vMG4QGMW4acV8fK2LMLWWLg9eP1W65vy3PithFBKUJo4MxYNxeuC5jKhXSo54stAEKkkWGRE355LTSLaNXUsD',
         '3FTwh8VWAnzb3PbfuFkiRTZgLFGR36cMRpnrGKoLa5eixf9SxJDCZbBEaqwro6MdXdzKcBzdM1e98NUkxaavSXF6',
         '4c23pJA3aTfCQmKjJL5nT9j6vD6uqiQbQTA9t1oTSMNTJskDYF8Mu3xA7sb6NEMVuSEuLUShGb3yit88uewBAV5n'],
     'review_note':'Positive published 7/30/90-day realized results, 1.38x reported equal-stake result and 5/8 profitable weeks. The 90-day dollar figure is rounded as published; reported results are not independently audited and exclude our copying latency, 20% loss stops and modeled execution costs. Low win rate: larger winners drive reported returns.'},
)
BONK='DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263'


def connection_status():
    with closing(database.get_connection()) as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_copy_connection_checks'").fetchone()
        row=conn.execute('SELECT report_json FROM solana_copy_connection_checks WHERE id=1').fetchone() if exists else None
    return json.loads(row[0]) if row else None


def check_connections():
    import solana_copy_trader as copy
    import solana_copy_solscan as solscan
    now=time.time();provider=copy.Birdeye();wallet=SEEDS[0]['wallet'];checks=[]
    def check(name,call):
        try:
            data=call()
            checks.append({'name':name,'ok':True,'detail':'Read-only API request succeeded.',
                           'rows':len(data) if isinstance(data,list) else None})
            return data
        except copy.DataUnavailable as error:checks.append({'name':name,'ok':False,'detail':str(error)})
        except Exception:checks.append({'name':name,'ok':False,'detail':'Response schema unavailable or unsupported.'})
    check('Birdeye profit discovery',lambda:provider.request('/trader/gainers-losers',params={'type':'1W','sort_by':'realized_pnl','min_realized_pnl':1000,'limit':1,'offset':0}))
    check('Birdeye wallet P/L',lambda:provider.request('/wallet/v2/pnl/details',payload={'wallet':wallet,'duration':'all','position_scope':'cumulative','pnl_method':'wac','sort_by':'last_trade','limit':1}))
    check('Birdeye meme metadata',lambda:provider.meme(BONK))
    check('Birdeye swap history',lambda:provider.trades(wallet,{'at':now-60},now))
    check('Solscan swap history',lambda:solscan.request({'address':wallet,'activity_type[]':['ACTIVITY_TOKEN_SWAP','ACTIVITY_AGG_TOKEN_SWAP'],
                'from_time':int(now)-86400,'to_time':int(now),'page':1,'page_size':10,'sort_by':'block_time','sort_order':'desc'}))
    import solana_copy_rpc
    history=check('Public Solana wallet history · no key',lambda:solana_copy_rpc.request('getSignaturesForAddress',[wallet,{'limit':1,'commitment':'confirmed'}]))
    if isinstance(history,list) and history and isinstance(history[0],dict) and isinstance(history[0].get('signature'),str):
        check('Public Solana transaction details · no key',lambda:solana_copy_rpc.request('getTransaction',[history[0]['signature'],{'encoding':'jsonParsed','commitment':'confirmed','maxSupportedTransactionVersion':1}]))
    report={'checked_at':time.time(),'checks':checks,'paper_only':True,'orders_submitted':0}
    with closing(database.get_connection()) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS solana_copy_connection_checks(id INTEGER PRIMARY KEY CHECK(id=1),report_json TEXT NOT NULL)')
        conn.execute('INSERT OR REPLACE INTO solana_copy_connection_checks VALUES(1,?)',(json.dumps(report,allow_nan=False),));conn.commit()
    return report


def seed_wallets(expected_revision):
    import solana_copy_trader as copy
    import solana_copy_solscan as solscan
    from solana_meme_bot import Conflict,mint
    if type(expected_revision) is not int:raise ValueError('Revision required.')
    state,revision=copy.load()
    if expected_revision!=state['settings_revision']:raise Conflict('Copy settings changed. Reload before following wallets.')
    source='rpc' if state.get('public_rpc_enabled') else 'solscan' if solscan.configured() else 'birdeye'
    now=time.time()
    for seed in SEEDS:
        wallet=mint(seed['wallet'])
        if any(r['wallet']==wallet for r in state['traders']):continue
        evidence={**deepcopy(seed),'window':'90d','reviewed_on':seed.get('reviewed_on','2026-10-28'),
                  'basis':'KOL Explorer historical report; not independently audited or full-history provider qualification.'}
        state['traders'].append({'wallet':wallet,'name':seed['name'],'active':True,'selected_at':now,'trade_provider':source,
            'qualification':'owner_requested_research_seed','research_evidence':evidence,
            'ranking_window':'90d · external research','ranking_realized_usd':seed['reported_realized_usd'],
            'historical_realized_usd':None,'win_rate_percent':None,'sell_count':None})
        state['cursors'][wallet]={'at':now,'provider':source}
    with closing(database.get_connection()) as conn:
        copy.initialize(conn)
        if conn.execute('UPDATE solana_copy_state SET state_json=?,revision=revision+1 WHERE id=1 AND revision=?',
                        (json.dumps(state,allow_nan=False),revision)).rowcount!=1:raise Conflict('Copy cycle changed. Retry following wallets.')
        conn.commit()
    return copy.public()


def enable_public_rpc(expected_revision):
    """Explicit prospective source change; never migrate a wallet with open lots."""
    import solana_copy_trader as copy
    from solana_meme_bot import Conflict
    state,revision=copy.load()
    if type(expected_revision) is not int or expected_revision!=state['settings_revision']:
        raise Conflict('Copy settings changed. Refresh before selecting the public source.')
    held={p['wallet'] for p in state['positions'].values()}
    targets=[r for r in state['traders'] if state['cursors'][r['wallet']].get('provider')!='rpc']
    if any(r['wallet'] in held for r in targets):raise ValueError('Close existing copied lots before changing their wallet source.')
    now=time.time()
    for trader in targets:
        trader.update(trade_provider='rpc',buy_resume_at=now)
        state['cursors'][trader['wallet']]={'provider':'rpc','at':now}
    state['config']['enabled']=True
    state.update(public_rpc_enabled=True,error=None,health='starting',discovery_retry_at=0)
    state['settings_revision']+=1
    with closing(database.get_connection()) as conn:
        copy.initialize(conn)
        if conn.execute('UPDATE solana_copy_state SET state_json=?,revision=revision+1 WHERE id=1 AND revision=?',
                        (json.dumps(state,allow_nan=False),revision)).rowcount!=1:raise Conflict('Copy cycle changed. Refresh and retry.')
        conn.commit()
    return copy.public()
