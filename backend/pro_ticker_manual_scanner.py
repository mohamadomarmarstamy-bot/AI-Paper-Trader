"""Owner-submitted $2,000 PAPER entries; durable dispatch and scoped scanner exits.

This mode uses the original PAPER account deliberately, without enabling broad
scanner buying. Research-only endpoints never invoke this workflow.
"""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
import os
import time
import uuid

import database
import pro_ticker_premarket as premarket
from pro_ticker_manual_intake import parse_manual_symbols

VERSION = 'owner_manual_scanner_v1'
TARGET = Decimal('2000')
PAPER_URL = 'https://paper-api.alpaca.markets'
SCHEMA = '''CREATE TABLE IF NOT EXISTS pro_ticker_manual_scanner_entries (
 request_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, created_at REAL NOT NULL,
 updated_at REAL NOT NULL, state TEXT NOT NULL, account_fingerprint TEXT,
 shares INTEGER, reference_ask TEXT, client_order_id TEXT NOT NULL UNIQUE,
 broker_order_id TEXT, order_json TEXT, result_json TEXT, reason TEXT,
 exit_started_at REAL, exit_client_order_id TEXT, exit_json TEXT, exit_shares INTEGER,
 lease_owner TEXT, lease_until REAL NOT NULL DEFAULT 0,
 checked_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS pro_ticker_manual_scanner_health (
 id INTEGER PRIMARY KEY CHECK(id=1), checked_at REAL NOT NULL,
 account_fingerprint TEXT NOT NULL);'''


class EntryBlocked(ValueError):
    pass


@dataclass
class ManualScannerContext:
    request: object
    quote: object
    risk: object
    submit: object
    close: object
    scan: object
    record_entry: object = None
    record_exit: object = None
    cancel: object = None
    paper_base_url: str = PAPER_URL
    enabled: object = lambda: os.getenv('PRO_TICKER_MANUAL_SCANNER_ENABLED') == 'true'
    premarket_enabled: object = lambda: os.getenv('PRO_TICKER_PREMARKET_ENABLED') == 'true'
    quote_feed: str = 'iex'
    settings_revision: int = 0
    target_usd: float = 2000
    stop_loss_percent: float = 2
    take_profit_percent: float = 4
    exit_score: float = 40
    exit_confidence: float = 80

    def validate(self):
        if self.paper_base_url != PAPER_URL or self.enabled() is not True or self.quote_feed not in ('iex','sip'):
            raise EntryBlocked('Manual PAPER buying and scanner management are disabled.')


def _number(value):
    if isinstance(value, bool):
        raise ValueError('Invalid broker number.')
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('Finite broker number required.')
    return result


def _uuid(value):
    result = str(uuid.UUID(value))
    if result != value:
        raise ValueError('Canonical request ID required.')
    return result


def _stamp(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Timestamp timezone required.')
    return stamp.timestamp()


def _account(context):
    context.validate()
    account = context.request('GET', '/v2/account')
    if (not isinstance(account, dict) or not account.get('id')
            or str(account.get('status', '')).upper() != 'ACTIVE'
            or account.get('trading_blocked') is not False
            or account.get('account_blocked') is not False):
        raise EntryBlocked('The PAPER account is not ready for trading.')
    return hashlib.sha256(_uuid(account['id']).encode()).hexdigest()


def _save(request_id, **values):
    allowed = {'state', 'account_fingerprint', 'shares', 'reference_ask', 'broker_order_id',
               'order_json', 'result_json', 'reason', 'exit_started_at', 'exit_client_order_id',
               'exit_json', 'exit_shares', 'lease_owner', 'lease_until', 'checked_at', 'updated_at'}
    allowed |= set(premarket.COLUMNS)
    if not values or not set(values) <= allowed:
        raise ValueError('Invalid entry update.')
    with closing(database.get_connection()) as conn:
        conn.execute('UPDATE pro_ticker_manual_scanner_entries SET '
                     + ','.join(k + '=?' for k in values) + ' WHERE request_id=?',
                     (*values.values(), request_id))
        conn.commit()


def _row(request_id):
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA)
        premarket.migrate(conn)
        value = conn.execute('SELECT * FROM pro_ticker_manual_scanner_entries WHERE request_id=?',
                             (request_id,)).fetchone()
        return dict(value) if value else None


