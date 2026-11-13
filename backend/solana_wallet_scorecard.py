"""Receipt-based wallet evidence. Reporting only; never changes eligibility."""
from collections import defaultdict
from datetime import datetime
import json
import math
from zoneinfo import ZoneInfo

MIN_COMPLETED = 20
MIN_ACTIVE_DATES = 7
EASTERN = ZoneInfo('America/New_York')
UNATTRIBUTED = '__unattributed__'


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def integer(value):
    return type(value) is int


def stamp(order):
    from solana_copy_gap import receipt_time
    return receipt_time(order)[0]


def dated(value):
    if not number(value) or value <= 0: return False
    try: datetime.fromtimestamp(value, EASTERN)
    except (ValueError, OverflowError, OSError): return False
    return True


def series(events, completed, *, amount_complete=True):
    """Realized drawdown starts at zero. Group equal timestamps for cash P/L.

    Missing dates never receive a made-up chronological order. A mixed-sign
    tie between closed positions has no reliable loss-streak ordering.
    """
    timed = defaultdict(list); unknown_times = 0
    for ident, at, pnl in events:
        if not dated(at): unknown_times += 1
        else: timed[at].append(pnl)
    cumulative = peak = drawdown = 0
    for at, values in sorted(timed.items()):
        cumulative += sum(values); peak = max(peak, cumulative)
        drawdown = max(drawdown, peak-cumulative)
    closed_by_time = defaultdict(list)
    for item in completed:
        if dated(item['at']): closed_by_time[item['at']].append(item['pnl'])
    ordered = len(completed) == sum(map(len, closed_by_time.values()))
    ambiguous = any(any(p < 0 for p in values) and any(p >= 0 for p in values) for values in closed_by_time.values())
    longest = current = 0
    for at, values in sorted(closed_by_time.items()):
        for pnl in values:
            current = current+1 if pnl < 0 else 0; longest = max(longest, current)
    dates = {datetime.fromtimestamp(at, EASTERN).date().isoformat() for at in closed_by_time}
    profits = [p['pnl'] for p in completed]
    wins = sum(p > 0 for p in profits); losses = -sum(p for p in profits if p < 0); gains = sum(p for p in profits if p > 0)
    return {'completed_positions': len(completed), 'winning_positions': wins,
            'win_rate_percent': 100*wins/len(completed) if completed else None,
            'profit_factor': gains/losses if losses else None,
            'no_completed_losses': bool(completed) and losses == 0,
            'max_realized_drawdown': drawdown if events and amount_complete and not unknown_times else None,
            'max_completed_loss_streak': longest if completed and amount_complete and ordered and not ambiguous else None,
            'active_close_dates': len(dates), 'first_close_date': min(dates) if dates else None,
            'last_close_date': max(dates) if dates else None, 'unknown_event_times': unknown_times,
            'undated_completed_positions': len(completed)-sum(map(len, closed_by_time.values())),
            'ambiguous_streak_order': ambiguous}


