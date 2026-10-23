"""Execution prices/times require account, order, activity and share agreement."""
import asyncio
from contextlib import closing
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import unittest
from unittest.mock import Mock
import uuid
import httpx
from fastapi import FastAPI,HTTPException
import database
import test_pro_ticker_position_monitor as fixtures
from pro_ticker_execution_journal import sync_execution_journal,execution_journal_snapshot,gross_execution_metrics,grouped_execution_metrics
from pro_ticker_order_intents import IntentConflict
from pro_ticker_workspace import workspace_snapshot
from pro_ticker_paper_gateway import AlpacaPaperEvidenceGateway,PaperGatewayUnavailable
from pro_ticker_execution_routes import build_paper_execution_router


class ExecutionJournalTests(unittest.TestCase):
    make_draft=fixtures.PositionMonitorTests.make_draft
    prepare=fixtures.PositionMonitorTests.prepare

    def setUp(self):
        fixtures.PositionMonitorTests.setUp(self)
        fixtures.PositionMonitorTests.monitor(self)
        self.activities={self.parent['id']:[self.activity(self.parent,1,'10',1),self.activity(self.parent,1,'11',2)],self.legs[0]['id']:[],self.legs[1]['id']:[]}
        self.gateway.read_fill_activities.side_effect=lambda id:deepcopy(self.activities[id])

    def activity(self,order,qty,price,cum):
        return {'id':'execution::'+str(uuid.uuid4()),'activity_type':'FILL','type':'fill',
            'order_id':order['id'],'symbol':'AAPL','side':order['side'],'qty':str(qty),'cum_qty':str(cum),'price':price,
            'transaction_time':datetime.now(timezone.utc).isoformat()}

    def sync(self):return sync_execution_journal(self.intent['id'],context=self.context)

    def test_exact_activity_prices_and_execution_times_are_immutable_and_retry_safe(self):
        before=workspace_snapshot();self.assertEqual(self.sync()['execution_count'],2)
        first=execution_journal_snapshot();self.assertEqual(first['total_count'],2)
        self.assertEqual({r['price'] for r in first['rows']},{'10','11'})
        self.assertIsNone(first['gross_realized_usd']);self.assertIsNone(first['net_pnl']);self.assertIsNone(first['fees'])
        self.sync();self.assertEqual(execution_journal_snapshot(),first)
        after=workspace_snapshot()
        for key in ('budget','positions','journal'):self.assertEqual(before[key],after[key])
        self.gateway.submit_bracket.assert_not_called()

    def test_missing_activity_coverage_never_becomes_zero_profit(self):
        self.activities[self.parent['id']].pop()
        with self.assertRaises(IntentConflict):self.sync()
        self.assertEqual(execution_journal_snapshot()['total_count'],0)
        self.assertIsNone(execution_journal_snapshot()['gross_realized_usd'])

    def test_foreign_or_malformed_activities_are_rejected_atomically(self):
        original=deepcopy(self.activities[self.parent['id']][0])
        for change in ({'symbol':'MSFT'},{'side':'sell'},{'order_id':str(uuid.uuid4())},{'qty':'0.5'},
                       {'price':'NaN'},{'price':True},{'cum_qty':'2'},{'activity_type':'FEE'},
                       {'transaction_time':datetime.now(timezone.utc).replace(tzinfo=None).isoformat()},
                       {'transaction_time':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}):
            self.activities[self.parent['id']][0]={**original,**change}
            with self.subTest(change=change),self.assertRaises(ValueError):self.sync()
            self.assertEqual(execution_journal_snapshot()['total_count'],0)

    def test_reused_activity_id_cannot_rewrite_a_price(self):
        self.sync();self.activities[self.parent['id']][0]['price']='99'
        with self.assertRaises(IntentConflict):self.sync()
        self.assertEqual({r['price'] for r in execution_journal_snapshot()['rows']},{'10','11'})

    def test_posted_share_mismatch_rolls_back_all_new_execution_records(self):
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE pro_ticker_strategy_fill_events SET shares=1');conn.commit()
        with self.assertRaises(IntentConflict):self.sync()
        self.assertEqual(execution_journal_snapshot()['total_count'],0)

    def test_complete_closed_activities_compute_gross_without_counting_returned_budget(self):
        self.legs[1].update(filled_qty='2',status='filled',filled_avg_price='11.5');self.legs[0]['status']='canceled'
        fixtures.PositionMonitorTests.monitor(self)
        self.activities[self.legs[1]['id']]=[self.activity(self.legs[1],2,'11.5',2)]
        self.sync();self.assertEqual(execution_journal_snapshot()['closed_priced_count'],1)
        from pro_ticker_manual_release import prepare_manual_paper_release,approve_manual_paper_release,release_manually_approved_paper_budget
        def reader(id):return deepcopy(self.parent if id==self.parent['id'] else next(row for row in self.legs if row['id']==id))
        review=prepare_manual_paper_release(self.intent['reservation_id'],fetch_paper_order=reader)
        approve_manual_paper_release(review['review_id'],evidence_digest=review['evidence_digest'],approved_by='owner',independent_paper_provenance_confirmed=True)
        release_manually_approved_paper_budget(review['review_id'],fetch_paper_order=reader)
        result=execution_journal_snapshot();self.assertEqual(result['closed_priced_count'],1);self.assertEqual(result['gross_realized_usd'],'2.0')
        self.assertIsNone(result['net_pnl']);self.assertEqual(workspace_snapshot()['budget']['committed_cents'],0)
        self.sync();self.assertEqual(execution_journal_snapshot(),result)

    def test_http_owner_origin_default_off_and_browser_evidence_rejection(self):
        def owner(request):
            if request.headers.get('X-Owner')!='yes':raise HTTPException(401)
        app=FastAPI();app.include_router(build_paper_execution_router(require_session=owner,context=self.context))
        disabled=FastAPI();disabled.include_router(build_paper_execution_router(require_session=owner))
        async def run():
            path='/auto-trader/pro-ticker/order-intents/'+self.intent['id']+'/execution-sync';headers={'X-Owner':'yes','Origin':'https://test'}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as client:
                self.assertEqual((await client.get('/auto-trader/pro-ticker/execution-journal')).status_code,401)
                self.assertEqual((await client.post(path,json={},headers={**headers,'Origin':'https://evil'})).status_code,403)
                self.assertEqual((await client.post(path,json={'activities':[]},headers=headers)).status_code,422)
                result=await client.post(path,json={},headers=headers);self.assertEqual(result.status_code,200)
                history=await client.get('/auto-trader/pro-ticker/execution-journal',headers=headers);self.assertEqual(history.json()['total_count'],2)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=disabled),base_url='https://test') as client:self.assertEqual((await client.post(path,json={},headers=headers)).status_code,403)
        asyncio.run(run())


