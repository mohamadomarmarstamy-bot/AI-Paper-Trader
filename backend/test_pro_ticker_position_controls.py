"""Owner controls use synthetic broker state; no broker network is used."""
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
import test_pro_ticker_protection_review as fixtures
from pro_ticker_runtime import PaperManagementContext
from pro_ticker_position_controls import manage_paper_position,inspect_position_management,saved_action,_claim
from pro_ticker_order_intents import IntentConflict
from pro_ticker_position_monitor import monitor_paper_position
from pro_ticker_workspace import workspace_snapshot
from pro_ticker_paper_gateway import AlpacaPaperOrderGateway,PaperGatewayUnavailable


class PositionControlsTests(unittest.TestCase):
    make_draft=fixtures.ProtectionReviewTests.make_draft
    prepare=fixtures.ProtectionReviewTests.prepare

    def setUp(self):
        fixtures.ProtectionReviewTests.setUp(self)
        self.context=PaperManagementContext(self.context.expected_account_id,lambda:self.gateway,True,False,True)
        self.gateway.find_order.side_effect=lambda client:deepcopy(self.parent)
        self.gateway.read_clock.side_effect=lambda:{'is_open':True,'timestamp':datetime.now(timezone.utc).isoformat()}
        self.gateway.read_open_orders.return_value=[]
        self.close_order=None
        self.gateway.find_exit_order.side_effect=lambda client:deepcopy(self.close_order)
        self.gateway.read_positions.side_effect=lambda:[{'symbol':'AAPL','qty':str(workspace_snapshot()['positions']['rows'][0]['shares'])}] if workspace_snapshot()['positions']['rows'] else []
        def read(id):
            return deepcopy(self.parent if id==self.parent['id'] else self.close_order if self.close_order and id==self.close_order['id'] else next(c for c in self.legs if c['id']==id))
        self.gateway.read_order.side_effect=read
        def cancel(id):
            row=self.parent if id==self.parent['id'] else next(c for c in self.legs if c['id']==id)
            row['status']='canceled'
            return {'request_accepted':True,'cancellation_confirmed':False}
        self.gateway.cancel_owned_order.side_effect=cancel
        def submit(payload):
            self.close_order={'id':str(uuid.uuid4()),**deepcopy(payload),'status':'new','filled_qty':'0'}
            return deepcopy(self.close_order)
        self.gateway.submit_owned_close.side_effect=submit

    def manage(self,action='close'):
        return manage_paper_position(self.intent['id'],{'action':action,'confirmed':True},context=self.context)

    def advance_close(self):
        for _ in range(3):result=self.manage()
        return result

    def test_close_cancels_confirmed_children_then_sends_one_owned_sell(self):
        result=self.advance_close()
        self.assertFalse(result['position_closed']);self.assertEqual(result['close_order_status'],'new')
        self.assertTrue(result['close_dispatch_attempted']);self.assertFalse(result['budget_released'])
        self.assertEqual(self.gateway.cancel_owned_order.call_count,2)
        self.gateway.submit_owned_close.assert_called_once()
        payload=self.gateway.submit_owned_close.call_args.args[0]
        self.assertEqual(payload['qty'],'2');self.assertEqual(payload['position_intent'],'sell_to_close')
        self.assertTrue(payload['client_order_id'].startswith('ptx-'))
        self.manage();self.gateway.submit_owned_close.assert_called_once()
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)

    def test_partial_and_complete_close_fills_reconcile_and_stay_reserved(self):
        self.advance_close()
        self.close_order.update(filled_qty='1',status='partially_filled')
        partial=inspect_position_management(self.intent['id'],context=self.context)
        self.assertEqual(partial['remaining_shares'],1);self.assertFalse(partial['position_closed'])
        self.close_order.update(filled_qty='2',status='filled')
        final=inspect_position_management(self.intent['id'],context=self.context)
        self.assertTrue(final['position_closed']);self.assertEqual(final['remaining_shares'],0)
        self.assertTrue(monitor_paper_position(self.intent['id'],context=self.context)['closed'])
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)
        self.gateway.submit_owned_close.assert_called_once()

    def test_lost_close_response_recovers_exact_order_without_second_post(self):
        original=self.gateway.submit_owned_close.side_effect
        def lost(payload):original(payload);raise PaperGatewayUnavailable('private transport error')
        self.gateway.submit_owned_close.side_effect=lost
        with self.assertRaises(PaperGatewayUnavailable):self.manage()
        result=self.manage();self.assertEqual(result['close_order_status'],'new')
        self.gateway.submit_owned_close.assert_called_once()

    def test_absent_lost_close_never_authorizes_resubmission(self):
        self.gateway.submit_owned_close.side_effect=PaperGatewayUnavailable('unknown result')
        with self.assertRaises(PaperGatewayUnavailable):self.manage()
        for _ in range(2):self.assertEqual(self.manage()['state'],'close_unconfirmed')
        self.gateway.submit_owned_close.assert_called_once()

    def test_pending_cancel_is_not_confirmation_and_is_not_replayed(self):
        self.gateway.cancel_owned_order.side_effect=lambda id:{'request_accepted':True,'cancellation_confirmed':False}
        for _ in range(3):self.manage()
        self.gateway.cancel_owned_order.assert_called_once()
        self.gateway.submit_owned_close.assert_not_called()
        self.assertEqual(workspace_snapshot()['positions']['rows'][0]['shares'],2)

    def test_fill_during_exit_cancellation_reduces_close_quantity(self):
        def cancel(id):
            leg=next(c for c in self.legs if c['id']==id);leg['status']='canceled'
            if leg['type']=='limit':leg['filled_qty']='1'
        self.gateway.cancel_owned_order.side_effect=cancel
        self.advance_close()
        self.assertEqual(self.gateway.submit_owned_close.call_args.args[0]['qty'],'1')

    def test_mismatched_broker_position_or_open_orders_block_close(self):
        for kind in ('position','orders'):
            with self.subTest(kind=kind):
                self.legs[0]['status']=self.legs[1]['status']='canceled'
                if kind=='position':self.gateway.read_positions.side_effect=None;self.gateway.read_positions.return_value=[{'symbol':'AAPL','qty':'3'}]
                else:
                    self.gateway.read_positions.return_value=[{'symbol':'AAPL','qty':'2'}]
                    self.gateway.read_open_orders.return_value=[{'id':str(uuid.uuid4()),'symbol':'AAPL','qty':'1'}]
                with self.assertRaises(IntentConflict):self.manage()
                self.gateway.submit_owned_close.assert_not_called()

    def test_off_hours_does_not_remove_protection(self):
        self.gateway.read_clock.side_effect=lambda:{'is_open':False,'timestamp':datetime.now(timezone.utc).isoformat()}
        with self.assertRaises(IntentConflict):self.manage()
        self.gateway.cancel_owned_order.assert_not_called();self.gateway.submit_owned_close.assert_not_called()

    def test_disabled_or_unconfirmed_owner_cannot_mutate(self):
        disabled=PaperManagementContext(self.context.expected_account_id,lambda:self.gateway,True,False,False)
        with self.assertRaises(ValueError):manage_paper_position(self.intent['id'],{'action':'close','confirmed':True},context=disabled)
        for payload in ({'action':'close','confirmed':False},{'action':'close','confirmed':True,'broker_order':self.parent}):
            with self.assertRaises(ValueError):manage_paper_position(self.intent['id'],payload,context=self.context)
        self.gateway.cancel_owned_order.assert_not_called();self.gateway.submit_owned_close.assert_not_called()

    def test_foreign_parent_or_account_cannot_cancel(self):
        self.parent['client_order_id']='foreign'
        with self.assertRaises(ValueError):self.manage()
        self.gateway.cancel_owned_order.assert_not_called()
        self.parent['client_order_id']=self.intent['client_order_id']
        self.snapshot['account']['id']=str(uuid.uuid4())
        with self.assertRaises(ValueError):self.manage()
        self.gateway.cancel_owned_order.assert_not_called()

    def test_durable_claim_rejects_simultaneous_owner_calls(self):
        _claim(self.intent,'close',self.context)
        with self.assertRaises(IntentConflict):self.manage()
        self.gateway.cancel_owned_order.assert_not_called()

    def test_rejected_close_is_not_retried_and_never_marked_closed(self):
        self.advance_close();self.close_order['status']='rejected'
        result=self.manage();self.assertEqual(result['state'],'close_rejected');self.assertFalse(result['position_closed'])
        self.gateway.submit_owned_close.assert_called_once()

    def test_cancel_pending_entry_observes_terminal_status_and_retains_fills(self):
        # Use a fresh fixture whose entry has not posted its planned shares.
        import test_pro_ticker_submission_recovery as pending
        pending.SubmissionRecoveryTests.setUp(self)
        self.context=PaperManagementContext(self.context.expected_account_id,lambda:self.gateway,True,False,True)
        parent=self.gateway.find_order.return_value;parent['order_class']='bracket'
        self.gateway.read_order.side_effect=lambda id:deepcopy(parent)
        self.gateway.cancel_owned_order.side_effect=lambda id:parent.update(status='canceled')
        result=self.manage('cancel_entry')
        self.assertTrue(result['entry_cancellation_confirmed']);self.assertEqual(result['remaining_shares'],1)
        self.assertFalse(result['position_closed']);self.assertFalse(result['budget_released'])
        self.gateway.submit_owned_close.assert_not_called()

    def test_http_auth_origin_confirmation_and_management_gate(self):
        from pro_ticker_execution_routes import build_paper_execution_router
        def require(request):
            if request.headers.get('x-owner')!='yes':raise HTTPException(401)
        enabled=FastAPI();enabled.include_router(build_paper_execution_router(require_session=require,context=self.context))
        disabled=FastAPI();disabled.include_router(build_paper_execution_router(require_session=require,context=PaperManagementContext(self.context.expected_account_id,lambda:self.gateway,True,False,False)))
        async def run():
            path='/auto-trader/pro-ticker/order-intents/'+self.intent['id']+'/manage'
            headers={'x-owner':'yes','Origin':'https://test'}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=enabled),base_url='https://test') as client:
                self.assertEqual((await client.post(path,json={'action':'close','confirmed':True})).status_code,401)
                self.assertEqual((await client.post(path,json={'action':'close','confirmed':True},headers={**headers,'Origin':'https://evil'})).status_code,403)
                self.assertEqual((await client.post(path,json={'action':'close','confirmed':False},headers=headers)).status_code,422)
                self.gateway.cancel_owned_order.assert_not_called();self.gateway.submit_owned_close.assert_not_called()
                result=await client.post(path,json={'action':'close','confirmed':True},headers=headers)
                self.assertEqual(result.status_code,200,result.text)
                self.assertEqual(result.json()['close_order_status'],'new')
                recovered=await client.post(path+'-recover',json={},headers=headers)
                self.assertEqual(recovered.status_code,200,recovered.text)
                self.gateway.submit_owned_close.assert_called_once()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=disabled),base_url='https://test') as client:
                self.assertEqual((await client.post(path,json={'action':'close','confirmed':True},headers=headers)).status_code,403)
                self.assertEqual((await client.post(path+'-recover',json={},headers=headers)).status_code,200)
        asyncio.run(run())

    def test_partial_terminal_entry_with_canceled_children_can_close_its_actual_shares(self):
        import test_pro_ticker_submission_recovery as pending
        pending.SubmissionRecoveryTests.setUp(self)
        self.context=PaperManagementContext(self.context.expected_account_id,lambda:self.gateway,True,False,True)
        parent=self.gateway.find_order.return_value;parent.update(order_class='bracket',status='canceled')
        children=[{'id':str(uuid.uuid4()),'client_order_id':'stop-partial','symbol':'AAPL','side':'sell','qty':'2','filled_qty':'0','status':'canceled','type':'stop','stop_price':'9.98'},
                  {'id':str(uuid.uuid4()),'client_order_id':'target-partial','symbol':'AAPL','side':'sell','qty':'2','filled_qty':'0','status':'canceled','type':'limit','limit_price':'11.55'}]
        parent['legs']=children
        manual=[]
        self.gateway.read_order.side_effect=lambda id:deepcopy(parent if id==parent['id'] else manual[0] if manual and id==manual[0]['id'] else next(c for c in children if c['id']==id))
        self.gateway.read_clock.side_effect=lambda:{'is_open':True,'timestamp':datetime.now(timezone.utc).isoformat()}
        self.gateway.read_open_orders.return_value=[]
        self.gateway.read_positions.side_effect=lambda:[{'symbol':'AAPL','qty':'1'}] if not manual or manual[0]['filled_qty']=='0' else []
        self.gateway.find_exit_order.side_effect=lambda client:deepcopy(manual[0]) if manual else None
        def submit(payload):manual.append({'id':str(uuid.uuid4()),**payload,'filled_qty':'0','status':'new'})
        self.gateway.submit_owned_close.side_effect=submit
        result=self.manage();self.assertEqual(result['remaining_shares'],1)
        self.assertEqual(self.gateway.submit_owned_close.call_args.args[0]['qty'],'1')
        manual[0].update(filled_qty='1',status='filled')
        final=inspect_position_management(self.intent['id'],context=self.context)
        self.assertTrue(final['position_closed']);self.assertEqual(final['remaining_shares'],0)
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)
        # Price and release the canceled partial entry using its actual one share.
        from pro_ticker_execution_journal import sync_execution_journal,execution_journal_snapshot
        from pro_ticker_manual_release import prepare_manual_paper_release,approve_manual_paper_release,release_manually_approved_paper_budget
        def activities(id):
            row=parent if id==parent['id'] else manual[0] if id==manual[0]['id'] else next(c for c in children if c['id']==id)
            return [] if row['filled_qty']=='0' else [{'id':'fill-'+id,'activity_type':'FILL','type':'fill','order_id':id,'symbol':'AAPL','side':row['side'],'qty':'1','cum_qty':'1','price':'10' if row['side']=='buy' else '11','transaction_time':datetime.now(timezone.utc).isoformat()}]
        self.gateway.read_fill_activities.side_effect=activities
        sync_execution_journal(self.intent['id'],context=self.context)
        review=prepare_manual_paper_release(self.intent['reservation_id'],fetch_paper_order=self.gateway.read_order)
        approve_manual_paper_release(review['review_id'],evidence_digest=review['evidence_digest'],approved_by='owner',independent_paper_provenance_confirmed=True)
        release_manually_approved_paper_budget(review['review_id'],fetch_paper_order=self.gateway.read_order)
        priced=execution_journal_snapshot();self.assertEqual(priced['closed_priced_count'],1);self.assertEqual(priced['gross_realized_usd'],'1')

    def test_manual_exit_provenance_rejects_foreign_order(self):
        self.advance_close();self.close_order['client_order_id']='foreign'
        with self.assertRaises(ValueError):monitor_paper_position(self.intent['id'],context=self.context)
        self.assertEqual(workspace_snapshot()['positions']['rows'][0]['shares'],2)

    def test_management_account_change_cannot_adopt_saved_action(self):
        self.advance_close()
        with closing(database.get_connection()) as conn:
            conn.execute("UPDATE pro_ticker_position_actions SET account_fingerprint='foreign'");conn.commit()
        with self.assertRaises(IntentConflict):self.manage()
        self.gateway.submit_owned_close.assert_called_once()


