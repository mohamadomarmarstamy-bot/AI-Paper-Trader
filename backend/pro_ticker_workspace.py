"""Read-only, strategy-scoped workspace snapshot. Never query shared trade P/L."""
from pro_ticker_http_security import require_same_origin
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import re
from fastapi import APIRouter, HTTPException, Request
import database
from pro_ticker_paper_policy import PRO_TICKER_VIRTUAL_BUDGET


def validate_proposal_inputs(payload):
    """Validate sizing fields without database reads or price-level rounding."""
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
    return symbol, shares, values


def hypothetical_proposal(payload, *, connection=None):
    """Operator reference prices are estimates, never broker execution evidence."""
    symbol, shares, values = validate_proposal_inputs(payload)
    price = values['reference_price']
    def cents(value):
        return int((value * 100).quantize(Decimal('1'), rounding=ROUND_CEILING))
    allocation = cents(price * shares)
    if allocation <= 0:
        raise ValueError('Estimated allocation must be at least one cent.')
    stop_price = (price * (1-values['stop_loss_percent']/100)).quantize(Decimal('.0001'))
    target_price = (price * (1+values['take_profit_percent']/100)).quantize(Decimal('.0001'))
    if not 0 < stop_price < price < target_price:
        raise ValueError('Protection levels cannot collapse at supported price precision.')
    if connection is None:
        available_cents = workspace_snapshot(limit=1)['budget']['available_cents']
    else:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_paper_reservations'").fetchone()
        committed = connection.execute("SELECT COALESCE(SUM(notional_cents),0) FROM pro_ticker_paper_reservations WHERE status IN ('reserved','submitted','filled')").fetchone()[0] if exists else 0
        available_cents = max(0, round(PRO_TICKER_VIRTUAL_BUDGET*100)-committed)
    from pro_ticker_settings import read_settings
    settings = read_settings(connection)
    # Four-decimal protection rounding can widen actual stop distance. Use
    # the larger requested/rounded distance so sizing never understates it.
    stop_risk = cents(max(price * values['stop_loss_percent']/100, price-stop_price) * shares)
    reasons = []
    if allocation > settings['settings']['max_allocation_cents']:
        reasons.append('allocation_exceeds_pro_ticker_limit')
    if stop_risk > settings['settings']['max_stop_risk_cents']:
        reasons.append('estimated_stop_risk_exceeds_pro_ticker_limit')
    return {'paper': True, 'hypothetical': True, 'order_submitted': False,
            'order_execution_enabled': False, 'budget_reserved': False,
            'symbol': symbol, 'shares': shares, 'reference_price_source': 'operator_estimate',
            'allocation_cents': allocation,
            'stop_price': str(stop_price),
            'take_profit_price': str(target_price),
            'estimated_stop_risk_cents': stop_risk,
            'settings_revision': settings['revision'],
            'risk_limits_satisfied': not reasons, 'risk_limit_reasons': reasons,
            'available_virtual_cents': available_cents,
            'within_virtual_budget': allocation <= available_cents,
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
    # Discovery is separate from a market scanner. Surface its persisted
    # status without misrepresenting saved articles as live market signals.
    from pro_ticker_discovery import discovery_status
    discovery = discovery_status()
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
                    'public_discovery': discovery,
                    'unattributed_research_records': unattributed_research,
                    'last_scan_at': None, 'reason': 'Saved research is not a live scanner or a new AI evaluation.'},
        'health': {'database': 'readable', 'broker': 'not_checked', 'background_jobs': 'not_started_by_workspace'},
    }


def build_workspace_router(*, require_session):
    router = APIRouter()
    @router.get('/auto-trader/pro-ticker/settings')
    def settings(request: Request):
        require_session(request)
        from pro_ticker_settings import read_settings
        try:
            return read_settings()
        except Exception:
            raise HTTPException(503, 'Pro Ticker settings unavailable.') from None
    @router.put('/auto-trader/pro-ticker/settings')
    async def update_settings(request: Request):
        require_session(request)
        require_same_origin(request)
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            raise HTTPException(415, 'Expected JSON.')
        if len(body := await request.body()) > 2048:
            raise HTTPException(413, 'Settings request too large.')
        from pro_ticker_settings import save_settings, SettingsConflict
        from starlette.concurrency import run_in_threadpool
        import json
        try:
            return await run_in_threadpool(save_settings, json.loads(body))
        except SettingsConflict:
            raise HTTPException(409, 'Settings changed. Reload before saving again.') from None
        except ValueError:
            raise HTTPException(422, 'Invalid Pro Ticker settings.') from None
        except Exception:
            raise HTTPException(503, 'Pro Ticker settings could not be saved.') from None
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
        require_same_origin(request)
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
        require_same_origin(request)
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
    from pro_ticker_closed_performance import build_closed_performance_router
    router.include_router(build_closed_performance_router(require_session=require_session))
    return router
