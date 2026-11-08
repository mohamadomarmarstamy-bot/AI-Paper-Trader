"""Read-only execution report from a single snapshot of the complete live ledger."""
from contextlib import closing
from decimal import Decimal, InvalidOperation
import json
import math
import time

import database
from solana_copy_entry import SOL

from solana_execution_timing import PROFILE
TIMINGS = {
    'source_to_detection_ms':'Source buy → detection',
    'source_processing_ms':'Detection → saved signal',
    'feed_to_execution_ms':'Saved signal → execution',
    'source_to_broadcast_ms':'Source buy → broadcast attempt',
    'source_to_finalized_ms':'Source buy → observed finalization',
    'detection_to_broadcast_ms':'Detection → broadcast attempt',
    'trigger_to_broadcast_ms':'Sell trigger → broadcast attempt',
    'trigger_to_finalized_ms':'Sell trigger → observed finalization',
    'broadcast_to_finalized_ms':'Broadcast → observed finalization',
    'funding_checks_ms':'Funding checks', 'quote_ms':'Quote checks',
    'prepare_ms':'Preparation total', 'fee_check_ms':'Fee check',
    'initial_checks_ms':'Initial preparation checks', 'balances_ms':'Preparation balances',
    'route_checks_ms':'Route checks', 'simulation_ms':'Simulation',
    'submit_ms':'Submission request',
}


def number(value):
    return type(value) in (int,float) and math.isfinite(value)


def stats(values):
    values=sorted(v for v in values if number(v))
    if not values:return {'count':0,'median':None,'p90':None}
    def percentile(fraction):
        index=(len(values)-1)*fraction;lower=int(index);upper=math.ceil(index)
        return round(values[lower]+(values[upper]-values[lower])*(index-lower),6)
    return {'count':len(values),'median':percentile(.5),'p90':percentile(.9)}


def entry_price(order):
    """Actual net SOL paid per received token; source basis includes fees/rent."""
    receipt=order.get('result') or {};reference=order.get('source_entry') or {}
    result={'our_sol_per_token':None,'source_sol_per_token':None,'premium_percent':None}
    try:
        quantity=receipt['token_delta'];delta=receipt['native_delta']
        decimals=(order.get('prepared') or {}).get('decimals')
        if type(quantity) is not int or quantity<=0 or type(delta) is not int or delta>=0 or type(decimals) is not int or not 0<=decimals<=18:return result
        ours=Decimal(-delta)*10**decimals/(Decimal(10**9)*quantity)
        result['our_sol_per_token']=float(ours)
        if reference.get('mint')!=order.get('mint') or reference.get('quote_mint')!=SOL or reference.get('token_decimals')!=decimals:return result
        raw_cost=reference['quote_amount_raw'];raw_tokens=reference['token_amount_raw']
        cost=int(raw_cost);tokens=int(raw_tokens)
        if str(cost)!=raw_cost or str(tokens)!=raw_tokens or cost<=0 or tokens<=0:return result
        source=Decimal(cost)*10**decimals/(Decimal(10**9)*tokens)
        result['source_sol_per_token']=float(source)
        result['premium_percent']=float((Decimal(-delta)*tokens/(Decimal(quantity)*cost)-1)*100)
    except (KeyError,ValueError,TypeError,InvalidOperation,OverflowError):pass
    return {k:v if number(v) else None for k,v in result.items()}


def timing_summary(orders):
    # Finalized and failed broadcasts only: pending transactions would bias the
    # reported sample toward fast successes while still missing their final time.
    settled=[o for o in orders if o.get('status') in ('confirmed','failed')]
    rows=[]
    for side in ('buy','sell'):
        candidates=[o for o in settled if o.get('side')==side]
        for key,label in TIMINGS.items():
            if side=='sell' and key.startswith(('source_','detection_','funding_','feed_')):continue
            if side=='buy' and key.startswith('trigger_'):continue
            values=[(o.get('timing_ms') or {}).get(key) for o in candidates]
            rows.append({'side':side,'key':key,'label':label,**stats([v for v in values if number(v) and v>=0]),'eligible_orders':len(candidates)})
    return rows


def wallet_results(traders,orders,positions):
    names={t['wallet']:t.get('name') or t['wallet'] for t in traders}
    wallets=set(o.get('source_wallet') or '__unattributed__' for o in orders)|{p['source_wallet'] for p in positions.values()}
    results=[]
    for wallet in sorted(wallets):
        rows=[o for o in orders if (o.get('source_wallet') or '__unattributed__')==wallet]
        sales=[o for o in rows if o.get('side')=='sell' and o.get('status')=='confirmed']
        failed=[o for o in rows if o.get('status')=='failed']
        known=[o['realized_pnl_lamports'] for o in sales if type(o.get('realized_pnl_lamports')) is int]
        fee_values=[(o.get('result') or {}).get('fee_lamports') for o in failed]
        missing_fees=sum(type(v) is not int or v<0 for v in fee_values)
        fees=sum(v for v in fee_values if type(v) is int and v>=0)
        complete=len(known)==len(sales) and not missing_fees
        buys=[o for o in rows if o.get('side')=='buy' and o.get('status')=='confirmed']
        completed=[]
        for buy in buys:
            quantity=(buy.get('result') or {}).get('token_delta')
            exits=[o for o in sales if o.get('position_id')==buy['id']]
            quantities=[(o.get('result') or {}).get('token_delta') for o in exits]
            costs=[o.get('realized_pnl_lamports') for o in exits]
            group_fees=[(o.get('result') or {}).get('fee_lamports') for o in failed if o.get('position_id')==buy['id']]
            if (type(quantity) is int and quantity>0 and exits and all(type(q) is int and q<0 for q in quantities)
                    and -sum(quantities)==quantity and all(type(c) is int for c in costs)
                    and all(type(f) is int and f>=0 for f in group_fees)):
                completed.append(sum(costs)-sum(group_fees))
        premiums=[entry_price(o)['premium_percent'] for o in buys]
        results.append({'wallet':wallet,'name':names.get(wallet,'Unattributed' if wallet=='__unattributed__' else wallet),
            'confirmed_buys':len(buys),'confirmed_sales':len(sales),'completed_positions':len(completed),
            'winning_positions':sum(p>0 for p in completed),'win_rate_percent':100*sum(p>0 for p in completed)/len(completed) if completed else None,
            'open_positions':sum(p['source_wallet']==wallet for p in positions.values()),
            'allocated_sale_pnl_lamports':sum(known),'failed_fees_lamports':fees,'unknown_fee_records':missing_fees,
            'unallocated_sales':len(sales)-len(known),'net_realized_lamports':sum(known)-fees if complete else None,
            'failed_orders':len(failed),'rejected_orders':sum(o.get('status')=='rejected' for o in rows),
            'entry_premium':stats(premiums)})
    return results


