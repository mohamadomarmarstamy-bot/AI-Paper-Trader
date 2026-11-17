"""Repair PAPER learning records from complete, explicitly linked broker fills.

No broker writes. Ambiguous FIFO matches and incomplete lots remain review items.
Old recovered closes inform reports but never trigger the 25-new-trade learner.
"""
import asyncio
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
import math
import time
import database
from stock_performance import stamp

SCHEMA = '''CREATE TABLE IF NOT EXISTS stock_reconciliation_state(
 id INTEGER PRIMARY KEY CHECK(id=1), installed_at REAL NOT NULL, last_tick REAL,
 last_error TEXT, report_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS stock_reconciliation_audit(
 id INTEGER PRIMARY KEY, trade_book_id INTEGER NOT NULL, recorded_at REAL NOT NULL,
 before_json TEXT NOT NULL, after_json TEXT NOT NULL, evidence_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stock_broker_link_recovery_audit(
 id INTEGER PRIMARY KEY,entry_order_id TEXT NOT NULL,recorded_at REAL NOT NULL,
 before_json TEXT NOT NULL,evidence_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stock_learning_exclusions(
 trade_book_id INTEGER PRIMARY KEY, reason TEXT NOT NULL);'''


def initialize(conn, now):
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO stock_reconciliation_state(id,installed_at) VALUES(1,?)", (now,))
    conn.commit()


def number(value):
    if isinstance(value, bool):
        raise ValueError('Invalid numeric evidence')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Invalid numeric evidence')
    return result


def validate(trade, now):
    if trade.get('status') != 'CLOSED' or trade.get('pnl_complete') is not True:
        raise ValueError('incomplete_or_open')
    qty, price = number(trade['entry_shares']), number(trade['entry_price'])
    entered, exited = stamp(trade['entry_timestamp']), stamp(trade['exit_timestamp'])
    if qty <= 0 or price <= 0 or entered is None or exited is None or not 0 < entered <= exited <= now:
        raise ValueError('invalid_chronology_or_entry')
    allocations = trade.get('exit_allocations') or []
    if not allocations:
        raise ValueError('missing_exit_allocations')
    total = value = 0.
    for allocation in allocations:
        if allocation.get('match_method') not in ('broker_bracket_leg', 'trade_book_order_link'):
            raise ValueError('fifo_needs_review')
        shares, exit_price = number(allocation['shares']), number(allocation['price'])
        at = stamp(allocation['timestamp'])
        if not allocation.get('exit_order_id') or shares <= 0 or exit_price <= 0 or at is None or not entered <= at <= exited:
            raise ValueError('invalid_exit_allocation')
        total += shares
        value += shares * exit_price
    pnl = value - qty * price
    if not math.isclose(total, qty, rel_tol=1e-8, abs_tol=1e-8):
        raise ValueError('quantity_mismatch')
    if not math.isclose(number(trade['realized_profit_loss']), pnl, rel_tol=1e-5, abs_tol=.002):
        raise ValueError('pnl_mismatch')
    return qty, price, value / qty, pnl, pnl / (qty * price) * 100, exited - entered


