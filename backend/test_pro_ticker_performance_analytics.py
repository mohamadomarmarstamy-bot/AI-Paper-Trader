"""Analytical arithmetic, cohort boundaries and dedicated account evidence."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import database
from decimal import Decimal
from pro_ticker_performance_analytics import analytics_snapshot, summarize_trades, capture_equity
from pro_ticker_strategy_evaluation import evaluate_strategies


def trade(id, pnl, opened='2026-10-20T14:00:00Z', closed='2026-10-20T15:00:00Z'):
    return {'intent_id':id,'symbol':'TEST','opened_at':opened,'closed_at':closed,'gross_pnl_usd':pnl,
            'grade':{'grade':'A','version':'test_v1'},'strategy_version':None}


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'analytics.db');p.start();self.addCleanup(p.stop)

    def test_daily_cumulative_drawdown_and_cost_unknown(self):
        rows=[trade('a','10'),trade('b','-15',closed='2026-10-21T15:00:00Z'),trade('c','2',closed='2026-10-22T15:00:00Z')]
        result=summarize_trades(rows)
        self.assertEqual(result['gross_realized_usd'],'-3')
        self.assertEqual(result['max_gross_realized_daily_drawdown_usd'],'15')
        self.assertEqual([r['cumulative_gross_realized_usd'] for r in result['daily']],['10','-5','-3'])
        self.assertEqual(next(g for g in result['groups'] if g['dimension']=='research_grade')['samples'],3)
        empty=analytics_snapshot();self.assertIsNone(empty['net_pnl_usd']);self.assertIsNone(empty['gross_realized_usd'])
        self.assertIsNone(empty['observed_equity_drawdown_usd'])

    def test_period_filter_is_end_exclusive_and_keeps_whole_trade(self):
        rows=[trade('a','10'),trade('b','-15',closed='2026-10-21T15:00:00Z')]
        r=summarize_trades(rows,start='2026-10-20T15:00:00Z',end='2026-10-21T15:00:00Z')
        self.assertEqual(r['closed_count'],1);self.assertEqual(r['gross_realized_usd'],'10')
        for start,end in [('2026-10-20','2026-10-21T00:00:00Z'),('2026-10-22T00:00:00Z','2026-10-21T00:00:00Z')]:
            with self.assertRaises(ValueError):summarize_trades(rows,start=start,end=end)

    def test_chronological_cohorts_exclude_straddling_outcomes(self):
        rows=[trade('a','10'),trade('b','-15',closed='2026-10-22T15:00:00Z'),
              trade('c','2',opened='2026-10-22T14:00:00Z',closed='2026-10-22T15:00:00Z')]
        result=evaluate_strategies(rows,split_at='2026-10-21T00:00:00Z')
        self.assertEqual(result['development']['samples'],1);self.assertEqual(result['holdout']['samples'],1)
        self.assertEqual(result['split_spanning_excluded'],1)
        self.assertFalse(result['training_enabled']);self.assertFalse(result['automatic_strategy_changes'])
        self.assertEqual(result['holdout']['review_recommendations'],[])
        self.assertEqual(evaluate_strategies(rows)['status'],'unavailable_split_required')

    def context(self):
        gateway=Mock();gateway.read_account.return_value={'id':'dedicated','currency':'USD','equity':'100000','cash':'100000'}
        gateway.read_positions.return_value=[]
        return Mock(expected_account_id='dedicated',gateway=Mock(return_value=gateway)),gateway

    def test_identity_checked_observation_persists_without_orders(self):
        context,gateway=self.context();now=datetime(2026,10,26,15,tzinfo=timezone.utc).timestamp()
        capture_equity(context=context,clock=lambda:now)
        gateway.read_account.return_value['equity']='99990'
        capture_equity(context=context,clock=lambda:now+1)
        result=analytics_snapshot();self.assertEqual(len(result['equity_observations']),2)
        self.assertEqual(result['observed_equity_drawdown_usd'],'10')
        self.assertIsNone(result['net_pnl_usd']);gateway.submit_bracket.assert_not_called()
        gateway.read_account.return_value['id']='foreign'
        with self.assertRaises(ValueError):capture_equity(context=context,clock=lambda:now+2)
        self.assertEqual(len(analytics_snapshot()['equity_observations']),2)

    def test_write_lock_wait_cannot_save_expired_account_evidence(self):
        context,gateway=self.context()
        now=datetime(2026,10,26,15,tzinfo=timezone.utc).timestamp()
        clock={'now':now};original_connection=database.get_connection
        class DelayedConnection:
            def __init__(self):self.connection=original_connection()
            def __getattr__(self,name):return getattr(self.connection,name)
            def execute(self,sql,*args):
                result=self.connection.execute(sql,*args)
                if sql=='BEGIN IMMEDIATE':clock['now']=now+31
                return result
        with patch.object(database,'get_connection',side_effect=DelayedConnection):
            with self.assertRaises(ValueError):capture_equity(context=context,clock=lambda:clock['now'])
        self.assertEqual(analytics_snapshot()['equity_observations'],[])
        gateway.submit_bracket.assert_not_called()

    def test_foreign_holding_or_missing_cost_basis_does_not_save_valuation(self):
        context,gateway=self.context();gateway.read_positions.return_value=[{'symbol':'TEST','qty':'1','side':'long'}]
        with self.assertRaises(ValueError):capture_equity(context=context)
        self.assertEqual(analytics_snapshot()['equity_observations'],[])


class AnalyticsExecutionTests(unittest.TestCase):
    from test_pro_ticker_execution_journal import ExecutionJournalTests as Fixtures
    setUp=Fixtures.setUp
    make_draft=Fixtures.make_draft
    prepare=Fixtures.prepare
    activity=Fixtures.activity
    sync=Fixtures.sync

    def close_trade(self):
        import test_pro_ticker_position_monitor as monitor
        self.legs[1].update(filled_qty='2',status='filled',filled_avg_price='11.5')
        self.legs[0]['status']='canceled';monitor.PositionMonitorTests.monitor(self)
        self.activities[self.legs[1]['id']]=[self.activity(self.legs[1],2,'11.5',2)]
        self.sync()

    def test_closed_fills_reconcile_and_open_fills_excluded(self):
        self.sync();r=analytics_snapshot();self.assertEqual(r['closed_count'],0)
        self.assertEqual(r['incomplete_sync_count'],1)
        self.close_trade();r=analytics_snapshot()
        self.assertEqual(r['closed_count'],1);self.assertEqual(r['gross_realized_usd'],'2.0')
        self.assertEqual(r['rows'][0]['entry_average_usd'],'10.5')
        self.assertEqual(r['rows'][0]['exit_average_usd'],'11.5')
        self.assertIsNone(r['net_pnl_usd'])

    def test_corrupt_sell_coverage_cannot_inflate_results(self):
        self.close_trade()
        with closing(database.get_connection()) as conn:
            coverage=json_load(conn.execute('SELECT coverage_json FROM pro_ticker_execution_sync').fetchone()[0])
            coverage[-1]['shares']+=1
            import json
            conn.execute('UPDATE pro_ticker_execution_sync SET coverage_json=?',(json.dumps(coverage),));conn.commit()
        self.assertEqual(analytics_snapshot()['closed_count'],0)
        from pro_ticker_execution_journal import execution_journal_snapshot
        self.assertEqual(execution_journal_snapshot()['closed_priced_count'],0)

    def test_open_marks_require_owned_fills_and_fresh_quote(self):
        self.sync()
        self.gateway.read_account.side_effect=None
        self.gateway.read_account.return_value={'id':self.context.expected_account_id,'currency':'USD','equity':'100000','cash':'99979'}
        self.gateway.read_positions.return_value=[{'symbol':'AAPL','qty':'2','side':'long'}]
        now=datetime.now(timezone.utc).timestamp()
        self.gateway.read_trade.side_effect=None
        self.gateway.read_trade.return_value={'p':12,'t':datetime.fromtimestamp(now,timezone.utc).isoformat()}
        result=capture_equity(context=self.context,clock=lambda:now)
        mark=result['observation']['positions'][0]
        self.assertEqual(mark['cost_basis_usd'],'21');self.assertEqual(Decimal(mark['gross_unrealized_usd']),Decimal('3'))
        self.gateway.read_trade.return_value['t']='2000-01-01T00:00:00Z'
        with self.assertRaises(ValueError):capture_equity(context=self.context,clock=lambda:now+1)
        self.assertEqual(len(analytics_snapshot()['equity_observations']),1)

    def test_missing_exit_and_reentry_prices_cannot_reuse_an_old_cost_basis(self):
        import uuid
        self.sync()
        # Missing opposite-side price records can leave the same net shares.
        # Net quantity agreement alone does not prove a complete cost basis.
        with closing(database.get_connection()) as conn:
            for event,order,side in [('unsynced-exit',self.legs[1]['id'],'sell'),
                                      ('unsynced-reentry',str(uuid.uuid4()),'buy')]:
                conn.execute('INSERT INTO pro_ticker_strategy_fill_events VALUES (?,?,?,?,?,?)',
                             (event,order,'AAPL',side,2,datetime.now(timezone.utc).isoformat()))
            conn.commit()
        self.gateway.read_account.side_effect=None
        self.gateway.read_account.return_value={'id':self.context.expected_account_id,'currency':'USD','equity':'100000','cash':'99979'}
        self.gateway.read_positions.return_value=[{'symbol':'AAPL','qty':'2','side':'long'}]
        self.gateway.read_trade.side_effect=None
        self.gateway.read_trade.return_value={'p':12,'t':datetime.now(timezone.utc).isoformat()}
        with self.assertRaises(ValueError):capture_equity(context=self.context)
        self.assertEqual(analytics_snapshot()['equity_observations'],[])

    def test_changed_accounting_during_network_mark_is_not_committed(self):
        self.sync()
        self.gateway.read_account.side_effect=None
        self.gateway.read_account.return_value={'id':self.context.expected_account_id,'currency':'USD','equity':'100000','cash':'99979'}
        self.gateway.read_positions.return_value=[{'symbol':'AAPL','qty':'2','side':'long'}]
        def changed(symbol):
            with closing(database.get_connection()) as conn:
                conn.execute("UPDATE pro_ticker_strategy_positions SET updated_at='changed'");conn.commit()
            return {'p':12,'t':datetime.now(timezone.utc).isoformat()}
        self.gateway.read_trade.side_effect=changed
        with self.assertRaises(ValueError):capture_equity(context=self.context)
        self.assertEqual(analytics_snapshot()['equity_observations'],[])


def json_load(value):
    import json
    return json.loads(value)


class AnalyticsHttpTests(unittest.TestCase):
    setUp=AnalyticsTests.setUp
    context=AnalyticsTests.context
    def test_auth_origin_filters_and_no_browser_evidence(self):
        import asyncio
        import httpx
        from fastapi import FastAPI,HTTPException
        from pro_ticker_execution_routes import build_paper_execution_router
        context,gateway=self.context()
        def auth(request):
            if request.headers.get('x-owner')!='yes':raise HTTPException(401)
        app=FastAPI();app.include_router(build_paper_execution_router(require_session=auth,context=context))
        async def run():
            base='/auto-trader/pro-ticker/performance-analytics';headers={'x-owner':'yes','Origin':'https://test'}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as c:
                self.assertEqual((await c.get(base)).status_code,401)
                response=await c.get(base,headers=headers);self.assertEqual(response.status_code,200)
                self.assertEqual(response.headers['cache-control'],'no-store')
                self.assertEqual((await c.get(base+'?start=2026-10-20',headers=headers)).status_code,422)
                self.assertEqual((await c.get(base+'?unknown=true',headers=headers)).status_code,422)
                self.assertEqual((await c.post(base+'/capture',json={},headers={**headers,'Origin':'https://evil'})).status_code,403)
                self.assertEqual((await c.post(base+'/capture',json={'equity':'999'},headers=headers)).status_code,422)
                result=await c.post(base+'/capture',json={},headers=headers)
                self.assertEqual(result.status_code,200);self.assertFalse(result.json()['order_submitted'])
        asyncio.run(run());gateway.submit_bracket.assert_not_called()
