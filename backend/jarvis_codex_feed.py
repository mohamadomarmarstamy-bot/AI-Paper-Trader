"""Quota-bounded, exact-pool USD Codex trial and opt-in paper fallback."""
from datetime import datetime, timezone
import json
import ast
import math
import os

import solana_http
import jarvis_research as research
import jarvis_local_lab as local_lab

URL='https://graph.codex.io/graphql'
NETWORK=1399811149
DAILY_CU_LIMIT=250
MONTHLY_CU_LIMIT=8000
SCHEMA='''
CREATE TABLE IF NOT EXISTS codex_calls(id INTEGER PRIMARY KEY,at REAL NOT NULL,day TEXT NOT NULL,month TEXT NOT NULL,reserved INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS codex_calls_period ON codex_calls(month,day);
CREATE TABLE IF NOT EXISTS codex_samples(pair TEXT PRIMARY KEY,mint TEXT NOT NULL,at REAL NOT NULL,status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS codex_control(id INTEGER PRIMARY KEY CHECK(id=1),blocked_until REAL NOT NULL,status TEXT NOT NULL);
'''
OUTCOME_SCHEMA='''
CREATE TABLE IF NOT EXISTS codex_outcomes(id INTEGER PRIMARY KEY,at REAL NOT NULL,complete_rules INTEGER NOT NULL,matching_rules INTEGER NOT NULL);
'''
SCHEMA += OUTCOME_SCHEMA
META='''query JarvisPool($id:String!){pairMetadata(pairId:$id){id networkId token0{address networkId} token1{address networkId}}}'''
BARS='''query JarvisBars($id:String!,$start:Int!,$end:Int!,$side:QuoteToken!){getBars(symbol:$id,symbolType:POOL,quoteToken:$side,currencyCode:"USD",resolution:"1",from:$start,to:$end,countback:30,removeEmptyBars:false){s t o h l c buyVolume sellVolume pair{address networkId token0 token1}}}'''


class Unavailable(Exception):
    def __init__(self,status):self.status=status


