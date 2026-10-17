"""Public discovery persistence, bounded retries, isolation and source redirects."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import httpx
from fastapi import FastAPI, HTTPException
import database
from pro_ticker_discovery import discovery_status, run_discovery
from pro_ticker_workspace import workspace_snapshot, build_workspace_router

ROOT='https://www.protickersignals.com/latestnews/'


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'research.db');p.start();self.addCleanup(p.stop)
        database.initialize_database()

    def collector(self,url):
        return database.upsert_pro_ticker_research(article_url=url,symbol='MSFT' if url.endswith('2') else 'AAPL',article_title='Saved public evidence')

    def test_discovered_articles_appear_in_workspace_and_retries_skip_saved_urls(self):
        source=Mock(return_value=[{'article_url':ROOT+'1'},{'article_url':ROOT+'2'}])
        collect=Mock(side_effect=self.collector)
        result=run_discovery(discover=source,collect=collect)
        self.assertEqual(result['status'],'completed');self.assertEqual(result['run']['saved'],2)
        self.assertEqual({r['symbol'] for r in workspace_snapshot()['research']['rows']},{'AAPL','MSFT'})
        self.assertEqual(discovery_status()['run']['id'],result['run']['id'])
        source.assert_called_once_with(start_page=1,max_pages=1,max_articles=5)
        second=run_discovery(discover=source,collect=collect)
        self.assertEqual(second['run']['saved'],0);self.assertEqual(second['run']['skipped'],2)
        self.assertEqual(collect.call_count,2)
        self.assertEqual(workspace_snapshot()['reservations']['total'],0)

    def test_bound_and_duplicate_urls_are_respected(self):
        items=[{'article_url':ROOT+'1'}]*2+[{'article_url':ROOT+str(i)} for i in range(2,20)]
        collect=Mock(side_effect=self.collector)
        result=run_discovery(discover=lambda **_:items,collect=collect)
        self.assertEqual(collect.call_count,5)
        self.assertEqual(result['run']['saved'],5)
        self.assertEqual(len(result['run']['candidates']),2)
        self.assertEqual(result['run']['repeated_symbols'],3)
        self.assertEqual(result['run']['skipped'],1)

    def test_unparsed_article_cannot_break_the_financial_workspace(self):
        def collect(url):
            return database.upsert_pro_ticker_research(article_url=url,symbol=None,article_title='Unparsed article')
        result=run_discovery(discover=lambda **_:[{'article_url':ROOT+'missing'}],collect=collect)
        self.assertEqual(result['status'],'failed')
        data=workspace_snapshot()
        self.assertEqual(data['research']['total'],0)
        self.assertEqual(data['scanner']['unattributed_research_records'],1)
        self.assertEqual(data['budget']['available_cents'],5000000)

    def test_failed_articles_keep_successful_results_and_sanitize_errors(self):
        def collect(url):
            if url.endswith('2'):raise RuntimeError('private-secret-value')
            return self.collector(url)
        result=run_discovery(discover=lambda **_:[{'article_url':ROOT+'1'},{'article_url':ROOT+'2'}],collect=collect)
        self.assertEqual(result['status'],'partial');self.assertEqual(result['run']['failed'],1)
        self.assertNotIn('private-secret-value',str(discovery_status()))
        self.assertEqual(workspace_snapshot()['research']['total'],1)

    def test_source_failure_and_empty_response_are_not_reported_as_healthy_scans(self):
        result=run_discovery(discover=Mock(side_effect=RuntimeError('private-url')),collect=self.collector)
        self.assertEqual(result['status'],'failed');self.assertNotIn('private-url',str(result))
        result=run_discovery(discover=lambda **_:[],collect=self.collector)
        self.assertEqual(result['status'],'no_results')
        self.assertIn('unavailable',result['run']['reason'])

    def test_concurrent_requests_share_a_single_committed_run(self):
        entered=threading.Event();resume=threading.Event()
        def source(**_):
            entered.set();self.assertTrue(resume.wait(5));return []
        with ThreadPoolExecutor(max_workers=1) as pool:
            task=pool.submit(run_discovery,discover=source,collect=self.collector)
            self.assertTrue(entered.wait(5))
            duplicate=run_discovery(discover=Mock(side_effect=AssertionError('duplicate fetch')),collect=self.collector)
            self.assertFalse(duplicate['started']);self.assertEqual(duplicate['status'],'running')
            resume.set();original=task.result(timeout=5)
        self.assertEqual(duplicate['run']['id'],original['run']['id'])
        with closing(database.get_connection()) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM pro_ticker_workspace_discovery_runs').fetchone()[0],1)

    def test_expired_worker_is_observable_and_explicit_retry_preserves_history(self):
        run_discovery(discover=lambda **_:[],collect=self.collector)
        with closing(database.get_connection()) as c:
            c.execute("UPDATE pro_ticker_workspace_discovery_runs SET status='running',lease_until=?,finished_at=NULL",(time.time()-1,));c.commit()
        self.assertEqual(discovery_status()['status'],'interrupted')
        retry=run_discovery(discover=lambda **_:[],collect=self.collector)
        self.assertTrue(retry['started'])
        with closing(database.get_connection()) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM pro_ticker_workspace_discovery_runs WHERE status='interrupted'").fetchone()[0],1)

    def test_expired_worker_cannot_overwrite_replacement_status(self):
        now=time.time();ticks=iter([now,now,now+601])
        result=run_discovery(discover=lambda **_:[{'article_url':ROOT+'1'}],collect=self.collector,clock=lambda:next(ticks))
        self.assertFalse(result['result_recorded']);self.assertEqual(result['status'],'interrupted')

    def test_routes_require_auth_origin_empty_json_and_do_not_trigger_on_get(self):
        def auth(request):
            if request.headers.get('x-owner')!='yes':raise HTTPException(401,'Sign in')
        app=FastAPI();app.include_router(build_workspace_router(require_session=auth))
        runner=Mock(return_value={'paper':True,'status':'completed'})
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as c:
                path='/auto-trader/pro-ticker/discovery';headers={'x-owner':'yes','Origin':'https://test'}
                self.assertEqual((await c.get(path)).status_code,401)
                self.assertEqual((await c.post(path,json={})).status_code,401)
                self.assertEqual((await c.get(path,headers=headers)).json()['status'],'not_run');runner.assert_not_called()
                self.assertEqual((await c.post(path,json={},headers={**headers,'Origin':'https://other'})).status_code,403)
                self.assertEqual((await c.post(path,content='{}',headers=headers)).status_code,415)
                self.assertEqual((await c.post(path,json={'article_url':'https://other'},headers=headers)).status_code,422)
                self.assertEqual((await c.post(path,json={},headers=headers)).status_code,200)
        with patch('pro_ticker_discovery.run_discovery',runner):asyncio.run(send())
        runner.assert_called_once_with()

    def test_authorized_http_discovery_persists_real_research_and_refreshes_workspace(self):
        def auth(request):
            if request.headers.get('x-owner')!='yes':raise HTTPException(401,'Sign in')
        app=FastAPI();app.include_router(build_workspace_router(require_session=auth))
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test',headers={'x-owner':'yes','Origin':'https://test'}) as c:
                result=await c.post('/auto-trader/pro-ticker/discovery',json={})
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.json()['run']['saved'],2)
                status=await c.get('/auto-trader/pro-ticker/discovery')
                self.assertEqual(status.json()['run']['id'],result.json()['run']['id'])
                snapshot=(await c.get('/auto-trader/pro-ticker/workspace')).json()
                self.assertEqual(snapshot['research']['total'],2)
                self.assertEqual(snapshot['budget']['committed_cents'],0)
                self.assertFalse(snapshot['order_execution_enabled'])
        with patch('pro_ticker_collector.discover_pro_ticker_articles',return_value=[{'article_url':ROOT+'1'},{'article_url':ROOT+'2'}]),patch('pro_ticker_collector.collect_pro_ticker_article',side_effect=self.collector):
            asyncio.run(send())


class PublicSourceRedirectTests(unittest.TestCase):
    def fetch(self,url,responses):
        from pro_ticker_collector import _fetch_public_html
        with patch('pro_ticker_collector.requests.get',side_effect=responses) as get:
            result=_fetch_public_html(url)
            self.assertTrue(all(c.kwargs['allow_redirects'] is False for c in get.call_args_list))
            return result

    def response(self,status=200,location=None):
        return Mock(status_code=status,headers={'Content-Type':'text/html',**({'Location':location} if location else {})},text='<p>public evidence</p>')

    def test_same_source_relative_redirect_is_supported(self):
        self.assertIn('public evidence',self.fetch(ROOT,[self.response(302,'/latestnews/article'),self.response()]))

    def test_external_redirect_is_rejected_before_second_network_request(self):
        from pro_ticker_collector import _fetch_public_html
        with patch('pro_ticker_collector.requests.get',return_value=self.response(302,'http://127.0.0.1/private')) as get:
            with self.assertRaises(ValueError):_fetch_public_html(ROOT)
            self.assertEqual(get.call_count,1)

    def test_invalid_source_and_redirect_loops_are_rejected(self):
        from pro_ticker_collector import _fetch_public_html
        for url in ('http://localhost/','https://user:pass@www.protickersignals.com/latestnews','https://www.protickersignals.com:1234/latestnews'):
            with patch('pro_ticker_collector.requests.get') as get:
                with self.assertRaises(ValueError):_fetch_public_html(url)
                get.assert_not_called()
        with patch('pro_ticker_collector.requests.get',return_value=self.response(302,'/latestnews')) as get:
            with self.assertRaises(ValueError):_fetch_public_html(ROOT)
            self.assertEqual(get.call_count,4)
