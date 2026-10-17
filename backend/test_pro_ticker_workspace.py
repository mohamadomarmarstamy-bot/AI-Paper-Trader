"""Workspace integration uses persisted strategy data, never shared broker equity."""
import asyncio
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from fastapi import FastAPI, HTTPException
import database
from pro_ticker_workspace import workspace_snapshot, build_workspace_router, hypothetical_proposal
from pro_ticker_budget_ledger import reserve_pro_ticker_budget
from pro_ticker_position_ledger import post_verified_paper_fill


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        patcher=patch.object(database,'DATABASE_PATH',Path(self.temp.name)/'workspace.db')
        patcher.start(); self.addCleanup(patcher.stop)

    def test_empty_snapshot_reads_without_creating_strategy_tables_or_inventing_profit(self):
        data=workspace_snapshot()
        self.assertEqual(data['budget']['available_cents'],5000000)
        self.assertEqual(data['journal']['total'],0)
        self.assertIsNone(data['performance']['realized_pnl'])
        self.assertIsNone(data['performance']['win_rate'])
        self.assertFalse(data['order_execution_enabled'])
        with closing(database.get_connection()) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()[0],0)

    def test_scanner_exposes_persisted_discovery_status_without_claiming_live_scan(self):
        from pro_ticker_discovery import run_discovery
        result = run_discovery(discover=lambda **kwargs: [], collect=lambda url: None)
        self.assertEqual(result['status'], 'no_results')
        data = workspace_snapshot()
        self.assertEqual(data['scanner']['public_discovery']['status'], 'no_results')
        self.assertEqual(data['scanner']['public_discovery']['run']['id'], result['run']['id'])
        self.assertEqual(data['scanner']['status'], 'saved_research_only')
        self.assertFalse(data['scanner']['automatic_discovery_enabled'])
        self.assertFalse(data['order_execution_enabled'])

    def test_partial_entry_exit_lifecycle_is_visible_without_shared_account_rows(self):
        reservation=reserve_pro_ticker_budget('AAPL',100)
        for event,side,qty in [('entry-1','buy',3),('entry-2','buy',2),('exit-1','sell',2)]:
            post_verified_paper_fill(event_id=event,broker_order_id='entry' if side=='buy' else 'exit',symbol='AAPL',side=side,filled_shares=qty,ownership_verified=True,fill_verified=True)
        data=workspace_snapshot()
        self.assertEqual(data['positions']['rows'][0]['shares'],3)
        self.assertEqual(data['journal']['total'],3)
        self.assertEqual(data['budget']['committed_cents'],10000)
        self.assertEqual(data['reservations']['rows'][0]['id'],reservation['reservation_id'])
        post_verified_paper_fill(event_id='exit-2',broker_order_id='exit',symbol='AAPL',side='sell',filled_shares=3,ownership_verified=True,fill_verified=True)
        data=workspace_snapshot()
        self.assertEqual(data['positions']['total'],0)
        self.assertEqual(data['budget']['committed_cents'],10000) # closure alone never releases
        self.assertIsNone(data['performance']['realized_pnl'])

    def test_limited_snapshot_uses_all_reservations_for_budget(self):
        reserve_pro_ticker_budget('AAPL',100); reserve_pro_ticker_budget('MSFT',200)
        data=workspace_snapshot(limit=1)
        self.assertEqual(len(data['reservations']['rows']),1)
        self.assertTrue(data['reservations']['has_more'])
        self.assertEqual(data['reservations']['total'],2)
        self.assertEqual(data['budget']['committed_cents'],30000)

    def test_only_strategy_tables_are_reported(self):
        with closing(database.get_connection()) as c:
            c.execute('CREATE TABLE trade_book(symbol TEXT, profit REAL)')
            c.execute("INSERT INTO trade_book VALUES ('OTHER',999999)");c.commit()
        data=workspace_snapshot()
        self.assertNotIn('OTHER',str(data));self.assertIsNone(data['performance']['realized_pnl'])

    def test_research_uses_existing_saved_records(self):
        database.initialize_database()
        with closing(database.get_connection()) as c:
            c.execute("INSERT INTO pro_ticker_research(symbol,article_url,article_title,our_scanner_score,created_at,updated_at) VALUES ('AAPL','https://example.test','Stored evidence',81,'2026-10-17','2026-10-17')");c.commit()
        data=workspace_snapshot()
        self.assertEqual(data['research']['rows'][0]['our_scanner_score'],81)
        self.assertEqual(data['scanner']['status'],'saved_research_only')
        self.assertFalse(data['scanner']['automatic_discovery_enabled'])

    def request(self,app,headers=None):
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as client:
                return await client.get('/auto-trader/pro-ticker/workspace',headers=headers)
        return asyncio.run(send())

    def test_http_authentication_before_database_and_sanitized_failure(self):
        def auth(request):
            if request.headers.get('x-test-owner')!='yes':raise HTTPException(401,'Sign in')
        app=FastAPI();app.include_router(build_workspace_router(require_session=auth))
        with patch('pro_ticker_workspace.workspace_snapshot',side_effect=RuntimeError('secret-private-path')) as read:
            self.assertEqual(self.request(app).status_code,401);read.assert_not_called()
            r=self.request(app,{'x-test-owner':'yes'});self.assertEqual(r.status_code,503)
            self.assertNotIn('secret-private-path',r.text)
        self.assertEqual(self.request(app,{'x-test-owner':'yes'}).status_code,200)

    def test_invalid_limit_rejected(self):
        for value in (True,0,501,'10'):
            with self.assertRaises(ValueError):workspace_snapshot(limit=value)

    def proposal(self, **updates):
        return hypothetical_proposal(dict(symbol='AAPL',reference_price='10.25',shares=5,
                                          stop_loss_percent='2',take_profit_percent='4',**updates))

    def test_hypothetical_proposal_uses_decimal_estimates_without_reserving(self):
        result=self.proposal()
        self.assertEqual(result['allocation_cents'],5125)
        self.assertEqual(result['stop_price'],'10.0450')
        self.assertEqual(result['take_profit_price'],'10.6600')
        self.assertEqual(result['estimated_stop_risk_cents'],103)
        self.assertFalse(result['budget_reserved']);self.assertFalse(result['order_submitted'])
        self.assertEqual(workspace_snapshot()['reservations']['total'],0)

    def test_hypothetical_proposal_cannot_exceed_remaining_virtual_budget(self):
        reserve_pro_ticker_budget('MSFT',49999)
        self.assertFalse(self.proposal()['within_virtual_budget'])
        self.assertEqual(self.proposal()['available_virtual_cents'],100)

    def test_hypothetical_proposal_rejects_untrusted_numbers_and_fields(self):
        base=dict(symbol='AAPL',reference_price='10',shares=1,stop_loss_percent='2',take_profit_percent='4')
        for update in ({'shares':True},{'shares':1.5},{'reference_price':'NaN'},
                       {'reference_price':'Infinity'},{'reference_price':'-1'},
                       {'stop_loss_percent':'100'},{'actor':'owner'}, {'reference_price':{}},
                       {'symbol':'<script>'}):
            with self.subTest(update=update),self.assertRaises(ValueError):
                hypothetical_proposal({**base,**update})

    def test_proposal_http_requires_auth_origin_and_json(self):
        def auth(request):
            if request.headers.get('x-test-owner')!='yes':raise HTTPException(401,'Sign in')
        app=FastAPI();app.include_router(build_workspace_router(require_session=auth))
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as c:
                body=dict(symbol='AAPL',reference_price='10',shares=1,stop_loss_percent='2',take_profit_percent='4')
                self.assertEqual((await c.post('/auto-trader/pro-ticker/proposal',json=body)).status_code,401)
                self.assertEqual((await c.post('/auto-trader/pro-ticker/proposal',json=body,headers={'x-test-owner':'yes','Origin':'https://evil.test'})).status_code,403)
                self.assertEqual((await c.post('/auto-trader/pro-ticker/proposal',content='bad',headers={'x-test-owner':'yes','Origin':'https://test'})).status_code,415)
                r=await c.post('/auto-trader/pro-ticker/proposal',json=body,headers={'x-test-owner':'yes','Origin':'https://test'})
                self.assertEqual(r.status_code,200);self.assertFalse(r.json()['order_submitted'])
        asyncio.run(run())
