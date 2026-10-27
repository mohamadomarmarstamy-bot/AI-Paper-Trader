"""Calendar-verified manual PAPER premarket limits and regular-hours protection.

No extended market/bracket orders, delayed quote fallback, or POST replay.
Premarket stops are app-managed; the OCO itself executes in regular hours.
"""
from contextlib import closing
from datetime import datetime, time as daytime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import json
from zoneinfo import ZoneInfo

import database

EASTERN = ZoneInfo('America/New_York')
COLUMNS = {
    'entry_session': "TEXT NOT NULL DEFAULT 'regular'",
    'entry_limit': 'TEXT', 'entry_expires_at': 'REAL',
    'stop_loss_percent': 'TEXT', 'take_profit_percent': 'TEXT',
    'stop_price': 'TEXT', 'take_profit_price': 'TEXT',
    'protection_started_at': 'REAL', 'protection_client_order_id': 'TEXT',
    'protection_order_id': 'TEXT', 'exit_order_type': "TEXT NOT NULL DEFAULT 'market'",
    'exit_limit': 'TEXT',
}


def migrate(conn):
    existing = {row[1] for row in conn.execute('PRAGMA table_info(pro_ticker_manual_scanner_entries)')}
    for name, declaration in COLUMNS.items():
        if name not in existing:
            try:
                conn.execute('ALTER TABLE pro_ticker_manual_scanner_entries ADD COLUMN '+name+' '+declaration)
            except database.sqlite3.OperationalError as error:
                if 'duplicate column name' not in str(error).lower():
                    raise
    conn.commit()


def session(context, clock, *, entry=False):
    from pro_ticker_manual_scanner import _stamp, EntryBlocked
    market = context.request('GET','/v2/clock')
    if market.get('is_open') is True:
        if entry and _stamp(market['next_close'])-clock() < 120:
            raise EntryBlocked('Too close to market close for a new managed entry.')
        return 'regular', market
    if context.premarket_enabled() is not True:
        raise EntryBlocked('Regular market is closed. No buy is queued; submit a new review after opening.')
    now = datetime.fromtimestamp(clock(),EASTERN)
    if now.weekday() >= 5 or not daytime(4) <= now.time() < daytime(9,30):
        raise EntryBlocked('Outside premarket and regular trading hours. No buy is queued.')
    if not 0 <= clock()-_stamp(market['timestamp']) <= 30:
        raise EntryBlocked('A fresh broker market clock is required.')
    day = now.date().isoformat()
    calendar = context.request('GET','/v2/calendar',params={'start':day,'end':day})
    if not isinstance(calendar,list) or len(calendar)!=1 or calendar[0].get('date')!=day:
        raise EntryBlocked('No verified trading session today. No buy is queued.')
    opening = datetime.fromisoformat(day+'T'+calendar[0]['open']).replace(tzinfo=EASTERN)
    if opening.time()!=daytime(9,30) or _stamp(market['next_open'])!=opening.timestamp():
        raise EntryBlocked('Broker calendar and next opening do not agree.')
    if not now < opening or entry and opening.timestamp()-clock() < 75:
        raise EntryBlocked('Too close to regular opening for a new premarket entry.')
    return 'premarket',market


def tick_price(price, *, buy=False):
    from pro_ticker_manual_scanner import _number
    price = _number(price)
    if price <= 0:
        raise ValueError('Positive limit price required.')
    return price.quantize(Decimal('.01') if price>=1 else Decimal('.0001'),
                          rounding=ROUND_UP if buy else ROUND_DOWN)


def risk(context, market_session, **kwargs):
    if market_session=='premarket':
        kwargs['manual_premarket']=True
    return context.risk(**kwargs)


def fresh_quote(context, symbol, clock):
    from pro_ticker_manual_scanner import _number,_stamp,EntryBlocked
    quote = context.quote(symbol)
    if (not isinstance(quote,dict) or quote.get('symbol')!=symbol
            or not 0 <= clock()-_stamp(quote['timestamp']) <= 30
            or not 0 < _number(quote['bid']) <= _number(quote['ask'])):
        raise EntryBlocked('A fresh bid/ask quote is required; the configured feed may not cover this premarket time.')
    return quote


