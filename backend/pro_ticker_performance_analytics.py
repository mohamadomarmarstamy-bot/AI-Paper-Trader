"""Execution-scoped descriptive analytics; never orders, fees or strategy changes."""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import json
import time
import database
from pro_ticker_execution_journal import _ownership, gross_execution_metrics

VERSION = 'pro_ticker_performance_v1'
SCHEMA = '''CREATE TABLE IF NOT EXISTS pro_ticker_equity_observations (
 observed_at TEXT PRIMARY KEY, account_fingerprint TEXT NOT NULL,
 equity_usd TEXT NOT NULL, cash_usd TEXT NOT NULL, payload_json TEXT NOT NULL);'''


def _stamp(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Timezone required.')
    return stamp.astimezone(timezone.utc)


def _money(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError('Finite monetary evidence required.')
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('Finite monetary evidence required.')
    return result


def covered_trades(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'pro_ticker_execution_journal', 'pro_ticker_execution_sync'}.issubset(tables):
        return [], 0
    trades, incomplete = [], 0
    for sync in conn.execute('SELECT * FROM pro_ticker_execution_sync'):
        intent, orders, _ = _ownership(conn, sync['intent_id'])
        coverage = json.loads(sync['coverage_json'])
        if (len(coverage) != len(orders) or {c['broker_order_id'] for c in coverage} != set(orders)
                or not all(c['terminal'] is True for c in coverage)):
            incomplete += 1
            continue
        events = [dict(r) for r in conn.execute('SELECT * FROM pro_ticker_execution_journal WHERE intent_id=? ORDER BY executed_at,activity_id', (intent['id'],))]
        complete = True
        for item in coverage:
            owned = orders[item['broker_order_id']]
            selected = [r for r in events if r['broker_order_id'] == item['broker_order_id']]
            posted = conn.execute('SELECT COALESCE(SUM(shares),0) FROM pro_ticker_strategy_fill_events WHERE broker_order_id=?', (item['broker_order_id'],)).fetchone()[0]
            if (item['side'] != owned['side'] or type(item['shares']) is not int or item['shares'] < 0
                    or item['shares']>owned['expected_shares']
                    or sum(r['shares'] for r in selected) != item['shares'] or posted != item['shares']
                    or any(r['side'] != owned['side'] or r['symbol'] != intent['plan']['symbol'] or r['shares'] <= 0 or _money(r['price']) <= 0 for r in selected)):
                complete = False
        buys = sum(r['shares'] for r in events if r['side'] == 'buy')
        sells = sum(r['shares'] for r in events if r['side'] == 'sell')
        if not complete or any(r['broker_order_id'] not in orders for r in events) or not 0 < buys <= intent['plan']['shares'] or buys != sells:
            incomplete += 1
            continue
        entry = sum((_money(r['price'])*r['shares'] for r in events if r['side'] == 'buy'), Decimal(0))
        exit_value = sum((_money(r['price'])*r['shares'] for r in events if r['side'] == 'sell'), Decimal(0))
        closed_at = max(_stamp(r['executed_at']) for r in events)
        trades.append({'intent_id': intent['id'], 'symbol': intent['plan']['symbol'], 'closed_at': closed_at.isoformat(),
                       'opened_at': min(_stamp(r['executed_at']) for r in events if r['side']=='buy').isoformat(),
                       'entry_notional_usd': str(entry), 'exit_notional_usd': str(exit_value),
                       'gross_pnl_usd': str(exit_value-entry), 'net_pnl_usd': None,
                       'grade': intent['plan'].get('entry_grade'), 'strategy_version': intent['plan'].get('strategy_version'),
                       'entry_average_usd': str(entry/buys), 'exit_average_usd': str(exit_value/sells),
                       'allocation_reference_usd': intent['plan'].get('reference_price'),
                       'planned_stop_usd': intent['plan'].get('stop_price'), 'planned_target_usd': intent['plan'].get('take_profit_price')})
    return trades, incomplete


def summarize_trades(trades, *, start=None, end=None):
    lower, upper = _stamp(start) if start else None, _stamp(end) if end else None
    if lower and upper and lower >= upper:
        raise ValueError('Start must precede end.')
    rows = sorted((r for r in trades if (lower is None or _stamp(r['closed_at']) >= lower) and (upper is None or _stamp(r['closed_at']) < upper)), key=lambda r:(_stamp(r['closed_at']),r['intent_id']))
    daily, groups = {}, {}
    for row in rows:
        pnl = _money(row['gross_pnl_usd'])
        day = _stamp(row['closed_at']).astimezone(ZoneInfo('America/New_York')).date().isoformat()
        daily[day] = daily.get(day, Decimal(0))+pnl
        grade = row.get('grade') or {}
        research = grade.get('research_context',{}).get('report',{})
        try:
            reference=_money(row.get('allocation_reference_usd'))
            if reference<=0:raise ValueError('Invalid reference.')
            row['entry_vs_allocation_reference_percent']=str((_money(row['entry_average_usd'])/reference-1)*100)
        except (ValueError,KeyError):
            row['entry_vs_allocation_reference_percent']=None
        row['exit_quality_status']='realized_average_only_intraday_path_unavailable'
        dimensions = {'research_grade': grade.get('grade'), 'research_version': grade.get('version'),
                      'strategy_version': row.get('strategy_version'), 'market_regime': research.get('market_regime')}
        for dimension, label in dimensions.items():
            if not isinstance(label,str) or not 1<=len(label)<=120:
                label = 'unattributed'
            groups.setdefault((dimension,label),[]).append(pnl)
    cumulative, peak, drawdown = Decimal(0), Decimal(0), Decimal(0)
    series=[]
    for day,pnl in sorted(daily.items()):
        cumulative += pnl;peak=max(peak,cumulative);drawdown=max(drawdown,peak-cumulative)
        series.append({'date':day,'gross_realized_usd':str(pnl),'cumulative_gross_realized_usd':str(cumulative), 'gross_realized_drawdown_usd':str(peak-cumulative)})
    outcomes=[_money(r['gross_pnl_usd']) for r in rows]
    return {'closed_count':len(rows),'rows':rows,'daily':series,'timezone':'America/New_York',
            'gross_realized_usd':str(sum(outcomes,Decimal(0))) if outcomes else None,
            'max_gross_realized_daily_drawdown_usd':str(drawdown) if outcomes else None,
            'drawdown_kind':'closed_trade_gross_daily_curve_not_account_equity', **gross_execution_metrics(outcomes),
            'groups':[{'dimension':d,'label':label,'samples':len(values),'gross_realized_usd':str(sum(values,Decimal(0))),**gross_execution_metrics(values)} for (d,label),values in sorted(groups.items())]}


def analytics_snapshot(*, start=None, end=None):
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN')
        trades,incomplete=covered_trades(conn)
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_equity_observations'").fetchone()
        observations=[json.loads(r[0]) for r in conn.execute('SELECT payload_json FROM pro_ticker_equity_observations ORDER BY observed_at')] if exists else []
    if start:observations=[r for r in observations if _stamp(r['observed_at'])>=_stamp(start)]
    if end:observations=[r for r in observations if _stamp(r['observed_at'])<_stamp(end)]
    peak=None;drawdown=Decimal(0)
    for row in observations:
        value=_money(row['equity_usd']);peak=value if peak is None else max(peak,value);drawdown=max(drawdown,peak-value)
    return {'version':VERSION,'paper':True,'research_only':True,'order_execution_authorized':False,
            'model_updated':False,'automatic_strategy_changes':False,'start':start,'end_exclusive':end,
            **summarize_trades(trades,start=start,end=end),'incomplete_sync_count':incomplete,
            'net_pnl_usd':None,'fees_usd':None,'adjustments_usd':None,'cost_status':'unavailable_verified_cost_evidence',
            'equity_observations':observations,'observed_equity_drawdown_usd':str(drawdown) if observations else None,
            'equity_limitations':'Dedicated broker equity snapshots only; external cash flows and adjustments are unverified. This raw observed decline is not a cash-flow-adjusted strategy return or an intraday maximum drawdown.',
            'limitations':['Closed fully covered executions only; an unsynced trade is not a confirmed outcome.',
                           'Time filters apply to final execution time; complete trade costs remain together.',
                           'Daily realized drawdown excludes unrealized excursions and begins at zero in the selected window.',
                           'Returned allocations are never profit. Missing fees and adjustments remain unavailable.']}


def capture_equity(*, context, clock=time.time):
    """Explicit read-only dedicated broker observation, with durable local history."""
    import hashlib
    context.validate()
    gateway=context.gateway();started=clock();account=gateway.read_account()
    if not isinstance(account,dict) or account.get('id')!=context.expected_account_id or account.get('currency')!='USD':
        raise ValueError('Dedicated USD PAPER identity required.')
    equity,cash=_money(account.get('equity')),_money(account.get('cash'))
    if equity<0 or not 0<=clock()-started<=30:
        raise ValueError('Fresh valid broker account evidence required.')
    fingerprint=hashlib.sha256(context.expected_account_id.encode()).hexdigest()[:12]
    # Open-position values are independently marked from owned, priced fills.
    positions=gateway.read_positions()
    if not isinstance(positions,list) or len(positions)>500:
        raise ValueError('Complete bounded position snapshot required.')
    marks=[]
    def ledger_state(conn):
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows={}
        for table in ('pro_ticker_strategy_positions','pro_ticker_execution_journal','pro_ticker_order_intents','pro_ticker_paper_exit_links','pro_ticker_strategy_fill_events'):
            rows[table]=[tuple(r) for r in conn.execute('SELECT * FROM '+table+' ORDER BY rowid')] if table in tables else []
        return hashlib.sha256(repr(rows).encode()).hexdigest()
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN')
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        owned={r['symbol']:r['shares'] for r in conn.execute('SELECT * FROM pro_ticker_strategy_positions WHERE shares>0')} if 'pro_ticker_strategy_positions' in tables else {}
        state=ledger_state(conn)
        # Persist valuation only if every broker holding matches the strategy ledger.
        if len(positions)!=len(owned) or len({p.get('symbol') for p in positions})!=len(positions):
            raise ValueError('Broker positions do not match owned strategy holdings.')
        for position in positions:
            symbol=position.get('symbol');qty=_money(position.get('qty'))
            if position.get('side')!='long' or symbol not in owned or qty!=owned[symbol]:
                raise ValueError('Owned broker quantity mismatch.')
            events=[dict(r) for r in conn.execute('SELECT * FROM pro_ticker_execution_journal WHERE symbol=? AND account_fingerprint=? ORDER BY executed_at,activity_id',(symbol,fingerprint))] if 'pro_ticker_execution_journal' in tables else []
            posted={(r['broker_order_id'],r['side']):r['total_shares'] for r in conn.execute(
                'SELECT broker_order_id,side,SUM(shares) AS total_shares FROM pro_ticker_strategy_fill_events WHERE symbol=? GROUP BY broker_order_id,side',
                (symbol,))} if 'pro_ticker_strategy_fill_events' in tables else {}
            priced={}
            for event in events:
                key=(event['broker_order_id'],event['side'])
                if type(event['shares']) is not int or event['shares']<=0 or _money(event['price'])<=0:
                    raise ValueError('Valid priced execution increments required.')
                priced[key]=priced.get(key,0)+event['shares']
            if posted!=priced:
                raise ValueError('Every owned posted fill requires matching priced execution coverage.')
            for event in events:
                intent,orders,_=_ownership(conn,event['intent_id'])
                if event['broker_order_id'] not in orders or orders[event['broker_order_id']]['side']!=event['side'] or intent['plan']['symbol']!=symbol:
                    raise ValueError('Open execution ownership mismatch.')
            # Weighted-average cost, applying each sale only to the then-held balance.
            shares=0;basis=Decimal(0)
            for event in events:
                if event['side']=='buy':shares+=event['shares'];basis+=_money(event['price'])*event['shares']
                elif 0<event['shares']<=shares:
                    basis-=basis*Decimal(event['shares'])/shares;shares-=event['shares']
                else:raise ValueError('Incomplete execution cost basis.')
            if shares!=qty or shares<=0:raise ValueError('Open-position execution coverage missing.')
            marks.append({'symbol':symbol,'shares':shares,'cost_basis_usd':str(basis),
                          'basis_method':'weighted_average_owned_execution_prices','net_unrealized_usd':None})
    # Fetch marks after releasing the SQLite read snapshot; a slow provider must
    # not block concurrent ledger commits. Recheck that ledger before saving.
    from pro_ticker_manual_signals import quote_snapshot
    for mark in marks:
        quote=gateway.read_trade(mark['symbol'])
        reference=quote_snapshot(quote,now=clock())
        if (not isinstance(quote,dict) or quote.get('symbol') not in (None,mark['symbol'])
                or reference['fresh'] is not True):raise ValueError('Fresh owned-symbol mark required.')
        value=_money(reference['price'])*mark['shares']
        mark.update(mark=str(reference['price']),market_timestamp=reference['market_timestamp'],
                    market_value_usd=str(value),gross_unrealized_usd=str(value-_money(mark['cost_basis_usd'])))
    if not 0<=clock()-started<=30:raise ValueError('Valuation window expired.')
    if any(not 0<=clock()-_stamp(mark['market_timestamp']).timestamp()<=90 for mark in marks):
        raise ValueError('Mark expired before observation commit.')
    observed=datetime.fromtimestamp(clock(),timezone.utc).isoformat()
    result={'observed_at':observed,'equity_usd':str(equity),'cash_usd':str(cash),'positions':marks,
            'source':'identity_verified_dedicated_paper_account','cash_flow_adjusted':False,'net_pnl_usd':None}
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA);conn.execute('BEGIN IMMEDIATE')
        if ledger_state(conn)!=state:raise ValueError('Accounting changed while marks were fetched; retry observation.')
        fingerprints={r[0] for r in conn.execute('SELECT DISTINCT account_fingerprint FROM pro_ticker_equity_observations')}
        if fingerprints and fingerprints!={fingerprint}:raise ValueError('Equity history belongs to another account.')
        # A busy writer or a slow ledger recheck can exhaust the evidence window.
        # Recheck immediately before insertion, after acquiring the write lock.
        committed_at=clock()
        if not 0<=committed_at-started<=30 or any(
                not 0<=committed_at-_stamp(mark['market_timestamp']).timestamp()<=90 for mark in marks):
            raise ValueError('Valuation expired while waiting to commit.')
        observed=datetime.fromtimestamp(committed_at,timezone.utc).isoformat()
        result['observed_at']=observed
        conn.execute('INSERT INTO pro_ticker_equity_observations VALUES (?,?,?,?,?)',(observed,fingerprint,str(equity),str(cash),json.dumps(result,allow_nan=False)));conn.commit()
    return {'paper':True,'order_submitted':False,'budget_released':False,'observation':result}
