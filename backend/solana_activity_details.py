"""Read-only activity attribution and realized sale slices from stored receipts."""
import json


def enrich_orders(conn, orders, state):
    result = [dict(row) for row in orders]
    ids = {row.get('position_id') for row in result if row.get('position_id')}
    if not ids:
        return result
    placeholders = ','.join('?' for _ in ids)
    buys = {row['id']: row for row in (json.loads(r[0]) for r in conn.execute(
        f'SELECT details_json FROM solana_real_orders WHERE id IN ({placeholders})', tuple(ids)))}
    history = [json.loads(r[0]) for r in conn.execute(
        f"SELECT details_json FROM solana_real_orders WHERE status='confirmed' AND json_extract(details_json,'$.position_id') IN ({placeholders}) ORDER BY created_at,rowid", tuple(ids))]
    affected = {r.get('position_id') for r in state.get('balance_adjustments', []) + state.get('accounting_reviews', [])}
    reviews = {r.get('order_id'): r for r in state.get('accounting_reviews', [])}
    amounts = {}
    for ident, buy in buys.items():
        receipt = buy.get('result') or {}
        quantity, delta = receipt.get('token_delta'), receipt.get('native_delta')
        if buy.get('status') == 'confirmed' and type(quantity) is int and quantity > 0 and type(delta) is int and delta < 0:
            amounts[ident] = [quantity, -delta]
    profits = {}
    for row in history:
        ident = row.get('position_id'); buy = buys.get(ident)
        receipt = row.get('result') or {}
        quantity, delta = receipt.get('token_delta'), receipt.get('native_delta')
        if row.get('side') != 'sell' or not buy or row.get('mint') != buy.get('mint') or row.get('wallet') != buy.get('wallet'):
            continue
        if type(quantity) is not int or quantity >= 0 or type(delta) is not int:
            amounts.pop(ident, None)
            continue
        review = reviews.get(row['id'])
        if ident in affected:
            if review and type(review.get('unreconciled_cost_lamports')) is int and review.get('proceeds_lamports') == delta:
                profits[row['id']] = delta - review['unreconciled_cost_lamports']
            continue
        remaining = amounts.get(ident)
        sold = -quantity
        if not remaining or sold > remaining[0]:
            amounts.pop(ident, None)
            continue
        cost = remaining[1] if sold == remaining[0] else remaining[1] * sold // remaining[0]
        profits[row['id']] = delta - cost
        remaining[0] -= sold; remaining[1] -= cost
    for row in result:
        buy = buys.get(row.get('position_id'))
        if buy and row.get('mint') == buy.get('mint') and row.get('wallet') == buy.get('wallet'):
            row['source_wallet'] = buy.get('source_wallet')
        if row['id'] in profits:
            row['realized_pnl_lamports'] = profits[row['id']]
    return result
