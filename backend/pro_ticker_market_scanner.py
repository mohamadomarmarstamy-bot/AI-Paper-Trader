"""Owner-triggered, rotating research scans. No brokerage credentials or writes."""
from pro_ticker_http_security import require_same_origin
from contextlib import closing
from datetime import datetime,timezone
from decimal import Decimal
import json
import re
import time
from fastapi import APIRouter,Request,HTTPException
from starlette.concurrency import run_in_threadpool
import database
from pro_ticker_signal_proposals import _uuid
from pro_ticker_market_intake import collect_research_snapshots
from pro_ticker_market_store import persist_market_research_run,initialize_market_research_store
from pro_ticker_market_snapshot import normalize_market_snapshot

_SCHEMA='''CREATE TABLE IF NOT EXISTS pro_ticker_market_scan_jobs (
 id TEXT PRIMARY KEY,request_json TEXT NOT NULL,status TEXT NOT NULL,
 started_at TEXT NOT NULL,finished_at TEXT,lease_until REAL NOT NULL,
 next_offset INTEGER NOT NULL,result_json TEXT NOT NULL);
 CREATE UNIQUE INDEX IF NOT EXISTS pro_ticker_single_market_scan
 ON pro_ticker_market_scan_jobs(status) WHERE status='running';'''


class ScanConflict(ValueError):pass


def yahoo_research_snapshot(symbol):
    """Keyless read-only fallback already used by this app; timestamp is real.

    Volume ratio compares today's cumulative regular-session minute volume with
    the mean of 20 completed daily sessions, not time-of-day adjusted RVOL.
    Delayed/stale data fails the same 120-second validation as every provider.
    """
    import yfinance as yf
    from zoneinfo import ZoneInfo
    ticker=yf.Ticker(symbol)
    minutes=ticker.history(period='1d',interval='1m',auto_adjust=False,prepost=False,timeout=3)
    daily=ticker.history(period='3mo',interval='1d',auto_adjust=False,prepost=False,timeout=3)
    if minutes.empty or daily.empty or minutes.index.tz is None or daily.index.tz is None:
        raise ValueError('Timestamped market history unavailable.')
    observed=minutes.index[-1].to_pydatetime();session=observed.astimezone(ZoneInfo('America/New_York')).date()
    completed=daily[[stamp.to_pydatetime().astimezone(ZoneInfo('America/New_York')).date()<session for stamp in daily.index]].tail(20)
    if len(completed)!=20:raise ValueError('20 completed sessions required.')
    # Missing volume is not silently summed as zero.
    if minutes['Volume'].isna().any() or completed['Volume'].isna().any():raise ValueError('Volume unavailable.')
    previous=Decimal(str(completed.iloc[-1]['Close']));price=Decimal(str(minutes.iloc[-1]['Close']))
    average=Decimal(str(completed['Volume'].sum()))/20
    if previous<=0 or average<=0:raise ValueError('Historical reference unavailable.')
    return {'symbol':symbol,'price':str(price),'volume':int(minutes['Volume'].sum()),
            'average_volume':str(average),'observed_at':observed.isoformat(),'source':'yahoo_finance_minute_close',
            'price_change_percent':str((price/previous-1)*100)}


def yahoo_discovery_universe():
    """One bounded Yahoo screen; no brokerage or market-cap restriction."""
    import yfinance as yf
    query=yf.EquityQuery('and',[
        yf.EquityQuery('eq',['region','us']),
        yf.EquityQuery('gte',['intradayprice',0.25]),
        yf.EquityQuery('gte',['avgdailyvol3m',50000]),
        yf.EquityQuery('gte',['percentchange',5]),
    ])
    return yf.screen(query,offset=0,size=250,sortField='percentchange',sortAsc=False)


