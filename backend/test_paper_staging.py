"""Dedicated staging factory never exposes production order routes or lifespan."""
import asyncio
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
import database
from paper_staging import create_staging_app, validate_staging_config


class IsolatedStagingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'staging-data'
        self.env = patch.dict(os.environ, {
            'PAPER_STAGING_MODE': 'true', 'DATABASE_PATH': str(self.root / 'trader.db'),
            'AUTO_TRADER_START_ENABLED': 'false', 'PRO_TICKER_MANUAL_RELEASE_ENABLED': 'true',
            'APP_ACCESS_PASSWORD': 'isolated-staging-test-password',
            'APP_SESSION_SECRET': 'isolated-staging-test-session-secret-not-real',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        db_patch = patch.object(database, 'DATABASE_PATH', self.root / 'trader.db')
        db_patch.start()
        self.addCleanup(db_patch.stop)

    def app(self):
        app = create_staging_app(_data_directory=self.root)
        self.main = importlib.import_module('main')
        for name, value in [('APP_SESSION_SECRET', os.environ['APP_SESSION_SECRET']),
                            ('APP_ACCESS_PASSWORD', os.environ['APP_ACCESS_PASSWORD'])]:
            patcher = patch.object(self.main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return app

    def request(self, app, method, path, *, authenticated=False, base_url='https://staging.test', **kwargs):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url) as client:
                if authenticated:
                    client.cookies.set(self.main.APP_SESSION_COOKIE, self.main.create_app_session_token())
                return await client.request(method, path, **kwargs)
        return asyncio.run(run())

    def test_required_configuration_fails_before_application_import(self):
        for update in ({'PAPER_STAGING_MODE': ''}, {'DATABASE_PATH': '/data/trader.db'},
                       {'AUTO_TRADER_START_ENABLED': 'true'}, {'AUTO_TRADER_START_ENABLED': ''},
                       {'APP_SESSION_SECRET': ''}, {'APP_ACCESS_PASSWORD': 'short'}):
            with self.subTest(update=update), patch.dict(os.environ, update):
                with self.assertRaises(RuntimeError):
                    validate_staging_config(data_directory=self.root)

    def test_cached_database_cannot_point_at_production(self):
        with patch.object(database, 'DATABASE_PATH', Path('/data/trader.db')):
            with self.assertRaisesRegex(RuntimeError, 'Loaded database'):
                validate_staging_config(data_directory=self.root)

    def test_health_identifies_isolation_and_no_broker_writes(self):
        app = self.app()
        response = self.request(app, 'GET', '/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['environment'], 'isolated-staging')
        self.assertFalse(response.json()['background_jobs_enabled'])
        self.assertFalse(response.json()['broker_order_submission_enabled'])

    def test_staging_lifespan_never_enters_production_lifespan(self):
        app = self.app()
        async def run():
            with patch.object(self.main.app.router, 'lifespan_context', side_effect=AssertionError('production lifespan started')) as production:
                async with app.router.lifespan_context(app):
                    self.assertEqual(len(asyncio.all_tasks()), 1)
                production.assert_not_called()
        asyncio.run(run())

    def test_workspace_and_hypothetical_proposal_use_real_owner_auth_without_broker_calls(self):
        app = self.app()
        path = '/auto-trader/pro-ticker/workspace'
        self.assertEqual(self.request(app, 'GET', path).status_code, 401)
        with patch.object(self.main, 'alpaca_paper_request', side_effect=AssertionError('unexpected broker call')):
            response = self.request(app, 'GET', path, authenticated=True)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['scope'], 'pro_ticker_strategy_ledger')
            html = self.request(app, 'GET', '/', authenticated=True).text
            self.assertIn('/js/pro-ticker-workspace.js', html)
            self.assertEqual(html.count('role="tabpanel"'), 8)
            payload = dict(symbol='AAPL', reference_price='10', shares=2,
                           stop_loss_percent='2', take_profit_percent='4')
            response = self.request(app, 'POST', '/auto-trader/pro-ticker/proposal',
                authenticated=True, headers={'Origin': 'https://staging.test'}, json=payload)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['order_submitted'])
            response = self.request(app, 'POST', '/auto-trader/pro-ticker/proposal',
                authenticated=True, headers={'Origin': 'https://evil.test'}, json=payload)
            self.assertEqual(response.status_code, 403)

    def test_order_account_and_email_controls_are_absent(self):
        app = self.app()
        with patch.object(self.main, 'alpaca_paper_request', side_effect=AssertionError('unexpected broker request')):
            for method, path in [('POST', '/auto-trader/positions/close-all'), ('POST', '/auto-trader/run'),
                                 ('POST', '/auto-trader/report/test-email'), ('GET', '/account/live'),
                                 ('GET', '/auto-trader/status'), ('GET', '/docs'), ('GET', '/openapi.json')]:
                with self.subTest(path=path):
                    response = self.request(app, method, path, authenticated=True,
                                            headers={'Origin': 'https://staging.test'})
                    self.assertEqual(response.status_code, 404)

    def test_login_and_owner_research_page(self):
        app = self.app()
        self.assertEqual(self.request(app, 'GET', '/').status_code, 303)
        self.assertEqual(self.request(app, 'GET', '/auto-trader/pro-ticker-paper/release-status').status_code, 401)
        login = self.request(app, 'POST', '/login', data={'password': os.environ['APP_ACCESS_PASSWORD']},
                             headers={'Origin': 'https://staging.test'})
        self.assertEqual(login.status_code, 200)
        self.assertIn('Secure', login.headers['set-cookie'])
        response = self.request(app, 'GET', '/', authenticated=True)
        self.assertIn('Isolated PAPER staging', response.text)
        self.assertIn('/js/pro-ticker-release.js', response.text)
        self.assertNotIn('js/account.js', response.text)
        status = self.request(app, 'GET', '/auto-trader/pro-ticker-paper/release-status', authenticated=True).json()
        self.assertTrue(status['manual_release_enabled'])
        self.assertFalse(status['automatic_release_enabled'])

    def test_https_proxy_preserves_origin_checks_and_secure_login(self):
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
        app = ProxyHeadersMiddleware(self.app(), trusted_hosts='*')
        response = self.request(app, 'POST', '/login', base_url='http://staging.test',
            data={'password': os.environ['APP_ACCESS_PASSWORD']},
            headers={'Origin': 'https://staging.test', 'X-Forwarded-Proto': 'https'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Secure', response.headers['set-cookie'])
        blocked = self.request(app, 'POST', '/login', base_url='http://staging.test',
            data={'password': os.environ['APP_ACCESS_PASSWORD']},
            headers={'Origin': 'https://other.test', 'X-Forwarded-Proto': 'https'})
        self.assertEqual(blocked.status_code, 403)

    def test_cross_origin_login_and_research_writes_are_blocked(self):
        app = self.app()
        for path in ('/login', '/auto-trader/pro-ticker-manual-submissions'):
            with self.subTest(path=path):
                response = self.request(app, 'POST', path, authenticated=True,
                    headers={'Origin': 'https://other.test'}, json={'symbols': 'AAPL'})
                self.assertEqual(response.status_code, 403)


if __name__ == '__main__':
    unittest.main()
