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

    def request(self, app, method, path, *, authenticated=False, **kwargs):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://staging.test') as client:
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

    def test_cross_origin_login_and_research_writes_are_blocked(self):
        app = self.app()
        for path in ('/login', '/auto-trader/pro-ticker-manual-submissions'):
            with self.subTest(path=path):
                response = self.request(app, 'POST', path, authenticated=True,
                    headers={'Origin': 'https://other.test'}, json={'symbols': 'AAPL'})
                self.assertEqual(response.status_code, 403)


if __name__ == '__main__':
    unittest.main()