def real_metrics(wallet, orders, positions, adjustments, summary):
    rows = [o for o in orders if (o.get('source_wallet') or UNATTRIBUTED) == wallet]
    by_id = {o['id']: o for o in rows}; rows = list(by_id.values())
    buys = [o for o in rows if o.get('side') == 'buy' and o.get('status') == 'confirmed']
    sales = [o for o in rows if o.get('side') == 'sell' and o.get('status') == 'confirmed']
    failed = [o for o in rows if o.get('status') == 'failed']
    repairs = [a for a in adjustments if a['source_wallet'] == wallet]
    events = []; complete = True
    for order in sales:
        pnl = order.get('realized_pnl_lamports')
        if integer(pnl): events.append((order['id'], stamp(order), pnl))
        else: complete = False
    for order in failed:
        fee = (order.get('result') or {}).get('fee_lamports')
        if integer(fee) and fee >= 0: events.append((order['id'], stamp(order), -fee))
        else: complete = False
    for repair in repairs:
        # Audit-validated repair amounts belong in net P/L. The existing audit
        # does not expose settlement time; never use review time as trade time.
        events.append(('repair:'+repair['repair_id'], None, repair['pnl_lamports']))
    completed = []
    for buy in buys:
        receipt = buy.get('result') or {}; quantity = receipt.get('token_delta'); cost = receipt.get('native_delta')
        exits = [o for o in sales if o.get('position_id') == buy['id'] and o.get('wallet') == buy.get('wallet') and o.get('mint') == buy.get('mint')]
        repaired = [r for r in repairs if r['position_id'] == buy['id']]
        amounts = [(o.get('result') or {}).get('token_delta') for o in exits]+[-r['quantity_raw'] for r in repaired]
        values = [o.get('realized_pnl_lamports') for o in exits]+[r['pnl_lamports'] for r in repaired]
        costs = [(o.get('result') or {}).get('fee_lamports') for o in failed if o.get('position_id') == buy['id']]
        held = any(p.get('id', ident) == buy['id'] for ident, p in positions.items())
        if (not held and integer(quantity) and quantity > 0 and integer(cost) and cost < 0 and amounts
                and all(integer(q) and q < 0 for q in amounts) and -sum(amounts) == quantity
                and all(integer(p) for p in values) and all(integer(f) and f >= 0 for f in costs)):
            times = [stamp(o) for o in exits]
            at = max(times) if times and all(number(t) and t > 0 for t in times) and not repaired else None
            completed.append({'id': buy['id'], 'pnl': sum(values)-sum(costs), 'at': at})
    settled = [o for o in rows if o.get('status') not in ('preparing', 'signed', 'submitted', 'unknown')]
    held = [p for p in positions.values() if p.get('source_wallet') == wallet]
    observed = bool(rows or repairs or held)
    metrics = series(events, completed, amount_complete=complete)
    metrics.update(net_realized=summary.get('net_realized_lamports') if observed else None,
                   known_allocated_net=sum(p for _, _, p in events) if observed else None,
                   accounting_complete=complete and summary.get('net_realized_lamports') is not None if observed else True,
                   confirmed_buys=len(buys), confirmed_sales=len(sales), open_positions=len(held),
                   unscored_buys=max(0, len(buys)-len(completed)-len(held)),
                   paid_failed_fees=summary.get('failed_fees_lamports', 0), unknown_fee_records=summary.get('unknown_fee_records', 0),
                   unknown_sale_allocations=summary.get('unallocated_sales', 0), manual_adjustments=len(repairs),
                   manual_external_sales=sum(o.get('reason') == 'manual_external' for o in sales),
                   buy_attempts=sum(o.get('side') == 'buy' for o in settled),
                   sell_attempts=sum(o.get('side') == 'sell' for o in settled),
                   failed_buys=sum(o.get('side') == 'buy' and o.get('status') == 'failed' for o in rows),
                   failed_sells=sum(o.get('side') == 'sell' and o.get('status') == 'failed' for o in rows),
                   rejected_buys=sum(o.get('side') == 'buy' and o.get('status') == 'rejected' for o in rows),
                   rejected_sells=sum(o.get('side') == 'sell' and o.get('status') == 'rejected' for o in rows),
                   pending_orders=len(rows)-len(settled), entry_premium=summary.get('entry_premium', {'count': 0, 'median': None, 'p90': None}))
    return metrics


def paper_metrics(wallet, closes, positions):
    rows = [r for r in closes if r.get('wallet') == wallet and not r.get('voided')]
    held = [p for p in positions.values() if p.get('wallet') == wallet]
    lots = defaultdict(list); events = []; complete = True
    for row in rows:
        lots[row.get('lot_id') or row['id']].append(row)
        if number(row.get('pnl_usd')): events.append((row['id'], row.get('closed_at'), row['pnl_usd']))
        else: complete = False
    completed = []
    for ident, exits in lots.items():
        if any(p.get('id', key) == ident for key, p in positions.items()): continue
        final = [r for r in exits if not r.get('partial_exit', False)]
        if final and all(number(r.get('pnl_usd')) for r in exits):
            dates = [r.get('closed_at') for r in exits]
            at = max(dates) if all(number(t) and t > 0 for t in dates) else None
            completed.append({'id': ident, 'pnl': sum(r['pnl_usd'] for r in exits), 'at': at})
    metrics = series(events, completed, amount_complete=complete)
    metrics.update(net_realized=sum(p for _, _, p in events) if complete and (rows or held) else None,
                   accounting_complete=complete, open_positions=len(held), exit_records=len(rows), basis='Modeled USD; all stored original paper history')
    return metrics