def public_entry(row):
    row = dict(row)
    attempted = row['state'] not in ('preparing', 'blocked')
    order = json.loads(row['order_json']) if row['order_json'] else {}
    return {'request_id': row['request_id'], 'symbol': row['symbol'], 'state': row['state'],
            'target_usd': str(row.get('target_usd','2000')), 'shares': row['shares'], 'reference_ask': row['reference_ask'],
            'order_submitted': None if row['state'] in ('uncertain', 'submitting') else attempted,
            'broker_order_id': row['broker_order_id'], 'reason': row['reason'],
            'broker_status': order.get('status'), 'filled_shares': order.get('filled_qty'),
            'managed_by': 'existing_scanner_rules', 'paper': True, 'version': VERSION,
            'entry_session': row.get('entry_session','regular'), 'entry_limit':row.get('entry_limit'),
            'protection_mode': 'app_premarket_native_regular' if row.get('entry_session')=='premarket' else 'native_regular_app_premarket',
            'stop_price':row.get('stop_price'),'take_profit_price':row.get('take_profit_price'),
            'exit_order_type':row.get('exit_order_type','market'),'exit_limit':row.get('exit_limit')}


def saved_entries():
    with closing(database.get_connection()) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_manual_scanner_entries'").fetchone():
            return {}
        rows = conn.execute('SELECT * FROM pro_ticker_manual_scanner_entries ORDER BY created_at DESC LIMIT 200').fetchall()
        return {r['request_id']: public_entry(r) for r in rows}


_last_blocker_diagnostic = None


def status(*, enabled, premarket_enabled=False, quote_feed='iex', target_usd=2000, entry_settings_revision=0, clock=time.time):
    global _last_blocker_diagnostic
    ready = False
    blockers=[]
    with closing(database.get_connection()) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_manual_scanner_entries'").fetchone():
            blockers=[dict(row) for row in conn.execute("SELECT symbol,state,reason FROM pro_ticker_manual_scanner_entries WHERE state IN ('preparing','submitting','uncertain','closing','attention','accepted','open') ORDER BY CASE WHEN state IN ('accepted','open') THEN 1 ELSE 0 END,updated_at DESC LIMIT 20").fetchall()]
            for row in blockers:row['scope']='symbol' if row['state'] in ('accepted','open') else 'account'

        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_manual_scanner_health'").fetchone():
            row = conn.execute('SELECT * FROM pro_ticker_manual_scanner_health WHERE id=1').fetchone()
            ready = bool(row and 0 <= clock() - row['checked_at'] <= 60)
    diagnostic=tuple((b['symbol'],b['state'],b['reason']) for b in blockers)
    if diagnostic!=_last_blocker_diagnostic:
        if blockers or _last_blocker_diagnostic:print(json.dumps({'manual_entry_blockers':blockers}),flush=True)
        _last_blocker_diagnostic=diagnostic
    return {'paper': True, 'version': VERSION, 'enabled': enabled is True,
            'scanner_ready': ready and enabled is True, 'target_usd': format(Decimal(str(target_usd)).normalize(),'f'),
            'entry_blockers':blockers,'whole_shares_only': True, 'regular_session_only': not (premarket_enabled is True and enabled is True),
            'premarket_enabled':premarket_enabled is True and enabled is True,
            'premarket_protection':'app_monitored_limits_native_stops_regular_only','quote_feed':quote_feed,
            'broad_scanner_buying_enabled': False,'entry_settings_revision':entry_settings_revision}


def _ready(fingerprint, now):
    with closing(database.get_connection()) as conn:
        row = conn.execute('SELECT * FROM pro_ticker_manual_scanner_health WHERE id=1').fetchone()
        return bool(row and row['account_fingerprint'] == fingerprint and 0 <= now - row['checked_at'] <= 60)


def _verify_order(order, row, *, exit_order=False):
    if not isinstance(order, dict):
        raise ValueError('Broker order unavailable.')
    client = row['exit_client_order_id'] if exit_order else row['client_order_id']
    if (order.get('client_order_id') != client or order.get('symbol') != row['symbol']
            or order.get('side') != ('sell' if exit_order else 'buy')
            or _number(order.get('qty')) != (row['exit_shares'] if exit_order else row['shares'])
            or not 0 <= _number(order.get('filled_qty')) <= (row['exit_shares'] if exit_order else row['shares'])):
        raise ValueError('Broker order differs from the saved request.')
    _uuid(order.get('id'))
    if not exit_order and row.get('broker_order_id') and order['id'] != row['broker_order_id']:
        raise ValueError('Linked broker parent identity changed.')
    limit_order = row.get('exit_order_type')=='limit' if exit_order else row.get('entry_session')=='premarket'
    if limit_order:
        limit = row['exit_limit'] if exit_order else row['entry_limit']
        if (order.get('type')!='limit' or order.get('time_in_force')!='day'
                or order.get('extended_hours') is not True or order.get('order_class','simple') not in ('simple','')
                or _number(order.get('limit_price'))!=_number(limit)):
            raise ValueError('Saved extended-hours limit identity differs.')
    else:
        if order.get('type') != 'market' or order.get('time_in_force') != ('day' if exit_order else 'gtc'):
            raise ValueError('Unexpected manual broker order type.')
        if not exit_order and order.get('order_class') != 'bracket':
            raise ValueError('Protective bracket parent required.')
    return order


