"""Entry-time grade immutability, retries, missing data and authenticated routing."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch
import uuid
import httpx
from fastapi import FastAPI,HTTPException
import database
from pro_ticker_manual_signals import (VERSION,SignalConflict,quote_snapshot,technical_grade,
    submit_manual_signal,list_manual_signals,build_manual_signal_router)


def analysis(symbol='AAPL',score=4):
    return {'symbol':symbol,'score':score,'signal':'BUY' if score>=3 else 'SELL' if score<=-3 else 'HOLD','confidence':85,
            'indicators':{'sma_20':10,'sma_50':9,'rsi':55,'five_day_momentum':6,'volume_ratio':1.6},
            'reason':['Price is above its average.']}


class ManualSignalTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'signals.db');p.start();self.addCleanup(p.stop)
        self.now=time.time();self.payload={'symbol':'aapl','request_id':str(uuid.uuid4())}
        self.quote=Mock(return_value={'p':10.5,'t':datetime.fromtimestamp(self.now-1,timezone.utc).isoformat()})
        self.analyzer=Mock(return_value=analysis())

    def submit(self,payload=None,**kwargs):
        return submit_manual_signal(payload or self.payload,quote_fetcher=self.quote,analyzer=self.analyzer,**kwargs)

    def test_grade_and_reference_are_saved_without_orders_or_training(self):
        result=self.submit();row=result['signal']
        self.assertEqual(row['entry_grade']['grade'],'A');self.assertEqual(row['entry_grade']['version'],VERSION)
        self.assertEqual(row['market_snapshot']['price'],10.5);self.assertTrue(row['market_snapshot']['fresh'])
        self.assertEqual(row['symbol'],'AAPL');self.assertFalse(row['entry_grade']['training_eligible'])
        self.assertIsNone(row['entry_grade']['analysis_market_timestamp'])
        self.assertEqual(result['orders_submitted'],0);self.assertFalse(result['automatic_execution_enabled'])
        self.assertEqual(list_manual_signals()['signals'][0],row)

    def test_unchanged_retry_keeps_entry_score_even_after_market_changes(self):
        first=self.submit();self.quote.side_effect=AssertionError('must not refetch');self.analyzer.side_effect=AssertionError('must not regrade')
        duplicate=self.submit();self.assertTrue(duplicate['duplicate']);self.assertEqual(duplicate['signal'],first['signal'])
        self.assertEqual(self.quote.call_count,1);self.assertEqual(self.analyzer.call_count,1)
        self.assertEqual(list_manual_signals()['total_count'],1)

    def test_reusing_request_id_for_another_symbol_is_rejected(self):
        self.submit()
        with self.assertRaises(SignalConflict):self.submit({**self.payload,'symbol':'MSFT'})

    def test_missing_providers_are_recorded_without_fake_grade_or_price(self):
        self.quote.side_effect=RuntimeError('private-key');self.analyzer.side_effect=RuntimeError('private-key')
        row=self.submit()['signal'];self.assertEqual(row['status'],'unavailable')
        self.assertIsNone(row['market_snapshot']['price']);self.assertIsNone(row['entry_grade']['grade'])
        self.assertNotIn('private-key',str(row))

    def test_raw_future_outcomes_do_not_enter_grade_features(self):
        raw={**analysis(),'future_return':999,'reported_high':100000,'broker_order':{'id':'foreign'}}
        value=technical_grade(raw,'AAPL')
        self.assertNotIn('future_return',value);self.assertNotIn('reported_high',value)
        self.assertNotIn('broker_order',value);self.assertFalse(value['training_eligible'])

    def test_grade_scale_and_consistency(self):
        for score,letter in [(6,'A'),(4,'A'),(3,'B'),(2,'C'),(0,'C'),(-2,'C'),(-3,'D'),(-6,'D')]:
            self.assertEqual(technical_grade(analysis(score=score),'AAPL')['grade'],letter)
        for raw in [{**analysis(),'score':True},{**analysis(),'confidence':float('nan')},
                    {**analysis(),'symbol':'MSFT'},{**analysis(),'signal':'SELL'},
                    {**analysis(),'indicators':{}},{**analysis(),'reason':[{'private':'payload'}]}]:
            self.assertEqual(technical_grade(raw,'AAPL')['status'],'unavailable')

    def test_stale_invalid_and_future_quotes_never_claim_freshness(self):
        for raw in [{'p':True,'t':'2026-10-17T00:00:00Z'},{'p':float('nan'),'t':'2026-10-17T00:00:00Z'},
                    {'p':1,'t':'2026-10-17'},{'p':-1,'t':'2026-10-17T00:00:00Z'},
                    {'p':1,'t':datetime.fromtimestamp(self.now+1,timezone.utc).isoformat()}]:
            value=quote_snapshot(raw,now=self.now);self.assertFalse(value['fresh']);self.assertIsNone(value['price'])
        stale=quote_snapshot({'p':10,'t':datetime.fromtimestamp(self.now-91,timezone.utc).isoformat()},now=self.now)
        self.assertEqual(stale['status'],'captured');self.assertFalse(stale['fresh'])

    def test_quote_is_captured_before_analysis_and_never_replaced_by_analysis_price(self):
        order=[]
        def quote(symbol):order.append('quote');return self.quote.return_value
        def analyze(symbol):order.append('analysis');return {**analysis(),'price':999}
        result=submit_manual_signal(self.payload,quote_fetcher=quote,analyzer=analyze)
        self.assertEqual(order,['quote','analysis']);self.assertEqual(result['signal']['market_snapshot']['price'],10.5)

    def test_concurrent_same_symbol_has_one_pending_review(self):
        entered=threading.Event();resume=threading.Event()
        def analyze(symbol):entered.set();self.assertTrue(resume.wait(5));return analysis(symbol)
        with ThreadPoolExecutor(max_workers=1) as pool:
            task=pool.submit(submit_manual_signal,self.payload,quote_fetcher=self.quote,analyzer=analyze)
            self.assertTrue(entered.wait(5))
            duplicate=self.submit();self.assertTrue(duplicate['duplicate']);self.assertEqual(duplicate['signal']['status'],'pending')
            with self.assertRaises(SignalConflict):self.submit({**self.payload,'request_id':str(uuid.uuid4())})
            resume.set();self.assertEqual(task.result(timeout=5)['signal']['status'],'graded')
        self.assertEqual(list_manual_signals()['total_count'],1)

    def test_late_worker_cannot_post_an_expired_grade(self):
        now=self.now;ticks=iter([now,now,now+301])
        result=self.submit(clock=lambda:next(ticks))
        self.assertEqual(result['signal']['status'],'interrupted');self.assertIsNone(result['signal']['entry_grade'])

    def test_unknown_execution_fields_batches_and_malformed_ids_are_rejected(self):
        for payload in [{**self.payload,'execute':True},{**self.payload,'symbol':'AAPL MSFT'},
                        {**self.payload,'request_id':'bad'},{**self.payload,'symbol':True},
                        {**self.payload,'quote':{'p':99}}]:
            with self.subTest(payload=payload),self.assertRaises(ValueError):self.submit(payload)
        self.quote.assert_not_called();self.analyzer.assert_not_called()

    def test_http_authorization_origin_and_saved_history(self):
        def auth(request):
            if request.headers.get('x-owner')!='yes':raise HTTPException(401,'Sign in')
        app=FastAPI();app.include_router(build_manual_signal_router(require_session=auth,quote_fetcher=self.quote,analyzer=self.analyzer))
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as c:
                path='/auto-trader/pro-ticker/manual-signals';headers={'x-owner':'yes','Origin':'https://test'}
                self.assertEqual((await c.get(path)).status_code,401)
                self.assertEqual((await c.post(path,json=self.payload)).status_code,401)
                self.assertEqual((await c.post(path,json=self.payload,headers={**headers,'Origin':'https://other'})).status_code,403)
                self.assertEqual((await c.post(path,content='{}',headers=headers)).status_code,415)
                self.assertEqual((await c.post(path,json={**self.payload,'execute':True},headers=headers)).status_code,422)
                first=await c.post(path,json=self.payload,headers=headers);self.assertEqual(first.status_code,200)
                second=await c.post(path,json=self.payload,headers=headers);self.assertTrue(second.json()['duplicate'])
                history=await c.get(path,headers=headers);self.assertEqual(history.json()['total_count'],1)
                self.assertEqual(history.json()['signals'][0]['entry_grade']['score'],4)
        asyncio.run(send());self.assertEqual(self.analyzer.call_count,1)
