from copy import deepcopy
from datetime import datetime,timezone,timedelta
import unittest
from unittest.mock import patch,Mock
from fast_mover_discovery import snapshot_metrics,rank_pool
import universe

NOW=datetime(2026,10,27,15,tzinfo=timezone.utc).timestamp()

def iso(offset=0): return datetime.fromtimestamp(NOW+offset,timezone.utc).isoformat()
def snap(**change):
    data={'latestTrade':{'p':2.2,'t':iso()},'latestQuote':{'bp':2.19,'ap':2.21,'t':iso()},
          'dailyBar':{'v':200000,'vw':2,'t':'2026-10-27T04:00:00Z'},
          'prevDailyBar':{'c':2,'t':'2026-10-26T04:00:00Z'}}
    data.update(change);return data

class DiscoveryMetricTests(unittest.TestCase):
    def test_fresh_small_price_mover_with_activity_is_prioritized(self):
        m=snapshot_metrics(snap(),now=NOW)
        self.assertTrue(m['fast_mover_priority']);self.assertAlmostEqual(m['percent_change'],10)
        self.assertEqual(m['session_dollar_volume'],400000)
        self.assertFalse(m['market_cap_verified']);self.assertEqual(m['feed'],'iex')

    def test_stale_future_missing_and_wrong_session_data_are_not_live(self):
        for change in [dict(latestTrade={'p':2.2,'t':iso(-91)}),dict(latestQuote={'bp':2.19,'ap':2.21,'t':iso(1)}),
                       dict(dailyBar={'v':200000,'vw':2,'t':'2026-10-26T04:00:00Z'}),dict(prevDailyBar={'c':2}),
                       dict(latestTrade={'p':'NaN','t':iso()}),dict(latestTrade=[]),dict(latestTrade={'p':True,'t':iso()})]:
            self.assertEqual(snapshot_metrics(snap(**change),now=NOW),{})

    def test_wide_illiquid_expensive_and_negative_moves_do_not_get_priority(self):
        variants=[snap(latestQuote={'bp':2,'ap':2.5,'t':iso()}),snap(dailyBar={'v':2,'vw':2,'t':'2026-10-27T04:00:00Z'}),
            snap(latestTrade={'p':100,'t':iso()}),snap(latestTrade={'p':1.8,'t':iso()}),snap(dailyBar={'v':200000,'vw':2.5,'t':'2026-10-27T04:00:00Z'})]
        for data in variants:self.assertFalse(snapshot_metrics(data,now=NOW)['fast_mover_priority'])

    def test_pool_cap_and_exploration_prevent_hot_list_monopoly(self):
        pool=['HOT'+str(i) for i in range(550)]+['NEW'+str(i) for i in range(50)]
        metadata={s:{'fast_mover_priority':True,'fast_mover_score':10} for s in pool[:550]}
        result=rank_pool(pool,pool[-50:],metadata)
        self.assertEqual(len(result),350);self.assertTrue(set(pool[-50:])<=set(result))
        self.assertEqual(len(result),len(set(result)))

class UniverseCoverageTests(unittest.TestCase):
    def setUp(self):
        self.original=deepcopy(universe._hot_universe_cache);self.cursor=universe._rotation_cursor
        universe._hot_universe_cache.update(symbols=[],movers={},updated_at=0)
        universe._rotation_cursor=0
        self.addCleanup(lambda:universe._hot_universe_cache.update(self.original))
        self.addCleanup(setattr,universe,'_rotation_cursor',self.cursor)
        self.allowed=['S'+str(i) for i in range(2000)]
        self.clock=patch('universe.time.time',return_value=NOW);self.clock.start();self.addCleanup(self.clock.stop)
        self.asset=patch('universe._load_allowed_symbols',return_value=self.allowed);self.asset.start();self.addCleanup(self.asset.stop)
        self.research=patch('universe.record_explosive_movers_for_research',return_value=0);self.research.start();self.addCleanup(self.research.stop)
        self.calls=[]
    def data(self,method,path,params,timeout):
        self.assertEqual(method,'GET');self.calls.append((path,params))
        if path.endswith('/movers'):return {'gainers':[{'symbol':'S1900'}],'losers':[{'symbol':'NOT_ALLOWED'}]}
        if path.endswith('/most-actives'):return {'most_actives':[{'symbol':'S1800' if params['by']=='volume' else 'S1700'}]}
        if path.endswith('/snapshots'):return {s:snap() for s in params['symbols'].split(',')}
        raise AssertionError(path)
    def test_live_lists_plus_rotating_breadth_cached_and_bounded(self):
        first=universe.load_momentum_universe(request_func=Mock(),market_data_request_func=self.data)
        self.assertTrue({'S1900','S1800','S1700'}<=set(first));self.assertIn('S0',first)
        count=len(self.calls)
        self.assertEqual(universe.load_momentum_universe(request_func=Mock(),market_data_request_func=self.data),first)
        self.assertEqual(len(self.calls),count)
        second=universe.load_momentum_universe(request_func=Mock(),market_data_request_func=self.data,force_refresh=True)
        self.assertIn('S300',second);self.assertNotIn('S0',second)
        status=universe.get_momentum_universe_status()
        self.assertEqual(status['rotating_symbols'],300);self.assertGreater(status['screened_symbols'],300)
        self.assertTrue(all(len(p['symbols'].split(','))<=200 for path,p in self.calls if path.endswith('/snapshots')))
    def test_provider_failure_still_rotates_and_does_not_reuse_old_boosts(self):
        universe.load_momentum_universe(request_func=Mock(),market_data_request_func=self.data)
        result=universe.load_momentum_universe(request_func=Mock(),market_data_request_func=Mock(side_effect=RuntimeError('offline')),force_refresh=True)
        self.assertIn('S300',result);self.assertTrue(universe.get_momentum_universe_status()['partial'])
        self.assertEqual(universe.get_momentum_mover_metadata('S1900'),{})
    def test_selected_refresh_failure_clears_previously_fresh_metadata(self):
        universe.load_momentum_universe(request_func=Mock(),market_data_request_func=self.data)
        self.assertTrue(universe.get_momentum_mover_metadata('S1900'))
        universe.refresh_selected_momentum_snapshots(['S1900'],Mock(side_effect=RuntimeError('offline')))
        self.assertEqual(universe.get_momentum_mover_metadata('S1900'),{})

