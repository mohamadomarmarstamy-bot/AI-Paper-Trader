"""Quota-bounded, exact-pool USD Codex trial and opt-in paper fallback."""
from datetime import datetime, timezone
import json
import math
import os

import solana_http
import jarvis_research as research

URL='https://graph.codex.io/graphql'
NETWORK=1399811149
SCHEMA='''
CREATE TABLE IF NOT EXISTS codex_calls(id INTEGER PRIMARY KEY,at REAL NOT NULL,day TEXT NOT NULL,month TEXT NOT NULL,reserved INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS codex_calls_period ON codex_calls(month,day);
CREATE TABLE IF NOT EXISTS codex_samples(pair TEXT PRIMARY KEY,mint TEXT NOT NULL,at REAL NOT NULL,status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS codex_control(id INTEGER PRIMARY KEY CHECK(id=1),blocked_until REAL NOT NULL,status TEXT NOT NULL);
'''
META='''query JarvisPool($id:String!){pairMetadata(pairId:$id){id networkId token0{address networkId} token1{address networkId}}}'''
BARS='''query JarvisBars($id:String!,$start:Int!,$end:Int!,$side:QuoteToken!){getBars(symbol:$id,symbolType:POOL,quoteToken:$side,currencyCode:"USD",resolution:"1",from:$start,to:$end,countback:30,removeEmptyBars:false){s t o h l c buyVolume sellVolume pair{address networkId token0 token1}}}'''


class Unavailable(Exception):
    def __init__(self,status):self.status=status


def mode():return os.getenv('JARVIS_CODEX_MODE','trial').strip().lower()


def reserve(api,now,units):
    dt=datetime.fromtimestamp(now,timezone.utc)
    day,month=dt.strftime('%Y-%m-%d'),dt.strftime('%Y-%m')
    with api.connection() as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        control=conn.execute('SELECT * FROM codex_control WHERE id=1').fetchone()
        if control and control['blocked_until']>now:raise Unavailable(control['status'])
        if conn.execute('SELECT count(*) FROM codex_calls WHERE at>?',(now-1,)).fetchone()[0]>=4:
            raise Unavailable('paced')
        daily=conn.execute('SELECT COALESCE(sum(reserved),0) FROM codex_calls WHERE day=?',(day,)).fetchone()[0]
        monthly=conn.execute('SELECT COALESCE(sum(reserved),0) FROM codex_calls WHERE month=?',(month,)).fetchone()[0]
        if daily+units>200 or monthly+units>8000:raise Unavailable('quota_reached')
        if mode()=='trial' and conn.execute('SELECT count(*) FROM codex_calls').fetchone()[0]>=6:
            raise Unavailable('trial_complete')
        conn.execute('INSERT INTO codex_calls(at,day,month,reserved) VALUES (?,?,?,?)',(now,day,month,units))


def request(api,query,variables,units,clock,post):
    reserve(api,clock(),units)
    key=os.getenv('JARVIS_CODEX_API_KEY','').strip()
    try:
        response=post(URL,json={'query':query,'variables':variables},headers={'Authorization':key,'Content-Type':'application/json'},timeout=5,allow_redirects=False)
        if response.status_code in (401,403):raise Unavailable('authentication_or_plan')
        if response.status_code==429:raise Unavailable('rate_limited')
        if response.status_code!=200 or len(response.content)>400000:raise Unavailable('provider_error')
        data=response.json()
        if data.get('errors'):
            codes={e.get('extensions',{}).get('code') for e in data['errors'] if isinstance(e,dict)}
            raise Unavailable('authentication_or_plan' if codes & {'NOT_AUTHORIZED','UNAUTHENTICATED','FORBIDDEN'} else 'graphql_error')
        if not isinstance(data.get('data'),dict):raise Unavailable('invalid_response')
        return data['data']
    except Unavailable:raise
    except Exception:raise Unavailable('connection_error') from None


def parse_metadata(data,row):
    pool=data.get('pairMetadata')
    if not isinstance(pool,dict) or pool.get('id')!=row['pair']+':'+str(NETWORK) or pool.get('networkId')!=NETWORK:
        raise Unavailable('pool_not_indexed_or_mismatch')
    tokens=[pool.get(k) or {} for k in ('token0','token1')]
    if any(not isinstance(t,dict) or t.get('networkId')!=NETWORK for t in tokens):raise Unavailable('identity_mismatch')
    addresses=[t.get('address') for t in tokens]
    if any(not isinstance(a,str) or not research.ADDRESS.fullmatch(a) for a in addresses):raise Unavailable('identity_mismatch')
    if row['mint'] not in addresses or len(set(addresses))!=2:raise Unavailable('identity_mismatch')
    return 'token'+str(addresses.index(row['mint'])),addresses


