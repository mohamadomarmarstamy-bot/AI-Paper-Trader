"""Explicit buy route remains distinct from research and requires owner origin."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

import httpx
from fastapi import FastAPI, HTTPException
import database
from pro_ticker_manual_signals import build_manual_signal_router
from pro_ticker_manual_scanner import ManualScannerContext


class ManualScannerHTTPTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patched = patch.object(database,'DATABASE_PATH',Path(temp.name)/'http.sqlite3')
        patched.start()
        self.addCleanup(patched.stop)
        self.enabled = True
        self.context = ManualScannerContext(request=Mock(),quote=Mock(),risk=Mock(),
            submit=Mock(),close=Mock(),scan=Mock(),enabled=lambda:self.enabled)
        def auth(request):
            if request.headers.get('x-owner') != 'yes':
                raise HTTPException(401)
        self.factory = Mock(return_value=self.context)
        self.app = FastAPI()
        self.app.include_router(build_manual_signal_router(require_session=auth,
            quote_fetcher=lambda s:None,analyzer=lambda s:None,manual_entry_context=self.factory))
        self.payload = {'symbol':'TEST','request_id':str(uuid.uuid4())}
        self.headers = {'x-owner':'yes','origin':'https://test'}

    def request(self, method='POST', path='/auto-trader/pro-ticker/manual-signals/buy-and-scan',
                payload=None, headers=None):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='https://test') as client:
                return await client.request(method,path,headers=self.headers if headers is None else headers,
                    **({'json':self.payload if payload is None else payload} if method in ('POST','PUT') else {}))
        return asyncio.run(run())

    def test_unauthenticated_buy_and_status_are_denied_before_context(self):
        self.assertEqual(self.request(headers={'origin':'https://test'}).status_code,401)
        self.assertEqual(self.request('GET','/auto-trader/pro-ticker/manual-entry-status',headers={}).status_code,401)
        self.factory.assert_not_called()

    def test_cross_origin_and_browser_size_override_cannot_buy(self):
        self.assertEqual(self.request(headers={**self.headers,'origin':'https://evil.test'}).status_code,403)
        self.assertEqual(self.request(payload={**self.payload,'target_usd':100000}).status_code,422)
        self.assertEqual(self.request(payload={**self.payload,'shares':1000}).status_code,422)
        self.context.submit.assert_not_called()

    def test_feature_disabled_rejects_buy_without_saving_review(self):
        self.enabled = False
        self.assertEqual(self.request().status_code,403)
        self.context.request.assert_not_called()
        with database.get_connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_manual_signal_reviews'").fetchone())

    def test_research_only_submission_never_dispatches_when_buy_mode_is_enabled(self):
        with patch('pro_ticker_manual_scanner.submit_entry') as submit:
            response = self.request(path='/auto-trader/pro-ticker/manual-signals')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['orders_submitted'],0)
        submit.assert_not_called()
        self.context.submit.assert_not_called()

    def test_explicit_buy_route_reports_order_state_without_false_research_flags(self):
        entry = {'request_id':self.payload['request_id'],'symbol':'TEST','state':'blocked',
            'target_usd':'2000','shares':None,'reference_ask':None,'order_submitted':False,
            'broker_order_id':None,'reason':'Regular market is closed. No buy is queued.',
            'managed_by':'existing_scanner_rules','paper':True,'version':'owner_manual_scanner_v1'}
        with patch('pro_ticker_manual_scanner.submit_entry',return_value=entry) as submit:
            response = self.request()
        self.assertEqual(response.status_code,200)
        result = response.json()
        self.assertEqual(result['kind'],'owner_manual_scanner_v1')
        self.assertFalse(result['live_trading'])
        self.assertEqual(result['signal']['manual_entry'],entry)
        submit.assert_called_once_with(self.payload['request_id'],'TEST',context=self.context)

    def test_status_has_no_broker_reads_or_broad_scanner_enablement(self):
        response = self.request('GET','/auto-trader/pro-ticker/manual-entry-status')
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()['enabled'])
        self.assertFalse(response.json()['scanner_ready'])
        self.assertFalse(response.json()['broad_scanner_buying_enabled'])
        self.context.request.assert_not_called()


    def test_browser_cannot_override_server_session_or_limit(self):
        self.context.premarket_enabled=lambda:True
        for extra in ({'premarket':True},{'entry_session':'premarket'},{'limit_price':'1.00'}):
            self.assertEqual(self.request(payload={**self.payload,**extra}).status_code,422)
        self.context.submit.assert_not_called()
        self.context.request.assert_not_called()

    def test_server_status_exposes_premarket_mode_without_broker_reads(self):
        self.context.premarket_enabled=lambda:True
        response=self.request('GET','/auto-trader/pro-ticker/manual-entry-status')
        self.assertTrue(response.json()['premarket_enabled'])
        self.assertFalse(response.json()['regular_session_only'])
        self.context.request.assert_not_called()

    def test_manual_account_verification_reads_original_account_without_dedicated_configuration(self):
        self.context.request.return_value={'id':str(uuid.uuid4()),'status':'ACTIVE','trading_blocked':False,'account_blocked':False}
        result=self.request('GET','/auto-trader/pro-ticker/manual-account-status')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['scope'],'original_manual_paper_account')
        self.assertFalse(result.json()['order_submitted'])
        self.context.request.assert_called_once_with('GET','/v2/account')

    def test_entry_settings_reject_cross_origin_and_revision_conflicts(self):
        payload={'expected_revision':0,'settings':{'dollars_per_stock':1000,'stop_loss_percent':1,'take_profit_percent':3}}
        route='/auto-trader/pro-ticker/entry-settings'
        self.assertEqual(self.request('PUT',route,payload=payload,headers={**self.headers,'origin':'https://evil.test'}).status_code,403)
        self.assertEqual(self.request('PUT',route,payload=payload).status_code,200)
        self.assertEqual(self.request('PUT',route,payload=payload).status_code,409)
        self.assertEqual(self.request('GET',route).json()['settings']['dollars_per_stock'],1000)
        self.context.request.assert_not_called()


    def test_stale_order_settings_require_refresh_before_any_entry_dispatch(self):
        self.context.settings_revision=1
        self.assertEqual(self.request(payload={**self.payload,'entry_settings_revision':0}).status_code,409)
        self.assertEqual(self.request().status_code,409)
        self.context.submit.assert_not_called()
        self.context.request.assert_not_called()
