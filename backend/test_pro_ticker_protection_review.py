"""Independent exit evidence never authorizes a new order or budget release."""
import asyncio
import httpx
from fastapi import FastAPI, HTTPException
from pro_ticker_execution_routes import build_paper_execution_router
from copy import deepcopy
from contextlib import closing
import unittest
import uuid
import database
import test_pro_ticker_submission_recovery as fixtures
from pro_ticker_protection_review import review_paper_protection
from pro_ticker_paper_gateway import PaperGatewayUnavailable
from pro_ticker_workspace import workspace_snapshot


class ProtectionReviewTests(unittest.TestCase):
    make_draft=fixtures.SubmissionRecoveryTests.make_draft
    prepare=fixtures.SubmissionRecoveryTests.prepare

    def setUp(self):
        fixtures.SubmissionRecoveryTests.setUp(self)
        self.gateway.find_order.return_value.update(filled_qty='2',status='filled',filled_avg_price='10.5')
        fixtures.SubmissionRecoveryTests.recover(self)
        self.parent=deepcopy(self.gateway.find_order.return_value)
        self.parent['order_class']='bracket'
        self.legs=[{'id':str(uuid.uuid4()),'client_order_id':'stop-child','symbol':'AAPL','side':'sell','qty':'2','filled_qty':'0','status':'new','type':'stop','stop_price':'9.98'},
                   {'id':str(uuid.uuid4()),'client_order_id':'target-child','symbol':'AAPL','side':'sell','qty':'2','filled_qty':'0','status':'new','type':'limit','limit_price':'11.55'}]
        self.parent['legs']=self.legs
        self.gateway.read_order.side_effect=lambda id:deepcopy(self.parent if id==self.parent['id'] else next(leg for leg in self.legs if leg['id']==id))
        self.gateway.read_clock.side_effect=lambda:deepcopy(self.snapshot['clock'])
        self.gateway.read_positions.return_value=[{'symbol':'AAPL','qty':'2'}]
        self.gateway.read_trade.side_effect=lambda symbol:deepcopy(self.snapshot['quote'])
        self.payload={'request_id':str(uuid.uuid4())}

    def review(self,**kwargs):
        return review_paper_protection(self.intent['id'],self.payload,context=self.context,**kwargs)

    def test_verified_observation_does_not_change_orders_positions_or_budget(self):
        before=workspace_snapshot();result=self.review()
        self.assertTrue(result['checks_passed'],result['checks'])
        self.assertTrue(result['protection_orders_verified'])
        for flag in ('execution_enabled','order_submitted','budget_released','loss_cap_guaranteed'):self.assertFalse(result[flag])
        after=workspace_snapshot()
        for key in ('budget','positions','reservations','journal'):self.assertEqual(before[key],after[key])
        self.gateway.submit.assert_not_called();self.gateway.post.assert_not_called()
        self.assertEqual(self.gateway.read_order.call_count,3)

    def test_retry_is_immutable_and_expired_observation_is_not_verified(self):
        first=self.review();self.gateway.read_order.side_effect=AssertionError('must not refetch')
        second=self.review();self.assertTrue(second['duplicate']);self.assertEqual(first['checks'],second['checks'])
        self.assertFalse(self.review(clock=lambda:self.now+100)['protection_orders_verified'])

    def test_changed_position_invalidates_saved_observation(self):
        self.assertTrue(self.review()['protection_orders_verified'])
        with closing(database.get_connection()) as conn:
            conn.execute("UPDATE pro_ticker_strategy_positions SET shares=1 WHERE symbol='AAPL'");conn.commit()
        result=self.review();self.assertFalse(result['ledger_unchanged']);self.assertFalse(result['protection_orders_verified'])

    def test_partial_entry_and_after_hours_are_not_certified(self):
        for change in ('partial','closed'):
            with self.subTest(change=change):
                self.payload={'request_id':str(uuid.uuid4())}
                if change=='partial':self.parent.update(status='partially_filled',filled_qty='1')
                else:self.parent.update(status='filled',filled_qty='2');self.snapshot['clock']['is_open']=False
                self.assertFalse(self.review()['protection_orders_verified'])

    def test_invalid_or_inactive_exits_cannot_be_certified(self):
        original=deepcopy(self.legs[0])
        for change in ({'symbol':'MSFT'},{'side':'buy'},{'qty':'1'},{'filled_qty':'1'},{'status':'canceled'},{'type':'stop_limit'},{'stop_price':'9'},{'client_order_id':''}):
            with self.subTest(change=change):
                self.legs[0].clear();self.legs[0].update({**original,**change});self.payload={'request_id':str(uuid.uuid4())}
                self.assertFalse(self.review()['protection_orders_verified'])

    def test_independent_child_disagreement_blocks_protection(self):
        def read(id):
            if id==self.parent['id']:return deepcopy(self.parent)
            return {**next(leg for leg in self.legs if leg['id']==id),'status':'canceled'}
        self.gateway.read_order.side_effect=read
        result=self.review();self.assertFalse(result['protection_orders_verified'])
        self.assertFalse(next(c['passed'] for c in result['checks'] if c['code']=='leg_snapshots_match_parent'))

    def test_child_network_failure_is_not_saved_as_verified_evidence(self):
        self.gateway.read_order.side_effect=[deepcopy(self.parent),PaperGatewayUnavailable('unavailable')]
        with self.assertRaises(PaperGatewayUnavailable):self.review()
        with closing(database.get_connection()) as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM pro_ticker_protection_reviews').fetchone()[0],0)

    def test_browser_cannot_supply_broker_evidence(self):
        self.payload['legs']=self.legs
        with self.assertRaises(ValueError):self.review()
        self.gateway.read_order.assert_not_called()

    def test_http_owner_origin_default_off_and_sanitized_failure(self):
        def owner(request):
            if request.headers.get('X-Owner')!='yes':raise HTTPException(401,'Owner required')
        app=FastAPI();app.include_router(build_paper_execution_router(require_session=owner,context=self.context))
        disabled=FastAPI();disabled.include_router(build_paper_execution_router(require_session=owner))
        async def run():
            path='/auto-trader/pro-ticker/order-intents/'+self.intent['id']+'/protection-review'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as client:
                self.assertEqual((await client.post(path,json=self.payload)).status_code,401)
                self.assertEqual((await client.post(path,json=self.payload,headers={'X-Owner':'yes','Origin':'https://evil'})).status_code,403)
                headers={'X-Owner':'yes','Origin':'https://test'}
                self.assertEqual((await client.post(path,json={'request_id':str(uuid.uuid4()),'legs':[]},headers=headers)).status_code,422)
                self.gateway.read_order.side_effect=PaperGatewayUnavailable('private account credential')
                response=await client.post(path,json=self.payload,headers=headers)
                self.assertEqual(response.status_code,503);self.assertNotIn('private account credential',response.text)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=disabled),base_url='https://test') as client:
                self.assertEqual((await client.post(path,json=self.payload,headers=headers)).status_code,403)
        asyncio.run(run())
