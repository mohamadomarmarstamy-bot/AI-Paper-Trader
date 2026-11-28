from html import escape
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

_HEADERS = {
    'Cache-Control': 'private, no-store',
    'Vary': 'Cookie',
    'Referrer-Policy': 'no-referrer',
    'X-Robots-Tag': 'noindex, noarchive',
    'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
}
_TEMPLATE = Path(__file__).with_name('templates') / 'mom.html'


def build_router(require_session):
    router = APIRouter()

    @router.get('/i-love-mom', response_class=HTMLResponse, include_in_schema=False)
    def dedication(request: Request):
        try:
            require_session(request)
        except HTTPException as exc:
            exc.headers = {**(exc.headers or {}), **_HEADERS}
            raise
        name = escape(os.environ.get('MOM_DEDICATION_NAME', ''))
        page = _TEMPLATE.read_text(encoding='utf-8').replace('{{dedication_name}}', name)
        return HTMLResponse(page, headers=_HEADERS)

    return router