def eligible_discovery_symbols(response, *, now):
    """Recheck screen evidence locally; detailed snapshots are checked again."""
    from pro_ticker_broker_admission import _number
    if not isinstance(response,dict) or not isinstance(response.get('quotes'),list) or len(response['quotes'])>250:
        raise ValueError('Bounded Yahoo equity screen required.')
    ranked={}
    for row in response['quotes']:
        try:
            if not isinstance(row,dict) or row.get('quoteType')!='EQUITY' or row.get('exchange') not in {'NMS','NYQ','NGM','NCM','ASE','PCX','BTS','PNK'}:continue
            symbol=row.get('symbol')
            if not isinstance(symbol,str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}',symbol):continue
            price=_number(row.get('regularMarketPrice'));average=_number(row.get('averageDailyVolume3Month'))
            volume=_number(row.get('regularMarketVolume'));move=_number(row.get('regularMarketChangePercent'));observed=_number(row.get('regularMarketTime'))
            if not (Decimal('.25')<=price<=1000000 and average>=50000 and volume>=average*Decimal('1.5') and move>=5 and 0<=Decimal(str(now))-observed<=120):continue
            rank=(-move,-volume/average,symbol)
            if symbol not in ranked or rank<ranked[symbol]:ranked[symbol]=rank
        except (ValueError,TypeError,ArithmeticError):continue
    return sorted(ranked,key=lambda symbol:ranked[symbol])


def _saved_daily_grades(symbols):
    grades={}
    if not symbols:return grades
    with closing(database.get_connection()) as conn:
        columns={r['name'] for r in conn.execute('PRAGMA table_info(pro_ticker_manual_signal_reviews)')}
        if not {'symbol','request_id','status','completed_at','grade_json'}.issubset(columns):return grades
        rows=conn.execute("SELECT symbol,completed_at,grade_json FROM pro_ticker_manual_signal_reviews WHERE status='graded' AND symbol IN ("+','.join('?' for _ in symbols)+") ORDER BY completed_at DESC,request_id DESC LIMIT 1000",symbols)
        for record in rows:
            if record['symbol'] in grades:continue
            try:
                grade=json.loads(record['grade_json'])
                if grade.get('version')!='pro_ticker_daily_technical_grade_v1' or grade.get('status')!='graded' or type(grade.get('score')) is not int or not -6<=grade['score']<=6 or grade.get('grade') not in ('A','B','C','D') or not isinstance(record['completed_at'],str):continue
                expected='A' if grade['score']>=4 else 'B' if grade['score']>=3 else 'D' if grade['score']<=-3 else 'C'
                recorded=datetime.fromisoformat(record['completed_at'].replace('Z','+00:00'))
                if grade['grade']!=expected or recorded.tzinfo is None:continue
                grades[record['symbol']]={'grade':grade['grade'],'score':grade['score'],'recorded_at':record['completed_at'],'kind':'saved_daily_technical_not_intraday'}
            except (ValueError,TypeError,AttributeError):continue
    return grades


def _public(row,now):
    symbols=json.loads(row['result_json']).get('symbols',[]) if row else []
    return {'paper':True,'research_only':True,'order_execution_authorized':False,
            'provider':'yahoo_finance_minute_close','automatic_scanning_enabled':False,'saved_daily_grades':_saved_daily_grades(symbols),
            'status':('interrupted' if row['status']=='running' and row['lease_until']<=now else row['status']) if row else 'not_run',
            'run':{'id':row['id'],'started_at':row['started_at'],'finished_at':row['finished_at'],**json.loads(row['result_json'])} if row else None}


def scanner_status(*,clock=time.time):
    with closing(database.get_connection()) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_market_scan_jobs'").fetchone():return _public(None,clock())
        row=conn.execute('SELECT * FROM pro_ticker_market_scan_jobs ORDER BY started_at DESC,id DESC LIMIT 1').fetchone()
        return _public(row,clock())


def _universe(conn):
    tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    symbols=set()
    for table in ('pro_ticker_research','pro_ticker_manual_signal_reviews'):
        if table in tables:
            symbols.update(row[0] for row in conn.execute('SELECT DISTINCT symbol FROM '+table+' LIMIT 1000')
                           if isinstance(row[0],str) and re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}',row[0]))
    return sorted(symbols)[:1000]


