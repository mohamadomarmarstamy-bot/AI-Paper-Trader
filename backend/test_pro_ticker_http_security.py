"""External HTTPS origins behind an HTTP proxy retain authentication and CSRF."""
import asyncio
import hashlib
import hmac
import os
import time
import unittest
import uuid
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from pro_ticker_manual_signals import build_manual_signal_router
from test_pro_ticker_production_auth_http import production_auth

HOST = 'paper.example.com'
ORIGIN = 'https://' + HOST


class PublicOriginTests(unittest.TestCase):
    def request(self, origin=ORIGIN, *, public_origin=ORIGIN, host=HOST,
                authenticated=True, forwarded=None):
        auth = production_auth()
        app = FastAPI()
        app.include_router(build_manual_signal_router(
            require_session=auth['require_app_session'],
            quote_fetcher=lambda s: None, analyzer=lambda s: None))
        stamp = str(int(time.time()))
        signature = hmac.new(b'offline-http-test-secret', stamp.encode(), hashlib.sha256).hexdigest()
        headers = {'host': host}
        if origin is not None:
            headers['origin'] = origin
        if authenticated:
            headers['cookie'] = 'ai_paper_trader_session=' + stamp + '.' + signature
        if forwarded:
            headers.update(forwarded)

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='http://' + HOST) as client:
                return await client.post('/auto-trader/pro-ticker/manual-signals',
                    headers=headers, json={'symbol': 'AAPL', 'request_id': str(uuid.uuid4())})

        with patch.dict(os.environ, {'APP_PUBLIC_ORIGIN': public_origin}), patch(
                'pro_ticker_manual_signals.submit_manual_signal',
                return_value={'paper': True, 'orders_submitted': 0}) as save:
            response = asyncio.run(run())
            return response.status_code, save.call_count

    def test_signed_same_origin_https_request_saves_behind_http_proxy(self):
        self.assertEqual(self.request(), (200, 1))

    def test_cross_site_missing_null_wrong_scheme_port_and_host_remain_blocked(self):
        for origin in (None, 'null', 'https://other.example.com', 'http://' + HOST,
                       ORIGIN + ':444', ORIGIN + '/', ORIGIN + '.evil.test'):
            with self.subTest(origin=origin):
                self.assertEqual(self.request(origin), (403, 0))
        self.assertEqual(self.request(host='other.example.com'), (403, 0))

    def test_forwarded_headers_cannot_choose_the_accepted_origin(self):
        self.assertEqual(self.request('https://evil.test', forwarded={
            'x-forwarded-host': 'evil.test', 'x-forwarded-proto': 'https',
            'forwarded': 'host=evil.test;proto=https'}), (403, 0))

    def test_public_origin_never_replaces_owner_authentication(self):
        self.assertEqual(self.request(authenticated=False), (401, 0))

    def test_unconfigured_direct_http_requests_still_work(self):
        self.assertEqual(self.request('http://' + HOST, public_origin=''), (200, 1))
        self.assertEqual(self.request(public_origin=''), (403, 0))

    def test_invalid_server_configuration_fails_closed(self):
        for value in ('http://' + HOST, ORIGIN + '/', ORIGIN + '/path',
                      ORIGIN + '?q=1', ORIGIN + '#fragment',
                      'https://user:pass@' + HOST, ORIGIN + ',https://evil.test',
                      ' ' + ORIGIN):
            with self.subTest(value=value):
                self.assertEqual(self.request(value, public_origin=value), (403, 0))
