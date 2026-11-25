import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool
from pro_ticker_http_security import require_same_origin
from solana_meme_bot import public, configure, tick, manual_close, Conflict


def build_router(*,require_session):
    import solana_copy_trader as copy_bot
    import solana_profile as profile
    import solana_real_copy as real_copy
    import solana_paper_trials as trials
    import jarvis_test_bot as jarvis
    import jarvis_backlog as backlog
    import jarvis_hourly_mail as hourly_mail
    import jarvis_brain as brain

    @asynccontextmanager
    async def jarvis_lifespan(_app):
        await run_in_threadpool(jarvis.apply_preset_pause)
        await run_in_threadpool(jarvis.apply_pbh_closeout)
        tasks = [asyncio.create_task(worker()) for worker in
                 (jarvis.worker, jarvis.research_worker, jarvis.experiment_worker, jarvis.diagnostic_worker, backlog.worker, hourly_mail.worker, brain.worker)]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks)

    router = APIRouter(lifespan=jarvis_lifespan)

    @router.get('/meme-coins.css')
    def meme_style():
        return FileResponse(Path(__file__).parent/'frontend'/'meme-coins.css', media_type='text/css')

    @router.get('/jarvis-test-bot')
    def jarvis_page(request: Request):
        require_session(request)
        return FileResponse(Path(__file__).parent/'frontend'/'jarvis-test-bot.html')

    @router.get('/jarvis-test-bot/status')
    def jarvis_status(request: Request):
        require_session(request)
        return JSONResponse(jarvis.status(), headers={'Cache-Control': 'no-store'})

    @router.get('/jarvis-test-bot/brain')
    def brain_page(request: Request):
        require_session(request)
        return FileResponse(Path(__file__).parent/'frontend'/'jarvis-brain.html')

    @router.get('/jarvis-test-bot/brain/status')
    def brain_status(request: Request):
        require_session(request)
        with jarvis.connection() as conn:
            value=brain.status(conn)
            value['proposals']=[dict(row)|{'data':json.loads(row['data']),'report':json.loads(row['report'])}
                for row in conn.execute('SELECT * FROM brain_proposals ORDER BY created DESC LIMIT 15')]
        return JSONResponse(value,headers={'Cache-Control':'no-store'})

    @router.get('/jarvis-test-bot/local-worker/context')
    def local_worker_context(request: Request):
        import jarvis_local_lab as local
        if not local.authorized(request.headers.get('authorization')):
            raise HTTPException(401, 'Local worker token missing or invalid.')
        local.heartbeat(jarvis)
        return JSONResponse(local.context(jarvis), headers={'Cache-Control': 'no-store'})

    @router.post('/jarvis-test-bot/local-worker/strategy')
    async def local_worker_strategy(request: Request):
        import jarvis_local_lab as local
        if not local.authorized(request.headers.get('authorization')):
            raise HTTPException(401, 'Local worker token missing or invalid.')
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 20000:
                raise HTTPException(413, 'Strategy payload too large.')
        try:
            payload = json.loads(raw)
            result = await run_in_threadpool(local.submit, jarvis, payload)
        except (ValueError, TypeError, KeyError, RecursionError):
            raise HTTPException(422, 'Invalid paper strategy or paper engine unavailable.') from None
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})

    @router.get('/jarvis-test-bot/history')
    def jarvis_history(request: Request, jarvis_only: bool = False, before: int | None = None, bot: str | None = None, rugs_only: bool = False):
        require_session(request)
        if before is not None and not 0 < before < 2**63:
            raise HTTPException(422, 'Invalid history cursor.')
        if bot is not None and bot not in jarvis.BOTS:
            raise HTTPException(422, 'Unknown paper bot.')
        return JSONResponse(jarvis.history(jarvis_only, before, bot, rugs_only), headers={'Cache-Control': 'no-store'})

    @router.get('/jarvis-test-bot/archive')
    def jarvis_archive(request: Request, before: int | None = None):
        require_session(request)
        if before is not None and not 0<before<2**63:
            raise HTTPException(422,'Invalid archive cursor.')
        return JSONResponse(jarvis.archive(before),headers={'Cache-Control':'no-store'})

    @router.post('/jarvis-test-bot/run')
    async def jarvis_run(request: Request):
        payload = await body(request)
        if not isinstance(payload, dict) or set(payload) != {'enabled'} or type(payload['enabled']) is not bool:
            raise HTTPException(422, 'Expected a paper-run toggle.')
        return JSONResponse(await run_in_threadpool(jarvis.set_enabled, payload['enabled']),
                            headers={'Cache-Control': 'no-store'})

    @router.post('/jarvis-test-bot/preset-pause')
    async def preset_pause(request: Request):
        payload = await body(request)
        if not isinstance(payload,dict) or set(payload)!={'paused'} or type(payload['paused']) is not bool:
            raise HTTPException(422,'Expected a preset pause toggle.')
        return JSONResponse(await run_in_threadpool(jarvis.set_preset_pause,payload['paused']),headers={'Cache-Control':'no-store'})

    @router.post('/jarvis-test-bot/backlog-settings')
    async def backlog_settings(request: Request):
        payload = await body(request)
        try:
            return JSONResponse(await run_in_threadpool(backlog.configure, payload), headers={'Cache-Control': 'no-store'})
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @router.post('/jarvis-test-bot/buy-pause')
    async def buy_pause(request: Request):
        payload = await body(request)
        if not isinstance(payload, dict) or set(payload) != {'minutes'}:
            raise HTTPException(422, 'Expected a pause duration.')
        try:
            return JSONResponse(await run_in_threadpool(backlog.control, payload['minutes']), headers={'Cache-Control': 'no-store'})
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @router.get('/jarvis-test-bot/email-pause')
    def email_pause_page(request: Request):
        # A GET never consumes a link. Only browser navigation with explicit user
        # activation can auto-submit the subsequent same-origin POST. Previewers
        # and clients without that browser signal get an ordinary confirmation.
        activated = (request.headers.get('sec-fetch-user') == '?1'
                     and request.headers.get('sec-fetch-mode') == 'navigate'
                     and request.headers.get('sec-fetch-dest') == 'document')
        auto = 'yes' if activated else 'no'
        page = ('<!doctype html><html lang="en"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>Pause Jarvis paper buys</title><link rel="stylesheet" href="/style.css">'
                f'<body data-auto="{auto}"><main><h1>Pause Jarvis paper buys</h1>'
                '<p id="message" role="status">This email link pauses paper buys only. Sells keep running.</p>'
                '<button id="apply" type="button" class="primary-button">Apply the pause selected in your email</button>'
                '<p><a href="/jarvis-test-bot#settings">Open settings / Resume buys</a></p></main>'
                '<script src="/js/jarvis-email-pause.js"></script></body></html>')
        return HTMLResponse(page, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                            'X-Robots-Tag': 'noindex, nofollow',
                            'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"})

    @router.post('/jarvis-test-bot/email-pause')
    async def email_pause(request: Request):
        # This capability cannot resume entries, change settings, or touch real funds.
        require_same_origin(request)
        raw = await request.body()
        if len(raw) > 256:
            raise HTTPException(422, 'Invalid pause link.')
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != {'token'}:
                raise ValueError('Invalid pause link.')
            result = await run_in_threadpool(backlog.email_control, payload['token'])
        except (ValueError, TypeError):
            raise HTTPException(422, 'Pause link invalid or expired. Open app settings.') from None
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})

    @router.get('/meme-coins/token-labels')
    def token_labels(request:Request, mints:str=''):
        require_session(request)
        if len(mints)>1400:raise HTTPException(422,'Coin address batch too large.')
        from solana_token_labels import labels
        try:return JSONResponse({'labels':labels(mints.split(','))},headers={'Cache-Control':'private, max-age=60'})
        except ValueError as error:raise HTTPException(422,str(error)) from None

    @router.get('/meme-coins/copy/trials')
    def trial_page(request:Request):
        require_session(request)
        return FileResponse(Path(__file__).parent/'frontend'/'copy-trials.html')

    @router.get('/meme-coins/copy/trials/status')
    def trial_status(request:Request):
        require_session(request)
        return trials.status()

    @router.post('/meme-coins/copy/trials/start')
    async def trial_start(request:Request):
        if await body(request) != {}:
            raise HTTPException(422,'The paper trial uses its fixed reviewed roster.')
        try:return await run_in_threadpool(trials.start)
        except Conflict as error:raise HTTPException(409,str(error)) from None

    @router.get('/real-copy.css')
    def real_copy_style():
        return FileResponse(Path(__file__).parent/'frontend'/'real-copy.css',media_type='text/css')
    @router.get('/real-copy/status')
    def real_copy_status(request:Request):
        require_session(request)
        return JSONResponse(real_copy.public(),headers={'Cache-Control':'no-store'})
    @router.get('/real-copy/performance')
    def real_copy_performance(request:Request, offset:int=0, limit:int=20):
        require_session(request)
        from solana_copy_performance import public as performance
        try:return JSONResponse(performance(offset=offset,limit=limit),headers={'Cache-Control':'no-store'})
        except ValueError as error:raise HTTPException(422,str(error)) from None

    @router.post('/real-copy/challenge')
    async def real_copy_challenge(request:Request):
        payload=await body(request)
        if not isinstance(payload,dict) or set(payload)!={'wallet_address','expected_revision'}:raise HTTPException(422,'Public wallet and revision required.')
        try:return await run_in_threadpool(real_copy.challenge,payload['wallet_address'],payload['expected_revision'],request.headers['origin'])
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except (ValueError,TypeError):raise HTTPException(422,'Invalid public wallet challenge.') from None
    @router.get('/real-copy/balance')
    def real_copy_balance(request:Request):
        require_session(request)
        import solana_real_budget as budget
        return JSONResponse(budget.public(),headers={'Cache-Control':'no-store'})
    @router.post('/real-copy/settings')
    async def real_copy_settings(request:Request):
        payload=await body(request)
        if not isinstance(payload,dict) or set(payload)!={'changes','expected_revision'}:raise HTTPException(422,'Buy settings and revision required.')
        import solana_real_budget as budget
        try:return await run_in_threadpool(budget.configure,payload['changes'],payload['expected_revision'])
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except (ValueError,TypeError,OverflowError) as error:raise HTTPException(422,str(error)) from None
    @router.post('/real-copy/stop-experiment')
    async def real_copy_stop_experiment(request:Request):
        payload=await body(request)
        fields={'paper_revision','live_revision','exit_revision','acknowledgement'}
        if not isinstance(payload,dict) or set(payload)!=fields:raise HTTPException(422,'Current stop experiment revisions required.')
        from solana_copy_exit_settings import apply_split
        try:
            await run_in_threadpool(apply_split,**payload)
            return await run_in_threadpool(real_copy.public)
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except (ValueError,TypeError) as error:raise HTTPException(422,str(error)) from None

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
        payload=await body(request)
        if not isinstance(payload,dict) or set(payload)!={'expected_revision','wallet_revision','budget_revision','acknowledgement'}:raise HTTPException(423,'Complete live setup and explicitly acknowledge real-money activation.')
        import solana_real_worker as live
        try:
            await run_in_threadpool(live.activate,payload['expected_revision'],payload['wallet_revision'],payload['budget_revision'],payload['acknowledgement'])
            return real_copy.public()
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except ValueError as error:raise HTTPException(422,str(error)) from None
        except Exception as error:
            from solana_real_execution import Unavailable
            raise HTTPException(503,str(error) if isinstance(error,Unavailable) else 'Live activation checks failed. Trading remains off.') from None
    @router.post('/real-copy/pause')
    async def real_copy_pause(request:Request):
        payload=await body(request)
        if not isinstance(payload,dict) or set(payload)!={'expected_revision'}:raise HTTPException(422,'Real copier revision required.')
        import solana_real_worker as live
        try:
            await run_in_threadpool(live.pause,payload['expected_revision']);return real_copy.public()
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except ValueError as error:raise HTTPException(422,str(error)) from None
    @router.post('/real-copy/close')
    async def real_copy_close(request:Request):
        payload=await body(request)
        if not isinstance(payload,dict) or set(payload)!={'positions','pause_new_buys'}:raise HTTPException(422,'Live position identities required.')
        import solana_real_worker as live
        try:
            await run_in_threadpool(live.queue_close,payload['positions'],payload['pause_new_buys']);return real_copy.public()
        except Conflict as error:raise HTTPException(409,str(error)) from None
        except ValueError as error:raise HTTPException(422,str(error)) from None
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
    @router.get('/meme-coins/copy/wallet-review')
    def wallet_review(request:Request):
        require_session(request)
        from solana_wallet_review import public as review
        return JSONResponse(review(),headers={'Cache-Control':'no-store'})
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
