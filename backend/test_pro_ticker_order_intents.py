"""Atomic draft allocations and explicit never-submitted cancellation boundaries."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import uuid
import httpx
from fastapi import FastAPI, HTTPException
import database
import test_pro_ticker_manual_signals as fixtures
from pro_ticker_manual_signals import submit_manual_signal, list_manual_signals, build_manual_signal_router
from pro_ticker_signal_proposals import save_signal_proposal
from pro_ticker_order_intents import prepare_order_intent, cancel_prepared_intent, list_order_intents, IntentConflict
from pro_ticker_workspace import workspace_snapshot
from pro_ticker_settings import save_settings


class OrderIntentTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'intents.db');p.start();self.addCleanup(p.stop)
        self.now=time.time()
        self.quote=Mock(return_value={'p':10.5,'t':datetime.fromtimestamp(self.now-1,timezone.utc).isoformat()})
        self.draft=self.make_draft()
        self.payload={'request_id':str(uuid.uuid4()),'proposal_id':self.draft['request_id'],'confirmed':True}

    def make_draft(self,symbol='AAPL',shares=2):
        signal=str(uuid.uuid4())
        submit_manual_signal({'request_id':signal,'symbol':symbol},quote_fetcher=self.quote,analyzer=fixtures.analysis)
        return save_signal_proposal({'request_id':str(uuid.uuid4()),'signal_id':signal,'shares':shares,
                'stop_loss_percent':'5','take_profit_percent':'10'},quote_fetcher=self.quote)['proposal']

    def prepare(self,**kwargs):
        return prepare_order_intent({**self.payload,**kwargs},quote_fetcher=self.quote)

    def test_atomic_allocation_has_bound_shares_provenance_and_no_order(self):
        result=self.prepare();intent=result['intent'];plan=intent['plan']
        self.assertEqual(intent['status'],'prepared');self.assertTrue(intent['budget_reserved'])
        self.assertFalse(intent['execution_enabled']);self.assertFalse(intent['order_submitted'])
        self.assertFalse(intent['automatic_release_enabled']);self.assertTrue(plan['budget_reserved'])
        self.assertEqual(plan['draft_id'],self.draft['request_id']);self.assertEqual(plan['entry_grade'],self.draft['entry_grade'])
        self.assertEqual(plan['market_snapshot']['price'],10.5)
        snapshot=workspace_snapshot();self.assertEqual(snapshot['budget']['committed_cents'],2100)
        with closing(database.get_connection()) as conn:
            row=conn.execute('SELECT * FROM pro_ticker_paper_reservations').fetchone()
            self.assertEqual(row['id'],intent['reservation_id']);self.assertEqual(row['expected_shares'],2)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM pro_ticker_order_intent_events').fetchone()[0],1)
        history=list_manual_signals()['signals'][0]
        self.assertEqual(history['order_intent'],intent);self.assertFalse(history['proposal']['budget_reserved'])

    def test_retry_and_application_reopen_keep_same_intent_quote_and_client_id(self):
        first=self.prepare()['intent'];self.quote.side_effect=AssertionError('no refetch')
        retry=self.prepare();self.assertTrue(retry['duplicate']);self.assertEqual(retry['intent'],first)
        self.assertEqual(list_order_intents()['intents'][0],first)
        self.assertEqual(first['client_order_id'],'pt-'+first['reservation_id'])
        from pro_ticker_order_linking import verify_paper_entry_for_link
        from pro_ticker_budget_ledger import get_pro_ticker_reservation
        verified=verify_paper_entry_for_link(get_pro_ticker_reservation(first['reservation_id']),
            {'id':'synthetic-entry','client_order_id':first['client_order_id'],'symbol':'AAPL','side':'buy','status':'new'})
        self.assertTrue(verified['verified'])

    def test_concurrent_retry_reserves_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:self.prepare(),range(4)))
        self.assertEqual(sum(not r['duplicate'] for r in results),1)
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)
        self.assertEqual(workspace_snapshot()['reservations']['total'],1)

    def test_second_request_for_same_draft_and_reused_request_for_another_draft_conflict(self):
        self.prepare()
        with self.assertRaises(IntentConflict):self.prepare(request_id=str(uuid.uuid4()))
        other=self.make_draft('MSFT')
        with self.assertRaises(IntentConflict):self.prepare(proposal_id=other['request_id'])

    def test_missing_confirmation_injected_fields_and_invalid_ids_fail(self):
        for update in [{'confirmed':False},{'confirmed':1},{'confirmed':'true'},{'execute':True},
                       {'symbol':'OTHER'},{'request_id':'not-uuid'},{'broker_paper':True}]:
            with self.subTest(update=update),self.assertRaises(ValueError):self.prepare(**update)
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],0)

    def test_current_quote_cannot_increase_original_cost_or_risk_ceiling(self):
        self.quote.return_value['p']=10.6
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],0)

    def test_current_settings_read_under_allocation_transaction(self):
        def quote(symbol):
            save_settings({'expected_revision':0,'settings':{'max_allocation_cents':2000,'max_stop_risk_cents':5000000,'discovery_article_limit':5}})
            return self.quote.return_value
        with self.assertRaises(ValueError):prepare_order_intent(self.payload,quote_fetcher=quote)
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],0)

    def test_stale_wrong_symbol_and_future_quotes_cannot_reserve(self):
        for raw in [{'p':10,'symbol':'MSFT','t':self.quote.return_value['t']},
                    {'p':10,'t':datetime.fromtimestamp(self.now-100,timezone.utc).isoformat()},
                    {'p':10,'t':datetime.fromtimestamp(self.now+100,timezone.utc).isoformat()}]:
            self.quote.return_value=raw
            with self.subTest(raw=raw),self.assertRaises(ValueError):self.prepare()
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],0)

    def test_quote_expiry_before_commit_rolls_back_reservation_and_intent(self):
        with self.assertRaises(ValueError):prepare_order_intent(self.payload,quote_fetcher=self.quote,clock=Mock(side_effect=[self.now,self.now,self.now+100]))
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],0)
        self.assertEqual(list_order_intents()['total_count'],0)

    def test_audit_failure_rolls_back_reservation_intent_and_bound_shares(self):
        # Initialize schema using a rejected request, then reject the final audit.
        with self.assertRaises(ValueError):self.prepare(proposal_id=str(uuid.uuid4()))
        with closing(database.get_connection()) as conn:
            conn.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON pro_ticker_order_intent_events BEGIN SELECT RAISE(ABORT,'test failure'); END");conn.commit()
        with self.assertRaises(Exception):self.prepare()
        self.assertEqual(workspace_snapshot()['reservations']['total'],0);self.assertEqual(list_order_intents()['total_count'],0)
        with closing(database.get_connection()) as conn:conn.execute('DROP TRIGGER fail_audit');conn.commit()
        self.assertFalse(self.prepare()['duplicate'])

    def test_competing_symbols_cannot_overspend_total_virtual_budget(self):
        # Replace the fixture's unused tiny draft with distinct large intents.
        a=self.make_draft('AAPL',shares=3000);b=self.make_draft('MSFT',shares=3000)
        payloads=[{**self.payload,'request_id':str(uuid.uuid4()),'proposal_id':d['request_id']} for d in (a,b)]
        def prepare(payload):
            try:return prepare_order_intent(payload,quote_fetcher=self.quote)
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(prepare,payloads))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],3150000)

    def test_active_symbol_reservation_or_strategy_position_blocks(self):
        from pro_ticker_budget_ledger import reserve_pro_ticker_budget
        reserve_pro_ticker_budget('AAPL',1)
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],100)

    def test_open_strategy_position_blocks_new_allocation_and_cancellation(self):
        from pro_ticker_position_ledger import post_verified_paper_fill
        post_verified_paper_fill(event_id='old-entry',broker_order_id='other-order',symbol='AAPL',side='buy',filled_shares=1,ownership_verified=True,fill_verified=True)
        with self.assertRaises(IntentConflict):self.prepare()
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],0)
        post_verified_paper_fill(event_id='old-exit',broker_order_id='other-exit',symbol='AAPL',side='sell',filled_shares=1,ownership_verified=True,fill_verified=True)
        intent=self.prepare()['intent']
        post_verified_paper_fill(event_id='new-entry',broker_order_id='new-order',symbol='AAPL',side='buy',filled_shares=1,ownership_verified=True,fill_verified=True)
        with self.assertRaises(IntentConflict):cancel_prepared_intent(intent['id'],{'confirmed':True})
        # Even if the new position closes, intervening fill evidence prevents a
        # never-submitted cancellation from substituting for reconciliation.
        post_verified_paper_fill(event_id='new-exit',broker_order_id='new-exit-order',symbol='AAPL',side='sell',filled_shares=1,ownership_verified=True,fill_verified=True)
        with self.assertRaises(IntentConflict):cancel_prepared_intent(intent['id'],{'confirmed':True})
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)

    def test_owner_cancel_returns_only_never_submitted_funds_and_retries_once(self):
        intent=self.prepare()['intent'];result=cancel_prepared_intent(intent['id'],{'confirmed':True})
        self.assertFalse(result['intent']['budget_reserved']);self.assertEqual(result['intent']['status'],'canceled')
        self.assertEqual(result['intent']['virtual_funds_returned_cents'],2100)
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],0)
        self.assertTrue(cancel_prepared_intent(intent['id'],{'confirmed':True})['duplicate'])
        retry=self.prepare();self.assertTrue(retry['duplicate']);self.assertEqual(retry['intent']['status'],'canceled')
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],0)
        with closing(database.get_connection()) as conn:
            events=conn.execute('SELECT * FROM pro_ticker_order_intent_events ORDER BY id').fetchall()
            self.assertEqual(len(events),2);self.assertIn('"trading_profit": false',events[-1]['evidence_json'])

    def test_concurrent_cancel_returns_virtual_funds_once(self):
        intent=self.prepare()['intent']
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:cancel_prepared_intent(intent['id'],{'confirmed':True}),range(4)))
        self.assertEqual(sum(not r['duplicate'] for r in results),1)
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],0)

    def test_cancel_rejects_uncertain_attempt_linked_order_and_mismatched_reservation(self):
        intent=self.prepare()['intent']
        with self.assertRaises(ValueError):cancel_prepared_intent(intent['id'],{'confirmed':1})
        for sql,params in [("UPDATE pro_ticker_order_intents SET status='uncertain'",()),
                           ("UPDATE pro_ticker_order_intents SET status='prepared',attempt_started_at='started'",()),
                           ("UPDATE pro_ticker_order_intents SET attempt_started_at=NULL,broker_order_id='broker-order'",()),
                           ("UPDATE pro_ticker_order_intents SET broker_order_id=NULL",())]:
            with closing(database.get_connection()) as conn:
                conn.execute(sql,params)
                if sql.endswith('broker_order_id=NULL'):conn.execute("UPDATE pro_ticker_paper_reservations SET expected_shares=99")
                conn.commit()
            with self.subTest(sql=sql),self.assertRaises(IntentConflict):cancel_prepared_intent(intent['id'],{'confirmed':True})
            self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)

    def test_cancel_failure_rolls_back_both_states_and_audit(self):
        intent=self.prepare()['intent']
        with closing(database.get_connection()) as conn:
            conn.execute("CREATE TRIGGER fail_cancel BEFORE INSERT ON pro_ticker_order_intent_events WHEN NEW.action='owner_canceled_never_submitted' BEGIN SELECT RAISE(ABORT,'test failure'); END");conn.commit()
        with self.assertRaises(Exception):cancel_prepared_intent(intent['id'],{'confirmed':True})
        self.assertEqual(list_order_intents()['intents'][0]['status'],'prepared')
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)

    def test_http_owner_origin_content_type_and_provider_failure(self):
        app=FastAPI()
        def owner(request):
            if request.headers.get('x-test-owner')!='yes':raise HTTPException(401)
        app.include_router(build_manual_signal_router(require_session=owner,quote_fetcher=self.quote,analyzer=fixtures.analysis))
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as c:
                path='/auto-trader/pro-ticker/order-intents';headers={'x-test-owner':'yes','Origin':'https://test'}
                self.assertEqual((await c.get(path)).status_code,401)
                self.assertEqual((await c.post(path,json=self.payload)).status_code,401)
                self.assertEqual((await c.post(path,json=self.payload,headers={**headers,'Origin':'https://evil'})).status_code,403)
                self.assertEqual((await c.post(path,content='{}',headers=headers)).status_code,415)
                self.quote.side_effect=RuntimeError('secret provider key')
                r=await c.post(path,json=self.payload,headers=headers)
                self.assertEqual(r.status_code,503);self.assertNotIn('secret provider key',r.text)
                self.quote.side_effect=None
                r=await c.post(path,json=self.payload,headers=headers);self.assertEqual(r.status_code,200)
                intent_id=r.json()['intent']['id']
                self.assertEqual((await c.post(path+'/'+intent_id+'/submit',json={},headers=headers)).status_code,404)
                self.assertEqual((await c.post(path+'/'+intent_id+'/cancel',json={'confirmed':True},headers=headers)).status_code,200)
                self.assertEqual((await c.get(path,headers=headers)).json()['total_count'],1)
        asyncio.run(run())
