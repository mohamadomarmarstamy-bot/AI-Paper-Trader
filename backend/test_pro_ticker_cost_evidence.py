"""Cash evidence cannot fabricate final net returns, ownership or zero fees."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import asyncio
import json
import unittest
from unittest.mock import Mock
import uuid
import httpx
from fastapi import FastAPI, HTTPException
import database
from pro_ticker_cost_evidence import sync_cash_evidence,cash_evidence_snapshot,normalize_cash_activity
from pro_ticker_performance_analytics import analytics_snapshot
from pro_ticker_paper_gateway import AlpacaPaperEvidenceGateway,PaperGatewayUnavailable
from pro_ticker_execution_routes import build_paper_execution_router
import test_pro_ticker_execution_journal as fixtures


class CashEvidenceTests(unittest.TestCase):
    make_draft=fixtures.ExecutionJournalTests.make_draft
    prepare=fixtures.ExecutionJournalTests.prepare
    activity=fixtures.ExecutionJournalTests.activity
    sync=fixtures.ExecutionJournalTests.sync

    def setUp(self):
        fixtures.ExecutionJournalTests.setUp(self)
        self.gateway.read_account.side_effect=None
        self.gateway.read_account.return_value={'id':self.context.expected_account_id,'currency':'USD'}
        self.raw=[]
        self.gateway.read_nontrade_activities.side_effect=lambda:deepcopy(self.raw)

    def cash(self,kind='FEE',amount='-0.02',order=None,**changes):
        row={'id':'cash::'+str(uuid.uuid4()),'activity_type':kind,'net_amount':amount,'date':'2026-10-26','symbol':'AAPL'}
        if order is not None:row['order_id']=order
        return {**row,**changes}

    def observe(self):return sync_cash_evidence(context=self.context)

    def test_empty_history_never_means_zero_fees_or_final_net(self):
        result=self.observe();self.assertEqual(result['observed_activity_count'],0)
        data=cash_evidence_snapshot();self.assertEqual(data['rows'],[])
        self.assertIsNone(data['net_pnl_usd']);self.assertFalse(data['cost_coverage_final'])
        self.assertIsNotNone(data['last_observed_at'])
        self.gateway.submit_bracket.assert_not_called()

    def test_flows_unknown_adjustments_and_unlinked_fees_stay_separate(self):
        self.raw=[self.cash('CSD','50000'),self.cash(),self.cash('JNLC','9')]
        self.observe();data=cash_evidence_snapshot()
        self.assertEqual(data['category_cash_totals_usd'],{'external_cash_flow':'50000','other_adjustment':'9','reported_order_cost':'-0.02'})
        self.assertEqual(data['unattributed_count'],3)
        self.assertIsNone(data['net_pnl_usd'])

    def close(self):
        import test_pro_ticker_position_monitor as monitor
        self.legs[1].update(filled_qty='2',status='filled',filled_avg_price='11.5');self.legs[0]['status']='canceled'
        monitor.PositionMonitorTests.monitor(self)
        self.activities[self.legs[1]['id']]=[self.activity(self.legs[1],2,'11.5',2)]
        self.sync()

    def test_verified_order_costs_adjust_only_the_known_gross_subset(self):
        self.close();self.raw=[self.cash(order=self.legs[1]['id']),self.cash('PTR','0.01',order=self.legs[1]['id'])]
        self.observe();data=analytics_snapshot();row=data['cash_evidence']['closed_trade_known_costs'][0]
        self.assertEqual(row['known_linked_cost_cash_usd'],'-0.01')
        self.assertEqual(row['gross_after_known_costs_usd'],'1.99')
        self.assertIsNone(row['net_pnl_usd']);self.assertFalse(row['cost_coverage_final'])
        self.assertIsNone(data['net_pnl_usd']);self.assertEqual(data['gross_realized_usd'],'2.0')
        filtered=analytics_snapshot(start='2099-01-01T00:00:00Z')
        self.assertEqual(filtered['cash_evidence']['closed_trade_known_costs'],[])

    def test_same_symbol_and_date_are_not_enough_to_attribute_a_fee(self):
        self.raw=[self.cash(order=str(uuid.uuid4()))];self.observe()
        self.assertIsNone(cash_evidence_snapshot()['rows'][0]['intent_id'])

    def test_retries_are_idempotent_and_provider_edits_fail_atomically(self):
        self.raw=[self.cash()];self.observe();self.assertEqual(self.observe()['new_activity_count'],0)
        before=cash_evidence_snapshot();self.raw[0]['net_amount']='-999'
        with self.assertRaises(ValueError):self.observe()
        self.assertEqual(cash_evidence_snapshot(),before)
        self.raw=[]
        with self.assertRaises(ValueError):self.observe()
        self.assertEqual(cash_evidence_snapshot(),before)

    def test_bad_or_foreign_evidence_never_partially_commits(self):
        original=self.cash(order=self.parent['id'])
        for change in [{'net_amount':'NaN'},{'net_amount':True},{'date':'bad'},{'date':'2026-10-26T10:00:00'},
                       {'symbol':'MSFT'},{'order_id':'bad'},{'activity_type':'FILL'},
                       {'account_id':str(uuid.uuid4())},{'currency':'EUR'}]:
            self.raw=[self.cash(),{**original,**change}]
            with self.subTest(change=change),self.assertRaises((ValueError,ArithmeticError)):self.observe()
            self.assertEqual(cash_evidence_snapshot()['total_count'],0)
        self.raw=[original,original]
        with self.assertRaises(ValueError):self.observe()

    def test_unknown_provider_fields_cannot_certify_costs_or_leak_descriptions(self):
        self.raw=[self.cash(description='private-secret',cost_coverage_final=True,net_pnl_usd='100000')]
        self.observe();data=cash_evidence_snapshot()
        self.assertNotIn('private-secret',json.dumps(data));self.assertIsNone(data['net_pnl_usd'])
        self.assertFalse(data['cost_coverage_final'])

    def test_account_identity_and_fetch_expiry_fail_closed(self):
        self.gateway.read_account.return_value['id']=str(uuid.uuid4())
        with self.assertRaises(ValueError):self.observe()
        self.gateway.read_nontrade_activities.assert_not_called()
        self.gateway.read_account.return_value['id']=self.context.expected_account_id
        ticks=iter([1000,1031])
        with self.assertRaises(ValueError):sync_cash_evidence(context=self.context,clock=lambda:next(ticks))
        self.assertEqual(cash_evidence_snapshot()['total_count'],0)

    def test_cash_tables_survive_disposable_copy_validation(self):
        from validate_paper_database_copy import validate_database_backup
        self.raw=[self.cash()];self.observe();before=cash_evidence_snapshot()
        result=validate_database_backup(database.DATABASE_PATH)
        self.assertTrue(result['valid']);self.assertTrue(result['existing_values_preserved'])
        self.assertEqual(cash_evidence_snapshot(),before)

    def test_http_owner_origin_default_off_and_browser_evidence_rejection(self):
        def auth(request):
            if request.headers.get('x-owner')!='yes':raise HTTPException(401)
        app=FastAPI();app.include_router(build_paper_execution_router(require_session=auth,context=self.context))
        disabled=FastAPI();disabled.include_router(build_paper_execution_router(require_session=auth))
        async def run():
            headers={'x-owner':'yes','Origin':'https://test'};base='/auto-trader/pro-ticker/cash-evidence'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as c:
                self.assertEqual((await c.get(base)).status_code,401)
                self.assertEqual((await c.post(base+'/sync',json={},headers={**headers,'Origin':'https://evil'})).status_code,403)
                self.assertEqual((await c.post(base+'/sync',json={'net_amount':'0'},headers=headers)).status_code,422)
                self.assertEqual((await c.post(base+'/sync',json={},headers=headers)).status_code,200)
                r=await c.get(base,headers=headers);self.assertEqual(r.status_code,200);self.assertEqual(r.headers['cache-control'],'no-store')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=disabled),base_url='https://test') as c:
                self.assertEqual((await c.post(base+'/sync',json={},headers=headers)).status_code,403)
        asyncio.run(run());self.gateway.submit_bracket.assert_not_called()


class CashGatewayTests(unittest.TestCase):
    def gateway(self,get):
        return AlpacaPaperEvidenceGateway(account_id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',key_id='synthetic',secret_key='synthetic',http_get=get)

    def test_pagination_is_account_verified_bounded_and_read_only(self):
        account={'id':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'}
        page=[{'id':'cash::'+str(i)} for i in range(100)]
        get=Mock(side_effect=[Mock(status_code=200,json=lambda:account),Mock(status_code=200,json=lambda:page),Mock(status_code=200,json=lambda:[])])
        self.assertEqual(self.gateway(get).read_nontrade_activities(),page)
        self.assertIn('category=non_trade_activity',get.call_args_list[1].args[0])
        self.assertIn('page_token=cash%3A%3A99',get.call_args_list[2].args[0])
        self.assertTrue(all(c.kwargs['allow_redirects'] is False for c in get.call_args_list))

    def test_repeated_or_full_pages_cannot_claim_complete_history(self):
        account={'id':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'}
        page=[{'id':'cash::'+str(i)} for i in range(100)]
        get=Mock(side_effect=[Mock(status_code=200,json=lambda:account),Mock(status_code=200,json=lambda:page),Mock(status_code=200,json=lambda:page)])
        with self.assertRaises(PaperGatewayUnavailable):self.gateway(get).read_nontrade_activities()

    def test_five_full_distinct_pages_remain_explicitly_incomplete(self):
        account={'id':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'}
        responses=[Mock(status_code=200,json=lambda:account)]
        for page_number in range(5):
            page=[{'id':'cash::'+str(page_number*100+i)} for i in range(100)]
            responses.append(Mock(status_code=200,json=lambda page=page:page))
        get=Mock(side_effect=responses)
        with self.assertRaises(PaperGatewayUnavailable):self.gateway(get).read_nontrade_activities()
        self.assertEqual(get.call_count,6)
