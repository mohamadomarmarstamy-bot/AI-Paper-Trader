"""Point-in-time research provenance and saved-evidence replay boundaries."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from pro_ticker_research_evidence import VERSION, capture_research, research_report

NOW = datetime(2026, 10, 26, 15, tzinfo=timezone.utc)


def history(symbol):
    return {'source': 'yahoo_finance_daily', 'symbol': symbol, 'bars': [
        {'timestamp': (NOW-timedelta(days=60-i)).isoformat(), 'open': 10+i*.1,
         'high': 11+i*.1, 'low': 9+i*.1, 'close': 10+i*.1, 'volume': 100000}
        for i in range(60)]}


def envelope():
    return {'version': VERSION, 'symbol': 'TEST', 'observed_at': NOW.isoformat(),
            'reference': {'source': 'alpaca_latest_trade', 'price': 20, 'market_timestamp': NOW.isoformat()},
            'history': history('TEST'), 'market_history': history('SPY')}


class ResearchEvidenceTests(unittest.TestCase):
    def test_replay_is_identical_and_exposes_limits(self):
        evidence=envelope();first=research_report(evidence)
        self.assertEqual(first, research_report(deepcopy(evidence)))
        self.assertEqual(first['status'], 'available')
        self.assertEqual(first['market_regime'], 'uptrend')
        self.assertFalse(first['order_execution_authorized'])
        self.assertIsNone(first['score'])
        self.assertIn('independently_verified_catalyst', first['missing_information'])
        self.assertTrue(first['contradictions'])
        self.assertEqual(len(first['evidence_sha256']), 64)

    def test_stale_future_missing_reference_fails_closed(self):
        for delta in (-91, 1):
            e=envelope();e['reference']['market_timestamp']=(NOW+timedelta(seconds=delta)).isoformat()
            r=research_report(e);self.assertEqual(r['status'], 'unavailable')
            self.assertIn('fresh_reference', r['missing_information'])
        e=envelope();e['reference']=None
        self.assertEqual(research_report(e)['status'], 'unavailable')

    def test_wrong_symbol_stale_future_duplicate_and_contradictory_bars(self):
        mutations=[lambda h:h.update(symbol='OTHER'),
                   lambda h:h['bars'][-1].update(timestamp=NOW.isoformat()),
                   lambda h:h['bars'][-1].update(timestamp=h['bars'][-2]['timestamp']),
                   lambda h:h['bars'][-1].update(high=1),
                   lambda h:h['bars'][-1].update(volume=float('nan'))]
        for mutate in mutations:
            e=envelope();mutate(e['history'])
            # Non-finite envelope is not serializable evidence and must reject.
            try:r=research_report(e)
            except ValueError:continue
            self.assertEqual(r['status'], 'unavailable')
            self.assertIn('fresh_completed_daily_history',r['missing_information'])

    def test_market_missing_does_not_invent_regime(self):
        e=envelope();e['market_history']=None;r=research_report(e)
        self.assertIsNone(r['market_regime']);self.assertIn('market_regime',r['missing_information'])

    def test_provider_errors_sanitized_and_evidence_saved(self):
        def provider(symbol, **kwargs):raise RuntimeError('secret-token')
        r=capture_research('TEST',None,fetch_history=provider,fetch_news=lambda s:None,clock=lambda:NOW.timestamp())
        self.assertEqual(r['report']['status'],'unavailable')
        self.assertNotIn('secret-token',str(r))
        self.assertEqual(research_report(r['evidence']),r['report'])


class ResearchPersistenceTests(unittest.TestCase):
    def test_saved_context_replays_immutable_inputs_without_provider_refetch(self):
        import asyncio
        from contextlib import closing
        from pathlib import Path
        import tempfile
        from unittest.mock import Mock,patch
        import uuid
        import httpx
        from fastapi import FastAPI,HTTPException
        import database
        from pro_ticker_manual_signals import submit_manual_signal,build_manual_signal_router
        from test_pro_ticker_manual_signals import analysis
        with tempfile.TemporaryDirectory() as temp, patch.object(database,'DATABASE_PATH',Path(temp)/'evidence.db'):
            now=NOW.timestamp();collector=Mock(side_effect=lambda symbol,quote:capture_research(symbol,quote,fetch_history=lambda s,**kw:history(s),fetch_news=lambda s:None,clock=lambda:now))
            payload={'request_id':str(uuid.uuid4()),'symbol':'TEST'}
            quote=Mock(return_value={'p':20,'t':NOW.isoformat()});analyzer=Mock(return_value=analysis('TEST'))
            row=submit_manual_signal(payload,quote_fetcher=quote,analyzer=analyzer,clock=lambda:now,research_collector=collector)
            duplicate=submit_manual_signal(payload,quote_fetcher=quote,analyzer=analyzer,clock=lambda:now,research_collector=collector)
            self.assertEqual(row['signal'],duplicate['signal']);self.assertEqual(collector.call_count,1)
            def auth(request):
                if request.headers.get('x-owner')!='yes':raise HTTPException(401)
            app=FastAPI();app.include_router(build_manual_signal_router(require_session=auth,quote_fetcher=quote,analyzer=analyzer))
            async def run():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as client:
                    path='/auto-trader/pro-ticker/manual-signals/'+payload['request_id']+'/research-context'
                    self.assertEqual((await client.get(path)).status_code,401)
                    response=await client.get(path,headers={'x-owner':'yes'});self.assertEqual(response.status_code,200)
                    self.assertTrue(response.json()['replayed'])
                    self.assertEqual(response.json()['report'],row['signal']['entry_grade']['research_context']['report'])
                    self.assertEqual(response.headers['cache-control'],'no-store')
            asyncio.run(run())


class NewsEvidenceTests(unittest.TestCase):
    def test_only_fresh_attributed_symbol_news_is_saved(self):
        from pro_ticker_research_evidence import normalize_news
        article={'source':'benzinga','symbols':['TEST'],'headline':'TEST announces quarterly earnings',
                 'url':'https://www.benzinga.com/news/test','created_at':NOW.isoformat()}
        first=normalize_news([article,article],symbol='TEST',observed=NOW)
        self.assertEqual(len(first['articles']),1)
        self.assertEqual(first['articles'][0]['reported_topics'],['earnings'])
        self.assertFalse(first['independently_verified'])
        for change in [{'symbols':['OTHER']},{'source':'unknown'},{'url':'javascript:alert(1)'},
                       {'url':'https://benzinga.com.evil.test/x'},{'url':'https://user:password@benzinga.com/x'},
                       {'created_at':'2000-01-01T00:00:00Z'},{'created_at':(NOW+timedelta(seconds=1)).isoformat()}]:
            self.assertEqual(normalize_news([{**article,**change}],symbol='TEST',observed=NOW)['status'],'unavailable')

    def test_news_replay_has_same_hash_and_tags(self):
        e=envelope();e['news']=[{'source':'benzinga','symbols':['TEST'],'headline':'TEST prices stock offering',
                                'url':'https://benzinga.com/news/offer','created_at':NOW.isoformat()}]
        r=research_report(e);self.assertEqual(r,research_report(deepcopy(e)))
        self.assertEqual(r['news']['articles'][0]['reported_topics'],['financing'])
        self.assertIn('independently_verified_catalyst',r['missing_information'])
