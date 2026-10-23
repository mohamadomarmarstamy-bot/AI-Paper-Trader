"""Opt-in, same-origin app-owner controls for manual virtual PAPER release.

The application uses one owner password, not a multi-user role model. The
server supplies the owner identity; the request cannot choose an approver.
"""
import json
from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from pro_ticker_manual_release import (
    prepare_manual_paper_release, approve_manual_paper_release,
    release_manually_approved_paper_budget, _uuid,
)
from pro_ticker_release_reporting import list_paper_budget_releases


def build_paper_release_router(*, require_session, operator_enabled, make_paper_reader):
    router = APIRouter(prefix='/auto-trader/pro-ticker-paper')

    def require_operator(request):
        require_session(request)
        if operator_enabled() is not True:
            raise HTTPException(status_code=403, detail='Manual PAPER release controls are disabled.')
        # Cookies alone must not authorize a cross-site write, including when
        # another application origin is otherwise allowed by global CORS.
        expected_origin = request.base_url.scheme + '://' + request.base_url.netloc
        if request.headers.get('origin') != expected_origin:
            raise HTTPException(status_code=403, detail='Same-origin operator request required.')
        if request.headers.get('x-pro-ticker-operator') != 'manual-release':
            raise HTTPException(status_code=403, detail='Explicit operator request required.')
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise HTTPException(status_code=415, detail='JSON operator request required.')

    def lazy_paper_reader():
        bound = None
        def read(order_id):
            nonlocal bound
            if bound is None:
                bound = make_paper_reader()
            return bound(order_id)
        return read

    async def payload(request, fields):
        try:
            raw = await request.body()
            if len(raw) > 2048:
                raise HTTPException(status_code=413, detail='Operator request is too large.')
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(status_code=400, detail='Invalid JSON request.') from None
        if not isinstance(data, dict) or set(data) != fields:
            raise HTTPException(status_code=422, detail='Unexpected operator request fields.')
        return data

    @router.get('/release-status')
    def status(request: Request):
        require_session(request)
        return dict(paper=True, manual_release_enabled=operator_enabled() is True,
                    automatic_release_enabled=False, order_execution_enabled=False)

    @router.get('/release-events')
    def history(request: Request, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
        require_session(request)
        return list_paper_budget_releases(limit=limit, offset=offset)

    @router.post('/release-review')
    async def prepare(request: Request):
        require_operator(request)
        data = await payload(request, {'reservation_id'})
        try:
            _uuid(data['reservation_id'])
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(status_code=422, detail='Canonical reservation UUID required.') from None
        try:
            return await run_in_threadpool(prepare_manual_paper_release, data['reservation_id'], fetch_paper_order=lazy_paper_reader())
        except ValueError:
            raise HTTPException(status_code=409, detail='PAPER closure evidence is unavailable or inconsistent. Review the linked orders and retry.') from None
        except Exception:
            # Transport exceptions can contain credential-bearing URLs/headers.
            raise HTTPException(status_code=503, detail='PAPER review is temporarily unavailable.') from None

    @router.post('/release-confirm')
    async def confirm(request: Request):
        require_operator(request)
        data = await payload(request, {'review_id', 'evidence_digest', 'paper_provenance_confirmed'})
        try:
            _uuid(data['review_id'])
            digest = data['evidence_digest']
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(ch not in '0123456789abcdef' for ch in digest)
                    or data['paper_provenance_confirmed'] is not True):
                raise ValueError('Invalid approval')
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(status_code=422, detail='Exact review digest and explicit PAPER verification are required.') from None
        def apply_confirmation():
            approve_manual_paper_release(
                data['review_id'], evidence_digest=digest, approved_by='authenticated-app-owner',
                independent_paper_provenance_confirmed=True,
            )
            return release_manually_approved_paper_budget(data['review_id'], fetch_paper_order=lazy_paper_reader())
        try:
            # Broker GETs and SQLite locks must not block FastAPI's event loop.
            return await run_in_threadpool(apply_confirmation)
        except ValueError:
            raise HTTPException(status_code=409, detail='Approval expired or evidence changed. Prepare a fresh PAPER review.') from None
        except Exception:
            raise HTTPException(status_code=503, detail='PAPER release is temporarily unavailable; the same confirmation can be retried.') from None

    return router
