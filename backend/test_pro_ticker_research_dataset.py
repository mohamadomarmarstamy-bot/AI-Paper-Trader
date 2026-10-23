"""Loss-inclusive export, feature/label separation, limits and owner access."""
import asyncio
from contextlib import closing
import csv
from datetime import datetime,timezone
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
import uuid
import httpx
from fastapi import FastAPI,HTTPException
import database
import test_pro_ticker_manual_signals as fixtures
from pro_ticker_manual_signals import submit_manual_signal,build_manual_signal_router
from pro_ticker_signal_learning import evaluate_forward_outcome
from pro_ticker_research_dataset import research_dataset,dataset_csv


class ResearchDatasetTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'dataset.db');p.start();self.addCleanup(p.stop)
        self.now=time.time()
        self.quote=Mock(return_value={'p':10,'t':datetime.fromtimestamp(self.now-1,timezone.utc).isoformat()})

    def review(self,symbol='AAPL',analysis=None):
        return submit_manual_signal({'request_id':str(uuid.uuid4()),'symbol':symbol},quote_fetcher=self.quote,
            analyzer=analysis or fixtures.analysis,clock=lambda:self.now)['signal']

    def forward(self,review,price):
        later=self.now+86410
        return evaluate_forward_outcome(review['request_id'],quote_fetcher=lambda symbol:{'p':price,'t':datetime.fromtimestamp(later-1,timezone.utc).isoformat()},clock=lambda:later)

    def test_empty_read_has_no_fake_metrics_or_database_writes(self):
        data=research_dataset();self.assertEqual(data['rows'],[]);self.assertEqual(data['training_ready_samples'],0)
        self.assertFalse(data['training_enabled']);self.assertFalse(data['model_updated'])
        with closing(database.get_connection()) as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()[0],0)
        self.assertEqual(len(list(csv.reader(io.StringIO(dataset_csv(data))))),1)

    def test_gains_losses_flat_and_unevaluated_reviews_all_remain_in_export(self):
        for symbol,price in [('AAPL',12),('MSFT',8),('AMD',10)]:self.forward(self.review(symbol),price)
        self.review('NVDA');self.review('META',analysis=lambda symbol:None)
        data=research_dataset();self.assertEqual(data['total_count'],5);self.assertEqual(data['window_count'],5)
        self.assertEqual(data['graded_count'],4);self.assertEqual(data['evaluated_count'],3);self.assertEqual(data['unevaluated_count'],2)
        values=sorted(r['future_outcome']['return_percent'] for r in data['rows'] if r['future_outcome'])
        self.assertEqual(values,[-20,0,20])
        self.assertTrue(all(r['training_eligible'] is False for r in data['rows']))
        self.assertTrue(all('indicator_asof_times_unverified' in r['training_exclusions'] for r in data['rows']))
        missing=next(r for r in data['rows'] if r['symbol']=='META')
        self.assertIsNone(missing['entry_features']);self.assertIn('completed_grade_missing',missing['training_exclusions'])
        csv_rows=list(csv.DictReader(io.StringIO(dataset_csv(data))))
        self.assertEqual(len(csv_rows),5)
        loss=next(r for r in csv_rows if r['symbol']=='MSFT')
        self.assertEqual(loss['forward_return_percent'],'-20.0');self.assertEqual(loss['training_eligible'],'false')

    def test_future_and_unapproved_fields_cannot_become_entry_features(self):
        review=self.review(analysis=lambda symbol:{**fixtures.analysis(symbol),'future_return':999,'realized_profit':1234,'reported_high':999})
        self.forward(review,12)
        data=research_dataset();features=data['rows'][0]['entry_features']
        self.assertEqual(features['score'],4)
        for field in ['future_return','realized_profit','reported_high','return_percent','observed_price']:
            self.assertNotIn(field,features)
        self.assertEqual(data['rows'][0]['future_outcome']['return_percent'],20)
        self.assertNotIn('999',json.dumps(data))

    def test_reference_and_grade_remain_immutable_after_later_evaluation(self):
        self.review();before=research_dataset()['rows'][0]
        self.forward({'request_id':before['request_id']},8)
        after=research_dataset()['rows'][0]
        self.assertEqual(before['entry_features'],after['entry_features'])
        self.assertEqual(before['entry_reference'],after['entry_reference'])
        self.assertIsNone(before['future_outcome']);self.assertEqual(after['future_outcome']['return_percent'],-20)

    def test_window_limit_is_explicit_and_never_drops_only_losers(self):
        self.review('AAPL');self.review('MSFT');self.review('AMD')
        data=research_dataset(limit=2)
        self.assertEqual(data['total_count'],3);self.assertEqual(len(data['rows']),2);self.assertTrue(data['has_more'])
        rows=list(csv.DictReader(io.StringIO(dataset_csv(data))))
        self.assertTrue(all(r['export_has_more']=='true' and r['export_total_count']=='3' and r['export_window_count']=='2' for r in rows))
        for limit in [True,0,-1,501,'5']:
            with self.subTest(limit=limit),self.assertRaises(ValueError):research_dataset(limit=limit)

    def test_csv_text_cells_cannot_inject_spreadsheet_formulas(self):
        self.review();data=research_dataset()
        for text in ['=1+1','+SUM(A1)','-cmd','@sum(A1)','  =1+1','\tformula','\nformula']:
            data['rows'][0]['symbol']=text
            row=list(csv.DictReader(io.StringIO(dataset_csv(data))))[0]
            self.assertEqual(row['symbol'],"'"+text)

    def test_http_auth_formats_cache_controls_and_sanitized_failure(self):
        app=FastAPI()
        def owner(request):
            if request.headers.get('x-test-owner')!='yes':raise HTTPException(401)
        app.include_router(build_manual_signal_router(require_session=owner,quote_fetcher=self.quote,analyzer=fixtures.analysis))
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as c:
                path='/auto-trader/pro-ticker/manual-signals/dataset';headers={'x-test-owner':'yes'}
                self.assertEqual((await c.get(path)).status_code,401)
                self.assertEqual((await c.get(path+'?format=csv')).status_code,401)
                self.assertEqual((await c.get(path+'?format=bad',headers=headers)).status_code,422)
                self.assertEqual((await c.get(path+'?profit=999',headers=headers)).status_code,422)
                response=await c.get(path,headers=headers)
                self.assertEqual(response.status_code,200);self.assertEqual(response.headers['cache-control'],'no-store')
                response=await c.get(path+'?format=csv',headers=headers)
                self.assertEqual(response.status_code,200);self.assertTrue(response.headers['content-type'].startswith('text/csv'))
                self.assertEqual(response.headers['x-research-window-count'],'0')
                self.assertIn('attachment;',response.headers['content-disposition'])
                with patch('pro_ticker_research_dataset.research_dataset',side_effect=RuntimeError('secret key')):
                    response=await c.get(path,headers=headers)
                    self.assertEqual(response.status_code,503);self.assertNotIn('secret key',response.text)
        asyncio.run(run())