def persist_entry_fill(row, order):
    """Idempotent original-account journal posting, including lost-ACK recovery."""
    _verify_order(order, row)
    if order.get('status') not in ('filled','canceled','expired','rejected'):
        return
    if _number(order['filled_qty']) == 0:
        return
    quantity = _number(order['filled_qty'])
    price = _number(order['filled_avg_price'])
    stamp = order.get('filled_at')
    if stamp is None and order.get('status') != 'filled':
        # A canceled partial entry may have no broker completion timestamp.
        # Never invent it, and never block the safety liquidation for a
        # missing journal timestamp. Exact accounting then needs fill review.
        return
    _stamp(stamp)
    if not 0 < quantity <= row['shares'] or quantity != quantity.to_integral_value() or price <= 0:
        raise ValueError('Complete priced entry required for journal posting.')
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute('SELECT * FROM trade_book WHERE entry_order_id=?', (order['id'],)).fetchall()
        if len(existing) > 1:
            raise ValueError('Duplicate trade-book ownership requires review.')
        if existing:
            item = existing[0]
            if (item['symbol'] != row['symbol'] or _number(item['shares']) != quantity
                    or item['strategy'] != VERSION or abs(_number(item['entry_price'])-price)>Decimal('.000000001')
                    or _stamp(item['entry_timestamp']) != _stamp(stamp)):
                raise ValueError('Existing entry journal ownership differs.')
            book_id = item['id']
        else:
            book_id = conn.execute('''INSERT INTO trade_book
                (symbol,status,shares,entry_price,entry_timestamp,entry_order_id,entry_reason,strategy,created_at,updated_at)
                VALUES (?,'OPEN',?,?,?,?,?,?,?,?)''',
                (row['symbol'],float(quantity),float(price),stamp,order['id'],'owner_manual_submission',VERSION,stamp,stamp)).lastrowid
        if not conn.execute("SELECT 1 FROM trade_book_events WHERE trade_book_id=? AND event='entry'", (book_id,)).fetchone():
            details = json.dumps({'strategy_version':VERSION,'manual_signal_id':row['request_id'],
                                  'entry_source':'owner_submission','target_usd':str(row.get('target_usd','2000'))})
            conn.execute("INSERT INTO trade_book_events(trade_book_id,symbol,event,timestamp,details_json) VALUES (?,?,'entry',?,?)", (book_id,row['symbol'],stamp,details))
        conn.commit()
    database.upsert_broker_fill(order_id=order['id'],symbol=row['symbol'],side='BUY',
        shares=float(quantity),price=float(price),filled_at=stamp,raw_order=order,source='alpaca_paper_immediate')
    database.create_trade_book_order_link(trade_book_id=book_id,order_id=order['id'],
        client_order_id=row['client_order_id'],order_role='ENTRY',created_at=stamp)
    for leg in order.get('legs') or []:
        _uuid(leg.get('id'))
        if leg.get('symbol') != row['symbol'] or leg.get('side') != 'sell':
            raise ValueError('Foreign bracket journal leg.')
        database.create_trade_book_order_link(trade_book_id=book_id,order_id=leg['id'],
            client_order_id=leg.get('client_order_id'),order_role='EXIT',created_at=stamp)


def _owned_quantity(row):
    order = json.loads(row['order_json']) if row.get('order_json') else {}
    if order.get('status') in ('filled','canceled','expired','rejected'):
        quantity = _number(order['filled_qty'])
        if 0 < quantity <= row['shares'] and quantity == quantity.to_integral_value():
            return quantity
    return Decimal(row['shares'])


