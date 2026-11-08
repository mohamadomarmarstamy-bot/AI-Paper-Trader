from contextlib import closing
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
import database
import solana_copy_performance as performance
from solana_copy_entry import SOL


def buy(ident='buy',wallet='leader',**changes):
    row={'id':ident,'wallet':'owner','source_wallet':wallet,'mint':'coin','side':'buy','status':'confirmed',
         'created_at':10,'signature':'public-signature','prepared':{'decimals':6,'wire':'PRIVATE-WIRE'},
         'result':{'token_delta':1_000_000,'native_delta':-110_000_000,'fee_lamports':5000},
         'source_entry':{'mint':'coin','quote_mint':SOL,'quote_amount_raw':'100000000','token_amount_raw':'1000000','token_decimals':6},
         'timing_ms':{'source_to_broadcast_ms':2000}}
    return {**row,**changes}


def sell(ident='sell',**changes):
    return {'id':ident,'wallet':'owner','mint':'coin','source_wallet':'leader','position_id':'buy',
            'side':'sell','status':'confirmed','created_at':20,
            'result':{'token_delta':-1_000_000,'native_delta':120_000_000,'fee_lamports':5000},
            'realized_pnl_lamports':10_000_000,**changes}


class ReportMathTests(unittest.TestCase):
    def test_actual_receipt_entry_cost_not_quote_or_chart_price(self):
        row=performance.entry_price(buy())
        self.assertAlmostEqual(row['our_sol_per_token'],.11)
        self.assertAlmostEqual(row['source_sol_per_token'],.1)
        self.assertAlmostEqual(row['premium_percent'],10)
        order=buy();order['source_entry']['token_decimals']=9
        self.assertIsNone(performance.entry_price(order)['premium_percent'])
        order['source_entry']=None
        self.assertEqual(performance.entry_price(order)['our_sol_per_token'],.11)

    def test_unknown_or_malformed_reference_does_not_become_zero_premium(self):
        for bad in (None,{}, {'mint':'other'}, {'mint':'coin','quote_mint':SOL,'quote_amount_raw':'0','token_amount_raw':'1','token_decimals':6}):
            self.assertIsNone(performance.entry_price(buy(source_entry=bad))['premium_percent'])
        self.assertIsNone(performance.entry_price(buy(result={'native_delta':1,'token_delta':1}))['our_sol_per_token'])

    def test_partial_exits_group_one_completed_position_and_count_failed_fees_once(self):
        first=sell('half',result={'token_delta':-500_000,'native_delta':65_000_000},realized_pnl_lamports=10_000_000)
        second=sell('rest',result={'token_delta':-500_000,'native_delta':55_000_000},realized_pnl_lamports=0)
        fee=sell('failed',status='failed',result={'fee_lamports':100_000})
        row=performance.wallet_results([], [buy(),first,second,fee],{})[0]
        self.assertEqual(row['net_realized_lamports'],9_900_000)
        self.assertEqual(row['completed_positions'],1);self.assertEqual(row['win_rate_percent'],100)
        partial=performance.wallet_results([], [buy(),first],{})[0]
        self.assertEqual(partial['completed_positions'],0);self.assertIsNone(partial['win_rate_percent'])

    def test_unknown_fee_or_sale_allocation_marks_net_incomplete(self):
        for extra in (sell('unallocated',realized_pnl_lamports=None),sell('unknown-fee',status='failed',result={})):
            row=performance.wallet_results([], [buy(),extra],{})[0]
            self.assertIsNone(row['net_realized_lamports'])
        row=performance.wallet_results([], [sell(source_wallet=None)],{})[0]
        self.assertEqual(row['name'],'Unattributed')

    def test_timing_coverage_excludes_missing_invalid_and_pending_samples(self):
        orders=[buy(),buy('missing',timing_ms={}),buy('pending',status='submitted'),
                buy('negative',timing_ms={'source_to_broadcast_ms':-1}),buy('bad',timing_ms={'source_to_broadcast_ms':float('nan')})]
        row=next(r for r in performance.timing_summary(orders) if r['key']=='source_to_broadcast_ms')
        self.assertEqual(row['count'],1);self.assertEqual(row['eligible_orders'],4)
        self.assertEqual(row['median'],2000)

    def test_cohorts_use_recorded_versions_and_pagination_does_not_change_totals(self):
        orders=[buy(str(i),execution_profile=performance.PROFILE if i%2 else None,created_at=i) for i in range(25)]
        before=deepcopy(orders)
        report=performance.report([],{'wallet':'owner','positions':{},'realized_lamports':0},orders,offset=20,limit=20,now=100)
        self.assertEqual(len(report['entries']),5);self.assertEqual(report['entry_count'],25)
        self.assertEqual(report['cohorts'][1]['confirmed_buys'],12)
        self.assertEqual(report['entry_premium']['count'],25)
        self.assertEqual(orders,before)
        self.assertNotIn('PRIVATE-WIRE',json.dumps(report));self.assertNotIn('prepared',json.dumps(report))

    def test_ledger_gap_or_external_accounting_stays_visible(self):
        for runtime in ({'realized_lamports':8_000_000}, {'realized_lamports':10_000_000,'balance_adjustments':[{'position_id':'buy'}]}):
            report=performance.report([],runtime,[buy(),sell()])
            self.assertFalse(report['accounting_complete'])
            self.assertEqual(report['allocated_realized_lamports'],10_000_000)
        self.assertTrue(performance.report([],{'realized_lamports':10_000_000},[buy(),sell()])['accounting_complete'])


class ReportRouteTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        patcher=patch.object(database,'DATABASE_PATH',Path(temp.name)/'report.db');patcher.start();self.addCleanup(patcher.stop)
        from solana_meme_routes import build_router
        self.allowed=True
        def auth(request):
            if not self.allowed:raise HTTPException(401)
        app=FastAPI();app.include_router(build_router(require_session=auth));self.client=TestClient(app)

    def test_empty_report_is_read_only_authenticated_and_validates_pages(self):
        self.allowed=False;self.assertEqual(self.client.get('/real-copy/performance').status_code,401)
        self.allowed=True
        for query in ('offset=-1','limit=0','limit=51','offset=bad'):
            self.assertEqual(self.client.get('/real-copy/performance?'+query).status_code,422)
        response=self.client.get('/real-copy/performance');self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['cache-control'],'no-store');self.assertEqual(response.json()['entry_count'],0)
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0],0)

    def test_full_ledger_receipts_enriched_and_scoped_to_current_owner(self):
        import solana_real_worker as live
        import solana_copy_trader as paper
        with closing(database.get_connection()) as conn:
            live.initialize(conn);paper.initialize(conn)
            runtime=live.initial();runtime.update(wallet='owner',realized_lamports=10_000_000)
            conn.execute('UPDATE solana_real_run SET state_json=? WHERE id=1',(json.dumps(runtime),))
            rows=[buy(),sell(realized_pnl_lamports=None),{**buy('old-owner'),'wallet':'other-owner'}]
            for row in rows:conn.execute('INSERT INTO solana_real_orders VALUES(?,?,?,?)',(row['id'],row['created_at'],row['status'],json.dumps(row)))
            conn.commit()
        response=self.client.get('/real-copy/performance').json()
        self.assertEqual(response['order_count'],2);self.assertTrue(response['accounting_complete'])
        self.assertEqual(response['wallets'][0]['net_realized_lamports'],10_000_000)
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT revision FROM solana_real_run').fetchone()[0],0)
            self.assertIsNone(json.loads(conn.execute("SELECT details_json FROM solana_real_orders WHERE id='sell'").fetchone()[0])['realized_pnl_lamports'])
