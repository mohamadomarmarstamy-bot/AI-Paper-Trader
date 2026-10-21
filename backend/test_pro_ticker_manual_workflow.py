"""Integrated manual workflow with a trusted synthetic broker fixture.

HTTP drives research, drafts, allocation, views and release approval. Broker
submission/protection are deliberately NOT implemented or exercised: fabricated
broker snapshots are supplied directly to the existing verified fill bridges.
This establishes module compatibility, not full application execution readiness.
"""
import asyncio
from datetime import datetime,timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid
import httpx
from fastapi import FastAPI,HTTPException
import database
import test_pro_ticker_manual_signals as fixtures
from pro_ticker_manual_signals import build_manual_signal_router
from pro_ticker_workspace import build_workspace_router
from pro_ticker_release_routes import build_paper_release_router
from pro_ticker_budget_ledger import link_verified_paper_entry_order,record_verified_paper_order_state
from pro_ticker_verified_fill_bridge import account_linked_paper_entry_snapshot
from pro_ticker_verified_exit_fill_bridge import account_linked_paper_exit_snapshot
from pro_ticker_exit_links import link_verified_paper_exit,pro_ticker_exit_client_order_id


class ManualWorkflowIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'workflow.db');p.start();self.addCleanup(p.stop)

    def test_http_manual_grade_allocation_verified_partial_fills_closure_and_owner_release(self):
        orders={};app=FastAPI()
        def auth(request):
            if request.headers.get('x-test-owner')!='yes':raise HTTPException(401)
        def quote(symbol):return {'p':10.5,'t':datetime.fromtimestamp(time.time()-1,timezone.utc).isoformat()}
        app.include_router(build_manual_signal_router(require_session=auth,quote_fetcher=quote,analyzer=fixtures.analysis))
        app.include_router(build_workspace_router(require_session=auth))
        app.include_router(build_paper_release_router(require_session=auth,operator_enabled=lambda:True,
            make_paper_reader=lambda:lambda order_id:dict(orders[order_id])))
        async def run():
            headers={'x-test-owner':'yes','Origin':'https://test'}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test',headers=headers) as client:
                signals='/auto-trader/pro-ticker/manual-signals';intents='/auto-trader/pro-ticker/order-intents';workspace='/auto-trader/pro-ticker/workspace'
                review=await client.post(signals,json={'request_id':str(uuid.uuid4()),'symbol':'AAPL'})
                self.assertEqual(review.status_code,200);grade=review.json()['signal']['entry_grade'];self.assertEqual(grade['grade'],'A')
                draft=await client.post(signals+'/proposal',json={'request_id':str(uuid.uuid4()),'signal_id':review.json()['signal']['request_id'],
                    'shares':4,'stop_loss_percent':'5','take_profit_percent':'10'})
                self.assertEqual(draft.status_code,200)
                allocation_payload={'request_id':str(uuid.uuid4()),'proposal_id':draft.json()['proposal']['request_id'],'confirmed':True}
                allocation=await client.post(intents,json=allocation_payload);self.assertEqual(allocation.status_code,200)
                intent=allocation.json()['intent'];reservation=intent['reservation_id']
                self.assertEqual((await client.post(intents,json=allocation_payload)).json()['intent'],intent)
                self.assertEqual((await client.get(workspace)).json()['budget']['committed_cents'],4200)
                self.assertEqual((await client.post(intents+'/'+intent['id']+'/submit',json={})).status_code,404)

                # Trusted fixture replaces the absent broker dispatcher. No
                # endpoint accepts browser-provided broker fills as evidence.
                entry={'id':str(uuid.uuid4()),'client_order_id':intent['client_order_id'],'symbol':'AAPL','side':'buy','qty':'4','filled_qty':'0','status':'new'}
                link_verified_paper_entry_order(reservation,entry)
                orders[entry['id']]=entry
                linked=(await client.get(intents)).json()['intents'][0]
                self.assertEqual(linked['status'],'broker_linked');self.assertTrue(linked['order_submitted'])
                self.assertEqual((await client.post(intents+'/'+intent['id']+'/cancel',json={'confirmed':True})).status_code,409)
                bad={**entry,'symbol':'MSFT','filled_qty':'1','status':'partially_filled'}
                with self.assertRaises(ValueError):account_linked_paper_entry_snapshot(reservation,bad)
                for filled,status in [(1,'partially_filled'),(4,'filled'),(4,'filled')]:
                    entry={**entry,'filled_qty':str(filled),'status':status};orders[entry['id']]=entry
                    account_linked_paper_entry_snapshot(reservation,entry)
                    record_verified_paper_order_state(reservation,entry['id'],status,filled_quantity=filled)
                current=(await client.get(workspace)).json();self.assertEqual(current['positions']['rows'][0]['shares'],4)
                self.assertEqual(current['journal']['total'],2)

                exit_id=str(uuid.uuid4());exit_order={'id':str(uuid.uuid4()),'client_order_id':pro_ticker_exit_client_order_id(reservation,exit_id),
                    'symbol':'AAPL','side':'sell','qty':'4','filled_qty':'0','status':'new'}
                link_verified_paper_exit(reservation_id=reservation,symbol='AAPL',expected_shares=4,broker_order=exit_order,exit_id=exit_id)
                exit_order={**exit_order,'filled_qty':'2','status':'partially_filled'};orders[exit_order['id']]=exit_order
                account_linked_paper_exit_snapshot(exit_order['id'],exit_order)
                release_headers={**headers,'X-Pro-Ticker-Operator':'manual-release'};release='/auto-trader/pro-ticker-paper'
                self.assertEqual((await client.post(release+'/release-review',json={'reservation_id':reservation},headers=release_headers)).status_code,409)
                self.assertEqual((await client.get(workspace)).json()['budget']['committed_cents'],4200)
                exit_order={**exit_order,'filled_qty':'4','status':'filled'};orders[exit_order['id']]=exit_order
                account_linked_paper_exit_snapshot(exit_order['id'],exit_order)
                self.assertEqual(account_linked_paper_exit_snapshot(exit_order['id'],exit_order)['delta_shares'],0)
                current=(await client.get(workspace)).json();self.assertEqual(current['positions']['total'],0)
                self.assertEqual(current['budget']['committed_cents'],4200)  # Closure is not approval.
                prepared=await client.post(release+'/release-review',json={'reservation_id':reservation},headers=release_headers)
                self.assertEqual(prepared.status_code,200);evidence=prepared.json()
                confirmation={'review_id':evidence['review_id'],'evidence_digest':evidence['evidence_digest'],'paper_provenance_confirmed':True}
                denied=await client.post(release+'/release-confirm',json={**confirmation,'paper_provenance_confirmed':False},headers=release_headers)
                self.assertEqual(denied.status_code,422)
                applied=await client.post(release+'/release-confirm',json=confirmation,headers=release_headers)
                self.assertEqual(applied.status_code,200);self.assertTrue(applied.json()['budget_released'])
                retry=await client.post(release+'/release-confirm',json=confirmation,headers=release_headers)
                self.assertEqual(retry.status_code,200);self.assertFalse(retry.json()['budget_released'])
                final=(await client.get(workspace)).json()
                self.assertEqual(final['budget']['committed_cents'],0);self.assertEqual(final['budget']['available_cents'],5000000)
                self.assertEqual(final['journal']['total'],4);self.assertIsNone(final['performance']['realized_pnl'])
                closed=(await client.get(intents)).json()['intents'][0]
                self.assertEqual(closed['status'],'closed');self.assertFalse(closed['budget_reserved']);self.assertTrue(closed['order_submitted'])
                self.assertEqual(closed['virtual_funds_returned_cents'],4200)
                signal=(await client.get(signals)).json()['signals'][0]
                self.assertEqual(signal['entry_grade'],grade);self.assertEqual(signal['order_intent']['status'],'closed')
                dataset=(await client.get(signals+'/dataset')).json();self.assertEqual(dataset['training_ready_samples'],0)
                self.assertIsNone(dataset['rows'][0]['future_outcome'])
                self.assertEqual((await client.get(release+'/release-events')).json()['total_count'],1)
        asyncio.run(run())