def refresh_stale_links(payload, read_order, *, now=None):
    """Recover original nested bracket evidence for up to five stale entries.

    Uses an existing GET-only order reader. Never invents a bracket relation or
    overwrites conflicting fill amounts. Writes evidence and audit atomically.
    """
    now = time.time() if now is None else now
    result = {'checked': 0, 'refreshed': 0, 'unavailable': 0}
    if (payload.get('paper') is not True or payload.get('source') != 'broker_fills_trade_book_link_then_fifo' or
        payload.get('summary',{}).get('ledger_limit_reached') is not False):
        return result
    canonical = {t['entry_order_id']: t for t in payload['trades']}
    with closing(database.get_connection()) as conn:
        initialize(conn, now)
        entries = [dict(r) for r in conn.execute("SELECT * FROM trade_book WHERE status='OPEN' AND entry_reason='auto_trader_entry' ORDER BY id")
                   if r['entry_order_id'] in canonical and canonical[r['entry_order_id']]['status'] not in ('OPEN','PARTIAL')][:5]
    for entry in entries:
        result['checked'] += 1
        try:
            order = read_order(entry['entry_order_id'])
            if (order['id'] != entry['entry_order_id'] or order['symbol'] != entry['symbol'] or
                order['side'] != 'buy' or order['status'] != 'filled' or
                not str(order.get('client_order_id','')).startswith('auto-entry-') or
                not math.isclose(number(order['filled_qty']), entry['shares'], rel_tol=1e-8, abs_tol=1e-8) or
                not math.isclose(number(order['filled_avg_price']), entry['entry_price'], rel_tol=1e-6, abs_tol=1e-6)):
                raise ValueError('Original entry differs from saved record')
            entered = stamp(order['filled_at'])
            if entered is None or not math.isclose(entered,stamp(entry['entry_timestamp']),abs_tol=1):
                raise ValueError('Original entry timestamp differs')
            legs = []
            for leg in order.get('legs') or []:
                if leg.get('status') != 'filled':continue
                qty,price,at = number(leg['filled_qty']),number(leg['filled_avg_price']),stamp(leg['filled_at'])
                if (not leg.get('id') or leg['id']==order['id'] or leg.get('symbol')!=entry['symbol'] or
                    leg.get('side')!='sell' or qty<=0 or qty>entry['shares'] or price<=0 or at is None or not entered<=at<=now):
                    raise ValueError('Invalid original bracket leg')
                legs.append(leg)
            if not legs or sum(number(leg['filled_qty']) for leg in legs)>entry['shares']+1e-8:
                raise ValueError('Missing or conflicting original bracket quantities')
            with closing(database.get_connection()) as conn:
                conn.execute('BEGIN IMMEDIATE')
                parent = conn.execute('SELECT * FROM broker_fills WHERE order_id=?',(order['id'],)).fetchone()
                if (not parent or parent['symbol']!=entry['symbol'] or parent['side']!='BUY' or
                    not math.isclose(parent['shares'],entry['shares'],abs_tol=1e-8) or
                    not math.isclose(parent['price'],entry['entry_price'],rel_tol=1e-6,abs_tol=1e-6) or
                    not math.isclose(stamp(parent['filled_at']),entered,abs_tol=1)):
                    raise ValueError('Broker entry ledger conflict')
                before = [dict(parent)]
                for leg in legs:
                    saved = conn.execute('SELECT * FROM broker_fills WHERE order_id=?',(leg['id'],)).fetchone()
                    if saved and (saved['symbol']!=entry['symbol'] or saved['side']!='SELL' or
                        not math.isclose(saved['shares'],number(leg['filled_qty']),abs_tol=1e-8) or
                        not math.isclose(saved['price'],number(leg['filled_avg_price']),rel_tol=1e-6,abs_tol=1e-6) or
                        not math.isclose(stamp(saved['filled_at']),stamp(leg['filled_at']),abs_tol=1)):
                        raise ValueError('Broker exit ledger conflict')
                    before.append(dict(saved) if saved else {'order_id':leg['id'],'missing':True})
                iso = datetime.fromtimestamp(now,timezone.utc).isoformat()
                conn.execute('UPDATE broker_fills SET raw_order_json=?,last_seen_at=? WHERE order_id=?',
                             (json.dumps(order,allow_nan=False),iso,order['id']))
                for leg in legs:
                    conn.execute("""INSERT INTO broker_fills(order_id,symbol,side,shares,price,filled_at,source,raw_order_json,first_seen_at,last_seen_at)
                        VALUES(?,?,'SELL',?,?,?,'alpaca_paper',?,?,?) ON CONFLICT(order_id) DO NOTHING""",
                        (leg['id'],entry['symbol'],number(leg['filled_qty']),number(leg['filled_avg_price']),leg['filled_at'],json.dumps(leg,allow_nan=False),iso,iso))
                conn.execute('INSERT INTO stock_broker_link_recovery_audit(entry_order_id,recorded_at,before_json,evidence_json) VALUES(?,?,?,?)',
                             (order['id'],now,json.dumps(before),json.dumps(order,allow_nan=False)))
                conn.commit()
            result['refreshed'] += 1
        except Exception:
            result['unavailable'] += 1
    return result


