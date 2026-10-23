"""Closed gross estimates require owner release proof and never invent net P/L."""
import asyncio
from contextlib import closing
import json
import sqlite3
import uuid
from unittest.mock import patch
import unittest
import httpx
from fastapi import FastAPI,HTTPException
import database
import test_pro_ticker_broker_snapshot_audit as fixtures
from pro_ticker_manual_release import prepare_manual_paper_release,approve_manual_paper_release,release_manually_approved_paper_budget
from pro_ticker_closed_performance import closed_performance_snapshot
from pro_ticker_workspace import build_workspace_router


class ClosedPerformanceTests(unittest.TestCase):
    setUp=fixtures.BrokerSnapshotAuditTests.setUp

    def close(self,entry_price='10',exit_price='12'):
        entry={**self.entry_order,'filled_avg_price':entry_price};exit={**self.exit_order,'filled_avg_price':exit_price}
        def reader(order_id):return {entry['id']:entry,exit['id']:exit}[order_id].copy()
        review=prepare_manual_paper_release(self.reservation_id,fetch_paper_order=reader)
        approve_manual_paper_release(review['review_id'],evidence_digest=review['evidence_digest'],approved_by='test-owner',independent_paper_provenance_confirmed=True)
        result=release_manually_approved_paper_budget(review['review_id'],fetch_paper_order=reader)
        return result,review,reader

    def test_priced_closed_gross_uses_fills_not_released_allocation(self):
        result,_,_=self.close();self.assertEqual(result['released_cents'],10000)
        data=closed_performance_snapshot();row=data['rows'][0]
        self.assertEqual(data['gross_pnl_cents'],1000);self.assertEqual(row['entry_notional'],'50');self.assertEqual(row['exit_notional'],'60')
        self.assertEqual(row['gross_return_percent'],20);self.assertIsNone(row['net_pnl']);self.assertIsNone(row['fees'])
        self.assertFalse(row['training_eligible']);self.assertEqual(data['gross_win_rate_percent'],100)
        self.assertEqual(data['priced_count'],1);self.assertEqual(data['unpriced_count'],0);self.assertIsNone(data['gross_profit_factor'])
        self.assertIsNone(data['drawdown']);self.assertIsNone(data['unrealized_pnl'])

    def test_losses_and_flat_are_measured_without_becoming_virtual_returns(self):
        self.close(exit_price='9');data=closed_performance_snapshot()
        self.assertEqual(data['gross_pnl_cents'],-500);self.assertEqual(data['gross_losses'],1);self.assertEqual(data['gross_win_rate_percent'],0)
        self.assertEqual(data['gross_profit_factor'],0);self.assertEqual(data['average_gross_loss_cents'],-500)

    def test_all_closed_samples_drive_totals_even_when_display_is_limited(self):
        self.close(exit_price='12')
        from pro_ticker_budget_ledger import reserve_pro_ticker_budget,set_paper_reservation_expected_shares,link_verified_paper_entry_order,record_verified_paper_order_state
        from pro_ticker_position_ledger import post_verified_cumulative_paper_fill
        from pro_ticker_exit_links import link_verified_paper_exit
        for price in ['9','10']:
            self.reservation_id=reserve_pro_ticker_budget('AAPL',100)['reservation_id'];set_paper_reservation_expected_shares(self.reservation_id,5)
            self.entry_order={'id':str(uuid.uuid4()),'client_order_id':'pt-'+self.reservation_id,'symbol':'AAPL','side':'buy','qty':'5','filled_qty':'5','status':'filled'}
            link_verified_paper_entry_order(self.reservation_id,self.entry_order)
            record_verified_paper_order_state(self.reservation_id,self.entry_order['id'],'filled',filled_quantity=5)
            post_verified_cumulative_paper_fill(broker_order_id=self.entry_order['id'],symbol='AAPL',side='buy',cumulative_filled_shares=5,ownership_verified=True,fill_verified=True)
            exit_id=str(uuid.uuid4());self.exit_order={'id':str(uuid.uuid4()),'client_order_id':'ptx-'+exit_id,'symbol':'AAPL','side':'sell','qty':'5','filled_qty':'5','status':'filled'}
            link_verified_paper_exit(reservation_id=self.reservation_id,symbol='AAPL',expected_shares=5,broker_order=self.exit_order,exit_id=exit_id)
            post_verified_cumulative_paper_fill(broker_order_id=self.exit_order['id'],symbol='AAPL',side='sell',cumulative_filled_shares=5,ownership_verified=True,fill_verified=True)
            self.close(exit_price=price)
        data=closed_performance_snapshot(limit=1)
        self.assertTrue(data['has_more']);self.assertEqual(len(data['rows']),1)
        self.assertEqual(data['gross_pnl_cents'],500);self.assertEqual(data['gross_profit_factor'],2)
        self.assertEqual(data['gross_wins'],1);self.assertEqual(data['gross_losses'],1);self.assertEqual(data['gross_flat'],1)
        self.assertAlmostEqual(data['gross_win_rate_percent'],100/3)

    def test_missing_invalid_prices_preserve_release_but_do_not_become_zero_profit(self):
        for value in [None,True,'NaN','Infinity','-1','0']:
            # Pure capture is deliberately exercised through the approved service
            # in individual subtests on the same fixture by rolling back the
            # completed release to its initial snapshot with SQLite backup.
            with closing(database.get_connection()) as conn:
                import sqlite3
                backup=sqlite3.connect(':memory:');conn.backup(backup)
            try:
                with self.subTest(value=value):
                    result,_,_=self.close(entry_price=value);self.assertTrue(result['budget_released'])
                    data=closed_performance_snapshot();self.assertIsNone(data['gross_pnl_cents']);self.assertIsNone(data['gross_win_rate_percent'])
                    self.assertEqual(data['unpriced_count'],1);self.assertEqual(data['rows'][0]['status'],'unavailable')
            finally:
                with closing(database.get_connection()) as conn:backup.backup(conn)
                backup.close()

    def test_retry_never_reprices_first_closed_record(self):
        _,review,reader=self.close(exit_price='9');first=closed_performance_snapshot()['rows'][0]
        with patch.dict(self.exit_order,filled_avg_price='100'):
            retry=release_manually_approved_paper_budget(review['review_id'],fetch_paper_order=lambda _:self.fail('retry must not refetch'))
        self.assertTrue(retry['already_released']);self.assertEqual(first,closed_performance_snapshot()['rows'][0])

    def test_report_requires_matching_release_evidence_and_is_read_only(self):
        self.close()
        with closing(database.get_connection()) as conn:
            conn.execute("UPDATE pro_ticker_budget_release_events SET evidence_digest='different'");conn.commit()
        with self.assertRaises(ValueError):closed_performance_snapshot()

    def test_atomic_capture_failure_rolls_back_budget_release(self):
        with patch('pro_ticker_manual_release.capture_closed_performance',side_effect=RuntimeError('capture failure')):
            with self.assertRaises(RuntimeError):self.close()
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM pro_ticker_budget_release_events').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT status FROM pro_ticker_paper_reservations').fetchone()[0],'filled')
        self.assertEqual(closed_performance_snapshot()['closed_reservations_count'],0)

    def test_legacy_release_without_price_record_is_explicitly_unpriced(self):
        self.close()
        with closing(database.get_connection()) as conn:conn.execute('DELETE FROM pro_ticker_closed_performance');conn.commit()
        data=closed_performance_snapshot();self.assertEqual(data['closed_reservations_count'],1)
        self.assertEqual(data['unpriced_count'],1);self.assertIsNone(data['gross_pnl_cents']);self.assertEqual(data['rows'],[])

    def test_http_owner_auth_no_store_and_sanitized_failure(self):
        app=FastAPI()
        def owner(request):
            if request.headers.get('x-owner')!='yes':raise HTTPException(401)
        app.include_router(build_workspace_router(require_session=owner))
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as c:
                path='/auto-trader/pro-ticker/closed-performance'
                self.assertEqual((await c.get(path)).status_code,401)
                good=await c.get(path,headers={'x-owner':'yes'});self.assertEqual(good.status_code,200);self.assertEqual(good.headers['cache-control'],'no-store')
                with patch('pro_ticker_closed_performance.closed_performance_snapshot',side_effect=RuntimeError('private key')):
                    bad=await c.get(path,headers={'x-owner':'yes'});self.assertEqual(bad.status_code,503);self.assertNotIn('private key',bad.text)
        def connection():
            # The HTTP report runs in a worker thread and closes its own
            # connection; do not add it to the main-thread fixture tracker.
            conn=sqlite3.connect(database.DATABASE_PATH,timeout=30);conn.row_factory=sqlite3.Row;return conn
        with patch.object(database,'get_connection',side_effect=connection):asyncio.run(run())
