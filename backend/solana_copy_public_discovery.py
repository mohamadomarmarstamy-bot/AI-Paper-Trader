"""Continuously discover attributed PAPER research follows from a public board.

Published performance is not independently audited wallet P/L. This module
never signs transactions or substitutes richest wallets for trader rankings.
"""
from html.parser import HTMLParser
import re
import time
import requests
import solana_meme_bot as bot

URL='https://kolexplorer.com/'
INTERVAL=600


class Board(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.rows=[]
    def handle_starttag(self,tag,attrs):
        data=dict(attrs)
        if tag=='tr' and 'board-row' in data.get('class','').split():self.rows.append(data)


def parse(html,now,minimum_hold):
    parser=Board();parser.feed(html)
    if not parser.rows:raise ValueError('Public ranking format unavailable')
    candidates=[];seen=set()
    for row in parser.rows[:1000]:
        try:
            # data-wallet is a lowercased search key; ONLY data-address preserves
            # the case-sensitive Solana public key. Never reconstruct its case.
            wallet=bot.mint(row['data-address']);slug=row['data-slug']
            if not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*',slug) or len(slug)>100:continue
            name=row['data-kol-name'].strip()
            if not 1<=len(name)<=100:continue
            month=bot.num(row['data-pnl30d']);week=bot.num(row['data-pnl7d'])
            win=bot.num(row['data-wr30d']);hold=bot.num(row['data-avghold'])
            count=bot.num(row['data-txcount']);last=bot.num(row['data-last-tx-ts'])
            if wallet in seen or month<=0 or week<=0 or not 50<win<=100 or hold<minimum_hold or count<50 or not 0<=now-last<=7*86400:continue
            seen.add(wallet)
            candidates.append({'wallet':wallet,'name':name,'reported_realized_30d_usd':month,
                'reported_realized_7d_usd':week,'reported_win_rate_percent':win,
                'reported_median_hold_seconds':hold,'reported_transaction_count':count,'reported_last_trade_at':last,
                'source_url':'https://kolexplorer.com/kol/'+slug,'observed_at':now})
        except (ValueError,KeyError,TypeError,OverflowError):continue
    return sorted(candidates,key=lambda row:(-row['reported_realized_30d_usd'],row['wallet'])),len(parser.rows)


def fetch(now,minimum_hold,*,get=None):
    from solana_copy_trader import DataUnavailable
    try:
        started=time.monotonic()
        response=(get or requests.get)(URL,timeout=6,allow_redirects=False,stream=True)
        try:
            if response.status_code!=200:raise ValueError()
            data=bytearray()
            for chunk in response.iter_content(65536):
                if time.monotonic()-started>8:raise ValueError()
                data.extend(chunk)
                if len(data)>2_000_000:raise ValueError()
            html=data.decode('utf-8')
        finally:response.close()
        return parse(html,now,minimum_hold)
    except Exception:raise DataUnavailable('Public trader ranking unavailable or changed; existing follows continue. Retrying automatically.') from None


def scheduling_limit(config):
    # At least one complete 3-wallet sweep within 60 seconds at the configured
    # runtime interval. This is a data-coverage budget, not a financial limit.
    interval=max(10,config['poll_seconds'])
    return max(1,min(int(config['max_active_traders']),int(60//interval)*3 or 1))


def rebalance(state,now):
    config=state['config'];limit=scheduling_limit(config)
    owner=[r for r in state['traders'] if r.get('active',True) and not r.get('auto_discovered')]
    slots=max(0,limit-len(owner))
    ranked=sorted((r for r in state['traders'] if r.get('auto_discovered') and r.get('research_eligible',False)),
                  key=lambda row:(-row.get('ranking_realized_usd',0),row['wallet']))
    # Keep eligible current follows stable; add the next researched wallets
    # whenever a slot is free. Do not constantly reset their prospective cursor.
    selected=[r for r in ranked if r.get('active',False)][:slots]
    selected += [r for r in ranked if not r.get('active',False)][:max(0,slots-len(selected))]
    chosen={r['wallet'] for r in selected};held={p['wallet'] for p in state['positions'].values()}
    for trader in (r for r in state['traders'] if r.get('auto_discovered')):
        active=trader['wallet'] in chosen
        if active and not trader.get('active',False):
            trader['buy_resume_at']=now
            if trader['wallet'] not in held:state['cursors'][trader['wallet']]={'at':now,'provider':'rpc'}
        trader['active']=active
        trader['discovery_status']='following' if active else 'queued_capacity' if trader.get('research_eligible') else 'not_eligible'
    state['public_discovery']['active_capacity']=limit


def advance(state,clock,*,fetcher=None):
    now=clock();info=state.setdefault('public_discovery',{'last_fetch':0,'next_fetch':0,'checked':0,'refreshes':0,'error':None})
    if not state['config']['auto_discover']:return
    if now>=info.get('next_fetch',0):
        try:
            candidates,checked=(fetcher or fetch)(now,state['config']['minimum_trader_hold_seconds'])
            info.update(last_fetch=now,next_fetch=now+INTERVAL,checked=checked,qualified=len(candidates),
                refreshes=info.get('refreshes',0)+1,error=None)
            qualifying={row['wallet'] for row in candidates}
            for trader in state['traders']:
                if trader.get('auto_discovered'):trader['research_eligible']=trader['wallet'] in qualifying
            for candidate in candidates:
                wallet=candidate['wallet'];existing=next((r for r in state['traders'] if r['wallet']==wallet),None)
                evidence={**candidate,'basis':'Public KOL Explorer 30-day report; not independently audited or full-history provider qualification.'}
                if existing:
                    existing['public_research_evidence']=evidence
                    if existing.get('auto_discovered'):
                        existing.update(research_eligible=True,ranking_realized_usd=candidate['reported_realized_30d_usd'],reviewed_at=now)
                    continue
                enrolled=clock()
                state['traders'].append({'wallet':wallet,'name':candidate['name'],'active':False,'selected_at':enrolled,
                    'trade_provider':'rpc','qualification':'public_ranked_research','auto_discovered':True,'research_eligible':True,
                    'ranking_realized_usd':candidate['reported_realized_30d_usd'],'ranking_window':'30d · public report',
                    'historical_realized_usd':None,'win_rate_percent':None,'sell_count':None,'public_research_evidence':evidence,
                    'discovery_status':'queued_capacity'})
                state['cursors'][wallet]={'at':enrolled,'provider':'rpc'}
        except Exception:
            info.update(error='Public trader ranking unavailable or changed; retained existing follows.',next_fetch=now+120)
    rebalance(state,clock())
    info['roster_count']=sum(bool(r.get('auto_discovered')) for r in state['traders'])