def hourly_allowance(hour):
    return min(DAILY_CU_LIMIT, max(16, DAILY_CU_LIMIT * (hour + 1) // 24))


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
        if daily+units>DAILY_CU_LIMIT or monthly+units>MONTHLY_CU_LIMIT:raise Unavailable('quota_reached')
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


def possible(expression, values):
    """Three-valued evaluation for selection only; unknown is never an entry."""
    def visit(node):
        if isinstance(node, ast.Name):
            value=values.get(node.id)
            return value if type(value) in (int,float) and math.isfinite(value) else None
        if isinstance(node, ast.Constant):return node.value
        if isinstance(node, ast.BoolOp):
            children=[visit(n) for n in node.values]
            if isinstance(node.op,ast.And):
                return False if False in children else None if None in children else True
            return True if True in children else None if None in children else False
        if isinstance(node,ast.UnaryOp):
            value=visit(node.operand)
            return None if value is None else not value if isinstance(node.op,ast.Not) else -value
        if isinstance(node,ast.BinOp):
            a,b=visit(node.left),visit(node.right)
            if a is None or b is None:return None
            try:
                value=local_lab.OPS[type(node.op)](a,b)
                return value if math.isfinite(value) and abs(value)<=1e18 else None
            except (ZeroDivisionError,OverflowError):return None
        if isinstance(node,ast.Compare):
            values_=[visit(n) for n in [node.left,*node.comparators]]
            unknown=False
            for op,a,b in zip(node.ops,values_,values_[1:]):
                if a is None or b is None:unknown=True
                elif not local_lab.COMPARISONS[type(op)](a,b):return False
            return None if unknown else True
        raise ValueError('Unsupported expression')
    return visit(local_lab.parse(expression))


def blocked_rules(api,conn,state,row,now):
    """Return slots that could trade if missing chart observations matched."""
    if (not state['enabled'] or row.get('monitor_only') or research.pool_warming(row)
        or not 0<=now-row['last']<=90 or not 0<=now-row['first']<=api.ENTRY_WINDOW
        or now<row.get('retry',0)):return []
    values=local_lab.observations(row,api.entry_evidence(conn,row,now))
    closed={r['bot'] for r in conn.execute('SELECT bot FROM trades WHERE mint=?',(row['mint'],))}
    slots=[]
    for slot,rule in state.get('local_rules',{}).items():
        portfolio=state['bots'].get(slot,{})
        positions=portfolio.get('positions',{})
        if (slot not in local_lab.SLOTS or slot in closed or row['mint'] in positions
            or len(positions)>=local_lab.MAX_OPEN or portfolio.get('cash',0)<api.BUY_COST
            or row['first']<rule['created']):continue
        required={n.id for n in ast.walk(local_lab.parse(rule['entry'])) if isinstance(n,ast.Name)}
        missing={k for k in required if values.get(k) is None}
        if missing and missing<= {'rsi','volume_ratio'} and possible(rule['entry'],values) is None:
            slots.append(slot)
    return slots


def tick(api,*,clock):
    """Spend fallback quota on the most actionable primary-feed gap."""
    if not os.getenv('JARVIS_CODEX_API_KEY','').strip() or mode() not in ('trial','fallback'):return
    now=clock()
    with api.connection() as conn,conn:
        state,_=api.read(conn)
        if not state['enabled'] or api.backlog.paused(state,now) or (state.get('entry_catchup_pause') or {}).get('active'):return
        control=conn.execute('SELECT blocked_until FROM codex_control WHERE id=1').fetchone()
        if control and control['blocked_until']>now:return
        dt=datetime.fromtimestamp(now,timezone.utc)
        daily=conn.execute('SELECT COALESCE(sum(reserved),0) FROM codex_calls WHERE day=?',(dt.strftime('%Y-%m-%d'),)).fetchone()[0]
        monthly=conn.execute('SELECT COALESCE(sum(reserved),0) FROM codex_calls WHERE month=?',(dt.strftime('%Y-%m'),)).fetchone()[0]
        # Leave enough allowance for BOTH requests before starting metadata.
        # reserve() still enforces the hard limits immediately before each call.
        if daily+16>DAILY_CU_LIMIT or monthly+16>MONTHLY_CU_LIMIT:return
        # Release fallback allowance across UTC hours, with one request pair available immediately.
        paced_limit=hourly_allowance(dt.hour)
        if mode()=='fallback' and daily+16>paced_limit:return
        due=conn.execute("SELECT due FROM research_tasks WHERE name='codex-priority'").fetchone()
        if due and due['due']>now:return
        candidates=[]
        for row in state['watch'].values():
            if not all(isinstance(row.get(k),str) and research.ADDRESS.fullmatch(row[k]) for k in ('mint','pair')):continue
            previous=conn.execute('SELECT at FROM codex_samples WHERE pair=?',(row['pair'],)).fetchone()
            if previous and (mode()=='trial' or now-previous['at']<3600):continue
            found=conn.execute('SELECT evidence FROM research WHERE mint=? AND pair=?',(row['mint'],row['pair'])).fetchone()
            record=(json.loads(found['evidence']).get('chart') or {}) if found else {}
            # Try the free primary first. Never pay for healthy or unattempted data.
            if record.get('status')=='ready' or not 0<=now-record.get('attempt_at',0)<=180:continue
            slots=blocked_rules(api,conn,state,row,now)
            if slots:candidates.append((row,slots))
        if not candidates:return
        row,slots=max(candidates,key=lambda c:(len(c[1]),c[0].get('volume_m5_usd',0),c[0].get('liquidity_usd',0),c[0]['first']))
        conn.execute("INSERT OR REPLACE INTO research_tasks VALUES ('codex-priority',?)",(now+60,))
    value=attempt(api,row,clock=clock)
    if value is None:return
    at=clock()
    with api.connection() as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        current,_=api.read(conn)
        current_row=current['watch'].get(row['mint'])
        if not current_row or current_row['pair']!=row['pair']:return
        current_slots=blocked_rules(api,conn,current,current_row,at)
        if not current_slots:return
        full=local_lab.observations(current_row,{'chart':value})
        complete=sum(all(type(full.get(n.id)) in (int,float) and math.isfinite(full[n.id])
                     for n in ast.walk(local_lab.parse(current['local_rules'][slot]['entry'])) if isinstance(n,ast.Name)) for slot in current_slots)
        matching=sum(local_lab.run(current['local_rules'][slot]['entry'],full) for slot in current_slots)
        existing=conn.execute('SELECT evidence FROM research WHERE mint=? AND pair=?',(row['mint'],row['pair'])).fetchone()
        evidence=json.loads(existing['evidence']) if existing else {}
        evidence['chart']={'at':at,'value':value,'attempt_at':at,'status':'ready','next_attempt':at+research.CHART_REFRESH}
        conn.execute('INSERT OR REPLACE INTO research VALUES (?,?,?,?)',(row['mint'],row['pair'],at,json.dumps(evidence)))
        conn.execute('INSERT INTO codex_outcomes(at,complete_rules,matching_rules) VALUES (?,?,?)',(at,complete,matching))
        conn.execute('DELETE FROM codex_outcomes WHERE at<?',(at-30*86400,))


def status(conn, now=None):
    now=datetime.now(timezone.utc) if now is None else datetime.fromtimestamp(now,timezone.utc)
    month=now.strftime('%Y-%m');day=now.strftime('%Y-%m-%d')
    counts={r['status']:r['n'] for r in conn.execute('SELECT status,count(*) AS n FROM codex_samples GROUP BY status')}
    return {'configured':bool(os.getenv('JARVIS_CODEX_API_KEY','').strip()),'mode':mode(),
        'impact_since_tracking':dict(conn.execute('SELECT count(*) AS fallback_charts,COALESCE(sum(complete_rules>0),0) AS charts_completing_rules,COALESCE(sum(matching_rules>0),0) AS charts_matching_rules FROM codex_outcomes').fetchone()),
        'required_closed_entries':conn.execute("SELECT count(*) FROM trades WHERE json_extract(data,'$.codex_required')=1").fetchone()[0],
        'required_open_entries':sum(bool(p.get('codex_required')) for b in json.loads(conn.execute('SELECT state FROM run WHERE id=1').fetchone()[0])['bots'].values() for p in b['positions'].values()) if conn.execute('SELECT 1 FROM run WHERE id=1').fetchone() else 0,
        'samples':counts,'calls':conn.execute('SELECT count(*) FROM codex_calls').fetchone()[0],
        'reserved_cu_today':conn.execute('SELECT COALESCE(sum(reserved),0) FROM codex_calls WHERE day=?',(day,)).fetchone()[0],
        'reserved_cu_month':conn.execute('SELECT COALESCE(sum(reserved),0) FROM codex_calls WHERE month=?',(month,)).fetchone()[0],
        'daily_reserved_cu_limit':DAILY_CU_LIMIT,'monthly_reserved_cu_limit':MONTHLY_CU_LIMIT,'trial_request_limit':6}
