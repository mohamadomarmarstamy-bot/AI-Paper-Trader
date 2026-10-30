from pathlib import Path
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from pro_ticker_http_security import require_same_origin
from solana_meme_bot import public, configure, tick, manual_close, Conflict


def build_router(*,require_session):
    router=APIRouter()
    import solana_copy_trader as copy_bot
    import solana_profile as profile
    import solana_real_copy as real_copy
    @router.get('/real-copy/status')
    def real_copy_status(request:Request):
        require_session(request)
        return JSONResponse(real_copy.public(),headers={'Cache-Control':'no-store'})
    @router.post('/real-copy/challenge')
    async def real_copy_challenge(request:Request):
        payload=await body(request)
        if not isinstance(payload,dict) or set(payload)!={'wallet_address','expected_revision'}:raise HTTPException(422,'Public wallet and revision required.')
        try:return await run_in_threadpool(real_copy.challenge,payload['wallet_address'],payload['expected_revision'],request.headers['origin'])
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except (ValueError,TypeError):raise HTTPException(422,'Invalid public wallet challenge.') from None
    @router.post('/real-copy/verify')
    async def real_copy_verify(request:Request):
        payload=await body(request)
        if not isinstance(payload,dict) or set(payload)!={'challenge_id','signature'}:raise HTTPException(422,'Message signature required.')
        try:
            await run_in_threadpool(real_copy.verify,payload['challenge_id'],payload['signature'],request.headers['origin'])
            return real_copy.public()
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except (ValueError,TypeError):raise HTTPException(422,'Ownership verification failed or expired. Connect again.') from None
    @router.post('/real-copy/unlink')
    async def real_copy_unlink(request:Request):
        payload=await body(request)
        if not isinstance(payload,dict) or set(payload)!={'expected_revision'}:raise HTTPException(422,'Wallet revision required.')
        try:
            await run_in_threadpool(real_copy.unlink,payload['expected_revision'])
            return real_copy.public()
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except (ValueError,TypeError):raise HTTPException(422,'Invalid wallet revision.') from None
    @router.post('/real-copy/start')
    async def real_copy_start(request:Request):
        await body(request)
        raise HTTPException(423,'Live execution is locked. This release cannot sign or submit trades.')
    @router.get('/meme-coins/profile')
    def profile_page(request:Request):
        require_session(request)
        return FileResponse(Path(__file__).parent/'frontend'/'meme-profile.html',headers={'Cache-Control':'no-store'})
    @router.get('/meme-coins/profile/status')
    def profile_status(request:Request):
        require_session(request)
        return JSONResponse(profile.public(),headers={'Cache-Control':'no-store'})
    @router.post('/meme-coins/profile')
    async def profile_save(request:Request):
        payload=await body(request)
        try:
            result=await run_in_threadpool(profile.save,payload)
            return JSONResponse(result,headers={'Cache-Control':'no-store'})
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except profile.Unavailable as error:raise HTTPException(503,str(error)) from None
        except (ValueError,TypeError):raise HTTPException(422,'Invalid profile. Use a name up to 100 characters, a valid Solana wallet address, and an API key without spaces. Choose replace or remove.') from None
    @router.get('/meme-coins')
    @router.get('/meme-coins/trades')
    @router.get('/meme-coins/scanner')
    @router.get('/meme-coins/settings')
    @router.get('/meme-coins/history')
    def page(request:Request):
        require_session(request)
        return FileResponse(Path(__file__).parent/'frontend'/'meme-coins.html')
    @router.get('/meme-coins/copy')
    def copy_page(request:Request):
        require_session(request)
        return FileResponse(Path(__file__).parent/'frontend'/'copy-traders.html')
    @router.get('/meme-coins/copy/status')
    def copy_status(request:Request):
        require_session(request)
        return copy_bot.public()
    @router.post('/meme-coins/copy/config')
    async def copy_settings(request:Request):
        payload=await body(request)
        if not isinstance(payload,dict) or set(payload)!={'changes','expected_revision'}:raise HTTPException(422,'Settings and revision required.')
        try:return await run_in_threadpool(copy_bot.configure,payload['changes'],payload['expected_revision'])
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except copy_bot.DataUnavailable as error:raise HTTPException(503,str(error)) from None
        except (ValueError,TypeError,KeyError,OverflowError):raise HTTPException(422,'Invalid copy-paper settings. Use finite nonnegative values and increase-only positive virtual capital.') from None
    @router.post('/meme-coins/copy/connections')
    async def copy_connections(request:Request):
        payload=await body(request)
        if payload!={}:raise HTTPException(422,'This connection test takes no credentials or execution inputs.')
        from solana_copy_setup import check_connections
        return await run_in_threadpool(check_connections)
    @router.post('/meme-coins/copy/seeds')
    async def copy_seeds(request:Request):
        payload=await body(request)
        if not isinstance(payload,dict) or set(payload)!={'expected_revision'}:raise HTTPException(422,'Current copy revision required.')
        from solana_copy_setup import seed_wallets
        try:return await run_in_threadpool(seed_wallets,payload['expected_revision'])
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except (ValueError,TypeError):raise HTTPException(422,'Invalid copy revision.') from None
    @router.post('/meme-coins/copy/public-rpc')
    async def copy_public_rpc(request:Request):
        payload=await body(request)
        if not isinstance(payload,dict) or set(payload)!={'expected_revision'}:raise HTTPException(422,'Current copy revision required.')
        from solana_copy_setup import enable_public_rpc
        try:return await run_in_threadpool(enable_public_rpc,payload['expected_revision'])
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except (ValueError,TypeError) as error:raise HTTPException(422,str(error)) from None
    @router.post('/meme-coins/copy/close')
    async def copy_close(request:Request):
        payload=await body(request,262144)
        if not isinstance(payload,dict) or set(payload)!={'positions','pause_new_buys','expected_revision'}:raise HTTPException(422,'Exact positions, pause control and revision required.')
        try:return await run_in_threadpool(copy_bot.queue_exits,payload['positions'],payload['pause_new_buys'],payload['expected_revision'])
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except (ValueError,TypeError,KeyError):raise HTTPException(422,'Invalid copied positions.') from None
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
