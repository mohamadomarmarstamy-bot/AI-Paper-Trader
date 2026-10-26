"""Uncertain PAPER attempts are looked up, never retried as new buys."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime,timezone
import unittest
from unittest.mock import patch
import uuid
import httpx
from fastapi import FastAPI
import database
import test_pro_ticker_broker_admission as fixtures
from pro_ticker_submission_recovery import recover_paper_submission
from pro_ticker_order_intents import IntentConflict,cancel_prepared_intent,list_order_intents
from pro_ticker_workspace import workspace_snapshot
from pro_ticker_manual_signals import build_manual_signal_router


class SubmissionRecoveryTests(unittest.TestCase):
    make_draft=fixtures.BrokerAdmissionTests.make_draft
    prepare=fixtures.BrokerAdmissionTests.prepare

    def setUp(self):
        fixtures.BrokerAdmissionTests.setUp(self)
        self.gateway.find_order.return_value={'id':str(uuid.uuid4()),'client_order_id':self.intent['client_order_id'],
            'symbol':'AAPL','side':'buy','qty':'2','filled_qty':'1','status':'partially_filled'}
        with closing(database.get_connection()) as conn:
            conn.execute("UPDATE pro_ticker_order_intents SET status='submitting',attempt_started_at=? WHERE id=?",(datetime.now(timezone.utc).isoformat(),self.intent['id']));conn.commit()

    def recover(self):return recover_paper_submission(self.intent['id'],context=self.context)

    def test_existing_order_is_recovered_with_bound_identity_and_idempotent_fills(self):
        first=self.recover();self.assertTrue(first['resolved']);self.assertFalse(first['order_resubmitted']);self.assertFalse(first['budget_released'])
        self.assertFalse(first['protection_verified']);self.assertEqual(first['posted_entry_shares'],1)
        self.assertEqual(first['intent']['status'],'broker_linked')
        second=self.recover();self.assertTrue(second['duplicate']);self.assertEqual(second['posted_entry_shares'],0)
        self.assertEqual(workspace_snapshot()['positions']['rows'][0]['shares'],1)
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)
        self.gateway.submit.assert_not_called();self.gateway.post.assert_not_called()
        self.gateway.find_order.assert_called_with(self.intent['client_order_id'])
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM pro_ticker_order_intent_events WHERE action='broker_order_recovered'").fetchone()[0],1)

    def test_not_found_preserves_uncertainty_funds_and_cancellation_block(self):
        self.gateway.find_order.return_value=None
        result=self.recover();self.assertFalse(result['resolved']);self.assertEqual(result['intent']['status'],'uncertain')
        self.assertIsNone(result['intent']['order_submitted']);self.assertTrue(result['intent']['budget_reserved'])
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)
        with self.assertRaises(IntentConflict):cancel_prepared_intent(self.intent['id'],{'confirmed':True})
        self.recover();self.gateway.post.assert_not_called();self.gateway.submit.assert_not_called()
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM pro_ticker_order_intent_events WHERE action='broker_lookup_unresolved'").fetchone()[0],1)

    def test_later_visibility_resolves_same_intent_without_new_buy(self):
        order=self.gateway.find_order.return_value;self.gateway.find_order.return_value=None;self.recover()
        self.gateway.find_order.return_value=order;self.assertTrue(self.recover()['resolved'])
        self.assertEqual(list_order_intents()['total_count'],1);self.assertEqual(workspace_snapshot()['reservations']['total'],1)

    def test_partial_and_complete_fill_replay_and_stale_snapshots(self):
        self.recover();order=self.gateway.find_order.return_value
        order.update(filled_qty='2',status='filled');self.assertEqual(self.recover()['posted_entry_shares'],1)
        order.update(filled_qty='1',status='partially_filled');stale=self.recover()
        self.assertTrue(stale['stale_snapshot']);self.assertEqual(stale['posted_entry_shares'],0)
        self.assertEqual(workspace_snapshot()['positions']['rows'][0]['shares'],2)
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)

    def test_zero_fill_rejection_does_not_automatically_release_budget(self):
        self.gateway.find_order.return_value.update(filled_qty='0',status='rejected')
        result=self.recover();self.assertTrue(result['resolved']);self.assertEqual(result['posted_entry_shares'],0)
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)
        self.assertTrue(result['intent']['budget_reserved']);self.assertFalse(result['budget_released'])

    def test_foreign_or_malformed_order_cannot_link_or_post(self):
        original=dict(self.gateway.find_order.return_value)
        changes=[{'client_order_id':'foreign'},{'symbol':'MSFT'},{'side':'sell'},{'qty':'3'},
                 {'filled_qty':True},{'filled_qty':'NaN'},{'filled_qty':'3'},{'status':'unknown'},{'id':'not-uuid'}]
        for change in changes:
            self.gateway.find_order.return_value={**original,**change}
            with self.subTest(change=change),self.assertRaises(ValueError):self.recover()
        self.assertEqual(workspace_snapshot()['positions']['total'],0)
        with closing(database.get_connection()) as conn:self.assertIsNone(conn.execute('SELECT broker_order_id FROM pro_ticker_paper_reservations').fetchone()[0])

    def test_no_recorded_attempt_cannot_use_recovery_to_import_an_order(self):
        with closing(database.get_connection()) as conn:
            conn.execute("UPDATE pro_ticker_order_intents SET status='prepared',attempt_started_at=NULL");conn.commit()
        with self.assertRaises(IntentConflict):self.recover()
        self.gateway.find_order.assert_not_called()

    def test_wrong_account_identity_blocks_order_lookup(self):
        self.gateway.read_account.side_effect=lambda:{'id':str(uuid.uuid4())}
        with self.assertRaises(ValueError):self.recover()
        self.gateway.find_order.assert_not_called()

    def test_concurrent_recovery_links_and_posts_once(self):
        with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(lambda _:self.recover(),range(3)))
        self.assertEqual(sum(r['posted_entry_shares'] for r in results),1)
        self.assertEqual(workspace_snapshot()['journal']['total'],1)
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)

    def test_recovery_audit_failure_rolls_back_intent_and_link(self):
        with closing(database.get_connection()) as conn:
            conn.execute("CREATE TRIGGER reject_recovery BEFORE INSERT ON pro_ticker_order_intent_events WHEN NEW.action='broker_order_recovered' BEGIN SELECT RAISE(ABORT,'test failure'); END");conn.commit()
        with self.assertRaises(Exception):self.recover()
        with closing(database.get_connection()) as conn:
            self.assertIsNone(conn.execute('SELECT broker_order_id FROM pro_ticker_paper_reservations').fetchone()[0])
            conn.execute('DROP TRIGGER reject_recovery');conn.commit()
        self.assertTrue(self.recover()['resolved'])

    def test_interrupted_fill_posting_recovers_on_same_order_retry(self):
        with patch('pro_ticker_verified_fill_bridge.account_linked_paper_entry_snapshot',side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):self.recover()
        self.assertEqual(list_order_intents()['intents'][0]['status'],'broker_linked')
        self.assertEqual(workspace_snapshot()['journal']['total'],0)
        result=self.recover();self.assertTrue(result['duplicate']);self.assertEqual(result['posted_entry_shares'],1)

    def test_linked_order_disappearing_never_unlinks_or_releases(self):
        self.recover();self.gateway.find_order.return_value=None
        with self.assertRaises(IntentConflict):self.recover()
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)
        self.assertEqual(list_order_intents()['intents'][0]['status'],'broker_linked')

    def test_http_recovery_rejects_browser_order_evidence_and_sanitizes_errors(self):
        app=FastAPI();app.include_router(build_manual_signal_router(require_session=lambda _:None,quote_fetcher=self.quote,analyzer=lambda _:None,execution_context=self.context))
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as client:
                path='/auto-trader/pro-ticker/order-intents/'+self.intent['id']+'/recover';headers={'Origin':'https://test'}
                self.assertEqual((await client.post(path,json={'broker_order':self.gateway.find_order.return_value},headers=headers)).status_code,422)
                self.gateway.find_order.side_effect=RuntimeError('private transport key')
                response=await client.post(path,json={},headers=headers)
                self.assertEqual(response.status_code,503);self.assertNotIn('private transport key',response.text)
                self.gateway.find_order.side_effect=None
                self.assertEqual((await client.post(path,json={},headers=headers)).status_code,200)
        asyncio.run(run())
