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
    @router.get(base+'/market-reference')
    def market_reference(request:Request,symbol:str):
        require_session(request)
        import re
        from fastapi.responses import JSONResponse
        from pro_ticker_market_reference import market_reference_snapshot
        if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}',symbol):
            raise HTTPException(422,'Provide one uppercase stock symbol.')
        if execution_status(context)['admission_review_enabled'] is not True:
            raise HTTPException(403,'Dedicated PAPER account is not configured.')
        try:result=market_reference_snapshot(symbol,context=context)
        except Exception:
            raise HTTPException(503,'Broker reference unavailable. No funds reserved or order submitted.') from None
        return JSONResponse(result,headers={'Cache-Control':'no-store'})
    @router.get(base+'/account-status')
    def account_status(request:Request):
        require_session(request)
        from fastapi.responses import JSONResponse
        if execution_status(context)['admission_review_enabled'] is not True:
            raise HTTPException(403,'Dedicated PAPER account is not configured.')
        try:
            account=context.gateway().read_account()
            if not isinstance(account,dict) or account.get('id')!=context.expected_account_id:
                raise ValueError('Account identity mismatch.')
            # Never return credentials, account identifiers or arbitrary broker fields.
            result={'paper':True,'account_verified':True,'order_execution_authorized':False,
                    'account_active':account.get('status')=='ACTIVE',
                    'currency_usd':account.get('currency')=='USD',
                    'trading_unblocked':all(account.get(key) is False for key in
                        ('trading_blocked','account_blocked','trade_suspended_by_user'))}
        except Exception:
            raise HTTPException(503,'Dedicated PAPER connection could not be verified. No order was submitted.') from None
        return JSONResponse(result,headers={'Cache-Control':'no-store'})
    @router.get(base+'/execution-status')
    def status(request:Request):
        require_session(request)
        from fastapi.responses import JSONResponse
        return JSONResponse(execution_status(context),headers={'Cache-Control':'no-store'})
    @router.get(base+'/monitor-status')
    def monitor_status(request:Request):
        require_session(request)
        from fastapi.responses import JSONResponse
        from pro_ticker_monitor_worker import monitor_snapshot
        return JSONResponse(monitor_snapshot(context=context),headers={'Cache-Control':'no-store'})

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
    @router.post(base+'/order-intents/{intent_id}/protection-review')
    async def protection(intent_id:str,request:Request):
        payload=await body(request)
        from pro_ticker_protection_review import review_paper_protection
        try:return await run_in_threadpool(review_paper_protection,intent_id,payload,context=context)
        except IntentConflict:raise HTTPException(409,'Owned active entry and unchanged ledger required. Refresh saved state.') from None
        except PaperGatewayUnavailable:raise HTTPException(503,'Dedicated PAPER protection evidence unavailable.') from None
        except ValueError:raise HTTPException(422,'Known intent and canonical review request ID required.') from None
        except Exception:raise HTTPException(503,'Protection review unavailable. No broker order was changed.') from None
    @router.post(base+'/order-intents/{intent_id}/submit')
    async def submit(intent_id:str,request:Request):
        payload=await body(request)
        from pro_ticker_submission import submit_paper_intent,PaperSubmissionContext
        if not isinstance(context,PaperSubmissionContext) or context.owner_submission_enabled is not True:
            raise HTTPException(403,'Dedicated owner PAPER submission is disabled.')
        try:return await run_in_threadpool(submit_paper_intent,intent_id,payload,context=context)
        except IntentConflict:raise HTTPException(409,'Admission or intent changed. Refresh; recover any prior attempt before proceeding.') from None
        except ValueError:raise HTTPException(422,'Explicit owner confirmation, dedicated PAPER gateway and valid bracket required.') from None
        except Exception:raise HTTPException(503,'Submission unconfirmed. Refresh saved intent and recover by client ID; do not create another buy.') from None
    @router.post(base+'/order-intents/{intent_id}/monitor')
    async def monitor(intent_id:str,request:Request):
        payload=await body(request)
        if payload!={}:raise HTTPException(422,'No browser-supplied broker evidence accepted.')
        from pro_ticker_position_monitor import monitor_paper_position
        try:return await run_in_threadpool(monitor_paper_position,intent_id,context=context)
        except IntentConflict:raise HTTPException(409,'Conflicting position evidence. Funds remain held for manual review.') from None
        except PaperGatewayUnavailable:raise HTTPException(503,'Dedicated PAPER monitoring request unavailable. Funds remain held.') from None
        except ValueError:raise HTTPException(422,'Complete owned PAPER bracket evidence required.') from None
        except Exception:raise HTTPException(503,'Position reconciliation unconfirmed. Retry reads existing orders; no new order or release.') from None
    @router.post(base+'/order-intents/{intent_id}/manage')
    async def manage(intent_id:str,request:Request):
        payload=await body(request)
        if getattr(context,'owner_management_enabled',False) is not True:
            raise HTTPException(403,'Owner PAPER position management is disabled.')
        from pro_ticker_position_controls import manage_paper_position
        try:return await run_in_threadpool(manage_paper_position,intent_id,payload,context=context)
        except IntentConflict:raise HTTPException(409,'Management evidence changed or another request is active. Inspect existing orders; funds remain held.') from None
        except PaperGatewayUnavailable:raise HTTPException(503,'PAPER management is unconfirmed. Inspect saved state; do not create another close.') from None
        except ValueError:raise HTTPException(422,'Explicit owner confirmation and complete owned PAPER evidence required.') from None
        except Exception:raise HTTPException(503,'Management unconfirmed. Recover saved operation; do not create another close.') from None

    @router.post(base+'/order-intents/{intent_id}/manage-recover')
    async def manage_recover(intent_id:str,request:Request):
        payload=await body(request)
        if payload!={}:raise HTTPException(422,'No browser-supplied broker evidence accepted.')
        from pro_ticker_position_controls import inspect_position_management
        try:return await run_in_threadpool(inspect_position_management,intent_id,context=context)
        except IntentConflict:raise HTTPException(409,'Management evidence conflicts. Funds remain held for review.') from None
        except PaperGatewayUnavailable:raise HTTPException(503,'PAPER management evidence unavailable; no broker mutation was requested.') from None
        except ValueError:raise HTTPException(422,'Complete owned PAPER management evidence required.') from None
        except Exception:raise HTTPException(503,'Management status unavailable. No broker mutation requested by recovery.') from None

    @router.get(base+'/performance-analytics')
    def analytics(request:Request, start:str|None=None, end:str|None=None, split_at:str|None=None):
        require_session(request)
        from fastapi.responses import JSONResponse
        from pro_ticker_performance_analytics import analytics_snapshot
        from pro_ticker_strategy_evaluation import evaluate_strategies
        if set(request.query_params)-{'start','end','split_at'}:raise HTTPException(422,'Unsupported analytics filter.')
        try:
            result=analytics_snapshot(start=start,end=end)
            result['strategy_evaluation']=evaluate_strategies(result['rows'],split_at=split_at)
            return JSONResponse(result,headers={'Cache-Control':'no-store'})
        except ValueError:raise HTTPException(422,'Valid timezone-aware time filters and intact accounting evidence required.') from None
        except Exception:raise HTTPException(503,'Performance analytics unavailable.') from None
    @router.post(base+'/performance-analytics/capture')
    async def capture_performance(request:Request):
        payload=await body(request)
        if payload!={}:raise HTTPException(422,'No browser-supplied accounting evidence accepted.')
        from pro_ticker_performance_analytics import capture_equity
        try:return await run_in_threadpool(capture_equity,context=context)
        except ValueError:raise HTTPException(422,'Fresh dedicated account, owned position and complete execution evidence required.') from None
        except Exception:raise HTTPException(503,'PAPER equity observation unavailable. No order submitted.') from None

    @router.get(base+'/cash-evidence')
    def cash_evidence(request:Request):
        require_session(request)
        from fastapi.responses import JSONResponse
        from pro_ticker_cost_evidence import cash_evidence_snapshot
        try:return JSONResponse(cash_evidence_snapshot(),headers={'Cache-Control':'no-store'})
        except Exception:raise HTTPException(503,'Cash evidence unavailable.') from None
    @router.post(base+'/cash-evidence/sync')
    async def cash_sync(request:Request):
        payload=await body(request)
        if payload!={}:raise HTTPException(422,'No browser-supplied cash evidence or attribution accepted.')
        from pro_ticker_cost_evidence import sync_cash_evidence
        try:return await run_in_threadpool(sync_cash_evidence,context=context)
        except ValueError:raise HTTPException(422,'Complete unchanged dedicated PAPER cash evidence required.') from None
        except Exception:raise HTTPException(503,'Cash evidence sync unavailable. No order or release occurred.') from None

    @router.get(base+'/execution-journal')
    def execution_journal(request:Request):
        require_session(request)
        from pro_ticker_execution_journal import execution_journal_snapshot
        from fastapi.responses import JSONResponse
        try:return JSONResponse(execution_journal_snapshot(),headers={'Cache-Control':'no-store'})
        except Exception:raise HTTPException(503,'Priced execution journal unavailable.') from None
    @router.get(base+'/execution-journal.csv')
    def full_execution_export(request:Request):
        require_session(request)
        from fastapi.responses import StreamingResponse
        from starlette.background import BackgroundTask
        from pro_ticker_execution_journal import prepare_execution_journal_export
        try:
            count,stream,close=prepare_execution_journal_export()
            return StreamingResponse(stream,media_type='text/csv',headers={'Cache-Control':'no-store',
                'Content-Disposition':'attachment; filename="pro-ticker-execution-history.csv"',
                'X-Execution-Total-Count':str(count)},background=BackgroundTask(close))
        except Exception:raise HTTPException(503,'Execution history export unavailable.') from None

    @router.post(base+'/order-intents/{intent_id}/execution-sync')
    async def execution_sync(intent_id:str,request:Request):
        payload=await body(request)
        if payload!={}:raise HTTPException(422,'No browser-supplied execution evidence accepted.')
        from pro_ticker_execution_journal import sync_execution_journal
        try:return await run_in_threadpool(sync_execution_journal,intent_id,context=context)
        except IntentConflict:raise HTTPException(409,'Execution coverage or ownership changed. Reconcile the existing position and retry.') from None
        except PaperGatewayUnavailable:raise HTTPException(503,'Dedicated PAPER execution history unavailable.') from None
        except ValueError:raise HTTPException(422,'Complete owned execution evidence required.') from None
        except Exception:raise HTTPException(503,'Execution sync unconfirmed. No order or budget release occurred.') from None
    return router
