"""Durable scheduling exercises real reconciliation with synthetic broker data."""
from contextlib import closing
import unittest
from unittest.mock import patch
import database
import test_pro_ticker_protection_review as fixtures
from pro_ticker_monitor_worker import run_monitor_batch, _claim, monitor_enabled, monitor_snapshot
from pro_ticker_workspace import workspace_snapshot


class MonitorWorkerTests(unittest.TestCase):
    make_draft = fixtures.ProtectionReviewTests.make_draft
    prepare = fixtures.ProtectionReviewTests.prepare
    setUp = fixtures.ProtectionReviewTests.setUp

    def test_repeated_observations_do_not_duplicate_fills_or_release_budget(self):
        for now in (1000,1031):
            self.assertEqual(run_monitor_batch(context=self.context,clock=lambda:now),{'observed':1,'errors':0})
        self.assertEqual(workspace_snapshot()['positions']['rows'][0]['shares'],2)
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)
        self.gateway.submit_bracket.assert_not_called()
        self.assertEqual(run_monitor_batch(context=self.context,clock=lambda:1032)['observed'],0)

    def test_outage_invalidates_protection_and_recovers_on_next_cycle(self):
        run_monitor_batch(context=self.context,clock=lambda:1000)
        with patch('pro_ticker_monitor_worker.monitor_paper_position',side_effect=RuntimeError('secret must never persist')):
            self.assertEqual(run_monitor_batch(context=self.context,clock=lambda:1031)['errors'],1)
        row=monitor_snapshot(context=self.context)['observations'][0]
        self.assertEqual(row['state'],'unavailable');self.assertFalse(row['protection_verified'])
        self.assertNotIn('secret',str(row))
        self.assertEqual(run_monitor_batch(context=self.context,clock=lambda:1062)['observed'],1)

    def test_crash_claim_excludes_competing_worker_then_expires(self):
        self.assertEqual(_claim(1000,'first'),self.intent['id'])
        self.assertIsNone(_claim(1001,'second'))
        self.assertEqual(_claim(1301,'replacement'),self.intent['id'])

    def test_restart_reconciles_exit_without_releasing_allocation(self):
        run_monitor_batch(context=self.context,clock=lambda:1000)
        self.legs[1].update(filled_qty='2',status='filled');self.legs[0]['status']='canceled'
        self.assertEqual(run_monitor_batch(context=self.context,clock=lambda:1031)['observed'],1)
        self.assertEqual(workspace_snapshot()['positions']['total'],0)
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)
        self.assertEqual(monitor_snapshot(context=self.context)['observations'][0]['state'],'closed')
        self.gateway.submit_bracket.assert_not_called()

    def test_unattempted_intent_is_never_polled(self):
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE pro_ticker_order_intents SET attempt_started_at=NULL');conn.commit()
        with patch('pro_ticker_monitor_worker.monitor_paper_position') as monitor:
            self.assertEqual(run_monitor_batch(context=self.context,clock=lambda:1000)['observed'],0)
            monitor.assert_not_called()

    def test_requires_explicit_opt_in_and_dedicated_context(self):
        self.assertFalse(monitor_enabled(self.context,{}))
        self.assertFalse(monitor_enabled(None,{'PRO_TICKER_POSITION_MONITOR_ENABLED':'true'}))
        self.assertTrue(monitor_enabled(self.context,{'PRO_TICKER_POSITION_MONITOR_ENABLED':'true'}))
        self.assertFalse(monitor_enabled(self.context,{'PRO_TICKER_POSITION_MONITOR_ENABLED':'TRUE'}))

    def test_stale_observation_cannot_claim_verified_protection(self):
        run_monitor_batch(context=self.context,clock=lambda:1000)
        with patch('pro_ticker_monitor_worker.time.time',return_value=1121):
            row=monitor_snapshot(context=self.context)['observations'][0]
            self.assertTrue(row['stale']);self.assertFalse(row['protection_verified'])

    def test_status_requires_auth_and_never_calls_broker(self):
        import asyncio
        import httpx
        from fastapi import FastAPI,HTTPException
        from pro_ticker_execution_routes import build_paper_execution_router
        def require(request):
            if request.headers.get('x-owner')!='yes':raise HTTPException(401)
        app=FastAPI();app.include_router(build_paper_execution_router(require_session=require,context=self.context))
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                self.assertEqual((await client.get('/auto-trader/pro-ticker/monitor-status')).status_code,401)
                result=await client.get('/auto-trader/pro-ticker/monitor-status',headers={'x-owner':'yes'})
                self.assertEqual(result.status_code,200);self.assertEqual(result.headers['cache-control'],'no-store')
                self.assertEqual(result.json()['observations'],[])
        with patch('pro_ticker_monitor_worker.monitor_paper_position') as monitor:
            asyncio.run(check());monitor.assert_not_called()

    def test_loop_retries_database_failure_and_cancels_cleanly(self):
        import asyncio
        from pro_ticker_monitor_worker import monitor_loop,stop_monitor,start_monitor
        async def check():
            calls=[]
            async def sleep(seconds):
                calls.append(seconds)
                if len(calls)==2:raise asyncio.CancelledError
            with patch('pro_ticker_monitor_worker.asyncio.sleep',side_effect=sleep),patch('pro_ticker_monitor_worker.run_monitor_batch',side_effect=[RuntimeError('db unavailable'),{}]) as batch:
                task=asyncio.create_task(monitor_loop(self.context))
                try:await task
                except asyncio.CancelledError:pass
                self.assertEqual(batch.call_count,2)
                await stop_monitor(task)
            with patch.dict('os.environ',{},clear=True):self.assertIsNone(start_monitor(self.context))
        asyncio.run(check())