class ExecutionPaginationTests(unittest.TestCase):
    def gateway(self,get):return AlpacaPaperEvidenceGateway(account_id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',key_id='synthetic',secret_key='synthetic',http_get=get)

    def test_order_filtered_bounded_get_pagination(self):
        first=[{'id':'execution::'+str(i)} for i in range(100)]
        get=Mock(side_effect=[Mock(status_code=200,json=lambda:first),Mock(status_code=200,json=lambda:[])])
        order=str(uuid.uuid4());self.assertEqual(self.gateway(get).read_fill_activities(order),first)
        self.assertIn('order_id='+order,get.call_args_list[0].args[0]);self.assertIn('page_token=execution%3A%3A99',get.call_args_list[1].args[0])
        self.assertTrue(all(c.kwargs['allow_redirects'] is False for c in get.call_args_list))

    def test_repeated_page_or_malformed_status_cannot_claim_complete_history(self):
        page=[{'id':'execution::'+str(i)} for i in range(100)]
        get=Mock(return_value=Mock(status_code=200,json=lambda:page))
        with self.assertRaises(PaperGatewayUnavailable):self.gateway(get).read_fill_activities(str(uuid.uuid4()))
        self.assertEqual(get.call_count,2)
        get.return_value=Mock(status_code='200',json=lambda:[])
        with self.assertRaises(PaperGatewayUnavailable):self.gateway(get).read_fill_activities(str(uuid.uuid4()))


class VerifiedGrossOutcomeTests(unittest.TestCase):
    setUp=ExecutionJournalTests.setUp
    make_draft=ExecutionJournalTests.make_draft
    prepare=ExecutionJournalTests.prepare
    activity=ExecutionJournalTests.activity
    sync=ExecutionJournalTests.sync

    def complete(self,price):
        exit_order=self.legs[0]
        exit_order.update(filled_qty='2',status='filled');self.legs[1]['status']='canceled'
        fixtures.PositionMonitorTests.monitor(self)
        self.activities[exit_order['id']]=[self.activity(exit_order,2,price,2)]
        self.sync();return execution_journal_snapshot()

    def test_priced_winner_counts_before_budget_return(self):
        result=self.complete('11.5')
        self.assertEqual(result['closed_priced_count'],1);self.assertEqual(result['gross_wins'],1)
        self.assertEqual(result['gross_losses'],0);self.assertEqual(result['gross_win_rate_percent'],100)
        self.assertEqual(result['gross_realized_usd'],'2.0')
        self.assertEqual(result['gross_profit_usd'],'2.0');self.assertEqual(result['gross_loss_usd'],'0')
        self.assertIsNone(result['gross_profit_factor']);self.assertEqual(result['average_gross_result_usd'],'2.000000')
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)
        self.assertIsNone(result['fees']);self.assertIsNone(result['adjustments']);self.assertIsNone(result['net_pnl'])
        self.assertEqual(result['net_pnl_status'],'unavailable_cost_evidence')
        group=result['entry_grade_groups'][0]
        self.assertEqual(group['grade'],self.intent['plan']['entry_grade']['grade'])
        self.assertEqual(group['grade_version'],self.intent['plan']['entry_grade']['version'])
        self.assertEqual(group['closed_count'],1);self.assertEqual(group['gross_realized_usd'],'2.0')
        self.assertEqual(result['unattributed_closed_count'],0);self.assertFalse(result['training_eligible'])

    def test_priced_loser_uses_actual_execution_cost(self):
        result=self.complete('9.5')
        self.assertEqual(result['gross_losses'],1);self.assertEqual(result['gross_wins'],0)
        self.assertEqual(result['gross_realized_usd'],'-2.0');self.assertEqual(result['gross_win_rate_percent'],0)
        self.assertEqual(result['gross_loss_usd'],'2.0');self.assertEqual(result['gross_profit_factor'],'0.000000')
        self.assertEqual(result['average_gross_result_usd'],'-2.000000')

    def test_breakeven_is_neither_a_win_nor_a_loss(self):
        result=self.complete('10.5')
        self.assertEqual(result['gross_flat'],1);self.assertEqual(result['gross_wins']+result['gross_losses'],0)
        self.assertEqual(result['gross_realized_usd'],'0.0')
        self.assertEqual(result['average_gross_result_usd'],'0.000000');self.assertIsNone(result['gross_profit_factor'])


