"""Authenticated read-only admission/recovery controls. No submit endpoint."""
import json
from fastapi import APIRouter,Request,HTTPException
from starlette.concurrency import run_in_threadpool
from pro_ticker_broker_admission import execution_status,review_paper_admission
from pro_ticker_submission_recovery import recover_paper_submission
from pro_ticker_order_intents import IntentConflict
from pro_ticker_paper_gateway import PaperGatewayUnavailable


def build_paper_execution_router(*,require_session,context=None):
    router=APIRouter();base='/auto-trader/pro-ticker'
    @router.get(base+'/execution-status')
    def status(request:Request):
        require_session(request)
        from fastapi.responses import JSONResponse
        return JSONResponse(execution_status(context),headers={'Cache-Control':'no-store'})
    async def body(request):
        require_session(request)
        if request.headers.get('origin')!=str(request.base_url).rstrip('/'):raise HTTPException(403,'Same-origin request required.')
        if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':raise HTTPException(415,'Expected JSON.')
        raw=await request.body()
        if len(raw)>2048:raise HTTPException(413,'Request too large.')
        try:payload=json.loads(raw)
        except (ValueError,UnicodeError,RecursionError):raise HTTPException(422,'Valid JSON required.') from None
        if execution_status(context)['admission_review_enabled'] is not True:
            raise HTTPException(403,'A separately approved dedicated PAPER gateway is required. Submission remains disabled.')
        return payload
    @router.post(base+'/order-intents/{intent_id}/admission-review')
    async def review(intent_id:str,request:Request):
        payload=await body(request)
        try:return await run_in_threadpool(review_paper_admission,intent_id,payload,context=context)
        except IntentConflict:raise HTTPException(409,'Intent or review changed. Refresh saved state.') from None
        except PaperGatewayUnavailable:raise HTTPException(503,'Dedicated PAPER evidence request unavailable. No order was submitted.') from None
        except ValueError:raise HTTPException(422,'Prepared intent, valid review ID and complete dedicated PAPER evidence required.') from None
        except Exception:raise HTTPException(503,'PAPER admission evidence unavailable. No order was submitted.') from None
    @router.post(base+'/order-intents/{intent_id}/recover')
    async def recover(intent_id:str,request:Request):
        payload=await body(request)
        if payload!={}:raise HTTPException(422,'No browser-supplied broker evidence accepted.')
        try:return await run_in_threadpool(recover_paper_submission,intent_id,context=context)
        except IntentConflict:raise HTTPException(409,'Recovery evidence conflicts with the ledger. Funds remain reserved for review.') from None
        except PaperGatewayUnavailable:raise HTTPException(503,'Dedicated PAPER lookup unavailable. No order was resubmitted.') from None
        except ValueError:raise HTTPException(422,'Recorded attempt and exact dedicated PAPER order evidence required.') from None
        except Exception:raise HTTPException(503,'Recovery unconfirmed. Refresh saved state; no order was resubmitted.') from None
    return router