def simulated_metrics(wallet, rows):
    rows = [r for r in rows if r.get('source_wallet') == wallet]
    events = []; completed = []; complete = True
    for row in rows:
        values = [e.get('pnl_lamports') for e in row.get('exits', [])]
        valid = integer(row.get('realized_lamports')) and all(integer(v) for v in values) and sum(values) == row['realized_lamports']
        if not valid: complete = False; continue
        events.extend((f"{row['id']}:{i}", e.get('quote_at'), e['pnl_lamports']) for i, e in enumerate(row.get('exits', [])))
        if row.get('status') == 'closed' and values:
            completed.append({'id': row['id'], 'pnl': row['realized_lamports'], 'at': row.get('closed_at')})
    metrics = series(events, completed, amount_complete=complete)
    metrics.update(net_realized=sum(r['realized_lamports'] for r in rows) if complete and rows else None,
                   accounting_complete=complete, open_positions=sum(r.get('status') == 'open' for r in rows),
                   signals=len(rows), skipped=sum(r.get('status') == 'skipped' for r in rows),
                   queued=sum(r.get('status') == 'queued' for r in rows), basis='Minimum-output quote simulation; forward history only')
    return metrics


def read_context(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return [json.loads(r[0]) for r in conn.execute('SELECT details_json FROM solana_copy_closes')] if 'solana_copy_closes' in tables else []


def build(traders, orders, runtime, paper_closes, paper_positions, shadow_rows, shadow_started_at, base):
    from solana_live_source_policy import paper_only
    owner = base.get('wallet') or ''
    orders = [o for o in orders if o.get('wallet') == owner]
    summaries = {w['wallet']: w for w in base['wallets']}
    traders_by_id = {t['wallet']: t for t in traders}
    wallets = (set(traders_by_id) | set(summaries) | {r.get('wallet') for r in paper_closes}
               | {p.get('wallet') for p in paper_positions.values()} | {r.get('source_wallet') for r in shadow_rows})-{None, ''}
    rows = []
    for wallet in sorted(wallets):
        trader = traders_by_id.get(wallet, {}); summary = summaries.get(wallet, {})
        actual = real_metrics(wallet, orders, runtime.get('positions', {}), base.get('accounting_adjustments', []), summary)
        original = paper_metrics(wallet, paper_closes, paper_positions)
        simulated = simulated_metrics(wallet, shadow_rows)
        only_paper = wallet != UNATTRIBUTED and paper_only(wallet, traders)
        enough = actual['completed_positions'] >= MIN_COMPLETED and actual['active_close_dates'] >= MIN_ACTIVE_DATES
        if not actual['accounting_complete']: label = 'Accounting incomplete'; priority = 3
        elif actual['confirmed_buys'] and not base.get('accounting_complete'): label = 'Reconciliation pending'; priority = 3
        elif only_paper and not actual['confirmed_buys']: label = 'Paper only'; priority = 4
        elif not enough: label = 'Insufficient data'; priority = 2
        elif actual['manual_external_sales'] or actual['manual_adjustments']: label = 'Manual exits included'; priority = 2
        else:
            value = actual['net_realized']
            label = 'Observed positive net' if value > 0 else 'Observed negative net' if value < 0 else 'Observed break-even'
            priority = 0 if value > 0 else 1
        rows.append({'wallet': wallet, 'name': trader.get('name') or summary.get('name') or ('Unattributed' if wallet == UNATTRIBUTED else wallet),
                     'active': trader.get('active', True) if trader else False, 'paper_only': only_paper,
                     'evidence': label, 'enough_history': enough, 'sort_priority': priority,
                     'actual': actual, 'realistic': simulated, 'paper': original})
    rows.sort(key=lambda r: (r['sort_priority'], -(r['actual']['net_realized'] or 0), r['name']))
    return {'wallets': rows, 'owner': owner or None, 'automatic_promotion': False,
            'minimum_completed_positions': MIN_COMPLETED, 'minimum_active_close_dates': MIN_ACTIVE_DATES,
            'shadow_started_at': shadow_started_at,
            'basis': 'Evidence labels are reporting gates, not a profitability forecast or a 0–100 score. At least 20 fully closed actual positions across seven distinct Eastern close dates are required for an observed-net label; this is a conservative display rule, not statistical proof. Actual and original paper use their full stored histories; realistic results start with the forward comparison. Different sizes, rules and currencies prevent direct profit comparisons. Partial exits affect realized P/L but are not separate completed wins. Failed transaction fees are included once; rejected orders have no assumed fee. Drawdown covers recorded realized P/L only, excludes open holdings, and groups equal settlement timestamps. Missing dates or costs leave risk metrics unavailable. Mixed-sign completion-time ties leave loss streaks unavailable. Archived manual repairs remain in net P/L but have no assumed settlement dates. No wallet selection, eligibility, sizing or trading setting changes automatically.'}
