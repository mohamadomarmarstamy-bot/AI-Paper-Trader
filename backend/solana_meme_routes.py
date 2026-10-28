from pathlib import Path
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from pro_ticker_http_security import require_same_origin
from solana_meme_bot import public, configure, tick, manual_close, Conflict


def build_router(*,require_session):
    router=APIRouter()
    @router.get('/meme-coins')
    @router.get('/meme-coins/trades')
    @router.get('/meme-coins/scanner')
    @router.get('/meme-coins/settings')
    @router.get('/meme-coins/history')
    def page(request:Request):
        require_session(request)
        return FileResponse(Path(__file__).parent/'frontend'/'meme-coins.html')
    @router.get('/meme-coins/status')
    def saved(request:Request):
        require_session(request)
        return public()
    async def body(request,limit=4096):
        require_session(request);require_same_origin(request)
        if request.headers.get('content-type','').split(';')[0]!='application/json':raise HTTPException(415,'Expected JSON.')
        raw=await request.body()
        if len(raw)>limit:raise HTTPException(413,'Request too large.')
        try:return json.loads(raw)
        except ValueError:raise HTTPException(422,'Valid JSON required.') from None
    @router.post('/meme-coins/config')
    async def settings(request:Request):
        payload=await body(request)
        if not isinstance(payload,dict) or set(payload)!={'changes','expected_revision'}:
            raise HTTPException(422,'Settings changes and expected revision required.')
        try:return await run_in_threadpool(configure,payload['changes'],payload['expected_revision'])
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except (ValueError,TypeError,KeyError,OverflowError):raise HTTPException(422,'Invalid paper settings. Use finite nonnegative numbers, percentages up to 100, whole coin counts, and valid mint addresses. Check confirmation range and increase-only virtual funding.') from None
    @router.post('/meme-coins/scan')
    async def scan(request:Request):
        payload=await body(request)
        if payload!={}:raise HTTPException(422,'This scan takes no execution inputs.')
        try:return await run_in_threadpool(tick,force=True)
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except Exception:raise HTTPException(503,'Paper scan unavailable. Refresh status; no on-chain transaction was sent.') from None
    @router.post('/meme-coins/close')
    async def close(request:Request):
        payload=await body(request,262144)
        if not isinstance(payload,dict) or set(payload)!={'positions','pause_new_buys'}:raise HTTPException(422,'Position identities and pause control required.')
        try:return await run_in_threadpool(manual_close,payload['positions'],payload['pause_new_buys'])
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except (ValueError,TypeError,KeyError):raise HTTPException(422,'Invalid paper position identities.') from None
        except Exception:raise HTTPException(503,'Manual PAPER close unavailable. Refresh holdings before retrying.') from None
    return router
