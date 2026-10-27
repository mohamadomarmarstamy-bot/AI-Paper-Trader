"""Read-only execution views. FIFO matching is display-only, never trade accounting."""
from collections import defaultdict, deque
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone


def positive(value):
    if isinstance(value, bool):
        return None
    try:
        n = Decimal(str(value))
        return n if n.is_finite() and n > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def execution_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def execution_rows(persisted, orders):
    """Merge by broker order ID; cumulative partial fills are one execution row."""
    rows = {}
    def add(item, broker=False):
        if not isinstance(item, dict):
            return
        key = str(item.get('id' if broker else 'order_id') or '').strip()
        side = str(item.get('side') or '').upper()
        qty = positive(item.get('filled_qty' if broker else 'shares'))
        price = positive(item.get('filled_avg_price' if broker else 'price'))
        timestamp = item.get('filled_at') or (item.get('updated_at') if broker else None)
        when = execution_time(timestamp)
        symbol = str(item.get('symbol') or '').strip().upper()
        if not key or side not in {'BUY', 'SELL'} or not symbol or qty is None or price is None or when is None:
            return
        old = rows.get(key)
        # A late/older broker snapshot must not shrink already persisted cumulative fills.
        if old and Decimal(str(old['shares'])) > qty:
            return
        rows[key] = dict(order_id=key, symbol=symbol, side=side, shares=float(qty),
                         price=float(price), total=float(qty * price), filled_at=when.isoformat(),
                         timestamp=when.timestamp(), status=str(item.get('status') or 'filled'),
                         source='alpaca_paper_order' if broker else 'saved_broker_fill')
    for item in persisted:
        add(item)
    def visit(item):
        if not isinstance(item, dict):
            return
        add(item, True)
        for leg in item.get('legs') or []:
            visit(leg)
    for item in orders:
        visit(item)
    return sorted(rows.values(), key=lambda r: (r['timestamp'], r['side'] == 'SELL', r['order_id']))


def build_activity(persisted, orders, *, broker_available=True, ledger_limit=10000):
    fills = execution_rows(persisted, orders)
    lots, complete, unmatched = defaultdict(deque), [], []
    for fill in fills:
        qty, price = Decimal(str(fill['shares'])), Decimal(str(fill['price']))
        if fill['side'] == 'BUY':
            lots[fill['symbol']].append(dict(fill, remaining=qty, sold=Decimal(0), proceeds=Decimal(0), exits=[]))
            continue
        while qty > 0 and lots[fill['symbol']]:
            lot = lots[fill['symbol']][0]
            used = min(qty, lot['remaining'])
            lot['remaining'] -= used
            lot['sold'] += used
            lot['proceeds'] += used * price
            lot['exits'].append(dict(order_id=fill['order_id'], shares=float(used), price=fill['price'], filled_at=fill['filled_at']))
            qty -= used
            if lot['remaining'] == 0:
                lots[fill['symbol']].popleft()
                cost = lot['sold'] * Decimal(str(lot['price']))
                complete.append(dict(symbol=lot['symbol'], entry_order_id=lot['order_id'],
                    shares=float(lot['sold']), buy_price=lot['price'], sell_price=float(lot['proceeds']/lot['sold']),
                    buy_total=float(cost), sell_total=float(lot['proceeds']),
                    profit_loss=float(lot['proceeds']-cost), bought_at=lot['filled_at'],
                    sold_at=fill['filled_at'], exits=lot['exits']))
        if qty > 0:
            unmatched.append(dict(order_id=fill['order_id'], symbol=fill['symbol'], shares=float(qty)))
    grouped = {}
    for lot in complete:
        group = grouped.setdefault(lot['symbol'], dict(symbol=lot['symbol'], shares=0, buy_total=0, sell_total=0, profit_loss=0, trades=[]))
        for key in ('shares', 'buy_total', 'sell_total', 'profit_loss'):
            group[key] += lot[key]
        group['trades'].append(lot)
    for group in grouped.values():
        group['buy_price'] = group['buy_total'] / group['shares']
        group['sell_price'] = group['sell_total'] / group['shares']
        group['trades'].sort(key=lambda r: r['sold_at'], reverse=True)
    return dict(paper=True, read_only=True, source='recent_and_saved_broker_fills',
        broker_available=broker_available, ledger_limit_reached=len(persisted) >= ledger_limit,
        generated_at=datetime.now(timezone.utc).timestamp(),
        buys=[r for r in reversed(fills) if r['side']=='BUY'],
        sells=[r for r in reversed(fills) if r['side']=='SELL'],
        completed=sorted(grouped.values(), key=lambda r: r['trades'][0]['sold_at'], reverse=True),
        unmatched_sells=unmatched, matching='FIFO display of fully sold buy lots; not tax-lot or bot-journal accounting')
