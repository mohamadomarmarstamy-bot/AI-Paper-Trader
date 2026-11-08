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
            # Early full-sale records predate staged exits and have no lot_id.
            # Their immutable close id identifies the one completed position.
            lots.setdefault(row.get('lot_id') or row['id'], []).append(row)
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


def real_results(traders, orders, adjustments=()):
    names = {r['wallet']: r.get('name', r['wallet']) for r in traders}
    results = []
    unattributed = '__unattributed__'
    for wallet in sorted(set(names) | {r.get('source_wallet') or unattributed for r in orders} | {r['source_wallet'] for r in adjustments}):
        rows = [r for r in orders if (r.get('source_wallet') or unattributed) == wallet]
        repairs=[r for r in adjustments if r['source_wallet']==wallet]
        repair_pnl=sum(r['pnl_lamports'] for r in repairs)
        sells = [r for r in rows if r['side'] == 'sell' and r['status'] == 'confirmed']
        fee_values=[(r.get('result') or {}).get('fee_lamports') for r in rows if r['status']=='failed']
        unknown_fees=sum(type(f) is not int or f<0 for f in fee_values)
        failed_fees=sum(f for f in fee_values if type(f) is int and f>=0)
        known = [r['realized_pnl_lamports'] for r in sells if type(r.get('realized_pnl_lamports')) is int]
        results.append(dict(wallet=wallet, name=names.get(wallet, 'Unattributed' if wallet == unattributed else wallet),
            confirmed_buys=sum(r['side'] == 'buy' and r['status'] == 'confirmed' for r in rows),
            confirmed_sell_records=len(sells), unallocated_sell_records=len(sells)-len(known),
            allocated_sale_pnl_sol=sum(known)/10**9, failed_transaction_fees_sol=failed_fees/10**9,
            manual_adjustment_count=len(repairs),manual_adjustment_pnl_sol=repair_pnl/10**9, unknown_fee_records=unknown_fees,
            net_realized_lamports=sum(known)+repair_pnl-failed_fees if len(known)==len(sells) and not unknown_fees else None,
            net_realized_sol=(sum(known)+repair_pnl-failed_fees)/10**9 if len(known) == len(sells) and not unknown_fees else None,
            rejected_orders=sum(r['status'] == 'rejected' for r in rows)))
    return results


def public():
    import solana_copy_trader as paper
    import solana_real_worker as live
    from solana_activity_details import enrich_orders
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        source_row=conn.execute('SELECT state_json FROM solana_copy_state WHERE id=1').fetchone() if 'solana_copy_state' in tables else None
        run_row=conn.execute('SELECT state_json FROM solana_real_run WHERE id=1').fetchone() if 'solana_real_run' in tables else None
        state=json.loads(source_row[0]) if source_row else paper.initial()
        runtime=json.loads(run_row[0]) if run_row else live.initial()
        closes = [json.loads(r[0]) for r in conn.execute('SELECT details_json FROM solana_copy_closes')] if 'solana_copy_closes' in tables else []
        orders = [json.loads(r[0]) for r in conn.execute('SELECT details_json FROM solana_real_orders ORDER BY created_at,rowid')] if 'solana_real_orders' in tables else []
        if runtime.get('wallet'):orders=[o for o in orders if o.get('wallet')==runtime['wallet']]
        orders = enrich_orders(conn, orders, runtime) if orders else []
    from solana_accounting_repairs import audit
    repairs=audit(orders,runtime)
    real = real_results(state['traders'], orders,repairs['adjustments'])
    allocated = sum(r['net_realized_lamports'] for r in real if r['net_realized_lamports'] is not None)
    difference = runtime['realized_lamports'] - allocated
    return dict(checked_at=time.time(), complete_history=True,
        paper=paper_results(state['traders'], closes, list(state['positions'].values())),
        real=real, accounting_adjustments=repairs['adjustments'], accounting_issues=repairs['issues'],
        source_totals_reconciled=difference == 0 and not repairs['issues'] and not live.accounting_pending(runtime) and all(r['net_realized_sol'] is not None for r in real),
        ledger_realized_sol=runtime['realized_lamports']/10**9,
        allocated_source_realized_sol=allocated/10**9, ledger_minus_source_pnl_sol=difference/10**9,
        paper_exit_records=sum(not r.get('voided') for r in closes), real_order_records=len(orders),
        real_accounting_complete=not live.accounting_pending(runtime),
        basis='All stored records for the active trading wallet. Paper uses modeled USD costs; real uses finalized SOL receipts, failed-transaction fees and validated archived adjustments. Partial exits are not separate winning positions. Unknown sale allocation remains incomplete.')
