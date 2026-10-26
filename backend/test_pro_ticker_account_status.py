"""Account verification is authenticated, sanitized and read-only."""
import unittest
from unittest.mock import Mock
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pro_ticker_execution_routes import build_paper_execution_router
from pro_ticker_broker_admission import PaperEvidenceContext
from pro_ticker_paper_gateway import PAPER_URL

ACCOUNT='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
class AccountStatusTests(unittest.TestCase):
    def setUp(self):
        self.gateway=Mock(paper=True,base_url=PAPER_URL,account_id=ACCOUNT)
        self.gateway.read_account.return_value={'id':ACCOUNT,'status':'ACTIVE','currency':'USD',
            'trading_blocked':False,'account_blocked':False,'trade_suspended_by_user':False,'secret':'never-return'}
        self.factory=Mock(return_value=self.gateway)
        self.context=PaperEvidenceContext(ACCOUNT,self.factory,True)
    def client(self,context=True,authorized=True):
        app=FastAPI()
        def auth(request):
            if not authorized:raise HTTPException(401,'Sign in required.')
        app.include_router(build_paper_execution_router(require_session=auth,context=self.context if context else None))
        return TestClient(app)
    def test_verified_identity_without_identifiers_or_broker_writes(self):
        response=self.client().get('/auto-trader/pro-ticker/account-status')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(response.json(),dict(paper=True,account_verified=True,order_execution_authorized=False,
            account_active=True,currency_usd=True,trading_unblocked=True))
        self.assertNotIn(ACCOUNT,response.text);self.assertNotIn('never-return',response.text)
        self.assertEqual([c[0] for c in self.gateway.method_calls],['read_account'])
    def test_auth_and_configuration_checked_before_network(self):
        self.assertEqual(self.client(authorized=False).get('/auto-trader/pro-ticker/account-status').status_code,401)
        self.assertEqual(self.client(context=False).get('/auto-trader/pro-ticker/account-status').status_code,403)
        self.factory.assert_not_called()
    def test_mismatch_and_provider_failure_are_sanitized(self):
        for account in ({'id':'other'},None):
            self.gateway.read_account.return_value=account
            self.assertEqual(self.client().get('/auto-trader/pro-ticker/account-status').status_code,503)
        self.gateway.read_account.side_effect=RuntimeError('secret-provider-detail')
        response=self.client().get('/auto-trader/pro-ticker/account-status')
        self.assertEqual(response.status_code,503);self.assertNotIn('secret-provider-detail',response.text)
    def test_unknown_readiness_does_not_appear_ready(self):
        self.gateway.read_account.return_value={'id':ACCOUNT}
        result=self.client().get('/auto-trader/pro-ticker/account-status').json()
        self.assertTrue(result['account_verified']);self.assertFalse(result['account_active'])
        self.assertFalse(result['currency_usd']);self.assertFalse(result['trading_unblocked'])
