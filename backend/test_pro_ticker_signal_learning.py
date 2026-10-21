"""Separate forward outcomes preserve entry evidence and include losses."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import unittest
from unittest.mock import Mock,patch
import uuid
import httpx
from fastapi import FastAPI,HTTPException
import test_pro_ticker_manual_signals as fixtures
from pro_ticker_manual_signals import submit_manual_signal,list_manual_signals,build_manual_signal_router
from pro_ticker_signal_learning import evaluate_forward_outcome,learning_summary,ForwardNotDue,KIND


class ForwardResearchTests(unittest.TestCase):
    setUp=fixtures.ManualSignalTests.setUp

    def entry(self,score=4):
        payload={**self.payload,'request_id':str(uuid.uuid4())}
        return submit_manual_signal(payload,quote_fetcher=self.quote,analyzer=lambda _:fixtures.analysis(score=score),clock=lambda:self.now)['signal']

    def evaluate(self,entry,price=12):
        future=self.now+86410
        provider=Mock(return_value={'p':price,'t':datetime.fromtimestamp(future-1,timezone.utc).isoformat()})
        return evaluate_forward_outcome(entry['request_id'],quote_fetcher=provider,clock=lambda:future)

    def test_early_check_never_fetches_future_quote(self):
        entry=self.entry();provider=Mock(side_effect=AssertionError('too early'))
        with self.assertRaises(ForwardNotDue):evaluate_forward_outcome(entry['request_id'],quote_fetcher=provider,clock=lambda:self.now+100)
        provider.assert_not_called();self.assertEqual(learning_summary()['evaluated_count'],0)

    def test_outcome_is_saved_separately_without_regrading_or_training(self):
        entry=self.entry();result=self.evaluate(entry)
        self.assertEqual(result['outcome']['kind'],KIND);self.assertFalse(result['model_updated'])
        self.assertAlmostEqual(result['outcome']['return_percent'],(12/10.5-1)*100)
        loaded=list_manual_signals()['signals'][0]
        self.assertEqual(loaded['entry_grade'],entry['entry_grade']);self.assertEqual(loaded['market_snapshot'],entry['market_snapshot'])
        self.assertEqual(loaded['forward_outcome'],result['outcome'])
        self.assertFalse(result['outcome']['training_eligible']);self.assertEqual(learning_summary()['training_ready_samples'],0)

    def test_retry_never_replaces_outcome_with_a_later_better_price(self):
        entry=self.entry();first=self.evaluate(entry,9)
        provider=Mock(side_effect=AssertionError('retry must not refetch'))
        retry=evaluate_forward_outcome(entry['request_id'],quote_fetcher=provider,clock=lambda:self.now+999999)
        self.assertTrue(retry['duplicate']);self.assertEqual(first['outcome'],retry['outcome']);provider.assert_not_called()

    def test_same_grade_summary_includes_winners_and_losers(self):
        first=self.entry();self.evaluate(first,12)
        second=self.entry();self.evaluate(second,9)
        summary=learning_summary();group=summary['groups'][0]
        self.assertEqual(group['samples'],2);self.assertEqual(group['positive'],1);self.assertEqual(group['negative'],1)
        self.assertAlmostEqual(group['mean_forward_return_percent'],0)
        self.assertFalse(summary['training_enabled']);self.assertFalse(summary['model_updated'])

    def test_stale_reference_cannot_become_training_evidence(self):
        self.quote.return_value={'p':10.5,'t':datetime.fromtimestamp(self.now-1000,timezone.utc).isoformat()}
        entry=self.entry()
        with self.assertRaises(ValueError):self.evaluate(entry)
        self.assertEqual(learning_summary()['evaluated_count'],0)

    def test_stale_or_future_forward_quote_is_rejected(self):
        entry=self.entry();future=self.now+86410
        for stamp in (self.now,self.now+90000):
            provider=lambda _:{'p':12,'t':datetime.fromtimestamp(stamp,timezone.utc).isoformat()}
            with self.assertRaises(ValueError):evaluate_forward_outcome(entry['request_id'],quote_fetcher=provider,clock=lambda:future)
        self.assertEqual(learning_summary()['evaluated_count'],0)

    def test_concurrent_checks_keep_one_outcome(self):
        entry=self.entry()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda price:self.evaluate(entry,price),[9,12]))
        self.assertCountEqual([r['duplicate'] for r in results],[False,True])
        self.assertEqual(results[0]['outcome'],results[1]['outcome']);self.assertEqual(learning_summary()['evaluated_count'],1)

    def test_summary_keeps_different_score_versions_and_grades_separate(self):
        first=self.entry(4);self.evaluate(first)
        second=self.entry(-4);self.evaluate(second,9)
        self.assertEqual({r['grade'] for r in learning_summary()['groups']},{'A','D'})

    def test_http_outcomes_require_auth_origin_and_reject_browser_supplied_profit(self):
        entry=self.entry()
        def auth(request):
            if request.headers.get('x-owner')!='yes':raise HTTPException(401,'Sign in')
        app=FastAPI();app.include_router(build_manual_signal_router(require_session=auth,quote_fetcher=self.quote,analyzer=self.analyzer))
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as c:
                path='/auto-trader/pro-ticker/manual-signals/'+entry['request_id']+'/forward-review';headers={'x-owner':'yes','Origin':'https://test'}
                self.assertEqual((await c.post(path,json={})).status_code,401)
                self.assertEqual((await c.post(path,json={},headers={**headers,'Origin':'https://other'})).status_code,403)
                self.assertEqual((await c.post(path,json={'profit':999},headers=headers)).status_code,422)
                self.assertEqual((await c.post(path,json={},headers=headers)).status_code,409)
                self.assertEqual((await c.get('/auto-trader/pro-ticker/manual-signals/learning')).status_code,401)
                summary=await c.get('/auto-trader/pro-ticker/manual-signals/learning',headers=headers)
                self.assertEqual(summary.status_code,200);self.assertFalse(summary.json()['training_enabled'])
        asyncio.run(run())

    def test_http_forward_review_history_and_learning_use_the_same_saved_outcome(self):
        entry=self.entry();future=self.now+86410
        provider=Mock(return_value={'p':9,'t':datetime.fromtimestamp(future-1,timezone.utc).isoformat()})
        app=FastAPI();app.include_router(build_manual_signal_router(require_session=lambda _:None,quote_fetcher=provider,analyzer=self.analyzer))
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as c:
                result=await c.post('/auto-trader/pro-ticker/manual-signals/'+entry['request_id']+'/forward-review',json={},headers={'Origin':'https://test'})
                self.assertEqual(result.status_code,200);self.assertFalse(result.json()['model_updated'])
                history=(await c.get('/auto-trader/pro-ticker/manual-signals')).json()
                self.assertEqual(history['signals'][0]['forward_outcome'],result.json()['outcome'])
                summary=(await c.get('/auto-trader/pro-ticker/manual-signals/learning')).json()
                self.assertEqual(summary['groups'][0]['negative'],1)
                self.assertEqual(summary['training_ready_samples'],0)
                self.assertEqual(history['signals'][0]['entry_grade'],entry['entry_grade'])
        with patch('pro_ticker_signal_learning.time.time',return_value=future):asyncio.run(run())