def reconcile(payload, *, now=None, link_recovery=None):
    now = time.time() if now is None else now
    if payload.get('paper') is not True or payload.get('source') != 'broker_fills_trade_book_link_then_fifo':
        raise ValueError('Canonical PAPER ledger required')
    if payload.get('summary', {}).get('ledger_limit_reached') is not False:
        raise ValueError('Incomplete broker ledger; reconciliation deferred')
    trades = payload['trades']
    entries = Counter(t.get('entry_order_id') for t in trades)
    owners = {}
    for trade in trades:
        for allocation in trade.get('exit_allocations') or []:
            owners.setdefault(allocation.get('exit_order_id'), set()).add(trade.get('entry_order_id'))
    report = {'checked': len(trades), 'repaired': 0, 'imported': 0, 'unchanged': 0,
              'excluded_historical': 0, 'blocked': {}, 'examples': [], 'anomalies': []}
    if link_recovery is not None:report['link_recovery'] = link_recovery
    def block(trade, reason):
        report['blocked'][reason] = report['blocked'].get(reason, 0) + 1
        if len(report['examples']) < 20:
            report['examples'].append({'symbol': trade.get('symbol'), 'entry_order_id': trade.get('entry_order_id'), 'reason': reason})
    with closing(database.get_connection()) as conn:
        initialize(conn, now)
        conn.execute('BEGIN IMMEDIATE')
        cutoff = conn.execute('SELECT installed_at FROM stock_reconciliation_state WHERE id=1').fetchone()[0]
        for trade in trades:
            try:
                order = trade.get('entry_order_id')
                if not order or entries[order] != 1:
                    raise ValueError('duplicate_or_missing_entry_id')
                qty, price, exit_price, pnl, ret, held = validate(trade, now)
                if any(len(owners[a['exit_order_id']]) != 1 for a in trade['exit_allocations']):
                    raise ValueError('shared_exit_needs_review')
            except (ValueError, KeyError, TypeError, OverflowError) as error:
                reason = str(error) if isinstance(error, ValueError) else 'malformed_evidence'
                block(trade, reason)
                continue
            if abs(ret) >= 20 and len(report['anomalies']) < 20:
                report['anomalies'].append({'symbol': trade['symbol'], 'return_percent': ret,
                    'reason': 'Large price move: inspect raw broker fills and corporate actions; not proof of an accounting error.'})
            rows = conn.execute('SELECT * FROM trade_book WHERE entry_order_id=?', (order,)).fetchall()
            if len(rows) > 1:
                block(trade, 'duplicate_internal_entry');continue
            before = dict(rows[0]) if rows else None
            if before and (before['entry_reason'] != 'auto_trader_entry' or before['symbol'] != trade['symbol'] or
                           not math.isclose(before['shares'], qty, rel_tol=1e-8, abs_tol=1e-8) or
                           not math.isclose(before['entry_price'], price, rel_tol=1e-6, abs_tol=1e-6)):
                block(trade, 'internal_entry_conflict');continue
            iso = datetime.fromtimestamp(now, timezone.utc).isoformat()
            if not rows:
                cursor = conn.execute('''INSERT INTO trade_book(symbol,status,shares,entry_price,entry_timestamp,
                    entry_order_id,entry_reason,created_at,updated_at) VALUES(?,'OPEN',?,?,?,?, 'auto_trader_entry',?,?)''',
                    (trade['symbol'], qty, price, trade['entry_timestamp'], order, iso, iso))
                ident = cursor.lastrowid
            else:
                ident = before['id']
            outcome = conn.execute('SELECT * FROM learning_outcomes WHERE trade_book_id=?', (ident,)).fetchone()
            fields = {'status': 'CLOSED', 'exit_price': exit_price, 'exit_timestamp': trade['exit_timestamp'],
                      'realized_profit_loss': round(pnl, 4), 'realized_return_percent': round(ret, 6)}
            same_trade = before and all(before.get(k) == v if not isinstance(v, float) else
                before.get(k) is not None and math.isclose(before[k], v, rel_tol=1e-7, abs_tol=1e-6) for k, v in fields.items())
            same_outcome = outcome and all(outcome[k] is not None and math.isclose(outcome[k], v, rel_tol=1e-7, abs_tol=1e-6)
                for k, v in {'entry_price': price, 'exit_price': exit_price, 'shares': qty,
                             'realized_profit_loss': round(pnl, 4), 'realized_return_percent': round(ret, 6)}.items())
            if same_trade and same_outcome:
                report['unchanged'] += 1;continue
            reason = (before or {}).get('exit_reason') or 'broker_fill_reconciliation'
            # Preserve entry metadata and all prior learning features. Multiple exits
            # remain in audited evidence; do not invent one exit-order ownership link.
            conn.execute('''UPDATE trade_book SET status=?,exit_price=?,exit_timestamp=?,realized_profit_loss=?,
                realized_return_percent=?,exit_reason=?,updated_at=? WHERE id=?''', (*fields.values(), reason, iso, ident))
            values = (trade['symbol'], price, exit_price, qty, round(pnl, 4), round(ret, 6), reason, held, int(pnl > 0))
            if outcome:
                conn.execute('''UPDATE learning_outcomes SET symbol=?,entry_price=?,exit_price=?,shares=?,
                    realized_profit_loss=?,realized_return_percent=?,exit_reason=?,holding_seconds=?,won=? WHERE trade_book_id=?''', (*values, ident))
            else:
                conn.execute('''INSERT INTO learning_outcomes(symbol,entry_price,exit_price,shares,realized_profit_loss,
                    realized_return_percent,exit_reason,holding_seconds,won,trade_book_id,created_at,metadata_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (*values, ident, trade['exit_timestamp'],
                    json.dumps({'source': 'explicitly_linked_broker_fills', 'entry_features': 'unknown_unless_separately_captured'})))
            if stamp(trade['exit_timestamp']) <= cutoff:
                conn.execute("INSERT OR IGNORE INTO stock_learning_exclusions VALUES(?,'Historical accounting repair; not a new forward close')", (ident,))
                report['excluded_historical'] += 1
            after = dict(conn.execute('SELECT * FROM trade_book WHERE id=?', (ident,)).fetchone())
            conn.execute('INSERT INTO stock_reconciliation_audit(trade_book_id,recorded_at,before_json,after_json,evidence_json) VALUES(?,?,?,?,?)',
                         (ident, now, json.dumps({'trade': before, 'outcome': dict(outcome) if outcome else None}),
                          json.dumps(after), json.dumps(trade, allow_nan=False)))
            report['repaired' if before else 'imported'] += 1
        conn.execute('UPDATE stock_reconciliation_state SET last_tick=?,last_error=NULL,report_json=? WHERE id=1',
                     (now, json.dumps(report, allow_nan=False)))
        conn.commit()
    return report


def status(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='stock_reconciliation_state'").fetchone():
        return {'state': 'waiting_for_first_reconciliation'}
    row = conn.execute('SELECT * FROM stock_reconciliation_state WHERE id=1').fetchone()
    if not row:return {'state': 'waiting_for_first_reconciliation'}
    totals = conn.execute("SELECT COUNT(*) AS updates, SUM(json_extract(before_json,'$.trade') IS NULL) AS imports FROM stock_reconciliation_audit").fetchone()
    result = dict(row)
    result['report'] = json.loads(result.pop('report_json'))
    result.update(total_updates=totals['updates'], total_imports=totals['imports'] or 0,
        basis='Only complete explicitly linked PAPER closes are repaired. FIFO-only, conflicting and incomplete evidence stays unresolved. Historical repairs never count as new 25-trade learning samples.')
    return result


async def worker(read_canonical, read_order=None):
    while True:
        try:
            payload = await asyncio.to_thread(read_canonical)
            recovered = None
            if read_order is not None:
                recovered = await asyncio.to_thread(refresh_stale_links, payload, read_order)
                if recovered['refreshed']:
                    payload = await asyncio.to_thread(read_canonical)
            await asyncio.to_thread(reconcile, payload, link_recovery=recovered)
        except Exception:
            try:
                with closing(database.get_connection()) as conn:
                    initialize(conn, time.time())
                    conn.execute("UPDATE stock_reconciliation_state SET last_error='Broker reconciliation failed; records left unchanged and will retry' WHERE id=1")
                    conn.commit()
            except Exception:
                pass  # Retry after a transient database outage as well.
        await asyncio.sleep(300)
