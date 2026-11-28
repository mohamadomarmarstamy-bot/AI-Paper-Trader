from html import escape
import base64
import secrets
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

_HEADERS = {
    'Cache-Control': 'private, no-store',
    'Vary': 'Cookie',
    'Referrer-Policy': 'no-referrer',
    'X-Robots-Tag': 'noindex, noarchive',
    'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; media-src data:; frame-ancestors 'none'; base-uri 'none'",
}
_TEMPLATE = Path(__file__).with_name('templates') / 'mom.html'


_SIREN = base64.b64encode(_TEMPLATE.with_name('cruiser.wav').read_bytes()).decode('ascii')


_VOICE = base64.b64encode(_TEMPLATE.with_name('heart.mp3').read_bytes()).decode('ascii')


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
        nonce = secrets.token_urlsafe(24)
        headers = dict(_HEADERS)
        headers['Content-Security-Policy'] += f"; script-src 'nonce-{nonce}'"
        page = _TEMPLATE.read_text(encoding='utf-8').replace('{{nonce}}', nonce).replace('{{voice_audio}}', _VOICE).replace('{{siren_audio}}', _SIREN).replace('{{dedication_name}}', name)
        return HTMLResponse(page, headers=headers)

    return router
