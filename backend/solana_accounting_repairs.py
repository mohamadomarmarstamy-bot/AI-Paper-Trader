"""Read-only attribution of previously posted, archived accounting repairs.

Supports the audited GOON bundle from the November 1 operator repair. This
projects that stored adjustment into reports; it never posts P/L or writes data.
"""
import hashlib
import math

WALLET = '3tmujS2fwJiPHRXc6NskYz1vJHf1Eq9JNvx2547MKut6'
MINT = '8buabJcozHuvWi8PEwzPK8gQdNJSJ6A8cAqsoqhBxQfQ'
QUANTITY = 338072070401
COST = 212834386
EXPECTED_PROCEEDS = 99679352
SIGNATURES = ('5yvsLX9Yo9kNrqkkXL9AyHYdNu6gqMLswjtMEdu7J2wAPmwYQMX3NRKKt1T5cstCh1utVnf9QL4nBZVDqrTWBLVZ', '32LQktuzkoQa6w9gnu2TecRmPMkwuumPKj3sGmG3YQTmfL7Htu5fnWRfBDhZwinJJdbyyNwVp9m1vHWpLkt64nkD', '2ZZUgs5wGMy8AgwC3ynx3vjsCP2TjBb8HBpFFd9JMzThr7eQ6gjgNcjoakFAcoaKJFNCy2jLPCzTE34SxuVe8U97', '3udeeRpHWQG9qV9wP43zHpoVzhcf4MdrjaMafdcc4fFCHrzPwmTy68BhhHn13PURo6UJkcVZqDmWfedo1bmEctCP', '3iSBYgYwzmza3KXwCx9dyJi2F5xMLz1uWaMuV5BoTT58J8GRSqusWBjsti9dF9QKXVRCoaywX6Y7D82ZDDzwuCe2')
REPAIR_ID=hashlib.sha256(('GOON external exit:'+','.join(SIGNATURES)).encode()).hexdigest()


def integer(value):return type(value) is int


def signatures(order):
    result=set()
    if order.get('signature'):result.add(order['signature'])
    result.update(r.get('signature') for r in (order.get('result') or {}).get('receipts',[]) if r.get('signature'))
    return result