def submit_limit(row, context, clock):
    from pro_ticker_manual_scanner import _account,assert_dispatch,_number,EntryBlocked
    context.validate()
    if context.premarket_enabled() is not True or session(context,clock,entry=True)[0]!='premarket':
        raise EntryBlocked('Premarket entry window changed; no buy was sent.')
    if clock()>=row['entry_expires_at'] or _account(context)!=row['account_fingerprint']:
        raise EntryBlocked('Premarket entry checks expired or account changed.')
    quote=fresh_quote(context,row['symbol'],clock)
    if tick_price(quote['ask'],buy=True)>_number(row['entry_limit']):
        raise EntryBlocked('Ask moved above the saved limit. No buy was sent.')
    result=risk(context,'premarket',symbol=row['symbol'],shares=row['shares'],side='buy')
    if result.get('approved') is not True:
        raise EntryBlocked('Premarket risk checks blocked dispatch.')
    context.validate()
    if (session(context,clock,entry=True)[0]!='premarket' or clock()>=row['entry_expires_at']
            or _account(context)!=row['account_fingerprint']
            or tick_price(fresh_quote(context,row['symbol'],clock)['ask'],buy=True)>_number(row['entry_limit'])):
        raise EntryBlocked('Premarket dispatch checks changed; no buy was sent.')
    # The durable submitting marker already exists. One broker POST only.
    client=assert_dispatch(row['request_id'],row['symbol'],row['shares'])
    return context.request('POST','/v2/orders',json_body={
        'symbol':row['symbol'],'qty':str(row['shares']),'side':'buy','type':'limit',
        'time_in_force':'day','limit_price':row['entry_limit'],'extended_hours':True,
        'client_order_id':client})


def settle_entry(row, parent, context, clock):
    """Expire pending entry exposure; cancellation is confirmed before management."""
    from pro_ticker_manual_scanner import _verify_order,_number,_save,_row
    terminal={'filled','canceled','expired','rejected'}
    if parent.get('status') not in terminal:
        if _number(parent['filled_qty'])==0 and clock()<row['entry_expires_at']:
            return None
        # Cancel partial fills immediately or unfilled entries after 60 seconds.
        # DELETE is idempotent; no replacement buy is ever posted.
        context.request('DELETE','/v2/orders/'+parent['id'])
        parent=context.request('GET','/v2/orders/'+parent['id'],params={'nested':'true'})
        _verify_order(parent,row)
        if parent.get('status') not in terminal:
            raise ValueError('Premarket entry cancellation is not confirmed.')
    _save(row['request_id'],order_json=json.dumps(parent,allow_nan=False))
    if _number(parent['filled_qty'])==0:
        _save(row['request_id'],state='closed',reason='Premarket limit expired or was canceled without a fill.')
        return None
    quantity=_number(parent['filled_qty'])
    if quantity!=quantity.to_integral_value():
        raise ValueError('Fractional partial ownership requires review.')
    _save(row['request_id'],state='open')
    if callable(context.record_entry):
        context.record_entry(_row(row['request_id']),parent)
    return parent