def run_market_scan(payload,*,fetch_snapshot=yahoo_research_snapshot,fetch_universe=yahoo_discovery_universe,clock=time.time):
    if not isinstance(payload,dict) or set(payload) not in ({'request_id','symbols'},{'request_id','symbols','mode'}):raise ValueError('Scan request ID and symbol list required.')
    discovery=payload.get('mode','symbols')=='discover'
    if payload.get('mode','symbols') not in ('symbols','discover'):raise ValueError('Unknown scanner mode.')
    id=_uuid(payload['request_id']);symbols=payload['symbols']
    if not isinstance(symbols,list) or len(symbols)>8 or any(not isinstance(s,str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}',s) for s in symbols):
        raise ValueError('At most eight canonical symbols required.')
    if discovery and symbols:raise ValueError('Discovery does not accept operator symbols.')
    symbols=list(dict.fromkeys(symbols));request_json=json.dumps({'mode':'discover'}) if discovery else json.dumps(symbols);started=clock()
    with closing(database.get_connection()) as conn:
        conn.executescript(_SCHEMA);initialize_market_research_store(conn);conn.commit();conn.execute('BEGIN IMMEDIATE')
        previous=conn.execute('SELECT * FROM pro_ticker_market_scan_jobs WHERE id=?',(id,)).fetchone()
        if previous:
            if previous['request_json']!=request_json:raise ScanConflict('Request ID belongs to another symbol batch.')
            return {**_public(previous,clock()),'duplicate':True}
        conn.execute("UPDATE pro_ticker_market_scan_jobs SET status='interrupted' WHERE status='running' AND lease_until<=?",(started,))
        if conn.execute("SELECT 1 FROM pro_ticker_market_scan_jobs WHERE status='running'").fetchone():raise ScanConflict('A market scan is already running.')
        source='yahoo_us_equity_movers' if discovery else 'operator_symbols' if symbols else 'saved_research_and_manual_symbols'
        offset=0
        if discovery:
            universe=[]
            last=conn.execute('SELECT next_offset FROM pro_ticker_market_scan_jobs WHERE request_json=? ORDER BY started_at DESC,id DESC LIMIT 1',(request_json,)).fetchone()
            offset=last[0] if last else 0
        elif not symbols:
            universe=_universe(conn)
            last=conn.execute('SELECT next_offset FROM pro_ticker_market_scan_jobs WHERE request_json=? ORDER BY started_at DESC,id DESC LIMIT 1',('[]',)).fetchone()
            offset=(last[0] if last else 0)%len(universe) if universe else 0
            symbols=(universe[offset:]+universe[:offset])[:8]
        initial={'symbols':symbols,'universe_source':source,'universe_count':len(universe) if source!='operator_symbols' else len(symbols)}
        conn.execute("INSERT INTO pro_ticker_market_scan_jobs VALUES (?,?,'running',?,NULL,?,?,?)",(id,request_json,datetime.fromtimestamp(started,timezone.utc).isoformat(),started+180,offset+len(symbols),json.dumps(initial)))
        conn.commit()
    if discovery:
        try:
            universe_response=fetch_universe()
            universe=eligible_discovery_symbols(universe_response,now=clock())
            offset=offset%len(universe) if universe else 0
            symbols=(universe[offset:]+universe[:offset])[:8]
            initial.update(symbols=symbols,universe_count=len(universe),
                           universe_returned_count=len(universe_response['quotes']),
                           universe_limited=len(universe_response['quotes'])==250,
                           filters={'minimum_price':'.25','minimum_average_volume':50000,'minimum_move_percent':5,'minimum_relative_volume':'1.5'})
        except Exception:
            # Persist a visible failure instead of silently falling back to saved
            # names or calling unavailable data an empty successful universe.
            with closing(database.get_connection()) as conn:
                conn.execute('BEGIN IMMEDIATE');now=clock()
                row=conn.execute('SELECT * FROM pro_ticker_market_scan_jobs WHERE id=?',(id,)).fetchone()
                if row['status']!='running' or now>=row['lease_until']:raise ScanConflict('Discovery lease expired.')
                failed={**initial,'error':'Yahoo market discovery unavailable or invalid. No saved-symbol fallback was used.'}
                conn.execute("UPDATE pro_ticker_market_scan_jobs SET status='failed',finished_at=?,result_json=? WHERE id=?",(datetime.fromtimestamp(now,timezone.utc).isoformat(),json.dumps(failed),id));conn.commit()
                return {**_public(conn.execute('SELECT * FROM pro_ticker_market_scan_jobs WHERE id=?',(id,)).fetchone(),now),'duplicate':False}
    def bounded(symbol):
        if clock()-started>45:raise ValueError('Scan time budget exhausted.')
        return fetch_snapshot(symbol)
    result=collect_research_snapshots(symbols,fetch_snapshot=bounded,now=datetime.fromtimestamp(clock(),timezone.utc),limit=8)
    # Recheck freshness at completion, rather than labeling an early quote fresh
    # after a slow later provider call. Preserve failures without inventing data.
    candidates=[]
    for row in result['candidates']:
        try:
            candidate=normalize_market_snapshot(row,now=datetime.fromtimestamp(clock(),timezone.utc))
            if discovery and not (Decimal(candidate['price'])>=Decimal('.25') and Decimal(candidate['average_volume'])>=50000 and candidate['price_change_percent'] is not None and Decimal(candidate['price_change_percent'])>=5 and Decimal(candidate['relative_volume'])>=Decimal('1.5')):
                raise ValueError('Snapshot no longer passes discovery filters.')
            candidates.append(candidate)
        except ValueError:result['failures'].append({'symbol':row['symbol'],'reason':'Fresh quote unavailable or discovery filters no longer pass'})
    result.update(candidates=candidates,accepted=len(candidates),failed=len(result['failures']),count=len(candidates),has_more=False)
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE');now=clock()
        row=conn.execute('SELECT * FROM pro_ticker_market_scan_jobs WHERE id=?',(id,)).fetchone()
        if row['status']!='running' or now>=row['lease_until']:raise ScanConflict('Scan lease expired; results were not committed.')
        persist_market_research_run(conn,id,result)
        conn.execute("UPDATE pro_ticker_market_scan_jobs SET status='completed',finished_at=?,result_json=?,next_offset=? WHERE id=?",(datetime.fromtimestamp(now,timezone.utc).isoformat(),json.dumps({**initial,**result},allow_nan=False),offset+len(symbols),id));conn.commit()
        return {**_public(conn.execute('SELECT * FROM pro_ticker_market_scan_jobs WHERE id=?',(id,)).fetchone(),now),'duplicate':False}


def build_market_scanner_router(*,require_session,fetch_snapshot=yahoo_research_snapshot,fetch_universe=yahoo_discovery_universe):
    router=APIRouter();path='/auto-trader/pro-ticker/market-scan'
    @router.get(path)
    def status(request:Request):
        require_session(request)
        from fastapi.responses import JSONResponse
        try:return JSONResponse(scanner_status(),headers={'Cache-Control':'no-store'})
        except Exception:raise HTTPException(503,'Market scan history unavailable.') from None
    @router.post(path)
    async def scan(request:Request):
        require_session(request)
        require_same_origin(request)
        if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':raise HTTPException(415,'JSON required.')
        raw=await request.body()
        if len(raw)>2048:raise HTTPException(413,'Request too large.')
        try:return await run_in_threadpool(run_market_scan,json.loads(raw),fetch_snapshot=fetch_snapshot,fetch_universe=fetch_universe)
        except ScanConflict:raise HTTPException(409,'Scan active, interrupted, or request ID reused. Refresh saved status.') from None
        except (ValueError,RecursionError):raise HTTPException(422,'Valid request ID and at most eight symbols required.') from None
        except Exception:raise HTTPException(503,'Scan unconfirmed. Refresh saved status before retrying.') from None
    return router