class ManagementTransportTests(unittest.TestCase):
    def test_delete_is_paper_scoped_and_204_never_confirms_cancellation(self):
        account='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';id=str(uuid.uuid4())
        get=Mock(return_value=Mock(status_code=200,json=lambda:{'id':account}))
        delete=Mock(return_value=Mock(status_code=204));post=Mock()
        gateway=AlpacaPaperOrderGateway(account_id=account,key_id='synthetic',secret_key='synthetic-secret',http_get=get,http_post=post,http_delete=delete)
        result=gateway.cancel_owned_order(id)
        self.assertFalse(result['cancellation_confirmed']);self.assertTrue(result['request_accepted'])
        self.assertEqual(delete.call_args.args[0],'https://paper-api.alpaca.markets/v2/orders/'+id)
        self.assertFalse(delete.call_args.kwargs['allow_redirects']);post.assert_not_called()
        delete.side_effect=RuntimeError('private-key')
        with self.assertRaises(PaperGatewayUnavailable) as error:gateway.cancel_owned_order(id)
        self.assertNotIn('private-key',str(error.exception))

    def test_close_transport_rejects_buy_and_uses_only_saved_sell_to_close(self):
        account='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
        get=Mock(return_value=Mock(status_code=200,json=lambda:{'id':account}));post=Mock(return_value=Mock(status_code=200,json=lambda:{'id':'synthetic'}))
        gateway=AlpacaPaperOrderGateway(account_id=account,key_id='synthetic',secret_key='synthetic-secret',http_get=get,http_post=post)
        payload={'symbol':'AAPL','qty':'2','side':'sell','type':'market','time_in_force':'day','extended_hours':False,'position_intent':'sell_to_close','client_order_id':'ptx-'+str(uuid.uuid4())}
        for change in ({'side':'buy'},{'position_intent':'sell_to_open'},{'qty':'1.5'},{'extended_hours':True},{'client_order_id':'foreign'}):
            with self.assertRaises(ValueError):gateway.submit_owned_close({**payload,**change})
        post.assert_not_called();gateway.submit_owned_close(payload)
        self.assertEqual(post.call_args.args[0],'https://paper-api.alpaca.markets/v2/orders')
        self.assertFalse(post.call_args.kwargs['allow_redirects'])
