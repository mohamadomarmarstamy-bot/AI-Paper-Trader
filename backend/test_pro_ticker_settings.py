"""Isolated settings, optimistic locking, risk enforcement and HTTP boundaries."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from fastapi import FastAPI, HTTPException
import database
from pro_ticker_settings import DEFAULTS, SettingsConflict, read_settings, save_settings
from pro_ticker_workspace import build_workspace_router, hypothetical_proposal
from pro_ticker_discovery import run_discovery


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'settings.db');p.start();self.addCleanup(p.stop)

    def payload(self, revision=0, **changes):
        return {'expected_revision':revision,'settings':{**DEFAULTS,**changes}}

    def test_default_read_does_not_create_tables_or_enable_trading(self):
        value=read_settings()
        self.assertEqual(value['revision'],0);self.assertEqual(value['settings'],DEFAULTS)
        self.assertFalse(value['order_execution_enabled']);self.assertFalse(value['automatic_release_enabled'])
        with closing(database.get_connection()) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()[0],0)

    def test_settings_and_server_assigned_audit_persist_together(self):
        result=save_settings(self.payload(max_allocation_cents=12345))
        self.assertEqual(result['revision'],1);self.assertEqual(read_settings(),result)
        with closing(database.get_connection()) as c:
            row=dict(c.execute('SELECT * FROM pro_ticker_workspace_settings_events').fetchone())
            self.assertEqual(row['changed_by'],'authenticated-app-owner')
            self.assertEqual(json.loads(row['previous_json']),DEFAULTS)
            self.assertEqual(json.loads(row['settings_json'])['max_allocation_cents'],12345)
            self.assertEqual(row['changed_at'],result['updated_at'])

    def test_stale_replay_cannot_overwrite_or_duplicate_audit(self):
        save_settings(self.payload(max_stop_risk_cents=100))
        for payload in (self.payload(max_stop_risk_cents=200),self.payload(max_stop_risk_cents=100)):
            with self.assertRaises(SettingsConflict):save_settings(payload)
        self.assertEqual(read_settings()['settings']['max_stop_risk_cents'],100)
        with closing(database.get_connection()) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM pro_ticker_workspace_settings_events').fetchone()[0],1)

    def test_concurrent_initial_saves_have_one_winner(self):
        def save(value):
            try:return save_settings(self.payload(max_allocation_cents=value))['revision']
            except SettingsConflict:return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(save,[100,200]))
        self.assertCountEqual(results,[1,'conflict'])
        self.assertIn(read_settings()['settings']['max_allocation_cents'],[100,200])

    def test_interrupted_audit_write_rolls_back_settings(self):
        before=save_settings(self.payload())
        with closing(database.get_connection()) as c:
            c.execute("CREATE TRIGGER fail_settings BEFORE INSERT ON pro_ticker_workspace_settings_events BEGIN SELECT RAISE(ABORT,'interrupted'); END;");c.commit()
        with self.assertRaises(Exception):save_settings(self.payload(1,max_allocation_cents=100))
        self.assertEqual(read_settings(),before)

    def test_unknown_fields_flags_amounts_and_revision_are_rejected(self):
        values=[{'expected_revision':True,'settings':DEFAULTS},{'expected_revision':-1,'settings':DEFAULTS},
                {**self.payload(),'approved_by':'other'}, self.payload(order_execution_enabled=True),
                self.payload(max_allocation_cents=True),self.payload(max_allocation_cents=0),
                self.payload(max_stop_risk_cents=5000001),self.payload(discovery_article_limit=6),
                self.payload(discovery_article_limit=1.5),self.payload(max_allocation_cents='100')]
        for value in values:
            with self.subTest(value=value),self.assertRaises(ValueError):save_settings(value)
        self.assertEqual(read_settings()['revision'],0)

    def test_original_trader_settings_are_not_read_or_modified(self):
        with closing(database.get_connection()) as c:
            c.execute('CREATE TABLE auto_trader_settings (value TEXT)');c.execute("INSERT INTO auto_trader_settings VALUES ('original')");c.commit()
        save_settings(self.payload(discovery_article_limit=2))
        with closing(database.get_connection()) as c:
            self.assertEqual(c.execute('SELECT value FROM auto_trader_settings').fetchone()[0],'original')

    def test_proposal_checks_saved_limits_without_reserving(self):
        save_settings(self.payload(max_allocation_cents=1000,max_stop_risk_cents=10))
        result=hypothetical_proposal(dict(symbol='AAPL',reference_price='10',shares=2,stop_loss_percent='2',take_profit_percent='4'))
        self.assertTrue(result['within_virtual_budget']);self.assertFalse(result['risk_limits_satisfied'])
        self.assertEqual(result['settings_revision'],1)
        self.assertEqual(result['risk_limit_reasons'],['allocation_exceeds_pro_ticker_limit','estimated_stop_risk_exceeds_pro_ticker_limit'])
        self.assertFalse(result['budget_reserved']);self.assertFalse(result['order_submitted'])

    def test_limits_at_exact_boundary_are_allowed_only_as_estimates(self):
        save_settings(self.payload(max_allocation_cents=2000,max_stop_risk_cents=40))
        result=hypothetical_proposal(dict(symbol='AAPL',reference_price='10',shares=2,stop_loss_percent='2',take_profit_percent='4'))
        self.assertTrue(result['risk_limits_satisfied']);self.assertFalse(result['order_execution_enabled'])

    def test_discovery_captures_revision_and_bounds_fetch_and_collection(self):
        save_settings(self.payload(discovery_article_limit=1))
        from unittest.mock import Mock
        source=Mock(return_value=[{'article_url':'one'},{'article_url':'two'}]);collect=Mock(return_value={'symbol':'AAPL'})
        result=run_discovery(discover=source,collect=collect)
        source.assert_called_once_with(start_page=1,max_pages=1,max_articles=1)
        self.assertEqual(collect.call_count,1)
        self.assertEqual(result['run']['settings_revision'],1);self.assertEqual(result['run']['article_limit'],1)

    def test_http_auth_origin_size_json_and_revision_conflict(self):
        def auth(request):
            if request.headers.get('x-owner')!='yes':raise HTTPException(401,'Sign in')
        app=FastAPI();app.include_router(build_workspace_router(require_session=auth))
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as c:
                url='/auto-trader/pro-ticker/settings';headers={'x-owner':'yes','Origin':'https://test'}
                self.assertEqual((await c.get(url)).status_code,401)
                self.assertEqual((await c.put(url,json=self.payload())).status_code,401)
                self.assertEqual((await c.get(url,headers=headers)).json()['revision'],0)
                self.assertEqual((await c.put(url,json=self.payload(),headers={**headers,'Origin':'https://evil'})).status_code,403)
                self.assertEqual((await c.put(url,content='{}',headers=headers)).status_code,415)
                self.assertEqual((await c.put(url,content=' '*2049,headers={**headers,'Content-Type':'application/json'})).status_code,413)
                self.assertEqual((await c.put(url,json=self.payload(order_execution_enabled=True),headers=headers)).status_code,422)
                self.assertEqual((await c.put(url,json=self.payload(),headers=headers)).json()['revision'],1)
                self.assertEqual((await c.put(url,json=self.payload(),headers=headers)).status_code,409)
        asyncio.run(send())

    def test_corrupt_saved_limits_fail_closed_without_default_fallback(self):
        save_settings(self.payload())
        with closing(database.get_connection()) as c:
            c.execute("UPDATE pro_ticker_workspace_settings SET settings_json='{}'");c.commit()
        with self.assertRaises(ValueError):read_settings()
        with self.assertRaises(ValueError):hypothetical_proposal(dict(symbol='AAPL',reference_price='10',shares=2,stop_loss_percent='2',take_profit_percent='4'))
