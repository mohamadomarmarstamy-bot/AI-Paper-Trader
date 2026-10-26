"""Dedicated PAPER staging factory; never mount the production application.

Launch only as: uvicorn paper_staging:create_staging_app --factory.
The allowlist intentionally excludes trading controls, account mutation,
notifications/email, background loops and all live-account routes.
"""
from pathlib import Path
import os


def validate_staging_config(*, data_directory='/staging-data'):
    if os.getenv('PAPER_STAGING_MODE') != 'true':
        raise RuntimeError('Explicit isolated PAPER staging mode required.')
    directory = Path(data_directory).resolve()
    expected = directory / 'trader.db'
    supplied = os.getenv('DATABASE_PATH', '')
    if not supplied or not Path(supplied).is_absolute() or Path(supplied).resolve() != expected:
        raise RuntimeError('Staging requires its dedicated database path.')
    if str(directory) in {'/data', str(Path(__file__).resolve().parent)}:
        raise RuntimeError('Production database locations are forbidden for staging.')
    if any(os.getenv(key) for key in ('ALPACA_API_KEY','ALPACA_SECRET_KEY','APCA_API_KEY_ID','APCA_API_SECRET_KEY')):
        raise RuntimeError('Original trader brokerage credentials are forbidden in staging.')
    if os.getenv('AUTO_TRADER_START_ENABLED', '').lower() != 'false':
        raise RuntimeError('Automatic trading must be explicitly disabled for staging.')
    if len(os.getenv('APP_ACCESS_PASSWORD', '')) < 16 or len(os.getenv('APP_SESSION_SECRET', '')) < 32:
        raise RuntimeError('Separate staging owner password and session secret required.')
    # Never allow an already-imported database module to point at another file.
    import database
    if Path(database.DATABASE_PATH).resolve() != expected:
        raise RuntimeError('Loaded database module does not use the staging database.')
    directory.mkdir(parents=True, exist_ok=True)
    return expected


def create_staging_app(*, _data_directory='/staging-data'):
    """Create an allowlisted app without production lifespan or background jobs.

    The private directory override exists solely for disposable process tests.
    The deployed uvicorn factory calls this with the dedicated volume default.
    """
    staging_database = validate_staging_config(data_directory=_data_directory)
    import main
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles
    from pro_ticker_release_routes import build_paper_release_router

    from pro_ticker_runtime import dedicated_paper_context
    execution_context=dedicated_paper_context()
    from contextlib import asynccontextmanager
    from pro_ticker_monitor_worker import start_monitor, stop_monitor, monitor_enabled

    @asynccontextmanager
    async def lifespan(app):
        task = start_monitor(execution_context)
        try:
            yield
        finally:
            await stop_monitor(task)

    app = FastAPI(title='Isolated AI PAPER Staging', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware('http')
    async def same_origin_writes(request: Request, call_next):
        if request.method not in {'GET', 'HEAD'}:
            expected = request.base_url.scheme + '://' + request.base_url.netloc
            if request.headers.get('origin') != expected:
                return JSONResponse({'detail': 'Same-origin staging request required.'}, status_code=403)
        return await call_next(request)

    @app.get('/health')
    def health():
        return dict(status='healthy', paper=True, environment='isolated-staging',
                    background_jobs_enabled=monitor_enabled(execution_context), position_monitor_enabled=monitor_enabled(execution_context),
                    broker_position_management_enabled=execution_context is not None and getattr(execution_context,'owner_management_enabled',False) is True,
                    broker_order_submission_enabled=execution_context is not None and (execution_context.owner_submission_enabled is True or getattr(execution_context,'owner_management_enabled',False) is True))

    app.add_api_route('/login', main.app_login_page, methods=['GET'], response_class=HTMLResponse)
    app.add_api_route('/login', main.app_login_submit, methods=['POST'], response_class=HTMLResponse)

    @app.post('/staging/database-backup')
    def database_backup(request: Request):
        main.require_app_session(request)
        from paper_staging_backups import staging_backup_response
        return staging_backup_response(staging_database)

    @app.get('/', response_class=HTMLResponse)
    def home(request: Request):
        if not main.request_has_valid_app_session(request):
            return RedirectResponse('/login', status_code=303)
        html = (Path(main.FRONTEND_DIR) / 'index.html').read_text(encoding='utf-8-sig')
        start = html.index('<section id="pro-ticker-manual-section"')
        section = html[start:html.index('</section>', start) + len('</section>')]
        section = section.replace('class="app-section hidden"', 'class="app-section"', 1).replace(' hidden>', '>', 1)
        return HTMLResponse('''<!doctype html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Isolated PAPER staging</title>
<link rel="stylesheet" href="/style.css"></head><body><main class="paper-staging-shell">
<header class="paper-staging-brand"><strong><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M3 18l6-6 4 3 8-11M15 4h6v6"/></svg>AI PAPER TRADER</strong><span>ISOLATED STAGING</span></header>
<p role="note" class="paper-staging-note">Isolated PAPER staging. Automatic buying and budget release are disabled. Dedicated PAPER monitoring and owner submission depend on server configuration.</p>
<details class="paper-staging-backup"><summary>Staging backup</summary><p>Private snapshot for migration validation. Keep it secure; this does not restore data.</p>
<form action="/staging/database-backup" method="post"><button type="submit">Download staging database backup</button></form></details>'''
            + section + '''</main><script src="/js/pro-ticker-manual.js"></script>
<script src="/js/pro-ticker-release.js"></script><script src="/js/pro-ticker-workspace.js?v=forward-v1"></script><script src="/js/pro-ticker-signals.js?v=research-feedback-v1"></script><script>
window.loadProTickerManualLab(); window.loadProTickerReleasePanel(); window.loadProTickerWorkspace(); window.loadProTickerSignals();</script></body></html>''')

    app.add_api_route('/style.css', main.frontend_style, methods=['GET'])
    app.mount('/js', StaticFiles(directory=str(Path(main.FRONTEND_DIR) / 'js')), name='js')
    app.add_api_route('/auto-trader/pro-ticker-manual-submissions', main.list_auto_trader_pro_ticker_manual_submissions, methods=['GET'])
    app.add_api_route('/auto-trader/pro-ticker-manual-submissions', main.auto_trader_pro_ticker_manual_submissions, methods=['POST'])
    app.add_api_route('/auto-trader/pro-ticker-paper-budget', main.pro_ticker_paper_budget, methods=['GET'])
    from pro_ticker_manual_signals import build_manual_signal_router
    from pro_ticker_research_evidence import capture_research
    app.include_router(build_manual_signal_router(require_session=main.require_app_session,
        quote_fetcher=main.fetch_pro_ticker_manual_signal_quote, analyzer=lambda symbol: main.analyze_symbol(symbol),research_collector=capture_research,execution_context=execution_context))
    from pro_ticker_workspace import build_workspace_router
    app.include_router(build_workspace_router(require_session=main.require_app_session))
    app.include_router(build_paper_release_router(
        require_session=main.require_app_session,
        operator_enabled=lambda: os.getenv('PRO_TICKER_MANUAL_RELEASE_ENABLED', '') == 'true',
        make_paper_reader=main.make_pro_ticker_manual_release_reader,
    ))
    return app