def persist_exit_fills(row, orders):
    """Close only this manual entry after complete, priced owned exit coverage."""
    unique = {}
    for order in orders:
        order_id = _uuid(order.get('id'))
        if order_id in unique:
            raise ValueError('Duplicate exit evidence.')
        if order.get('symbol') != row['symbol'] or order.get('side') != 'sell':
            raise ValueError('Foreign exit evidence.')
        quantity = _number(order.get('filled_qty'))
        price = _number(order.get('filled_avg_price'))
        stamp = order['filled_at']
        _stamp(stamp)
        if not 0 < quantity <= _owned_quantity(row) or price <= 0:
            raise ValueError('Priced exit evidence required.')
        unique[order_id] = (quantity,price,stamp,order)
    if sum((v[0] for v in unique.values()),Decimal(0)) != _owned_quantity(row):
        raise ValueError('Complete owned exit coverage required.')
    book = database.load_trade_book_entry_by_order_id(row['broker_order_id'])
    if not book or book['strategy'] != VERSION or book['symbol'] != row['symbol'] or _number(book['shares']) != _owned_quantity(row):
        raise ValueError('Owned manual entry journal required.')
    for order_id,(quantity,price,stamp,order) in unique.items():
        database.upsert_broker_fill(order_id=order_id,symbol=row['symbol'],side='SELL',
            shares=float(quantity),price=float(price),filled_at=stamp,raw_order=order,source='alpaca_paper_immediate')
        database.create_trade_book_order_link(trade_book_id=book['id'],order_id=order_id,
            client_order_id=order.get('client_order_id'),order_role='EXIT',created_at=stamp)
    exit_price = sum((q*p for q,p,_,_ in unique.values()),Decimal(0))/_owned_quantity(row)
    last_id = max(unique,key=lambda key:_stamp(unique[key][2]))
    stamp = unique[last_id][2]
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute('SELECT * FROM trade_book WHERE id=?',(book['id'],)).fetchone()
        if current['status'] == 'OPEN':
            entry_price = _number(current['entry_price'])
            conn.execute('''UPDATE trade_book SET status='CLOSED',exit_price=?,exit_timestamp=?,
                realized_profit_loss=?,realized_return_percent=?,exit_order_id=?,exit_reason=?,updated_at=? WHERE id=? AND status='OPEN' ''',
                (float(exit_price),stamp,float((exit_price-entry_price)*_owned_quantity(row)),
                 float((exit_price/entry_price-1)*100),last_id,'manual_scanner_exit',stamp,book['id']))
        elif abs(_number(current['exit_price'])-exit_price)>Decimal('.0001'):
            raise ValueError('Existing closed journal differs from broker exits.')
        if not conn.execute("SELECT 1 FROM trade_book_events WHERE trade_book_id=? AND event='exit'", (book['id'],)).fetchone():
            conn.execute("INSERT INTO trade_book_events(trade_book_id,symbol,event,timestamp,details_json) VALUES (?,?,'exit',?,?)", (book['id'],row['symbol'],stamp,json.dumps({'strategy_version':VERSION,'reason':'manual_scanner_exit','owned_exit_order_ids':list(unique)})))
        conn.commit()


def recover_entry(request_id, *, context, clock=time.time):
    row = _row(_uuid(request_id))
    if row is None:
        raise ValueError('Saved manual entry required.')
    if row['state'] in ('blocked', 'preparing'):
        return public_entry(row)
    if _account(context) != row['account_fingerprint']:
        raise EntryBlocked('PAPER account changed; inspect the saved order.')
    order = context.request('GET', '/v2/orders:by_client_order_id',
                            params={'client_order_id': row['client_order_id']})
    order = _verify_order(order, row)
    if row['broker_order_id'] and row['broker_order_id'] != order['id']:
        raise ValueError('Linked broker order changed.')
    state = str(order.get('status'))
    filled = _number(order['filled_qty'])
    if callable(context.record_entry):
        context.record_entry(row, order)
    resolved = 'open' if state == 'filled' else 'closed' if state in ('canceled', 'expired', 'rejected') and filled == 0 else 'accepted'
    if row['exit_started_at'] is not None:
        resolved = 'closing'
    _save(request_id, state=resolved, broker_order_id=order['id'],
          order_json=json.dumps(order, allow_nan=False), updated_at=clock())
    return public_entry(_row(request_id))


