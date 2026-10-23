"""Real-time reference diagnostics never reserve, dispatch or alter freshness policy."""
from datetime import datetime,timezone
import unittest
from unittest.mock import Mock,patch
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from pro_ticker_broker_admission import PaperEvidenceContext
from pro_ticker_execution_routes import build_paper_execution_router
from pro_ticker_market_reference import market_reference_snapshot
from pro_ticker_paper_gateway import PAPER_URL,AlpacaPaperEvidenceGateway

ACCOUNT='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
class MarketReferenceTests(unittest.TestCase):
    def setUp(self):
        self.now=1792780000.0
        self.gateway=Mock(paper=True,base_url=PAPER_URL,account_id=ACCOUNT)
        self.gateway.read_account.return_value={'id':ACCOUNT,'secret':'must-not-return'}
        self.factory=Mock(return_value=self.gateway)
        self.context=PaperEvidenceContext(ACCOUNT,self.factory,True)
        self.trade(1)
    def trade(self,age):
        self.gateway.read_trade.return_value={'p':4.115,'t':datetime.fromtimestamp(self.now-age,timezone.utc).isoformat(),'private':'must-not-return'}
    def snapshot(self):
        with patch('database.get_connection',side_effect=AssertionError('No ledger access allowed')):
            return market_reference_snapshot('OSUR',context=self.context,clock=lambda:self.now)
    def client(self,authorized=True,configured=True):
        def auth(request):
            if not authorized:raise HTTPException(401,'Sign in required.')
        app=FastAPI();app.include_router(build_paper_execution_router(require_session=auth,context=self.context if configured else None))
        return TestClient(app)
    def test_both_actual_policy_boundaries_remain_unchanged(self):
        for age,allocation,admission in [(0,True,True),(30,True,True),(30.001,True,False),(90,True,False),(90.001,False,False)]:
            self.trade(age);result=self.snapshot()
            with self.subTest(age=age):
                self.assertAlmostEqual(result['reference']['age_seconds'],age,places=5)
                self.assertIs(result['allocation_reference_fresh'],allocation)
                self.assertIs(result['admission_reference_fresh'],admission)
                self.assertFalse(result['order_execution_authorized'])
    def test_invalid_missing_and_future_data_never_appear_fresh(self):
        stamp=datetime.fromtimestamp(self.now,timezone.utc).isoformat()
        for raw in (None,{}, {'p':float('nan'),'t':stamp},{'p':True,'t':stamp},
                    {'p':4,'t':'invalid'},{'p':4,'t':datetime.fromtimestamp(self.now+1,timezone.utc).isoformat()}):
            self.gateway.read_trade.return_value=raw;result=self.snapshot()
            self.assertEqual(result['reference']['status'],'unavailable')
            self.assertFalse(result['allocation_reference_fresh']);self.assertFalse(result['admission_reference_fresh'])
    def test_auth_configuration_and_symbol_validation_precede_provider(self):
        path='/auto-trader/pro-ticker/market-reference?symbol=OSUR'
        self.assertEqual(self.client(authorized=False).get(path).status_code,401)
        self.assertEqual(self.client(configured=False).get(path).status_code,403)
        self.assertEqual(self.client().get(path.replace('OSUR','bad/path')).status_code,422)
        self.factory.assert_not_called()
    def test_http_response_is_sanitized_and_not_cached(self):
        with patch('pro_ticker_market_reference.time.time',return_value=self.now):
            response=self.client().get('/auto-trader/pro-ticker/market-reference?symbol=OSUR')
        self.assertEqual(response.status_code,200);self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertNotIn(ACCOUNT,response.text);self.assertNotIn('must-not-return',response.text)
        self.assertEqual([call[0] for call in self.gateway.method_calls],['read_account','read_trade'])
    def test_wrong_account_and_provider_failure_are_sanitized(self):
        self.gateway.read_account.return_value={'id':'other'}
        response=self.client().get('/auto-trader/pro-ticker/market-reference?symbol=OSUR')
        self.assertEqual(response.status_code,503);self.gateway.read_trade.assert_not_called()
        self.gateway.read_account.side_effect=RuntimeError('provider-secret')
        response=self.client().get('/auto-trader/pro-ticker/market-reference?symbol=OSUR')
        self.assertEqual(response.status_code,503);self.assertNotIn('provider-secret',response.text)
    def test_real_adapter_calls_only_fixed_account_and_market_gets(self):
        def get(url,**kwargs):
            self.assertFalse(kwargs['allow_redirects']);self.assertEqual(kwargs['timeout'],3.0)
            value={'id':ACCOUNT} if url==PAPER_URL+'/v2/account' else {'symbol':'OSUR','trade':self.gateway.read_trade.return_value}
            return Mock(status_code=200,json=lambda:value)
        http_get=Mock(side_effect=get)
        gateway=AlpacaPaperEvidenceGateway(account_id=ACCOUNT,key_id='synthetic-id',secret_key='synthetic-secret',http_get=http_get)
        context=PaperEvidenceContext(ACCOUNT,lambda:gateway,True)
        result=market_reference_snapshot('OSUR',context=context,clock=lambda:self.now)
        self.assertTrue(result['admission_reference_fresh'])
        self.assertEqual([call.args[0] for call in http_get.call_args_list],[PAPER_URL+'/v2/account','https://data.alpaca.markets/v2/stocks/OSUR/trades/latest'])
