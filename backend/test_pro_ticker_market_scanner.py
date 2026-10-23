"""Scanner HTTP -> provider -> persisted research -> reload, without trading."""
import asyncio
import importlib.util
from contextlib import closing
from datetime import datetime,timezone,timedelta
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
import uuid
import httpx
from fastapi import FastAPI,HTTPException
import database
from pro_ticker_market_scanner import run_market_scan,scanner_status,ScanConflict,build_market_scanner_router,yahoo_research_snapshot


class MarketScannerTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'scan.db');p.start();self.addCleanup(p.stop)
        self.now=time.time();self.clock=lambda:self.now
        self.provider=Mock(side_effect=lambda symbol:{'symbol':symbol,'price':'10.50','volume':300,
            'average_volume':'100.5','observed_at':datetime.fromtimestamp(self.now-1,timezone.utc).isoformat(),
            'source':'synthetic_fixture','price_change_percent':'5'})
        self.payload={'request_id':str(uuid.uuid4()),'symbols':['AAA','BBB']}

    def run_scan(self,payload=None):return run_market_scan(payload or self.payload,fetch_snapshot=self.provider,clock=self.clock)

    def test_persisted_real_fields_counts_and_retry_reopens_without_provider(self):
        first=self.run_scan();self.assertEqual(first['run']['accepted'],2)
        self.assertEqual(first['run']['candidates'][0]['price_change_percent'],'5')
        self.assertEqual(first['run']['candidates'][0]['average_volume'],'100.5')
        self.assertIsNone(first['run']['candidates'][0]['research_score'])
        self.provider.side_effect=AssertionError('must not refetch')
        retry=self.run_scan();self.assertTrue(retry['duplicate'])
        self.assertEqual(first['run'],scanner_status(clock=self.clock)['run'])
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM pro_ticker_market_research_runs').fetchone()[0],1)
            self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_order_intents'").fetchone())

    def test_input_validation_duplicate_symbols_and_conflicting_retry(self):
        for symbols in (['bad/path'],['aaa'],[True],list('ABCDEFGHI')):
            with self.subTest(symbols=symbols),self.assertRaises(ValueError):self.run_scan({**self.payload,'symbols':symbols})
        self.provider.assert_not_called()
        self.run_scan({**self.payload,'symbols':['AAA','AAA']});self.provider.assert_called_once_with('AAA')
        with self.assertRaises(ScanConflict):self.run_scan()

    def test_rotating_saved_universe_does_not_repeat_first_batch(self):
        with closing(database.get_connection()) as conn:
            conn.execute('CREATE TABLE pro_ticker_research(symbol TEXT)')
            conn.executemany('INSERT INTO pro_ticker_research VALUES (?)',[(s,) for s in 'ABCDEFGHIJ']);conn.commit()
        first=self.run_scan({'request_id':str(uuid.uuid4()),'symbols':[]})
        self.now+=1
        second=self.run_scan({'request_id':str(uuid.uuid4()),'symbols':[]})
        self.assertEqual(first['run']['symbols'],list('ABCDEFGH'))
        self.assertEqual(second['run']['symbols'][:2],['I','J'])
        self.assertEqual(first['run']['universe_count'],10)

    def test_empty_saved_universe_is_not_fabricated(self):
        result=self.run_scan({**self.payload,'symbols':[]})
        self.assertEqual(result['run']['requested'],0);self.provider.assert_not_called()

    def test_provider_failure_and_expired_quotes_are_explicit_and_sanitized(self):
        original=self.provider.side_effect
        def provider(symbol):
            if symbol=='BBB':raise RuntimeError('private secret')
            return original(symbol)
        self.provider.side_effect=provider;result=self.run_scan()
        self.assertEqual((result['run']['accepted'],result['run']['failed']),(1,1));self.assertNotIn('private secret',str(result))

    def test_quote_expiring_during_collection_is_not_committed_as_fresh(self):
        quote=self.provider.side_effect('AAA');self.provider.side_effect=lambda symbol:quote
        clock=Mock(side_effect=[self.now,self.now,self.now+121,self.now+121,self.now+121,self.now+121,self.now+121])
        result=run_market_scan({**self.payload,'symbols':['AAA']},fetch_snapshot=self.provider,clock=clock)
        self.assertEqual(result['run']['accepted'],0);self.assertEqual(result['run']['failed'],1)

    def test_single_lease_and_interrupted_retry_preserve_record(self):
        original=self.provider.side_effect
        def nested(symbol):
            with self.assertRaises(ScanConflict):self.run_scan({'request_id':str(uuid.uuid4()),'symbols':['CCC']})
            return original(symbol)
        self.provider.side_effect=nested;self.assertEqual(self.run_scan()['status'],'completed')
        self.now+=1;payload={'request_id':str(uuid.uuid4()),'symbols':['AAA']}
        def late(symbol):self.now+=181;return original(symbol)
        self.provider.side_effect=late
        with self.assertRaises(ScanConflict):self.run_scan(payload)
        self.assertEqual(scanner_status(clock=self.clock)['status'],'interrupted')
        self.assertEqual(self.run_scan(payload)['status'],'interrupted')

    def test_http_auth_origin_json_and_restart_read(self):
        def owner(request):
            if request.headers.get('X-Owner')!='yes':raise HTTPException(401)
        def app():
            a=FastAPI();a.include_router(build_market_scanner_router(require_session=owner,fetch_snapshot=self.provider));return a
        async def run():
            path='/auto-trader/pro-ticker/market-scan';headers={'X-Owner':'yes','Origin':'https://test'}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()),base_url='https://test') as client:
                self.assertEqual((await client.get(path)).status_code,401)
                self.assertEqual((await client.post(path,json=self.payload,headers={**headers,'Origin':'https://evil'})).status_code,403)
                self.assertEqual((await client.post(path,json={**self.payload,'credentials':'invalid'},headers=headers)).status_code,422)
                result=await client.post(path,json=self.payload,headers=headers);self.assertEqual(result.status_code,200)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()),base_url='https://test') as client:
                current=(await client.get(path,headers=headers)).json();self.assertEqual(current['run'],result.json()['run'])
        asyncio.run(run())


@unittest.skipUnless(importlib.util.find_spec("yfinance") and importlib.util.find_spec("pandas"),"Production market-data dependencies are checked in full-app CI")
class YahooResearchProviderTests(unittest.TestCase):
    def test_fractional_average_and_true_market_timestamp_without_broker_keys(self):
        import pandas as pd
        now=datetime.now(timezone.utc)
        minutes=pd.DataFrame({'Close':[10.5],'Volume':[300]},index=pd.DatetimeIndex([now]))
        daily=pd.DataFrame({'Close':[10]*20,'Volume':[100,101]*10},index=pd.date_range(end=now-timedelta(days=1),periods=20))
        ticker=Mock();ticker.history.side_effect=[minutes,daily]
        with patch('yfinance.Ticker',return_value=ticker):result=yahoo_research_snapshot('AAA')
        self.assertEqual(result['average_volume'],'100.5');self.assertEqual(result['price_change_percent'],'5.00')
        self.assertEqual(datetime.fromisoformat(result['observed_at']),now)
        self.assertTrue(all(c.kwargs['timeout']==3 and c.kwargs['prepost'] is False for c in ticker.history.call_args_list))