def protective_legs(row, parent, context, clock, *, ensure=False):
    """Durably attach/recover a native RTH OCO for a premarket-origin entry."""
    from pro_ticker_manual_scanner import _number,_uuid,_save,_row,_account,_owned_quantity
    if row.get('entry_session')!='premarket':
        return parent.get('legs') or []
    quantity=_owned_quantity(row)
    if row.get('protection_started_at') is None:
        if not ensure:
            return []
        context.validate()
        if _account(context)!=row['account_fingerprint']:
            raise ValueError('PAPER account changed before protection.')
        positions=context.request('GET','/v2/positions')
        owned=[p for p in positions if p.get('symbol')==row['symbol']]
        if len(owned)!=1 or _number(owned[0]['qty'])!=quantity:
            raise ValueError('Protection ownership differs from broker holdings.')
        orders=context.request('GET','/v2/orders',params={'status':'open','symbols':row['symbol'],'nested':'true'})
        if not isinstance(orders,list) or orders:
            raise ValueError('Existing orders prevent new protection.')
        price=_number(parent['filled_avg_price'])
        stop=tick_price(price*(1-_number(row['stop_loss_percent'])/100))
        target=tick_price(price*(1+_number(row['take_profit_percent'])/100),buy=True)
        if not 0<stop<price<target:
            raise ValueError('Protective price increments are unsupported.')
        client='ptm-protect-'+row['request_id'].replace('-','')
        with closing(database.get_connection()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            changed=conn.execute('''UPDATE pro_ticker_manual_scanner_entries
                SET protection_started_at=?,protection_client_order_id=?,stop_price=?,take_profit_price=?
                WHERE request_id=? AND protection_started_at IS NULL''',
                (clock(),client,str(stop),str(target),row['request_id'])).rowcount
            conn.commit()
        if changed:
            context.validate()
            if _account(context)!=row['account_fingerprint']:
                raise ValueError('PAPER account changed before protective dispatch.')
            try:
                context.request('POST','/v2/orders',json_body={
                    'symbol':row['symbol'],'qty':str(quantity),'side':'sell','type':'limit',
                    'time_in_force':'gtc','order_class':'oco',
                    'take_profit':{'limit_price':str(target)},'stop_loss':{'stop_price':str(stop)},
                    'client_order_id':client})
            except Exception:
                pass
        row=_row(row['request_id'])
    order=context.request('GET','/v2/orders:by_client_order_id',
                          params={'client_order_id':row['protection_client_order_id']})
    _uuid(order.get('id'))
    order=context.request('GET','/v2/orders/'+order['id'],params={'nested':'true'})
    if (order.get('client_order_id')!=row['protection_client_order_id']
            or order.get('symbol')!=row['symbol'] or order.get('side')!='sell'
            or order.get('order_class')!='oco' or order.get('type')!='limit'
            or order.get('time_in_force')!='gtc' or order.get('extended_hours',False) is not False
            or _number(order['qty'])!=quantity or _number(order['limit_price'])!=_number(row['take_profit_price'])):
        raise ValueError('Owned OCO identity or prices differ.')
    _uuid(order.get('id'))
    if row.get('protection_order_id') and row['protection_order_id']!=order['id']:
        raise ValueError('Linked protective OCO changed.')
    legs=[order,*(order.get('legs') or [])]
    if len(legs)!=2 or {leg.get('type') for leg in legs}!={'limit','stop'}:
        raise ValueError('Complete OCO stop and profit evidence required.')
    for leg in legs:
        _uuid(leg.get('id'))
        if leg.get('symbol')!=row['symbol'] or leg.get('side')!='sell':
            raise ValueError('Foreign protective leg.')
        if leg['type']=='stop' and _number(leg['stop_price'])!=_number(row['stop_price']):
            raise ValueError('Protective stop price changed.')
    _save(row['request_id'],protection_order_id=order['id'])
    book=database.load_trade_book_entry_by_order_id(row['broker_order_id']) if callable(context.record_entry) else None
    if book:
        for leg in legs:
            database.create_trade_book_order_link(trade_book_id=book['id'],order_id=leg['id'],
                client_order_id=leg.get('client_order_id'),order_role='EXIT',
                created_at=datetime.fromtimestamp(clock(),timezone.utc).isoformat())
    return legs


def dispatch_exit(row, remaining, owned_ids, context, clock):
    """One extended-hours DAY limit exit, bounded by a fresh bid."""
    from pro_ticker_manual_scanner import _number,_account,_save
    context.validate()
    if session(context,clock)[0]!='premarket' or _account(context)!=row['account_fingerprint']:
        raise ValueError('Premarket exit session/account changed.')
    quote=fresh_quote(context,row['symbol'],clock)
    limit=tick_price(_number(quote['bid'])*Decimal('.999'))
    if limit<=0:
        raise ValueError('Supported exit limit unavailable.')
    if risk(context,'premarket',symbol=row['symbol'],shares=remaining,side='sell',owned_order_ids=owned_ids).get('approved') is not True:
        raise ValueError('Premarket exit risk check failed.')
    client='ptm-exit-'+row['request_id'].replace('-','')
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        changed=conn.execute('''UPDATE pro_ticker_manual_scanner_entries SET state='closing',
            exit_started_at=?,exit_client_order_id=?,exit_shares=?,exit_order_type='limit',exit_limit=?
            WHERE request_id=? AND exit_started_at IS NULL''',
            (clock(),client,remaining,str(limit),row['request_id'])).rowcount
        conn.commit()
    if not changed:
        return
    # Marker survives cancellation failures/lost ACK. Never repeat the sell POST.
    if not callable(context.cancel):
        raise ValueError('Owned protective cancellation unavailable.')
    context.cancel(row['symbol'],owned_ids)
    orders=context.request('GET','/v2/orders',params={'status':'open','symbols':row['symbol'],'nested':'true'})
    positions=context.request('GET','/v2/positions')
    own=[p for p in positions if p.get('symbol')==row['symbol']]
    if not isinstance(orders,list) or orders or len(own)!=1 or _number(own[0]['qty'])!=remaining:
        raise ValueError('Exit ownership/cancellation changed.')
    context.validate()
    if session(context,clock)[0]!='premarket' or _account(context)!=row['account_fingerprint']:
        raise ValueError('Exit session/account changed after cancellation.')
    fresh_quote(context,row['symbol'],clock)
    context.request('POST','/v2/orders',json_body={
        'symbol':row['symbol'],'qty':str(remaining),'side':'sell','type':'limit',
        'time_in_force':'day','limit_price':str(limit),'extended_hours':True,'client_order_id':client})
