"""Gross closed-PAPER estimates from owner-reviewed terminal order averages.

These records never value open positions, infer fees, or treat budget release
as profit. Raw average prices can be rounded by the broker; exact cash/lot P&L
and net returns require priced executions, fees and corporate-action evidence.
"""
from contextlib import closing
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from fastapi import APIRouter, Request, HTTPException
import database

KIND='closed_gross_estimate_from_terminal_broker_averages'
SCHEMA='''CREATE TABLE IF NOT EXISTS pro_ticker_closed_performance (
 reservation_id TEXT PRIMARY KEY, recorded_at TEXT NOT NULL, outcome_json TEXT NOT NULL);'''


def _decimal(value):
    if isinstance(value,bool) or not isinstance(value,(str,int,float)) or len(str(value))>40:raise ValueError('Invalid broker price.')
    result=Decimal(str(value))
    if not result.is_finite():raise ValueError('Invalid broker number.')
    return result


def _notional(order):
    qty=_decimal(order.get('filled_qty'))
    if qty<0 or qty>1000000 or qty!=qty.to_integral_value():raise ValueError('Invalid filled shares.')
    if qty==0:return Decimal(0),0
    price=_decimal(order.get('filled_avg_price'))
    if not Decimal('.000001')<=price<=Decimal('1000000'):raise ValueError('Average fill price missing or invalid.')
    return qty*price,int(qty)


def _cents(value):
    return int((value*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))


def capture_closed_performance(conn, *, reservation, entry, exits, recorded_at):
    """Called ONLY after closure verification and owner release audit insertion.

    Caller commits this record atomically with the approved release. Missing
    prices produce an explicit unavailable record; they never become zero P/L.
    """
    if not conn.in_transaction:raise ValueError('Atomic approved release transaction required.')
    proof=conn.execute('SELECT * FROM pro_ticker_budget_release_events WHERE reservation_id=?',(reservation['id'],)).fetchone()
    if not proof or proof['released_cents']!=reservation['notional_cents']:
        raise ValueError('Owner release proof required before performance capture.')
    value={'paper':True,'kind':KIND,'reservation_id':reservation['id'],'symbol':reservation['symbol'],
           'recorded_at':recorded_at,'status':'unavailable','shares':None,'entry_notional':None,
           'exit_notional':None,'gross_pnl_cents':None,'gross_return_percent':None,
           'net_pnl':None,'fees':None,'training_eligible':False,
           'release_review_id':proof['review_id'],'evidence_digest':proof['evidence_digest'],
           'source':'owner_reviewed_terminal_paper_orders',
           'reason':'Average fill prices are missing or invalid. Released virtual funds are not profit.'}
    try:
        entry_notional,shares=_notional(entry)
        exit_values=[_notional(order) for order in exits.values()]
        exit_notional=sum((v[0] for v in exit_values),Decimal(0))
        if shares==0 and all(v[1]==0 for v in exit_values):
            value.update(status='not_traded',shares=0,reason='Verified unfilled cancellation. Returned allocation is not a trade, breakeven, win, or profit.')
            conn.execute('INSERT INTO pro_ticker_closed_performance VALUES (?,?,?)',(reservation['id'],recorded_at,json.dumps(value,allow_nan=False)))
            return value
        if shares<=0 or shares!=sum(v[1] for v in exit_values) or entry_notional<=0:
            raise ValueError('Closed filled-share totals differ.')
        value.update(status='priced',shares=shares,entry_notional=str(entry_notional),exit_notional=str(exit_notional),
                     gross_pnl_cents=_cents(exit_notional-entry_notional),
                     gross_return_percent=float((exit_notional/entry_notional-1)*100),
                     reason='Gross closed estimate from broker average fill prices. Excludes fees, slippage diagnostics and corporate-action adjustments; not net P/L or validated learning data.')
    except (ValueError,TypeError,InvalidOperation,OverflowError):
        pass
    conn.execute('INSERT INTO pro_ticker_closed_performance VALUES (?,?,?)',
                 (reservation['id'],recorded_at,json.dumps(value,allow_nan=False)))
    return value


def closed_performance_snapshot(*, limit=200):
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=500:raise ValueError('Invalid report limit.')
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN')
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        released=conn.execute('SELECT COUNT(*) FROM pro_ticker_budget_release_events').fetchone()[0] if 'pro_ticker_budget_release_events' in tables else 0
        rows=conn.execute('SELECT p.*,e.review_id AS proof_review_id,e.evidence_digest AS proof_digest FROM pro_ticker_closed_performance p LEFT JOIN pro_ticker_budget_release_events e ON e.reservation_id=p.reservation_id ORDER BY p.recorded_at DESC,p.reservation_id DESC').fetchall() if 'pro_ticker_closed_performance' in tables else []
    outcomes=[]
    for row in rows:
        value=json.loads(row['outcome_json'])
        if not row['proof_review_id'] or value['release_review_id']!=row['proof_review_id'] or value['evidence_digest']!=row['proof_digest'] or value['reservation_id']!=row['reservation_id']:
            raise ValueError('Closed performance lacks matching release proof.')
        outcomes.append(value)
    unfilled=sum(o['status']=='not_traded' for o in outcomes)
    outcomes=[o for o in outcomes if o['status']!='not_traded']
    released-=unfilled
    priced=[o for o in outcomes if o['status']=='priced']
    pnl=[o['gross_pnl_cents'] for o in priced]
    wins=[p for p in pnl if p>0];losses=[p for p in pnl if p<0]
    gross_profit=sum(wins);gross_loss=-sum(losses)
    def average(values):return int((Decimal(sum(values))/len(values)).quantize(Decimal('1'),rounding=ROUND_HALF_UP)) if values else None
    return {'paper':True,'kind':KIND,'model_updated':False,'net_pnl':None,'drawdown':None,'unrealized_pnl':None,
            'unfilled_cancellations_count':unfilled,'closed_reservations_count':released,'recorded_count':len(outcomes),'priced_count':len(priced),
            'unpriced_count':max(0,released-len(priced)),'has_more':len(outcomes)>limit,'rows':outcomes[:limit],
            'gross_pnl_cents':sum(pnl) if pnl else None,'gross_profit_cents':gross_profit if pnl else None,
            'gross_loss_cents':gross_loss if pnl else None,'gross_wins':len(wins),'gross_losses':len(losses),
            'gross_flat':sum(p==0 for p in pnl),'gross_win_rate_percent':100*len(wins)/len(pnl) if pnl else None,
            'gross_profit_factor':gross_profit/gross_loss if gross_loss else None,
            'average_gross_gain_cents':average(wins),'average_gross_loss_cents':average(losses),
            'reason':'Priced closed subset only; all summary totals include every stored priced outcome, even when displayed rows are limited. Net P/L, fees, exact execution prices, equity drawdown and open-position valuation remain unavailable. Legacy releases and missing-price records remain explicitly unpriced. Returned allocations are never profits.'}


def build_closed_performance_router(*,require_session):
    router=APIRouter()
    @router.get('/auto-trader/pro-ticker/closed-performance')
    def report(request:Request):
        require_session(request)
        try:
            from fastapi.responses import JSONResponse
            return JSONResponse(closed_performance_snapshot(),headers={'Cache-Control':'no-store'})
        except Exception:raise HTTPException(503,'Closed PAPER performance unavailable.') from None
    return router
