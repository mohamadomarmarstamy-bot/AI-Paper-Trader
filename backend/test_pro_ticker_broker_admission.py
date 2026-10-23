"""Dedicated PAPER evidence, consistent reviews, default-off HTTP controls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import datetime,timezone
import json
import unittest
from unittest.mock import Mock
import uuid
import httpx
from fastapi import FastAPI,HTTPException
import database
import test_pro_ticker_order_intents as fixtures
from pro_ticker_broker_admission import PaperEvidenceContext,execution_status,review_paper_admission
from pro_ticker_paper_gateway import AlpacaPaperEvidenceGateway,PAPER_URL,PaperGatewayUnavailable
from pro_ticker_manual_signals import build_manual_signal_router
from pro_ticker_workspace import workspace_snapshot

ACCOUNT='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


class BrokerAdmissionTests(unittest.TestCase):
    make_draft=fixtures.OrderIntentTests.make_draft
    prepare=fixtures.OrderIntentTests.prepare

    def setUp(self):
        fixtures.OrderIntentTests.setUp(self)
        self.intent=self.prepare()['intent']
        self.snapshot={'account':{'id':ACCOUNT,'status':'ACTIVE','currency':'USD','buying_power':'1000',
            'trading_blocked':False,'account_blocked':False,'trade_suspended_by_user':False},
            'clock':{'is_open':True,'timestamp':datetime.fromtimestamp(self.now-1,timezone.utc).isoformat()},
            'asset':{'symbol':'AAPL','status':'active','class':'us_equity','tradable':True},
            'positions':[],'orders':[],'quote':self.quote.return_value}
        self.gateway=Mock();self.gateway.paper=True;self.gateway.base_url=PAPER_URL;self.gateway.account_id=ACCOUNT
        self.gateway.read_snapshot.side_effect=lambda symbol:deepcopy(self.snapshot)
        self.gateway.read_account.side_effect=lambda:deepcopy(self.snapshot['account'])
        self.context=PaperEvidenceContext(ACCOUNT,lambda:self.gateway,True)
        self.review_payload={'request_id':str(uuid.uuid4())}

    def review(self,**kwargs):
        return review_paper_admission(self.intent['id'],self.review_payload,context=self.context,**kwargs)

    def test_all_checks_passing_still_cannot_authorize_or_submit_order(self):
        data=self.review();self.assertTrue(data['checks_passed'])
        self.assertFalse(data['execution_enabled']);self.assertFalse(data['order_submitted']);self.assertFalse(data['eligible_for_manual_submission'])
        self.assertIn('protective_orders_not_verified',data['submission_blockers'])
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)
        self.assertNotIn(ACCOUNT,json.dumps(data));self.gateway.submit.assert_not_called()

    def test_duplicate_review_does_not_refetch_or_cherry_pick_evidence(self):
        first=self.review();self.gateway.read_snapshot.side_effect=AssertionError('no refetch')
        duplicate=self.review();self.assertTrue(duplicate['duplicate'])
        self.assertEqual(first['checks'],duplicate['checks']);self.assertEqual(first['review_id'],duplicate['review_id'])
        expired=self.review(clock=lambda:self.now+100)
        self.assertTrue(expired['expired']);self.assertFalse(expired['eligible_for_manual_submission'])

    def test_concurrent_review_id_has_one_immutable_record(self):
        with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(lambda _:self.review(),range(3)))
        self.assertEqual(sum(not r['duplicate'] for r in results),1)
        with closing(database.get_connection()) as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM pro_ticker_admission_reviews').fetchone()[0],1)

    def test_each_missing_ambiguous_or_unsafe_snapshot_blocks_review(self):
        original=deepcopy(self.snapshot)
        changes=[('account',{'id':'other','status':'ACTIVE','currency':'USD','buying_power':'1000'}),
            ('account',{**original['account'],'trading_blocked':1}),('account',{**original['account'],'buying_power':'NaN'}),
            ('account',{**original['account'],'buying_power':'1'}),('clock',{'is_open':1,'timestamp':original['clock']['timestamp']}),
            ('asset',{**original['asset'],'tradable':1}),('asset',{**original['asset'],'symbol':'MSFT'}),
            ('positions',None),('positions',[{'symbol':'AAPL','qty':'1'}]),
            ('positions',[{'symbol':'AAPL','qty':'NaN'}]),('orders',[{'id':'open','symbol':'AAPL','qty':'1'}]),
            ('orders',[{'id':str(i),'symbol':'MSFT','qty':'1'} for i in range(500)]),
            ('orders',[{'id':'parent','symbol':'MSFT','qty':'1','legs':[{'id':'child','symbol':'AAPL','qty':'1'}]}]),
            ('quote',{'p':True,'t':original['quote']['t']}),('quote',{**original['quote'],'p':11})]
        for field,value in changes:
            self.snapshot=deepcopy(original);self.snapshot[field]=value;self.review_payload={'request_id':str(uuid.uuid4())}
            with self.subTest(field=field,value=str(value)[:60]):
                data=self.review();self.assertFalse(data['checks_passed']);self.assertFalse(data['execution_enabled'])
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)

    def test_stale_future_clock_and_slow_fetch_do_not_pass(self):
        for offset in [-100,100]:
            self.snapshot['clock']['timestamp']=datetime.fromtimestamp(self.now+offset,timezone.utc).isoformat()
            self.review_payload={'request_id':str(uuid.uuid4())};self.assertFalse(self.review()['checks_passed'])
        self.snapshot['clock']['timestamp']=datetime.fromtimestamp(self.now-1,timezone.utc).isoformat()
        self.review_payload={'request_id':str(uuid.uuid4())}
        data=self.review(clock=Mock(side_effect=[self.now,self.now+31,self.now+31]));self.assertFalse(data['checks_passed'])

    def test_current_settings_and_ledger_changes_invalidate_review(self):
        from pro_ticker_settings import save_settings
        def fetch(symbol):
            save_settings({'expected_revision':0,'settings':{'max_allocation_cents':1000,'max_stop_risk_cents':5000000,'discovery_article_limit':5}})
            return deepcopy(self.snapshot)
        self.gateway.read_snapshot.side_effect=fetch
        data=self.review();self.assertFalse(data['checks_passed'])
        failed={c['code'] for c in data['checks'] if not c['passed']}
        self.assertIn('ledger_unchanged_during_review',failed);self.assertIn('current_allocation_limit',failed)

    def test_unapproved_wrong_account_or_nonpaper_gateway_rejected(self):
        self.assertFalse(execution_status()['admission_review_enabled'])
        self.assertFalse(execution_status(PaperEvidenceContext(ACCOUNT,lambda:self.gateway,False))['admission_review_enabled'])
        for field,value in [('paper',1),('paper',False),('base_url','https://api.alpaca.markets'),('account_id',str(uuid.uuid4()))]:
            original=getattr(self.gateway,field);setattr(self.gateway,field,value)
            with self.subTest(field=field),self.assertRaises(ValueError):self.review()
            setattr(self.gateway,field,original)

    def test_browser_cannot_supply_admission_evidence(self):
        for extra in [{'buying_power':999999},{'account':self.snapshot['account']},{'execute':True}]:
            with self.assertRaises(ValueError):review_paper_admission(self.intent['id'],{**self.review_payload,**extra},context=self.context)

    def test_http_defaults_off_auth_origin_and_sanitized_failure(self):
        def auth(request):
            if request.headers.get('x-owner')!='yes':raise HTTPException(401)
        app=FastAPI();app.include_router(build_manual_signal_router(require_session=auth,quote_fetcher=self.quote,analyzer=lambda _:None))
        enabled=FastAPI();enabled.include_router(build_manual_signal_router(require_session=auth,quote_fetcher=self.quote,analyzer=lambda _:None,execution_context=self.context))
        async def run():
            for target,configured in [(app,False),(enabled,True)]:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=target),base_url='https://test') as c:
                    headers={'x-owner':'yes','Origin':'https://test'};path='/auto-trader/pro-ticker/order-intents/'+self.intent['id']+'/admission-review'
                    self.assertEqual((await c.get('/auto-trader/pro-ticker/execution-status')).status_code,401)
                    status=(await c.get('/auto-trader/pro-ticker/execution-status',headers=headers)).json()
                    self.assertEqual(status['admission_review_enabled'],configured);self.assertFalse(status['execution_enabled'])
                    self.assertEqual((await c.post(path,json=self.review_payload)).status_code,401)
                    self.assertEqual((await c.post(path,json=self.review_payload,headers={**headers,'Origin':'https://evil'})).status_code,403)
                    response=await c.post(path,json=self.review_payload,headers=headers)
                    self.assertEqual(response.status_code,200 if configured else 403)
                    self.assertEqual((await c.post(path.replace('/admission-review','/submit'),json={},headers=headers)).status_code,403)
                    if configured:
                        self.gateway.read_snapshot.side_effect=RuntimeError('secret-key')
                        response=await c.post(path,json={'request_id':str(uuid.uuid4())},headers=headers)
                        self.assertEqual(response.status_code,503);self.assertNotIn('secret-key',response.text)
        asyncio.run(run())


class PaperEvidenceGatewayTests(unittest.TestCase):
    def gateway(self,transport):return AlpacaPaperEvidenceGateway(account_id=ACCOUNT,key_id='synthetic-key',secret_key='synthetic-secret',http_get=transport)

    def test_reads_pin_hosts_get_only_no_redirects_and_bound_timeout(self):
        calls=[]
        def get(url,**kwargs):
            calls.append(url);self.assertFalse(kwargs['allow_redirects']);self.assertEqual(kwargs['timeout'],3)
            response=Mock();response.status_code=200
            response.json.return_value={'id':ACCOUNT} if url.endswith('/account') else {'symbol':'AAPL','trade':{'p':10,'t':'2026-10-21T00:00:00Z'}} if '/trades/latest' in url else []
            return response
        value=self.gateway(get).read_snapshot('AAPL');self.assertEqual(value['quote']['p'],10)
        self.assertEqual(len(calls),6)
        self.assertTrue(all(url.startswith(PAPER_URL+'/v2/') or url.startswith('https://data.alpaca.markets/v2/') for url in calls))

    def test_http_failures_redirects_and_malformed_statuses_are_sanitized(self):
        for code in [True,'200',200.0,201,204,301,302,400,401,429,500]:
            response=Mock();response.status_code=code
            with self.subTest(code=code),self.assertRaises(PaperGatewayUnavailable):self.gateway(Mock(return_value=response)).read_account()
        with self.assertRaisesRegex(PaperGatewayUnavailable,'request failed'):
            self.gateway(Mock(side_effect=RuntimeError('private credential'))).read_account()

    def test_wrong_account_identity_and_order_lookup_ids_fail_closed(self):
        response=Mock();response.status_code=200;response.json.return_value={'id':'foreign'}
        gateway=self.gateway(Mock(return_value=response))
        with self.assertRaises(PaperGatewayUnavailable):gateway.read_account()
        for client in ['pt-not-uuid','foreign','pt-../../orders']:
            with self.assertRaises(ValueError):gateway.find_order(client)
        response.status_code=404;self.assertIsNone(gateway.find_order('pt-'+str(uuid.uuid4())))
