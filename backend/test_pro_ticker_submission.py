"""Owner-approved PAPER dispatch, at-most-once attempt and restart recovery."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import datetime,timezone
import unittest
from unittest.mock import Mock,patch
import uuid
import httpx
from fastapi import FastAPI,HTTPException
import database
import test_pro_ticker_broker_admission as fixtures
from pro_ticker_submission import PaperSubmissionContext,submit_paper_intent,bracket_request
from pro_ticker_order_intents import IntentConflict,cancel_prepared_intent,list_order_intents
from pro_ticker_execution_routes import build_paper_execution_router
from pro_ticker_workspace import workspace_snapshot
from pro_ticker_paper_gateway import AlpacaPaperOrderGateway,PAPER_URL,PaperGatewayUnavailable


class PaperSubmissionTests(unittest.TestCase):
    make_draft=fixtures.BrokerAdmissionTests.make_draft
    prepare=fixtures.BrokerAdmissionTests.prepare

    def setUp(self):
        fixtures.BrokerAdmissionTests.setUp(self)
        self.context=PaperSubmissionContext(fixtures.ACCOUNT,lambda:self.gateway,True,True)
        self.order={'id':str(uuid.uuid4()),'client_order_id':self.intent['client_order_id'],'symbol':'AAPL',
                    'side':'buy','qty':'2','filled_qty':'0','status':'new','order_class':'bracket'}
        self.gateway.find_order.return_value=None
        def post(payload):
            self.assertEqual(payload,bracket_request(self.intent))
            self.gateway.find_order.return_value=deepcopy(self.order)
            return deepcopy(self.order)
        self.gateway.submit_bracket.side_effect=post

    def submit(self,**kwargs):return submit_paper_intent(self.intent['id'],{'confirmed':True},context=self.context,**kwargs)

    def test_owner_submit_is_persisted_before_transport_and_retry_only_reads(self):
        original=self.gateway.submit_bracket.side_effect
        def post(payload):
            state=list_order_intents()['intents'][0]
            self.assertEqual(state['status'],'submitting');self.assertIsNotNone(state['attempt_started_at'])
            with self.assertRaises(IntentConflict):cancel_prepared_intent(state['id'],{'confirmed':True})
            return original(payload)
        self.gateway.submit_bracket.side_effect=post
        first=self.submit();self.assertTrue(first['resolved']);self.assertTrue(first['dispatch_attempted'])
        retry=self.submit();self.assertTrue(retry['resolved']);self.assertFalse(retry['dispatch_attempted'])
        self.assertEqual(self.gateway.submit_bracket.call_count,1)
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)

    def test_timeout_missing_lookup_stays_held_then_recovers_without_second_post(self):
        self.gateway.submit_bracket.side_effect=TimeoutError('private-key')
        first=self.submit();self.assertFalse(first['resolved']);self.assertEqual(first['intent']['status'],'uncertain')
        self.gateway.find_order.return_value={**self.order,'filled_qty':'1','status':'partially_filled'}
        retry=self.submit();self.assertTrue(retry['resolved']);self.assertEqual(retry['posted_entry_shares'],1)
        self.gateway.find_order.return_value={**self.order,'filled_qty':'2','status':'filled'}
        self.assertEqual(self.submit()['posted_entry_shares'],1)
        self.assertEqual(workspace_snapshot()['positions']['rows'][0]['shares'],2)
        self.assertEqual(self.gateway.submit_bracket.call_count,1)

    def test_lost_post_response_is_recovered_by_independent_get(self):
        def post(payload):self.gateway.find_order.return_value=deepcopy(self.order);raise TimeoutError()
        self.gateway.submit_bracket.side_effect=post
        result=self.submit();self.assertTrue(result['resolved']);self.assertFalse(result['budget_released'])
        self.gateway.find_order.assert_called_once_with(self.intent['client_order_id'])

    def test_invalid_or_foreign_response_cannot_link_or_release(self):
        self.gateway.submit_bracket.side_effect=lambda payload:self.order
        self.gateway.find_order.return_value={**self.order,'symbol':'MSFT'}
        result=self.submit();self.assertFalse(result['resolved']);self.assertIsNone(result['intent']['linked_broker_order_id'])
        self.assertEqual(workspace_snapshot()['positions']['total'],0)
        self.assertTrue(result['intent']['budget_reserved'])

    def test_default_context_injection_confirmation_and_market_failure_block_before_post(self):
        for payload in ({'confirmed':False},{'confirmed':1},{'confirmed':True,'symbol':'MSFT'},{'confirmed':True,'paper':True}):
            with self.assertRaises(ValueError):submit_paper_intent(self.intent['id'],payload,context=self.context)
        with self.assertRaises(ValueError):submit_paper_intent(self.intent['id'],{'confirmed':True},context=fixtures.PaperEvidenceContext(fixtures.ACCOUNT,lambda:self.gateway,True))
        self.snapshot['clock']['is_open']=False
        with self.assertRaises(IntentConflict):self.submit()
        self.gateway.submit_bracket.assert_not_called()
        self.assertEqual(list_order_intents()['intents'][0]['status'],'prepared')

    def test_settings_change_after_review_blocks_claim(self):
        import pro_ticker_submission as module
        original=module.review_paper_admission
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            with closing(database.get_connection()) as conn:
                conn.execute("UPDATE pro_ticker_order_intents SET updated_at='changed'");conn.commit()
            return result
        with patch.object(module,'review_paper_admission',side_effect=changed),self.assertRaises(IntentConflict):self.submit()
        self.gateway.submit_bracket.assert_not_called()

    def test_concurrent_requests_cannot_dispatch_twice(self):
        def run(_):
            try:return self.submit()
            except IntentConflict:return None
        with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(run,range(3)))
        self.assertEqual(self.gateway.submit_bracket.call_count,1)
        self.assertTrue(any(r and r['resolved'] for r in results))

    def test_audit_failure_rolls_back_attempt_before_post(self):
        with patch('pro_ticker_submission._audit',side_effect=RuntimeError('audit unavailable')),self.assertRaises(RuntimeError):self.submit()
        self.gateway.submit_bracket.assert_not_called()
        self.assertIsNone(list_order_intents()['intents'][0]['attempt_started_at'])

    def test_position_appearing_after_broker_snapshot_blocks_dispatch(self):
        import pro_ticker_submission as module
        from pro_ticker_position_ledger import post_verified_paper_fill
        original=module.review_paper_admission
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            post_verified_paper_fill(event_id='new-independent-fill',broker_order_id='other-order',symbol='AAPL',side='buy',filled_shares=1,ownership_verified=True,fill_verified=True)
            return result
        with patch.object(module,'review_paper_admission',side_effect=changed),self.assertRaises(IntentConflict):self.submit()
        self.gateway.submit_bracket.assert_not_called()

    def test_crash_after_claim_never_retries_dispatch(self):
        self.gateway.submit_bracket.side_effect=KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.submit()
        self.assertEqual(list_order_intents()['intents'][0]['status'],'submitting')
        self.assertFalse(self.submit()['resolved']);self.assertEqual(self.gateway.submit_bracket.call_count,1)

    def test_http_owner_origin_disabled_and_explicit_confirm(self):
        def owner(request):
            if request.headers.get('X-Owner')!='yes':raise HTTPException(401,'Owner required')
        app=FastAPI();app.include_router(build_paper_execution_router(require_session=owner,context=self.context))
        disabled=FastAPI();disabled.include_router(build_paper_execution_router(require_session=owner,context=fixtures.PaperEvidenceContext(fixtures.ACCOUNT,lambda:self.gateway,True)))
        async def run():
            path='/auto-trader/pro-ticker/order-intents/'+self.intent['id']+'/submit';headers={'X-Owner':'yes','Origin':'https://test'}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as client:
                self.assertEqual((await client.post(path,json={'confirmed':True})).status_code,401)
                self.assertEqual((await client.post(path,json={'confirmed':True},headers={**headers,'Origin':'https://evil'})).status_code,403)
                self.assertEqual((await client.post(path,json={'confirmed':1},headers=headers)).status_code,422)
                result=await client.post(path,json={'confirmed':True},headers=headers)
                self.assertEqual(result.status_code,200);self.assertTrue(result.json()['resolved'])
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=disabled),base_url='https://test') as client:self.assertEqual((await client.post(path,json={'confirmed':True},headers=headers)).status_code,403)
        asyncio.run(run())


class PaperOrderTransportTests(unittest.TestCase):
    def test_pinned_paper_host_no_redirects_no_post_retries_and_account_mismatch(self):
        get=Mock(return_value=Mock(status_code=200,json=lambda:{'id':fixtures.ACCOUNT}));post=Mock(side_effect=TimeoutError('private key'))
        gateway=AlpacaPaperOrderGateway(account_id=fixtures.ACCOUNT,key_id='synthetic',secret_key='synthetic',http_get=get,http_post=post)
        payload={'symbol':'AAPL','qty':'2','side':'buy','type':'limit','limit_price':'10.50','time_in_force':'gtc','extended_hours':False,
                 'client_order_id':'pt-'+str(uuid.uuid4()),'order_class':'bracket','stop_loss':{'stop_price':'9.98'},'take_profit':{'limit_price':'11.55'}}
        with self.assertRaises(PaperGatewayUnavailable) as error:gateway.submit_bracket(payload)
        self.assertNotIn('private key',str(error.exception));self.assertEqual(post.call_count,1)
        self.assertEqual(post.call_args.args[0],PAPER_URL+'/v2/orders');self.assertFalse(post.call_args.kwargs['allow_redirects'])
        get.return_value=Mock(status_code=200,json=lambda:{'id':str(uuid.uuid4())})
        with self.assertRaises(PaperGatewayUnavailable):gateway.submit_bracket(payload)
        self.assertEqual(post.call_count,1)
        for change in ({'extended_hours':True},{'side':'sell'},{'type':'market'},{'qty':'1.5'},{'limit_price':'NaN'},{'limit_price':'10.501'}):
            with self.subTest(change=change),self.assertRaises(ValueError):gateway.submit_bracket({**payload,**change})