def parse_bars(data,row,addresses,now):
    chart=data.get('getBars')
    if not isinstance(chart,dict) or chart.get('s')!='ok':raise Unavailable('no_data')
    pool=chart.get('pair') or {}
    if not isinstance(pool,dict):raise Unavailable('identity_mismatch')
    if pool.get('address')!=row['pair'] or pool.get('networkId')!=NETWORK or [pool.get('token0'),pool.get('token1')]!=addresses:
        raise Unavailable('identity_mismatch')
    keys=('t','o','h','l','c','buyVolume','sellVolume')
    arrays=[chart.get(k) for k in keys]
    if any(not isinstance(a,list) for a in arrays) or not 20<=len(arrays[0])<=30 or len({len(a) for a in arrays})!=1:
        raise Unavailable('invalid_chart')
    bars=[];seen=set();cutoff=int(now-5)//60*60
    try:
        for values in zip(*arrays):
            if any(v is None or isinstance(v,bool) for v in values):raise ValueError()
            at,op,high,low,close,buy,sell=map(float,values)
            if not all(math.isfinite(v) for v in (at,op,high,low,close,buy,sell)) or at%60 or at in seen or buy<0 or sell<0 or not 0<low<=min(op,close)<=max(op,close)<=high:
                raise ValueError()
            seen.add(at)
            if at+60<=cutoff:bars.append([at,op,high,low,close,buy+sell])
    except (ValueError,TypeError,OverflowError):raise Unavailable('invalid_chart') from None
    try:value=research._chart_signal(bars,now)
    except research.ChartUnavailable as error:raise Unavailable(error.status) from None
    value.update(provider='codex',currency='USD',volume_currency='USD')
    return value


def attempt(api,row,*,clock,post=solana_http.post):
    if not os.getenv('JARVIS_CODEX_API_KEY','').strip() or mode() not in ('trial','fallback'):return None
    now=clock()
    if row.get('monitor_only') or research.pool_warming(row) or not all(isinstance(row.get(k),str) and research.ADDRESS.fullmatch(row[k]) for k in ('mint','pair')):return None
    with api.connection() as conn:
        previous=conn.execute('SELECT * FROM codex_samples WHERE pair=?',(row['pair'],)).fetchone()
        if previous and (mode()=='trial' or now-previous['at']<3600):return None
        if mode()=='trial' and conn.execute('SELECT count(*) FROM codex_samples').fetchone()[0]>=3:return None
    value=None
    try:
        data=request(api,META,{'id':row['pair']+':'+str(NETWORK)},11,clock,post)
        side,addresses=parse_metadata(data,row)
        end=int(clock()-5)//60*60
        data=request(api,BARS,{'id':row['pair']+':'+str(NETWORK),'start':end-30*60,'end':end,'side':side},5,clock,post)
        value=parse_bars(data,row,addresses,clock());status='validated'
    except Unavailable as error:status=error.status
    with api.connection() as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('INSERT OR REPLACE INTO codex_samples VALUES (?,?,?,?)',(row['pair'],row['mint'],clock(),status))
        if status in ('authentication_or_plan','graphql_error','rate_limited','connection_error','provider_error'):
            delay=3600 if status in ('authentication_or_plan','graphql_error') else 60
            conn.execute('INSERT OR REPLACE INTO codex_control VALUES (1,?,?)',(clock()+delay,status))
    return value if mode()=='fallback' and status=='validated' else None


def status(conn):
    now=datetime.now(timezone.utc);month=now.strftime('%Y-%m');day=now.strftime('%Y-%m-%d')
    counts={r['status']:r['n'] for r in conn.execute('SELECT status,count(*) AS n FROM codex_samples GROUP BY status')}
    return {'configured':bool(os.getenv('JARVIS_CODEX_API_KEY','').strip()),'mode':mode(),
        'samples':counts,'calls':conn.execute('SELECT count(*) FROM codex_calls').fetchone()[0],
        'reserved_cu_today':conn.execute('SELECT COALESCE(sum(reserved),0) FROM codex_calls WHERE day=?',(day,)).fetchone()[0],
        'reserved_cu_month':conn.execute('SELECT COALESCE(sum(reserved),0) FROM codex_calls WHERE month=?',(month,)).fetchone()[0],
        'daily_reserved_cu_limit':200,'monthly_reserved_cu_limit':8000,'trial_request_limit':6}
