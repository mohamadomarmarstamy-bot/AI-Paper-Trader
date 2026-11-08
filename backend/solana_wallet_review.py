"""Read-only wallet review from complete stored ledgers, never activity previews."""
from contextlib import closing
import json
import time
import database


def paper_results(traders, closes, positions):
    results = []
    wallets = {r['wallet'] for r in traders} | {r['wallet'] for r in closes}
    names = {r['wallet']: r.get('name', r['wallet']) for r in traders}
    for wallet in sorted(wallets):
        rows = [r for r in closes if r['wallet'] == wallet and not r.get('voided')]
        lots = {}
        for row in rows:
            lots.setdefault(row['lot_id'], []).append(row)
        completed = [sum(r['pnl_usd'] for r in lot) for lot in lots.values()
                     if any(not r.get('partial_exit', False) for r in lot)]
        gains = sum(p for p in completed if p > 0)
        losses = -sum(p for p in completed if p < 0)
        held = [p for p in positions if p['wallet'] == wallet]
        results.append(dict(wallet=wallet, name=names.get(wallet, wallet),
            realized_pnl_usd=sum(r['pnl_usd'] for r in rows), exit_records=len(rows),
            completed_positions=len(completed), winning_positions=sum(p > 0 for p in completed),
            profit_factor=gains/losses if losses else None,
            open_positions=len(held), open_cost_usd=sum(p['cost_usd'] for p in held),
            open_mark_usd=sum(p['mark_usd'] for p in held) if all(p.get('mark_usd') is not None for p in held) else None))
    return results


def real_results(traders, orders):
    names = {r['wallet']: r.get('name', r['wallet']) for r in traders}
    results = []
    for wallet in sorted(set(names) | {r.get('source_wallet') for r in orders if r.get('source_wallet')}):
        rows = [r for r in orders if r.get('source_wallet') == wallet]
        sells = [r for r in rows if r['side'] == 'sell' and r['status'] == 'confirmed']
        failed_fees = sum((r.get('result') or {}).get('fee_lamports', 0) for r in rows if r['status'] == 'failed')
        known = [r['realized_pnl_lamports'] for r in sells if type(r.get('realized_pnl_lamports')) is int]
        results.append(dict(wallet=wallet, name=names.get(wallet, wallet),
            confirmed_buys=sum(r['side'] == 'buy' and r['status'] == 'confirmed' for r in rows),
            confirmed_sell_records=len(sells), unallocated_sell_records=len(sells)-len(known),
            allocated_sale_pnl_sol=sum(known)/10**9, failed_transaction_fees_sol=failed_fees/10**9,
            net_realized_sol=(sum(known)-failed_fees)/10**9 if len(known) == len(sells) else None,
            rejected_orders=sum(r['status'] == 'rejected' for r in rows)))
    return results


def public():
    import solana_copy_trader as paper
    import solana_real_worker as live
    from solana_activity_details import enrich_orders
    state, _ = paper.load()
    runtime, _ = live.load()
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        closes = [json.loads(r[0]) for r in conn.execute('SELECT details_json FROM solana_copy_closes')] if 'solana_copy_closes' in tables else []
        orders = [json.loads(r[0]) for r in conn.execute('SELECT details_json FROM solana_real_orders ORDER BY created_at,rowid')] if 'solana_real_orders' in tables else []
        orders = enrich_orders(conn, orders, runtime) if orders else []
    return dict(checked_at=time.time(), complete_history=True,
        paper=paper_results(state['traders'], closes, list(state['positions'].values())),
        real=real_results(state['traders'], orders),
        paper_exit_records=sum(not r.get('voided') for r in closes), real_order_records=len(orders),
        real_accounting_complete=not live.accounting_pending(runtime),
        basis='All stored records. Paper uses modeled USD costs; real uses finalized SOL receipts and failed-transaction fees. Partial exits are not separate winning positions. Unknown sale allocation remains incomplete.')