class ScannerRankingAndCacheTests(unittest.TestCase):
    def test_fast_buy_ranking_changes_without_turning_holds_into_buys(self):
        import scanner
        rows=[{'symbol':'SLOW','signal':'BUY','score':99},
              {'symbol':'FAST','signal':'BUY','score':80,'fast_mover_priority':True,'fast_mover_score':45},
              {'symbol':'WATCH','signal':'HOLD','score':80,'fast_mover_priority':True,'fast_mover_score':90}]
        result=scanner._select_balanced_results(rows)
        self.assertEqual([r['symbol'] for r in result],['FAST','SLOW','WATCH'])
        self.assertEqual(result[-1]['signal'],'HOLD')
        self.assertEqual(result[0]['score'],80)

    def test_daily_analysis_reused_but_live_snapshots_refreshed_and_expire(self):
        import pandas as pd
        import scanner
        original=deepcopy(scanner._daily_candidate_cache);metrics=deepcopy(scanner._scan_metrics);cache=deepcopy(scanner._scan_cache)
        hot=deepcopy(universe._hot_universe_cache)
        self.addCleanup(lambda: (scanner._daily_candidate_cache.clear(),scanner._daily_candidate_cache.update(original),scanner._scan_metrics.clear(),scanner._scan_metrics.update(metrics),scanner._scan_cache.update(cache),universe._hot_universe_cache.update(hot)))
        scanner.clear_scanner_cache()
        clock=[NOW]
        download=Mock(return_value=pd.DataFrame({'Close':[2]}))
        data=Mock(side_effect=lambda *a,**kw:{'HOT':snap()})
        with patch('scanner.time.time',side_effect=lambda:clock[0]),patch('scanner.load_momentum_universe',return_value=['HOT']), \
             patch('pro_ticker_candidate_source.load_pro_ticker_scanner_symbols',return_value=[]), \
             patch('scanner.download_batch',download),patch('scanner.extract_symbol_history',return_value=pd.DataFrame({'Close':[2]})), \
             patch('scanner.analyze_stock',return_value={'symbol':'HOT','signal':'BUY','score':80,'price':2,'ma20':1}):
            first=scanner.scan_market(force_refresh=True,request_func=Mock(),market_data_request_func=data)
            self.assertTrue(first[0]['fast_mover_priority'])
            clock[0]+=10
            second=scanner.scan_market(force_refresh=True,request_func=Mock(),market_data_request_func=data)
            self.assertEqual(download.call_count,1);self.assertEqual(data.call_count,2)
            self.assertEqual(scanner.get_scanner_status()['reused_daily_analyses'],1)
            clock[0]+=91
            self.assertFalse(scanner._copy_results(second)[0]['fast_mover_priority'])
            clock[0]+=301
            scanner.scan_market(force_refresh=True,request_func=Mock(),market_data_request_func=data)
            self.assertEqual(download.call_count,2)

    def test_scan_time_budget_defers_remaining_downloads(self):
        import pandas as pd
        import scanner
        original=deepcopy(scanner._daily_candidate_cache);metrics=deepcopy(scanner._scan_metrics);cache=deepcopy(scanner._scan_cache)
        self.addCleanup(lambda:(scanner._daily_candidate_cache.clear(),scanner._daily_candidate_cache.update(original),scanner._scan_metrics.clear(),scanner._scan_metrics.update(metrics),scanner._scan_cache.update(cache)))
        scanner.clear_scanner_cache();clock=[NOW]
        def download(symbols):clock[0]+=50;return pd.DataFrame({'Close':[2]})
        with patch('scanner.time.time',side_effect=lambda:clock[0]),patch('scanner.load_momentum_universe',return_value=['S'+str(i) for i in range(60)]), \
             patch('pro_ticker_candidate_source.load_pro_ticker_scanner_symbols',return_value=[]),patch('scanner.download_batch',side_effect=download) as fetch, \
             patch('scanner.extract_symbol_history',return_value=pd.DataFrame({'Close':[2]})), \
             patch('scanner.analyze_stock',side_effect=lambda symbol,history:{'symbol':symbol,'signal':'HOLD','score':50}):
            scanner.scan_market(force_refresh=True,request_func=Mock())
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(scanner.get_scanner_status()['deferred_daily_histories'],35)