def audit(orders,state):
    """Require matching archived evidence and entry cost, not a matching gap.

    An archive is already booked in runtime realized P/L. Its loss must only be
    allocated here. Unknown/conflicting archives keep reconciliation incomplete.
    """
    archives=state.get('accounting_repairs') or []
    adjustments=[];issues=[];represented=[]
    if not isinstance(archives,list):return {'adjustments':[],'issues':[{'repair_id':None,'reason':'Archived repairs are malformed.'}],'already_represented':[]}
    by_id={o['id']:o for o in orders}
    for repair in archives:
        if not isinstance(repair,dict):
            issues.append({'repair_id':None,'reason':'Archived repair is malformed.'});continue
        ident=repair.get('repair_id')
        if sum(isinstance(r,dict) and r.get('repair_id')==ident for r in archives)!=1:
            issues.append({'repair_id':ident,'reason':'Duplicate archived repair identity.'});continue
        try:
            if ident!=REPAIR_ID:raise ValueError('Unsupported archived repair; review its evidence.')
            if state.get('wallet')!=WALLET or repair.get('wallet')!=WALLET or repair.get('mint')!=MINT:raise ValueError('Archived repair wallet or coin does not match.')
            fields={'quantity_raw':QUANTITY,'net_proceeds_lamports':EXPECTED_PROCEEDS,'removed_cost_lamports':COST,
                    'realized_pnl_adjustment_lamports':EXPECTED_PROCEEDS-COST}
            if any(not integer(repair.get(k)) or repair[k]!=v for k,v in fields.items()):raise ValueError('Archived repair amounts do not match the reviewed bundle.')
            reviewed=repair.get('reviewed_at')
            if type(reviewed) not in (int,float) or not math.isfinite(reviewed) or reviewed<=0:raise ValueError('Archived repair has no valid review timestamp.')
            original=repair.get('original_adjustment') or {};position_id=original.get('position_id')
            if (original.get('mint')!=MINT or not integer(original.get('quantity_removed_raw')) or original['quantity_removed_raw']!=QUANTITY
                    or not integer(original.get('unreconciled_cost_lamports')) or original['unreconciled_cost_lamports']!=COST
                    or original.get('proceeds_lamports') is not None):raise ValueError('Archived original cost adjustment is inconsistent.')
            evidence=repair.get('evidence') or []
            if len(evidence)!=len(SIGNATURES) or [r.get('signature') for r in evidence]!=list(SIGNATURES):raise ValueError('Archived receipt bundle is incomplete or reordered.')
            tokens={};native=0
            for row in evidence:
                if (not integer(row.get('slot')) or row['slot']<=0 or not integer(row.get('block_time')) or not 1793552926<=row['block_time']<=1793552991
                        or not integer(row.get('native_delta_lamports')) or not integer(row.get('fee_lamports')) or row['fee_lamports']<0):raise ValueError('Archived receipt amounts or dates are invalid.')
                native+=row['native_delta_lamports']
                if not isinstance(row.get('token_deltas'),dict):raise ValueError('Archived token evidence is missing.')
                for mint,quantity in row['token_deltas'].items():
                    if not integer(quantity):raise ValueError('Archived token changes are invalid.')
                    tokens[mint]=tokens.get(mint,0)+quantity
            if native!=EXPECTED_PROCEEDS or tokens.get(MINT)!=-QUANTITY or any(v for m,v in tokens.items() if m!=MINT):raise ValueError('Archived receipts do not support the recorded adjustment.')
            buy=by_id.get(position_id)
            if not buy or buy.get('side')!='buy' or buy.get('status')!='confirmed' or buy.get('wallet')!=WALLET or buy.get('mint')!=MINT:raise ValueError('Exact confirmed entry record is missing.')
            source=buy.get('source_wallet')
            if not isinstance(source,str) or not source:raise ValueError('Entry source-wallet attribution is missing.')
            receipts=set(SIGNATURES)
            overlap=[o for o in orders if o.get('status')=='confirmed' and signatures(o)&receipts]
            represented_order=None
            if overlap:
                if (len(overlap)==1 and overlap[0].get('position_id')==position_id and overlap[0].get('side')=='sell'
                        and overlap[0].get('wallet')==WALLET and overlap[0].get('mint')==MINT
                        and set(r.get('signature') for r in (overlap[0].get('result') or {}).get('receipts',[]))==receipts
                        and (overlap[0].get('result') or {}).get('native_delta')==EXPECTED_PROCEEDS
                        and (overlap[0].get('result') or {}).get('token_delta')==-QUANTITY
                        and overlap[0].get('realized_pnl_lamports')==EXPECTED_PROCEEDS-COST):
                    represented_order=overlap[0]['id']
                else:raise ValueError('Archived receipts overlap an existing trade; independent attribution is unsafe.')
            result=buy.get('result') or {};quantity=result.get('token_delta');cost=result.get('native_delta')
            if not integer(quantity) or quantity<=0 or not integer(cost) or cost>=0:raise ValueError('Entry receipt has no verified cost or quantity.')
            cost=-cost
            sales=sorted((o for o in orders if o.get('position_id')==position_id and o.get('side')=='sell' and o.get('status')=='confirmed' and o['id']!=represented_order),key=lambda o:o.get('created_at',0))
            for sale in sales:
                sold=(sale.get('result') or {}).get('token_delta')
                if sale.get('wallet')!=WALLET or sale.get('mint')!=MINT or not integer(sold) or sold>=0 or -sold>quantity:raise ValueError('Existing sale quantities cannot be reconciled with this repair.')
                sold=-sold;allocated=cost if sold==quantity else cost*sold//quantity
                cost-=allocated;quantity-=sold
            if quantity!=QUANTITY or cost!=COST or position_id in state.get('positions',{}):raise ValueError('Archived cost does not match the remaining closed entry.')
            if any(a.get('position_id')==position_id for a in state.get('balance_adjustments',[])):raise ValueError('The same cost remains unresolved in live accounting.')
            if represented_order:
                represented.append(ident);continue
            adjustments.append({'repair_id':ident,'position_id':position_id,'wallet':WALLET,'mint':MINT,'source_wallet':source,
                'quantity_raw':QUANTITY,'cost_lamports':COST,'proceeds_lamports':EXPECTED_PROCEEDS,
                'pnl_lamports':EXPECTED_PROCEEDS-COST,'receipt_signatures':list(SIGNATURES),
                'basis':'Previously posted audited manual sale; fees and account funding included in net receipt proceeds.'})
        except (ValueError,TypeError,KeyError,AttributeError) as error:
            reason=str(error) if isinstance(error,ValueError) else 'Archived repair evidence is malformed.'
            issues.append({'repair_id':ident,'reason':reason})
    return {'adjustments':adjustments,'issues':issues,'already_represented':represented}
