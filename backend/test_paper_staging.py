"""Dedicated staging factory never exposes production order routes or lifespan."""
import asyncio
import importlib
import os
from pathlib import Path
import tempfile
import sqlite3
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
                       {'APP_SESSION_SECRET': ''}, {'APP_ACCESS_PASSWORD': 'short'},
                       {'ALPACA_API_KEY':'synthetic-original'}, {'APCA_API_KEY_ID':'synthetic-original'}):
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
            self.assertEqual(html.count('role="tabpanel"'), 9)
            payload = dict(symbol='AAPL', reference_price='10', shares=2,
                           stop_loss_percent='2', take_profit_percent='4')
            response = self.request(app, 'POST', '/auto-trader/pro-ticker/proposal',
                authenticated=True, headers={'Origin': 'https://staging.test'}, json=payload)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['order_submitted'])
            response = self.request(app, 'POST', '/auto-trader/pro-ticker/proposal',
                authenticated=True, headers={'Origin': 'https://evil.test'}, json=payload)
            self.assertEqual(response.status_code, 403)

    def test_settings_require_signed_owner_and_persist_across_app_recreation(self):
        app = self.app()
        path = '/auto-trader/pro-ticker/settings'
        payload = {'expected_revision': 0, 'settings': {
            'max_allocation_cents': 10000, 'max_stop_risk_cents': 100,
            'discovery_article_limit': 1}}
        self.assertEqual(self.request(app, 'GET', path).status_code, 401)
        self.assertEqual(self.request(app, 'PUT', path, json=payload,
            headers={'Origin': 'https://staging.test'}).status_code, 401)
        self.assertEqual(self.request(app, 'PUT', path, authenticated=True, json=payload,
            headers={'Origin': 'https://other.test'}).status_code, 403)
        with patch.object(self.main, 'alpaca_paper_request', side_effect=AssertionError('unexpected broker call')):
            saved = self.request(app, 'PUT', path, authenticated=True, json=payload,
                headers={'Origin': 'https://staging.test'})
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.json()['revision'], 1)
            recreated = create_staging_app(_data_directory=self.root)
            loaded = self.request(recreated, 'GET', path, authenticated=True)
            self.assertEqual(loaded.json(), saved.json())
            self.assertEqual(self.request(recreated, 'PUT', path, authenticated=True, json=payload,
                headers={'Origin': 'https://staging.test'}).status_code, 409)
            self.assertEqual(self.request(recreated, 'GET', '/auto-trader/settings', authenticated=True).status_code, 404)

    def test_manual_signal_uses_signed_owner_read_providers_and_never_submits_orders(self):
        from datetime import datetime, timezone
        import uuid
        from test_pro_ticker_manual_signals import analysis
        app = self.app(); path = '/auto-trader/pro-ticker/manual-signals'
        payload = {'symbol': 'AAPL', 'request_id': str(uuid.uuid4())}
        self.assertEqual(self.request(app, 'GET', path).status_code, 401)
        with patch.object(self.main, 'alpaca_market_data_request', return_value={'trade': {
                'p': 10.5, 't': datetime.now(timezone.utc).isoformat()}}), \
             patch.object(self.main, 'analyze_symbol', return_value=analysis()) as analyze, \
             patch.object(self.main, 'alpaca_paper_request', side_effect=AssertionError('order access forbidden')) as broker:
            first = self.request(app, 'POST', path, authenticated=True, json=payload,
                headers={'Origin': 'https://staging.test'})
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()['signal']['entry_grade']['grade'], 'A')
            retry = self.request(app, 'POST', path, authenticated=True, json=payload,
                headers={'Origin': 'https://staging.test'})
            self.assertTrue(retry.json()['duplicate']); analyze.assert_called_once()
            self.assertEqual(self.request(app, 'GET', path, authenticated=True).json()['total_count'], 1)
            draft = {'signal_id': payload['request_id'], 'request_id': str(uuid.uuid4()),
                     'shares': 2, 'stop_loss_percent': '5', 'take_profit_percent': '10'}
            self.assertEqual(self.request(app, 'POST', path+'/proposal', json=draft,
                headers={'Origin': 'https://staging.test'}).status_code, 401)
            saved = self.request(app, 'POST', path+'/proposal', authenticated=True, json=draft,
                headers={'Origin': 'https://staging.test'})
            self.assertEqual(saved.status_code, 200)
            self.assertFalse(saved.json()['proposal']['budget_reserved'])
            recreated = self.app()
            history = self.request(recreated, 'GET', path, authenticated=True).json()
            self.assertEqual(history['signals'][0]['proposal'], saved.json()['proposal'])
            retry = self.request(recreated, 'POST', path+'/proposal', authenticated=True, json=draft,
                headers={'Origin': 'https://staging.test'})
            self.assertTrue(retry.json()['duplicate'])
            intent_path='/auto-trader/pro-ticker/order-intents'
            allocation={'request_id':str(uuid.uuid4()),'proposal_id':draft['request_id'],'confirmed':True}
            allocated=self.request(recreated,'POST',intent_path,authenticated=True,json=allocation,
                headers={'Origin':'https://staging.test'})
            self.assertEqual(allocated.status_code,200)
            self.assertTrue(allocated.json()['intent']['budget_reserved'])
            self.assertFalse(allocated.json()['intent']['order_submitted'])
            execution=self.request(recreated,'GET','/auto-trader/pro-ticker/execution-status',authenticated=True).json()
            self.assertFalse(execution['admission_review_enabled']);self.assertFalse(execution['recovery_enabled'])
            self.assertFalse(execution['execution_enabled'])
            for suffix,body in [('admission-review',{'request_id':str(uuid.uuid4())}),('recover',{}),('protection-review',{'request_id':str(uuid.uuid4())})]:
                disabled=self.request(recreated,'POST',intent_path+'/'+allocation['request_id']+'/'+suffix,authenticated=True,
                    json=body,headers={'Origin':'https://staging.test'})
                self.assertEqual(disabled.status_code,403)
            reopened=self.app()
            history=self.request(reopened,'GET',intent_path,authenticated=True).json()
            self.assertEqual(history['intents'][0],allocated.json()['intent'])
            canceled=self.request(reopened,'POST',intent_path+'/'+allocation['request_id']+'/cancel',authenticated=True,
                json={'confirmed':True},headers={'Origin':'https://staging.test'})
            self.assertEqual(canceled.status_code,200)
            self.assertFalse(canceled.json()['intent']['budget_reserved'])
            dataset_path=path+'/dataset'
            self.assertEqual(self.request(reopened,'GET',dataset_path).status_code,401)
            dataset=self.request(reopened,'GET',dataset_path,authenticated=True).json()
            self.assertEqual(dataset['total_count'],1)
            self.assertEqual(dataset['training_ready_samples'],0)
            self.assertEqual(dataset['rows'][0]['entry_features']['score'],4)
            exported=self.request(reopened,'GET',dataset_path+'?format=csv',authenticated=True)
            self.assertEqual(exported.status_code,200)
            self.assertEqual(exported.headers['x-research-window-count'],'1')
            performance_path='/auto-trader/pro-ticker/closed-performance'
            self.assertEqual(self.request(reopened,'GET',performance_path).status_code,401)
            performance=self.request(reopened,'GET',performance_path,authenticated=True).json()
            self.assertEqual(performance['closed_reservations_count'],0)
            self.assertIsNone(performance['gross_pnl_cents']);self.assertIsNone(performance['net_pnl'])
            broker.assert_not_called()

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

    def test_backup_requires_owner_and_same_origin_and_is_absent_from_production(self):
        app = self.app()
        path = '/staging/database-backup'
        with patch('paper_staging_backups.backup_database', side_effect=AssertionError('unauthorized snapshot')) as backup:
            self.assertEqual(self.request(app, 'POST', path, headers={'Origin':'https://staging.test'}).status_code, 401)
            self.assertEqual(self.request(app, 'POST', path, authenticated=True, headers={'Origin':'https://other.test'}).status_code, 403)
            self.assertEqual(self.request(app, 'POST', path, authenticated=True).status_code, 403)
            self.assertEqual(self.request(app, 'GET', path, authenticated=True).status_code, 405)
            backup.assert_not_called()
        self.assertNotIn(path, [getattr(route, 'path', None) for route in self.main.app.routes])

    def test_backup_download_preserves_wal_evidence_and_cleans_temporary_snapshot(self):
        app = self.app()
        import paper_staging_backups as backups
        from backup_paper_database import backup_database
        targets = []
        def snapshot(source, destination):
            targets.append(destination)
            return backup_database(source, destination)
        with sqlite3.connect(database.DATABASE_PATH) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('CREATE TABLE staging_owner_evidence (value TEXT)')
            conn.execute("INSERT INTO staging_owner_evidence VALUES ('retained')")
            conn.commit()
            conn.execute("INSERT INTO staging_owner_evidence VALUES ('uncommitted')")
            with patch.object(backups, 'backup_database', side_effect=snapshot), patch.object(self.main, 'alpaca_paper_request', side_effect=AssertionError('broker forbidden')):
                response = self.request(app, 'POST', '/staging/database-backup', authenticated=True,
                                        headers={'Origin':'https://staging.test'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        self.assertIn('attachment;', response.headers['content-disposition'])
        self.assertTrue(response.content.startswith(b'SQLite format 3'))
        target = self.root / 'download.sqlite3'
        target.write_bytes(response.content)
        with sqlite3.connect(target) as conn:
            self.assertEqual(conn.execute('SELECT value FROM staging_owner_evidence').fetchall(), [('retained',)])
        self.assertEqual(len(targets), 1)
        self.assertFalse(targets[0].parent.exists())

    def test_backup_failure_is_sanitized_and_allows_next_attempt(self):
        app = self.app()
        with patch('paper_staging_backups.backup_database', side_effect=RuntimeError('private-path-and-secret')):
            for _ in range(2):
                response = self.request(app, 'POST', '/staging/database-backup', authenticated=True,
                                        headers={'Origin':'https://staging.test'})
                self.assertEqual(response.status_code, 503)
                self.assertNotIn('private-path', response.text)

    def test_backup_busy_request_is_rejected_without_another_snapshot(self):
        app = self.app()
        import paper_staging_backups as backups
        self.assertTrue(backups._download_slot.acquire(blocking=False))
        try:
            with patch.object(backups, 'backup_database', side_effect=AssertionError('concurrent snapshot')):
                response = self.request(app, 'POST', '/staging/database-backup', authenticated=True,
                                        headers={'Origin':'https://staging.test'})
                self.assertEqual(response.status_code, 409)
        finally:
            backups._download_slot.release()

    def test_interrupted_backup_delivery_cleans_file_and_releases_slot(self):
        self.app()
        database.initialize_database()
        import paper_staging_backups as backups
        response = backups.staging_backup_response(database.DATABASE_PATH)
        target = Path(response.path)
        async def run():
            async def receive():
                return {'type':'http.disconnect'}
            async def send(message):
                raise RuntimeError('connection interrupted')
            await response({'type':'http','method':'POST','headers':[],
                            'asgi':{'spec_version':'2.4'}}, receive, send)
        with self.assertRaises(RuntimeError):
            asyncio.run(run())
        self.assertFalse(target.parent.exists())
        self.assertTrue(backups._download_slot.acquire(blocking=False))
        backups._download_slot.release()

    def test_cross_origin_login_and_research_writes_are_blocked(self):
        app = self.app()
        for path in ('/login', '/auto-trader/pro-ticker-manual-submissions'):
            with self.subTest(path=path):
                response = self.request(app, 'POST', path, authenticated=True,
                    headers={'Origin': 'https://other.test'}, json={'symbols': 'AAPL'})
                self.assertEqual(response.status_code, 403)


if __name__ == '__main__':
    unittest.main()
