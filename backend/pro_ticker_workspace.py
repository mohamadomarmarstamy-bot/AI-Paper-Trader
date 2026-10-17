"""Read-only, strategy-scoped workspace snapshot. Never query shared trade P/L."""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from fastapi import APIRouter, HTTPException, Request
import database
from pro_ticker_paper_policy import PRO_TICKER_VIRTUAL_BUDGET


def hypothetical_proposal(payload):
    """Operator reference prices are estimates, never broker execution evidence."""
    fields = {'symbol', 'reference_price', 'shares', 'stop_loss_percent', 'take_profit_percent'}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError('Provide exactly the proposal fields.')
    symbol = payload['symbol']
    if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}', symbol):
        raise ValueError('Provide a valid uppercase symbol.')
    shares = payload['shares']
    if isinstance(shares, bool) or not isinstance(shares, int) or not 1 <= shares <= 100000:
        raise ValueError('Provide 1 to 100000 whole shares.')
    values = {}
    for key in ('reference_price', 'stop_loss_percent', 'take_profit_percent'):
        raw = payload[key]
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)) or len(str(raw)) > 32:
            raise ValueError('Invalid proposal number.')
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            raise ValueError('Invalid proposal number.') from None
        maximum = Decimal('1000000') if key == 'reference_price' else Decimal('99')
        if not value.is_finite() or not 0 < value <= maximum:
            raise ValueError('Proposal numbers are outside permitted ranges.')
        values[key] = value
    price = values['reference_price']
    def cents(value):
        return int((value * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    allocation = cents(price * shares)
    if allocation <= 0:
        raise ValueError('Estimated allocation must be at least one cent.')
    budget = workspace_snapshot(limit=1)['budget']
    return {'paper': True, 'hypothetical': True, 'order_submitted': False,
            'order_execution_enabled': False, 'budget_reserved': False,
            'symbol': symbol, 'shares': shares, 'reference_price_source': 'operator_estimate',
            'allocation_cents': allocation,
            'stop_price': str((price * (1-values['stop_loss_percent']/100)).quantize(Decimal('.0001'))),
            'take_profit_price': str((price * (1+values['take_profit_percent']/100)).quantize(Decimal('.0001'))),
            'estimated_stop_risk_cents': cents(price * shares * values['stop_loss_percent']/100),
            'available_virtual_cents': budget['available_cents'],
            'within_virtual_budget': allocation <= budget['available_cents'],
            'reason': 'Estimate only. Broker buying power, market freshness, ownership and protection are unverified. Slippage, gaps and fees can increase losses.'}


def workspace_snapshot(*, limit=200):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError('Invalid workspace limit.')
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def read(table, columns, order, where=''):
            # All identifiers are internal constants; no browser-supplied SQL.
            if table not in tables:
                return {'rows': [], 'total': 0, 'has_more': False}
            total = conn.execute(f'SELECT COUNT(*) FROM {table} {where}').fetchone()[0]
            rows = conn.execute(f'SELECT {columns} FROM {table} {where} ORDER BY {order} LIMIT ?', (limit,)).fetchall()
            return {'rows': [dict(r) for r in rows], 'total': total, 'has_more': total > limit}
        reservations = read('pro_ticker_paper_reservations',
            'id,symbol,notional_cents,status,created_at,updated_at,broker_order_id', 'created_at DESC,id DESC')
        committed = 0
        if 'pro_ticker_paper_reservations' in tables:
            committed = conn.execute("SELECT COALESCE(SUM(notional_cents),0) FROM pro_ticker_paper_reservations WHERE status IN ('reserved','submitted','filled')").fetchone()[0]
        positions = read('pro_ticker_strategy_positions', 'symbol,shares,updated_at', 'symbol', 'WHERE shares > 0')
        journal = read('pro_ticker_strategy_fill_events',
            'event_id,broker_order_id,symbol,side,shares,recorded_at', 'recorded_at DESC,event_id DESC')
        research = read('pro_ticker_research',
            'id,symbol,article_title,published_date,direction,long_level,short_level,setup_type,relative_volume,rsi_context,volume_context,article_summary,our_scanner_score,our_scanner_confidence,our_skip_reason,updated_at',
            'updated_at DESC,id DESC', "WHERE symbol IS NOT NULL AND TRIM(symbol) != ''")
        unattributed_research = conn.execute("SELECT COUNT(*) FROM pro_ticker_research WHERE symbol IS NULL OR TRIM(symbol) = ''").fetchone()[0] if 'pro_ticker_research' in tables else 0
        conn.rollback()
    budget_cents = round(PRO_TICKER_VIRTUAL_BUDGET * 100)
    return {
        'paper': True, 'scope': 'pro_ticker_strategy_ledger',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'order_execution_enabled': False, 'automatic_release_enabled': False,
        'broker_reconciled': False,
        'budget': {'configured_cents': budget_cents, 'committed_cents': committed,
                   'available_cents': max(0, budget_cents - committed),
                   'overallocated_cents': max(0, committed - budget_cents)},
        'reservations': reservations, 'positions': positions, 'journal': journal, 'research': research,
        'performance': {'realized_pnl': None, 'unrealized_pnl': None, 'win_rate': None,
                        'profit_factor': None, 'drawdown': None,
                        'reason': 'Verified execution prices and current marks are not stored in the strategy share ledger.'},
        'scanner': {'status': 'saved_research_only', 'automatic_discovery_enabled': False,
                    'unattributed_research_records': unattributed_research,
                    'last_scan_at': None, 'reason': 'Saved research is not a live scanner or a new AI evaluation.'},
        'health': {'database': 'readable', 'broker': 'not_checked', 'background_jobs': 'not_started_by_workspace'},
    }


def build_workspace_router(*, require_session):
    router = APIRouter()
    @router.get('/auto-trader/pro-ticker/workspace')
    def workspace(request: Request):
        require_session(request)
        try:
            return workspace_snapshot()
        except Exception:
            raise HTTPException(status_code=503, detail='Pro Ticker workspace data unavailable.') from None
    @router.post('/auto-trader/pro-ticker/proposal')
    async def proposal(request: Request):
        require_session(request)
        if request.headers.get('origin') != str(request.base_url).rstrip('/'):
            raise HTTPException(403, 'Same-origin proposal request required.')
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            raise HTTPException(415, 'Expected JSON.')
        try:
            payload = await request.json()
            from starlette.concurrency import run_in_threadpool
            return await run_in_threadpool(hypothetical_proposal, payload)
        except ValueError:
            raise HTTPException(422, 'Invalid hypothetical proposal.') from None
        except Exception:
            raise HTTPException(503, 'Proposal data unavailable.') from None
    @router.get('/auto-trader/pro-ticker/discovery')
    def discovery(request: Request):
        require_session(request)
        from pro_ticker_discovery import discovery_status
        try:
            return discovery_status()
        except Exception:
            raise HTTPException(503, 'Discovery status unavailable.') from None
    @router.post('/auto-trader/pro-ticker/discovery')
    async def discover(request: Request):
        require_session(request)
        if request.headers.get('origin') != str(request.base_url).rstrip('/'):
            raise HTTPException(403, 'Same-origin discovery request required.')
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            raise HTTPException(415, 'Expected JSON.')
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(422, 'Expected empty JSON object.') from None
        if payload != {}:
            raise HTTPException(422, 'Discovery accepts no URLs, credentials or execution settings.')
        from pro_ticker_discovery import run_discovery
        from starlette.concurrency import run_in_threadpool
        try:
            return await run_in_threadpool(run_discovery)
        except Exception:
            raise HTTPException(503, 'Discovery unavailable.') from None
    return router