def report(traders,runtime,orders,*,offset=0,limit=20,now=None):
    names={t['wallet']:t.get('name') or t['wallet'] for t in traders}
    buys=sorted((o for o in orders if o.get('side')=='buy' and o.get('status')=='confirmed'),key=lambda o:(o.get('created_at',0),o['id']),reverse=True)
    entries=[]
    for o in buys[offset:offset+limit]:
        timings=o.get('timing_ms') or {}
        entries.append({'id':o['id'],'mint':o.get('mint'),'source_wallet':o.get('source_wallet'),
            'source_name':names.get(o.get('source_wallet'),o.get('source_wallet') or 'Unattributed'),
            'created_at':o.get('created_at'),'signature':o.get('signature'),**entry_price(o),
            'source_to_broadcast_ms':timings.get('source_to_broadcast_ms') if number(timings.get('source_to_broadcast_ms')) and timings['source_to_broadcast_ms']>=0 else None})
    wallets=wallet_results(traders,orders,runtime.get('positions',{}))
    complete=all(w['net_realized_lamports'] is not None for w in wallets)
    allocated=sum(w['net_realized_lamports'] for w in wallets if w['net_realized_lamports'] is not None)
    ledger=runtime.get('realized_lamports',0)
    pending=bool(runtime.get('balance_adjustments') or any(p.get('balance_observation') or p.get('external_change') for p in runtime.get('positions',{}).values()))
    reconciled=complete and not pending and ledger==allocated
    cohorts=[]
    for key,label in ((None,'Earlier recorded trades'),(PROFILE,'Current measured pipeline')):
        rows=[o for o in orders if (o.get('execution_profile')==PROFILE)==(key==PROFILE)]
        confirmed=[o for o in rows if o.get('side')=='buy' and o.get('status')=='confirmed']
        cohorts.append({'label':label,'orders':len(rows),'confirmed_buys':len(confirmed),
            'entry_premium':stats([entry_price(o)['premium_percent'] for o in confirmed]),'timings':timing_summary(rows)})
    return {'checked_at':time.time() if now is None else now,'wallet':runtime.get('wallet') or None,
        'order_count':len(orders),'pending_orders':sum(o.get('status') in ('preparing','signed','submitted','unknown') for o in orders),
        'accounting_complete':reconciled,'ledger_realized_lamports':ledger,
        'allocated_realized_lamports':allocated if complete else None,'ledger_difference_lamports':ledger-allocated if complete else None,
        'failed_fees_lamports':sum(w['failed_fees_lamports'] for w in wallets),
        'unknown_fee_records':sum(w['unknown_fee_records'] for w in wallets),
        'entry_premium':stats([entry_price(o)['premium_percent'] for o in buys]),
        'wallets':wallets,'timings':timing_summary(orders),'cohorts':cohorts,
        'entries':entries,'entry_count':len(buys),'offset':offset,'limit':limit,
        'basis':'All stored real orders for this trading wallet. SOL results use finalized receipts, allocated entry costs and failed-transaction fees. Open holdings are excluded from realized P/L.',
        'price_basis':'All-in SOL per received token includes fees and account funding, which can push this premium above the 5% swap-price cap. No historical USD conversion is estimated.',
        'comparison_basis':'The current group starts with paced quote requests. Earlier trades may have an older version tag or no tag. Different wallets, market conditions and missing samples prevent a causal before/after claim.'}


def public(*,offset=0,limit=20):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=50:raise ValueError('Invalid report page.')
    from solana_activity_details import enrich_orders
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN')
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def state(table):
            row=conn.execute(f'SELECT state_json FROM {table} WHERE id=1').fetchone() if table in tables else None
            return json.loads(row[0]) if row else {}
        runtime=state('solana_real_run');paper=state('solana_copy_state')
        orders=[json.loads(r[0]) for r in conn.execute('SELECT details_json FROM solana_real_orders ORDER BY created_at,rowid')] if 'solana_real_orders' in tables else []
        if runtime.get('wallet'):orders=[o for o in orders if o.get('wallet')==runtime['wallet']]
        if orders:orders=enrich_orders(conn,orders,runtime)
        from solana_quote_benchmark import public as benchmark_status
        benchmark=benchmark_status(conn)
    # Release the read transaction before aggregating; report rendering must not
    # hold a database read lock while the live worker persists an order.
    return {**report(paper.get('traders',[]),runtime,orders,offset=offset,limit=limit),'benchmark':benchmark}