class CompleteExecutionExportTests(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from pro_ticker_execution_journal import SCHEMA
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        patcher=patch.object(database,'DATABASE_PATH',Path(temp.name)/'export.db');patcher.start();self.addCleanup(patcher.stop)
        with closing(database.get_connection()) as conn:conn.executescript(SCHEMA);conn.commit()

    def insert(self,count,offset=0):
        with closing(database.get_connection()) as conn:
            conn.executemany('INSERT INTO pro_ticker_execution_journal VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                [('activity::'+str(i),'intent','reservation','order','AAPL','buy',1,'10','2026-10-23T14:00:00+00:00','2026-10-23T14:00:01+00:00','synthetic') for i in range(offset,offset+count)])
            conn.commit()

    def test_full_export_is_not_limited_to_the_500_row_ui_window(self):
        import csv,io
        from pro_ticker_execution_journal import prepare_execution_journal_export
        self.insert(1201)
        count,stream,close=prepare_execution_journal_export()
        chunks=list(stream);close()
        rows=list(csv.DictReader(io.StringIO(''.join(chunks))))
        self.assertEqual(count,1201);self.assertEqual(len(rows),1201)
        self.assertEqual(len({row['activity_id'] for row in rows}),1201)
        self.assertEqual(len(chunks),4)

    def test_export_uses_one_snapshot_and_escapes_spreadsheet_formulas(self):
        import csv,io
        from pro_ticker_execution_journal import prepare_execution_journal_export
        with closing(database.get_connection()) as conn:conn.execute('PRAGMA journal_mode=WAL')
        self.insert(2)
        with closing(database.get_connection()) as conn:conn.execute("UPDATE pro_ticker_execution_journal SET activity_id='=formula' WHERE activity_id='activity::0'");conn.commit()
        count,stream,close=prepare_execution_journal_export()
        self.insert(1,offset=2)
        rows=list(csv.DictReader(io.StringIO(''.join(stream))));close()
        self.assertEqual(count,2);self.assertEqual(len(rows),2)
        self.assertIn("'=formula",[row['activity_id'] for row in rows])

    def test_export_route_requires_owner_and_returns_full_count(self):
        import csv,io
        self.insert(600)
        def owner(request):
            if request.headers.get('X-Owner')!='yes':raise HTTPException(401)
        app=FastAPI();app.include_router(build_paper_execution_router(require_session=owner))
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as client:
                path='/auto-trader/pro-ticker/execution-journal.csv'
                self.assertEqual((await client.get(path)).status_code,401)
                response=await client.get(path,headers={'X-Owner':'yes'})
                self.assertEqual(response.status_code,200);self.assertEqual(response.headers['X-Execution-Total-Count'],'600')
                self.assertEqual(response.headers['Cache-Control'],'no-store')
                self.assertEqual(len(list(csv.DictReader(io.StringIO(response.text)))),600)
        asyncio.run(run())


class GrossExecutionMetricsTests(unittest.TestCase):
    def test_mixed_results_include_breakeven_in_trade_average(self):
        from decimal import Decimal
        result=gross_execution_metrics([Decimal('5'),Decimal('-2'),Decimal('0')])
        self.assertEqual(result,{'gross_profit_usd':'5','gross_loss_usd':'2',
            'gross_profit_factor':'2.500000','average_gross_result_usd':'1.000000'})

    def test_empty_and_no_loss_results_do_not_invent_profit_factor(self):
        from decimal import Decimal
        self.assertTrue(all(value is None for value in gross_execution_metrics([]).values()))
        for outcomes in ([Decimal('3')],[Decimal('0')]):
            self.assertIsNone(gross_execution_metrics(outcomes)['gross_profit_factor'])
        result=gross_execution_metrics([Decimal('-3')])
        self.assertEqual(result['gross_profit_factor'],'0.000000')
        self.assertEqual(result['average_gross_result_usd'],'-3.000000')


class GradeExecutionMetricsTests(unittest.TestCase):
    def test_versions_remain_separate_and_unknown_grades_have_denominator(self):
        from decimal import Decimal
        grade=lambda version,label='A':{'status':'graded','version':version,'grade':label}
        result=grouped_execution_metrics([(grade('v1'),Decimal('5')),(grade('v1'),Decimal('-2')),
            (grade('v2'),Decimal('0')),(None,Decimal('9')),(grade(''),Decimal('-4'))])
        self.assertEqual(result['unattributed_closed_count'],2)
        self.assertEqual([(r['grade_version'],r['closed_count']) for r in result['entry_grade_groups']],[('v1',2),('v2',1)])
        self.assertEqual(result['entry_grade_groups'][0]['gross_profit_factor'],'2.500000')
        self.assertEqual(result['entry_grade_groups'][0]['average_gross_result_usd'],'1.500000')
        self.assertEqual(result['entry_grade_groups'][1]['gross_flat'],1)
        self.assertFalse(result['training_eligible'])

    def test_empty_data_does_not_imply_grade_performance(self):
        result=grouped_execution_metrics([])
        self.assertEqual(result['entry_grade_groups'],[]);self.assertEqual(result['unattributed_closed_count'],0)
