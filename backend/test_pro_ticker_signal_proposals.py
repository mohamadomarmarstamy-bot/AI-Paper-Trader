"""Research-to-draft persistence, authorization and retry regression tests."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import asyncio
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import uuid
import httpx
from fastapi import FastAPI, HTTPException
import database
import test_pro_ticker_manual_signals as signals_fixture
from pro_ticker_manual_signals import submit_manual_signal, list_manual_signals, build_manual_signal_router
from pro_ticker_signal_proposals import save_signal_proposal, ProposalConflict


class SignalProposalTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        patcher=patch.object(database,'DATABASE_PATH',Path(temp.name)/'drafts.db');patcher.start();self.addCleanup(patcher.stop)
        self.now=time.time()
        self.quote=Mock(return_value={'p':10.5,'t':datetime.fromtimestamp(self.now-1,timezone.utc).isoformat()})
        self.signal_id=str(uuid.uuid4())
        submit_manual_signal({'symbol':'AAPL','request_id':self.signal_id},quote_fetcher=self.quote,analyzer=signals_fixture.analysis)
        self.payload={'signal_id':self.signal_id,'request_id':str(uuid.uuid4()),'shares':2,'stop_loss_percent':'5','take_profit_percent':'10'}

    def save(self, **overrides):
        return save_signal_proposal({**self.payload,**overrides},quote_fetcher=self.quote)

    def test_durable_linked_draft_has_no_order_reservation_or_training_authority(self):
        result=self.save();p=result['proposal']
        self.assertEqual(p['allocation_cents'],2100);self.assertEqual(p['entry_grade']['score'],4)
        self.assertEqual(p['symbol'],'AAPL');self.assertFalse(p['budget_reserved']);self.assertFalse(p['order_submitted'])
        self.assertFalse(p['execution_authorized']);self.assertFalse(p['entry_grade']['training_eligible'])
        self.assertEqual(p['reference_price_source'],'server_quote_at_draft_creation')
        self.assertEqual(list_manual_signals()['signals'][0]['proposal'],p)
        with closing(database.get_connection()) as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_paper_reservations'").fetchone())

    def test_retry_does_not_refetch_reprice_or_duplicate(self):
        first=self.save();self.quote.side_effect=AssertionError('must not fetch')
        retry=self.save();self.assertTrue(retry['duplicate']);self.assertEqual(first['proposal'],retry['proposal'])

    def test_conflicting_intent_or_second_request_for_same_grade_rejected(self):
        self.save()
        for changed in [{'shares':3},{'request_id':str(uuid.uuid4())},{'stop_loss_percent':5}]:
            with self.subTest(changed=changed),self.assertRaises(ProposalConflict):self.save(**changed)

    def test_rejects_browser_prices_outcomes_and_execution_switches(self):
        for key in ['reference_price','symbol','profit','execute','budget_reserved']:
            with self.subTest(key=key),self.assertRaises(ValueError):self.save(**{key:True})

    def test_invalid_size_is_rejected_before_provider_call(self):
        self.quote.reset_mock()
        for shares in [True,0,-1,1.5,100001,'2']:
            with self.subTest(shares=shares),self.assertRaises(ValueError):self.save(shares=shares)
        self.quote.assert_not_called()

    def test_stale_quote_and_broker_failure_allow_same_intent_retry(self):
        self.quote.return_value={'p':10,'t':datetime.fromtimestamp(self.now-120,timezone.utc).isoformat()}
        with self.assertRaises(ValueError):self.save()
        self.quote.side_effect=RuntimeError('private provider failure')
        with self.assertRaises(RuntimeError):self.save()
        self.quote.side_effect=None;self.quote.return_value={'p':10,'t':datetime.fromtimestamp(time.time()-1,timezone.utc).isoformat()}
        self.assertFalse(self.save()['duplicate'])

    def test_limits_and_budget_block_without_saving(self):
        for shares in [100000,5000]:
            with self.subTest(shares=shares),self.assertRaises(ValueError):self.save(shares=shares)
        from pro_ticker_settings import save_settings
        save_settings({'expected_revision':0,'settings':{'max_allocation_cents':1000,'max_stop_risk_cents':5000000,'discovery_article_limit':5}})
        with self.assertRaises(ValueError):self.save()
        self.assertIsNone(list_manual_signals()['signals'][0]['proposal'])

    def test_non_buy_or_missing_review_cannot_draft(self):
        with self.assertRaises(ValueError):self.save(signal_id=str(uuid.uuid4()))
        other=str(uuid.uuid4())
        submit_manual_signal({'symbol':'MSFT','request_id':other},quote_fetcher=self.quote,analyzer=lambda symbol:signals_fixture.analysis(symbol,0))
        with self.assertRaises(ValueError):self.save(signal_id=other)

    def test_concurrent_retries_save_one_immutable_draft(self):
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:self.save(),range(4)))
        self.assertEqual(sum(not r['duplicate'] for r in results),1)
        self.assertTrue(all(r['proposal']==results[0]['proposal'] for r in results))

    def test_insert_failure_rolls_back_and_retry_recovers(self):
        # A database rejection after BEGIN must not create a half-written draft.
        self.save()
        with closing(database.get_connection()) as conn:
            conn.execute('DELETE FROM pro_ticker_signal_proposals');conn.execute("CREATE TRIGGER fail_draft BEFORE INSERT ON pro_ticker_signal_proposals BEGIN SELECT RAISE(ABORT,'test failure'); END");conn.commit()
        with self.assertRaises(Exception):self.save()
        self.assertIsNone(list_manual_signals()['signals'][0]['proposal'])
        with closing(database.get_connection()) as conn:conn.execute('DROP TRIGGER fail_draft');conn.commit()
        self.assertFalse(self.save()['duplicate'])

    def test_wrong_symbol_or_quote_expiring_during_save_cannot_persist(self):
        self.quote.return_value['symbol']='MSFT'
        with self.assertRaises(ValueError):self.save()
        self.quote.return_value.pop('symbol')
        clock=Mock(side_effect=[self.now,self.now,self.now+100])
        with self.assertRaises(ValueError):save_signal_proposal(self.payload,quote_fetcher=self.quote,clock=clock)
        self.assertIsNone(list_manual_signals()['signals'][0]['proposal'])

    def test_http_auth_origin_validation_and_sanitized_provider_failure(self):
        app=FastAPI()
        def auth(request):
            if request.headers.get('x-test-owner')!='yes':raise HTTPException(401)
        app.include_router(build_manual_signal_router(require_session=auth,quote_fetcher=self.quote,analyzer=signals_fixture.analysis))
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as client:
                url='/auto-trader/pro-ticker/manual-signals/proposal'
                self.assertEqual((await client.post(url,json=self.payload)).status_code,401)
                headers={'x-test-owner':'yes','origin':'https://evil'}
                self.assertEqual((await client.post(url,json=self.payload,headers=headers)).status_code,403)
                headers['origin']='https://test'
                self.assertEqual((await client.post(url,json={**self.payload,'execute':True},headers=headers)).status_code,422)
                self.quote.side_effect=RuntimeError('secret-value')
                response=await client.post(url,json=self.payload,headers=headers)
                self.assertEqual(response.status_code,503);self.assertNotIn('secret-value',response.text)
                self.quote.side_effect=None
                self.assertEqual((await client.post(url,json=self.payload,headers=headers)).status_code,200)
        asyncio.run(run())