def submit_entry(request_id, symbol, *, context, clock=time.time):
    request_id = _uuid(request_id)
    symbols = parse_manual_symbols(symbol)
    if len(symbols) != 1:
        raise ValueError('One stock required.')
    symbol = symbols[0]
    context.validate()
    existing = _row(request_id)
    if existing:
        if existing['symbol'] != symbol:
            raise ValueError('Request belongs to another stock.')
        if existing['state'] in ('submitting', 'uncertain'):
            try:
                return recover_entry(request_id, context=context, clock=clock)
            except Exception:
                return public_entry(existing)
        return public_entry(existing)
    now = clock()
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        review = conn.execute('SELECT symbol,submitted_at FROM pro_ticker_manual_signal_reviews WHERE request_id=?',
                              (request_id,)).fetchone()
        if not review or review['symbol'] != symbol or not 0 <= now - _stamp(review['submitted_at']) <= 120:
            raise EntryBlocked('A new submission is required; saved reviews never create delayed buys.')
        if conn.execute("SELECT 1 FROM pro_ticker_manual_scanner_entries WHERE state IN ('preparing','submitting','uncertain','closing','attention') OR (symbol=? AND state IN ('accepted','open'))", (symbol,)).fetchone():
            raise EntryBlocked('Resolve the active order or existing position before another buy.')
        conn.execute('INSERT INTO pro_ticker_manual_scanner_entries(request_id,symbol,created_at,updated_at,state,client_order_id) VALUES (?,?,?,?,?,?)',
                     (request_id, symbol, now, now, 'preparing', 'ptm-' + uuid.UUID(request_id).hex))
        conn.commit()
    try:
        target=_number(context.target_usd)
        from pro_ticker_entry_settings import validate
        validate({'dollars_per_stock':float(target),'stop_loss_percent':context.stop_loss_percent,'take_profit_percent':context.take_profit_percent})
        _save(request_id,target_usd=format(target,'f').rstrip('0').rstrip('.') if '.' in format(target,'f') else str(target),stop_loss_percent=str(context.stop_loss_percent),take_profit_percent=str(context.take_profit_percent))
        fingerprint = _account(context)
        if not _ready(fingerprint, clock()):
            raise EntryBlocked('The scanner sell worker is not ready. No buy was sent.')
        market_session, market = premarket.session(context,clock,entry=True)
        positions = context.request('GET', '/v2/positions')
        if not isinstance(positions, list) or any(p.get('symbol') == symbol for p in positions):
            raise EntryBlocked('A position already exists or the account position snapshot is unavailable.')
        quote = context.quote(symbol)
        ask = _number(quote['ask'])
        if quote.get('symbol') != symbol or not ask > 0 or not 0 <= clock() - _stamp(quote['timestamp']) <= 30:
            raise EntryBlocked('A fresh execution quote is required.')
        sizing_price = ask
        if market_session=='premarket':
            premarket.fresh_quote(context,symbol,clock)
            sizing_price = premarket.tick_price(ask,buy=True)
            stop_percent,profit_percent = _number(context.stop_loss_percent),_number(context.take_profit_percent)
            if not 0 < stop_percent < 100 or not 0 < profit_percent < 100:
                raise EntryBlocked('Valid stop-loss and take-profit settings required.')
            _save(request_id,entry_session='premarket',entry_limit=str(sizing_price),
                  entry_expires_at=min(clock()+60,_stamp(market['next_open'])-15),
                  stop_loss_percent=str(stop_percent),take_profit_percent=str(profit_percent))
        shares = int((target / sizing_price).to_integral_value(rounding=ROUND_DOWN))
        if not 1 <= shares <= 100000:
            raise EntryBlocked('The configured dollar target cannot buy a supported whole-share quantity.')
        # Owner selection authorizes entry independently of research grades or
        # scanner coverage. The scanner still manages subsequent owned exits.
        candidate = {'symbol':symbol, 'signal':'OWNER_SUBMITTED',
                     'entry_source':'owner_manual_selection', 'score':None, 'confidence':None}
        risk = premarket.risk(context,market_session,symbol=symbol, shares=shares, side='buy')
        maximum = risk.get('max_allowed_shares')
        if (risk.get('approved') is not True and isinstance(maximum, int)
                and not isinstance(maximum, bool) and 1 <= maximum < shares):
            shares = maximum
            risk = premarket.risk(context,market_session,symbol=symbol, shares=shares, side='buy')
        if risk.get('approved') is not True:
            raise EntryBlocked(str(risk.get('error') or 'Buying-power, position or order limits blocked this buy.'))
        context.validate()
        if _account(context) != fingerprint:
            raise EntryBlocked('PAPER account changed during execution checks.')
        if not 0 <= clock() - now <= 45 or not 0 <= clock() - _stamp(quote['timestamp']) <= 30:
            raise EntryBlocked('Execution checks expired; no order was sent.')
        with closing(database.get_connection()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM pro_ticker_manual_scanner_entries WHERE request_id=?', (request_id,)).fetchone()
            if row['state'] != 'preparing' or not _ready(fingerprint, clock()):
                raise EntryBlocked('The execution claim changed.')
            conn.execute("UPDATE pro_ticker_manual_scanner_entries SET state='submitting',account_fingerprint=?,shares=?,reference_ask=?,updated_at=? WHERE request_id=?",
                         (fingerprint, shares, str(ask), clock(), request_id))
            conn.commit()
    except Exception as error:
        reason = str(error) if isinstance(error, EntryBlocked) else 'Broker or scanner checks are unavailable. No buy was sent.'
        _save(request_id, state='blocked', reason=reason, updated_at=clock())
        return public_entry(_row(request_id))
    # The durable marker precedes transport. Never replay this callback, even
    # when the response is lost; recovery looks up the stable client identity.
    try:
        if market_session=='premarket':
            result = premarket.submit_limit(_row(request_id),context,clock)
        else:
            result = context.submit(symbol=symbol, shares=shares, reference_price=float(ask),
                                    request_id=request_id, candidate=candidate)
        if isinstance(result, dict) and result.get('risk', {}).get('approved') is False:
            _save(request_id, state='blocked', reason='Risk checks blocked dispatch.', updated_at=clock())
            return public_entry(_row(request_id))
    except EntryBlocked as error:
        _save(request_id,state='blocked',reason=str(error),updated_at=clock())
        return public_entry(_row(request_id))
    except Exception:
        pass
    with closing(database.get_connection()) as conn:
        conn.execute("UPDATE pro_ticker_manual_scanner_entries SET state='uncertain',updated_at=? WHERE request_id=? AND state='submitting'", (clock(),request_id))
        conn.commit()
    try:
        return recover_entry(request_id, context=context, clock=clock)
    except Exception:
        return public_entry(_row(request_id))


def assert_dispatch(request_id, symbol, shares):
    row = _row(_uuid(request_id))
    if not row or row['state'] != 'submitting' or row['symbol'] != symbol or row['shares'] != shares:
        raise ValueError('Saved manual PAPER dispatch claim required.')
    return row['client_order_id']


def worker_tick(*, context, clock=time.time):
    fingerprint = _account(context)
    now = clock()
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA)
        premarket.migrate(conn)
        conn.execute('INSERT INTO pro_ticker_manual_scanner_health VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET checked_at=excluded.checked_at,account_fingerprint=excluded.account_fingerprint', (now, fingerprint))
        # A crashed preflight never becomes a deferred buy.
        conn.execute("UPDATE pro_ticker_manual_scanner_entries SET state='blocked',reason='Preflight expired; submit a new review.' WHERE state='preparing' AND created_at<?", (now - 60,))
        rows = conn.execute("SELECT request_id FROM pro_ticker_manual_scanner_entries WHERE state IN ('submitting','uncertain','accepted','open','closing','attention') ORDER BY checked_at LIMIT 8").fetchall()
        conn.commit()
    for item in rows:
        request_id = item['request_id']
        owner = str(uuid.uuid4())
        with closing(database.get_connection()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            claimed = conn.execute('UPDATE pro_ticker_manual_scanner_entries SET lease_owner=?,lease_until=?,checked_at=? WHERE request_id=? AND lease_until<=?',
                                    (owner, clock()+90, clock(), request_id, clock())).rowcount
            conn.commit()
        if not claimed:
            continue
        try:
            _manage(request_id, context=context, clock=clock)
        except Exception:
            # Preserve an uncertain dispatch identity; never expose provider
            # exception text or turn a read failure into another buy/sell.
            current = _row(request_id)
            _save(request_id, state=current['state'] if current['state'] in ('submitting','uncertain') else 'attention',
                  reason='Scanner/broker evidence unavailable. Inspect fills and protective orders; no further order was sent.')
        finally:
            with closing(database.get_connection()) as conn:
                conn.execute('UPDATE pro_ticker_manual_scanner_entries SET lease_until=0,lease_owner=NULL WHERE request_id=? AND lease_owner=?', (request_id, owner))
                conn.commit()
    return {'paper': True, 'checked': len(rows), 'broad_scanner_buying_enabled': False}


def _manage(request_id, *, context, clock):
    row = _row(request_id)
    if row['account_fingerprint'] != _account(context):
        raise ValueError('Manual entry account differs.')
    recover_entry(request_id, context=context, clock=clock)
    row = _row(request_id)
    if row['state'] == 'closed':
        return
    if row['exit_started_at'] is not None:
        # A protective fill can win the cancellation race before the explicit
        # scanner exit is posted. Resolve those owned fills first.
        parent = context.request('GET','/v2/orders/'+row['broker_order_id'],params={'nested':'true'})
        _verify_order(parent,row)
        prior = [leg for leg in premarket.protective_legs(row,parent,context,clock) if _number(leg.get('filled_qty','0'))>0]
        if sum((_number(leg['filled_qty']) for leg in prior),Decimal(0)) == _owned_quantity(row):
            if callable(context.record_exit):
                context.record_exit(row,prior)
            _save(request_id,state='closed',updated_at=clock())
            return
        order = context.request('GET', '/v2/orders:by_client_order_id', params={'client_order_id': row['exit_client_order_id']})
        order = _verify_order(order, row, exit_order=True)
        if order.get('status') == 'filled' and callable(context.record_exit):
            context.record_exit(row, [*prior,order])
        _save(request_id, exit_json=json.dumps(order, allow_nan=False),
              state='closed' if order.get('status') == 'filled' else 'closing' if order.get('status') in ('new','accepted','pending_new','partially_filled') else 'attention', updated_at=clock())
        return
    parent = context.request('GET', '/v2/orders/' + row['broker_order_id'], params={'nested': 'true'})
    parent = _verify_order(parent, row)
    if callable(context.record_entry):
        context.record_entry(row, parent)
    if row.get('entry_session')=='premarket':
        parent = premarket.settle_entry(row,parent,context,clock)
        if parent is None:
            return
        row = _row(request_id)
    filled = _number(parent['filled_qty'])
    if filled == 0:
        return
    if parent.get('status') != 'filled' and row.get('entry_session')!='premarket':
        # Native bracket legs activate only after the complete entry fills.
        # Cancel the remaining entry, confirm terminal broker quantity, then
        # flatten only the verified partial fill instead of leaving it naked.
        _flatten_partial(request_id, parent, context=context, clock=clock)
        return
    legs = premarket.protective_legs(row,parent,context,clock,ensure=True)
    row = _row(request_id)
    if not isinstance(legs, list) or len(legs) != 2:
        _save(request_id, state='attention', reason='Protective bracket evidence is incomplete.')
        return
    if {leg.get('type') for leg in legs} != {'limit','stop'}:
        raise ValueError('Both take-profit and stop-loss evidence required.')
    for leg in legs:
        _uuid(leg.get('id'))
        if leg.get('symbol') != row['symbol'] or leg.get('side') != 'sell':
            raise ValueError('Foreign bracket leg.')
    exited = sum((_number(leg.get('filled_qty', '0')) for leg in legs), Decimal(0))
    if exited > filled:
        raise ValueError('Exit coverage exceeds the owned entry.')
    if exited == filled:
        if callable(context.record_exit):
            context.record_exit(row, [leg for leg in legs if _number(leg.get('filled_qty','0'))>0])
        _save(request_id, state='closed', updated_at=clock())
        return
    remaining = filled - exited
    positions = context.request('GET', '/v2/positions')
    own = [p for p in positions if p.get('symbol') == row['symbol']]
    if len(own) != 1 or _number(own[0].get('qty')) != remaining or remaining != remaining.to_integral_value():
        _save(request_id, state='attention', reason='Partial or mixed holdings require reconciliation; no additional order is sent.')
        return
    if any(leg.get('status') not in ('new','accepted','pending_new','held','partially_filled') or _number(leg.get('qty'))-_number(leg.get('filled_qty','0')) != remaining for leg in legs):
        _save(request_id, state='attention', reason='Protective order status requires reconciliation.')
        return
    try:
        market_session,_ = premarket.session(context,clock)
    except EntryBlocked:
        return
    protection_trigger=False
    if market_session=='premarket':
        bid=_number(premarket.fresh_quote(context,row['symbol'],clock)['bid'])
        stop=next(leg for leg in legs if leg['type']=='stop')
        profit=next(leg for leg in legs if leg['type']=='limit')
        protection_trigger=bid<=_number(stop['stop_price']) or bid>=_number(profit['limit_price'])
    candidate = context.scan(row['symbol']) if not protection_trigger else None
    scanner_sell = (isinstance(candidate,dict) and candidate.get('symbol')==row['symbol']
                    and candidate.get('scanner_stale') is False and candidate.get('signal')=='SELL'
                    and 0<=_number(candidate.get('score'))<=context.exit_score
                    and context.exit_confidence<=_number(candidate.get('confidence'))<=100)
    if not protection_trigger and not scanner_sell:
        return
    # Check exit feasibility while protection is still present. Only owned
    # bracket IDs may be ignored by this preflight, never arbitrary orders.
    owned_ids = {parent['id'], *(leg['id'] for leg in legs)}
    risk = premarket.risk(context,market_session,symbol=row['symbol'], shares=int(remaining), side='sell', owned_order_ids=owned_ids)
    if risk.get('approved') is not True:
        _save(request_id, reason='Scanner SELL observed; exit risk check blocked cancellation.')
        return
    if market_session=='premarket':
        premarket.dispatch_exit(row,int(remaining),owned_ids,context,clock)
    else:
        _dispatch_close(row, int(remaining), owned_ids, context=context, clock=clock)


def _dispatch_close(row, remaining, owned_ids, *, context, clock):
    request_id = row['request_id']
    context.validate()
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        changed = conn.execute('UPDATE pro_ticker_manual_scanner_entries SET state=?,exit_started_at=?,exit_client_order_id=?,exit_shares=? WHERE request_id=? AND exit_started_at IS NULL',
                                ('closing', clock(), 'ptm-exit-'+uuid.UUID(request_id).hex, int(remaining), request_id)).rowcount
        conn.commit()
    if not changed:
        return
    try:
        context.close(symbol=row['symbol'], shares=int(remaining),
                      client_order_id='ptm-exit-'+uuid.UUID(request_id).hex,
                      expected_total_shares=int(remaining), owned_order_ids=owned_ids,
                      expected_entry_order_id=row['broker_order_id'])
    except Exception:
        pass
    # Keep the closing marker even on a lost response. Only recovery may run.


def _flatten_partial(request_id, parent, *, context, clock):
    row = _row(request_id)
    _verify_order(parent,row)
    if not callable(context.cancel):
        raise ValueError('Partial-entry cancellation is unavailable.')
    owned_ids = {parent['id']}
    for leg in parent.get('legs') or []:
        _uuid(leg.get('id'))
        if leg.get('symbol') != row['symbol'] or leg.get('side') != 'sell':
            raise ValueError('Foreign partial-entry leg.')
        owned_ids.add(leg['id'])
    if parent.get('status') not in ('canceled','expired','rejected'):
        context.cancel(row['symbol'],owned_ids)
    # The entry may fill while cancellation is in flight. Never infer the
    # position quantity from the earlier partial snapshot.
    latest = context.request('GET','/v2/orders/'+parent['id'],params={'nested':'true'})
    _verify_order(latest,row)
    if latest.get('status') == 'filled':
        recover_entry(request_id,context=context,clock=clock)
        legs = latest.get('legs') or []
        if (len(legs) == 2 and {leg.get('type') for leg in legs} == {'limit','stop'}
                and all(leg.get('status') in ('new','accepted','pending_new','held')
                        and _number(leg.get('qty')) == _number(latest['filled_qty'])
                        and _number(leg.get('filled_qty','0')) == 0 for leg in legs)):
            return
        # Cancellation may win against newly activated protective children.
        # If they are no longer confirmed working, flatten the full verified
        # entry rather than retaining an unprotected position.
    if latest.get('status') not in ('filled','canceled','expired','rejected'):
        raise ValueError('Partial entry cancellation is not confirmed.')
    quantity = _number(latest['filled_qty'])
    if not 0 < quantity <= row['shares'] or quantity != quantity.to_integral_value():
        raise ValueError('Whole-share partial ownership is not confirmed.')
    if any(_number(leg.get('filled_qty','0')) != 0 for leg in latest.get('legs') or []):
        raise ValueError('Partial entry also has protective fills; reconcile first.')
    positions = context.request('GET','/v2/positions')
    own = [p for p in positions if p.get('symbol') == row['symbol']]
    if len(own) != 1 or _number(own[0]['qty']) != quantity:
        raise ValueError('Partial entry ownership differs from account holdings.')
    market = context.request('GET','/v2/clock')
    if market.get('is_open') is not True:
        raise ValueError('Partial entry cannot be flattened outside the regular session.')
    risk = context.risk(symbol=row['symbol'],shares=int(quantity),side='sell',owned_order_ids=owned_ids)
    if risk.get('approved') is not True:
        raise ValueError('Partial-entry safety exit is blocked by risk controls.')
    _save(request_id,order_json=json.dumps(latest,allow_nan=False),reason='Partial entry canceled; closing its verified fill for safety.')
    row = _row(request_id)
    if callable(context.record_entry):
        context.record_entry(row,latest)
    _dispatch_close(row,int(quantity),owned_ids,context=context,clock=clock)
