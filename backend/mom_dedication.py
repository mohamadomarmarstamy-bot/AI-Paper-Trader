from html import escape
import base64
import hashlib
import hmac
import re
import sqlite3
import time
from contextlib import closing
from urllib.parse import parse_qs

import database
import secrets
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

_HEADERS = {
    'Cache-Control': 'private, no-store',
    'Vary': 'Cookie',
    'Referrer-Policy': 'no-referrer',
    'X-Robots-Tag': 'noindex, noarchive',
    'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; media-src data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}
_TEMPLATE = Path(__file__).with_name('templates') / 'mom.html'


_SIREN = base64.b64encode(_TEMPLATE.with_name('cruiser.wav').read_bytes()).decode('ascii')


_VOICE = base64.b64encode(_TEMPLATE.with_name('heart.mp3').read_bytes()).decode('ascii')


PAGE = '/i-love-mom'
COOKIE = '__Secure-mom_unlock'
TTL = 600
HASH = re.compile(r'scrypt\$([0-9a-f]{32})\$([0-9a-f]{64})')


def reserve_attempt():
    # A single rolling window shared by all app workers using the service volume.
    path = database.DATABASE_PATH.with_name('dedication-attempts.sqlite3')
    with closing(sqlite3.connect(path, timeout=0.5)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS attempts (at INTEGER NOT NULL)')
        now = int(time.time())
        db.execute('DELETE FROM attempts WHERE at <= ?', (now - TTL,))
        if db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] >= 5:
            return False
        db.execute('INSERT INTO attempts VALUES (?)', (now,))
        return True


def build_router(require_session, session_cookie, app_secret):
    router = APIRouter()

    def context(request):
        try:
            require_session(request)
        except HTTPException as exc:
            exc.headers = {**(exc.headers or {}), **_HEADERS}
            raise
        version = os.getenv('MOM_DEDICATION_PASSCODE_HASH', '')
        match = HASH.fullmatch(version)
        secret = app_secret()
        if not match or not secret:
            raise HTTPException(503, 'Dedication is unavailable.', headers=_HEADERS)
        key = hmac.digest(secret.encode(), b'dedication-v1', 'sha256')
        binding = (version + '|' + request.cookies[session_cookie]).encode()
        return match, hmac.digest(key, binding, 'sha256')

    def signature(key, purpose):
        return hmac.digest(key, purpose.encode(), 'sha256').hex()

    def lock_screen(key, message='', status=200):
        page = _TEMPLATE.with_name('mom_lock.html').read_text()
        return HTMLResponse(page.replace('{{csrf}}', signature(key, 'form')).replace('{{message}}', message),
                            status_code=status, headers=_HEADERS)

    async def form(request, key):
        if (request.headers.get('sec-fetch-site') in ('cross-site', 'same-site')
                or request.headers.get('content-type', '').split(';')[0] != 'application/x-www-form-urlencoded'):
            raise HTTPException(403, headers=_HEADERS)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 256:
                raise HTTPException(413, headers=_HEADERS)
            body.extend(chunk)
        try:
            fields = parse_qs(body.decode('ascii'), strict_parsing=True, max_num_fields=2)
        except ValueError:
            raise HTTPException(400, headers=_HEADERS)
        if fields.get('csrf') != [signature(key, 'form')]:
            raise HTTPException(403, headers=_HEADERS)
        return fields

    def redirect():
        return RedirectResponse(PAGE, 303, headers=_HEADERS)

    @router.post(PAGE + '/unlock', include_in_schema=False)
    async def unlock(request: Request):
        match, key = context(request)
        fields = await form(request, key)
        values = fields.get('passcode', [])
        try:
            allowed = await run_in_threadpool(reserve_attempt)
        except sqlite3.Error:
            raise HTTPException(503, 'Dedication is unavailable.', headers=_HEADERS)
        if not allowed:
            return lock_screen(key, 'Attempt limit reached. Try again in ten minutes.', 429)
        if len(values) == 1 and re.fullmatch(r'[0-9]{1,64}', values[0]):
            digest = await run_in_threadpool(hashlib.scrypt, values[0].encode(),
                                             salt=bytes.fromhex(match[1]), n=16384, r=8, p=1, dklen=32)
            if hmac.compare_digest(digest, bytes.fromhex(match[2])):
                expires = str(int(time.time()) + TTL)
                response = redirect()
                response.set_cookie(COOKIE, expires + '.' + signature(key, 'unlock|' + expires),
                                    max_age=TTL, path=PAGE, secure=True, httponly=True, samesite='strict')
                return response
        return lock_screen(key, 'Incorrect passcode.', 403)

    @router.post(PAGE + '/lock', include_in_schema=False)
    async def lock(request: Request):
        _, key = context(request)
        await form(request, key)
        response = redirect()
        response.delete_cookie(COOKIE, path=PAGE, secure=True, httponly=True, samesite='strict')
        return response

    @router.get(PAGE, response_class=HTMLResponse, include_in_schema=False)
    def dedication(request: Request):
        _, key = context(request)
        token = re.fullmatch(r'([0-9]{1,12})\.([0-9a-f]{64})', request.cookies.get(COOKIE, ''))
        if (not token or not 0 < int(token[1]) - int(time.time()) <= TTL
                or not hmac.compare_digest(token[2], signature(key, 'unlock|' + token[1]))):
            return lock_screen(key)
        name = escape(os.environ.get('MOM_DEDICATION_NAME', ''))
        nonce = secrets.token_urlsafe(24)
        headers = dict(_HEADERS)
        headers['Content-Security-Policy'] += f"; script-src 'nonce-{nonce}'"
        page = _TEMPLATE.read_text(encoding='utf-8').replace('{{csrf}}', signature(key, 'form')).replace('{{nonce}}', nonce).replace('{{voice_audio}}', _VOICE).replace('{{siren_audio}}', _SIREN).replace('{{dedication_name}}', name)
        return HTMLResponse(page, headers=headers)

    return router
