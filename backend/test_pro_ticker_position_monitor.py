"""Bracket parent ownership and independent child evidence drive exit posting."""
from contextlib import closing
import asyncio
from copy import deepcopy
import unittest
import uuid
import httpx
from fastapi import FastAPI,HTTPException
from unittest.mock import patch
import database
import test_pro_ticker_protection_review as fixtures
from pro_ticker_position_monitor import monitor_paper_position
from pro_ticker_order_intents import IntentConflict
from pro_ticker_workspace import workspace_snapshot
from pro_ticker_execution_routes import build_paper_execution_router
from pro_ticker_paper_gateway import PaperGatewayUnavailable


class PositionMonitorTests(unittest.TestCase):
    make_draft=fixtures.ProtectionReviewTests.make_draft
    prepare=fixtures.ProtectionReviewTests.prepare

    def setUp(self):fixtures.ProtectionReviewTests.setUp(self)
    def monitor(self):return monitor_paper_position(self.intent['id'],context=self.context)

    def test_full_entry_links_both_exits_and_independently_observes_protection(self):
        first=self.monitor();self.assertTrue(first['protection_verified']);self.assertFalse(first['closed'])
        self.assertFalse(first['budget_released']);self.assertEqual(first['posted_exit_shares'],0)
        second=self.monitor();self.assertEqual(second['posted_exit_shares'],0)
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM pro_ticker_paper_exit_links').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM pro_ticker_bracket_exit_provenance').fetchone()[0],2)
        self.gateway.submit_bracket.assert_not_called()

    def test_partial_take_profit_resizes_unfilled_stop_then_closes_without_release(self):
        self.monitor();self.legs[1].update(filled_qty='1',status='partially_filled',filled_avg_price='11.55');self.legs[0]['qty']='1'
        partial=self.monitor();self.assertEqual(partial['posted_exit_shares'],1);self.assertFalse(partial['closed']);self.assertFalse(partial['protection_verified'])
        self.assertEqual(workspace_snapshot()['positions']['rows'][0]['shares'],1)
        self.legs[1].update(filled_qty='2',status='filled');self.legs[0]['status']='canceled'
        closed=self.monitor();self.assertTrue(closed['closed']);self.assertEqual(closed['posted_exit_shares'],1)
        self.assertEqual(workspace_snapshot()['positions']['total'],0)
        self.assertEqual(workspace_snapshot()['budget']['committed_cents'],2100)
        self.assertEqual(self.monitor()['posted_exit_shares'],0)
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT expected_shares FROM pro_ticker_paper_exit_links WHERE broker_order_id=?',(self.legs[0]['id'],)).fetchone()[0],1)

    def test_open_unfilled_sibling_blocks_closure_even_when_position_is_zero(self):
        self.legs[1].update(filled_qty='2',status='filled')
        result=self.monitor();self.assertFalse(result['closed']);self.assertFalse(result['budget_released'])
        from pro_ticker_manual_release import prepare_manual_paper_release
        orders={self.parent['id']:self.parent,**{child['id']:child for child in self.legs}}
        with self.assertRaises(ValueError):prepare_manual_paper_release(self.intent['reservation_id'],fetch_paper_order=lambda id:deepcopy(orders[id]))

    def test_foreign_parent_and_child_disagreement_cannot_post_exits(self):
        for change in ({'client_order_id':'foreign'},{'order_class':'simple'},{'symbol':'MSFT'}):
            original=deepcopy(self.parent);self.parent.update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):self.monitor()
            self.parent.clear();self.parent.update(original)
        self.gateway.read_order.side_effect=lambda id:deepcopy(self.parent if id==self.parent['id'] else {**next(c for c in self.legs if c['id']==id),'symbol':'MSFT'})
        with self.assertRaises(ValueError):self.monitor()
        self.assertEqual(workspace_snapshot()['journal']['total'],1)

    def test_double_filled_oco_is_not_silently_accounted_as_safe_closure(self):
        for child in self.legs:child.update(status='filled',filled_qty='2')
        with self.assertRaises(IntentConflict):self.monitor()
        self.assertEqual(workspace_snapshot()['positions']['rows'][0]['shares'],2)

    def test_stale_exit_cannot_reduce_posted_shares(self):
        self.legs[1].update(filled_qty='1',status='partially_filled');self.monitor()
        self.legs[1].update(filled_qty='0',status='new')
        with self.assertRaises(IntentConflict):self.monitor()
        self.assertEqual(workspace_snapshot()['positions']['rows'][0]['shares'],1)

    def test_interruption_after_link_is_recoverable_without_duplicate_fill(self):
        self.legs[1].update(filled_qty='2',status='filled');self.legs[0]['status']='canceled'
        with patch('pro_ticker_verified_exit_fill_bridge.account_linked_paper_exit_snapshot',side_effect=RuntimeError('interrupted')),self.assertRaises(RuntimeError):self.monitor()
        result=self.monitor();self.assertEqual(result['posted_exit_shares'],2);self.assertTrue(result['closed'])
        self.assertEqual(self.monitor()['posted_exit_shares'],0)

    def test_foreign_existing_exit_link_is_not_overwritten(self):
        self.monitor()
        with closing(database.get_connection()) as conn:
            conn.execute("UPDATE pro_ticker_paper_exit_links SET client_order_id='foreign' WHERE broker_order_id=?",(self.legs[0]['id'],));conn.commit()
        with self.assertRaises(IntentConflict):self.monitor()

    def test_link_provenance_failure_rolls_back_both_exit_links_and_audit(self):
        from pro_ticker_position_monitor import SCHEMA
        from pro_ticker_exit_links import _SCHEMA
        with closing(database.get_connection()) as conn:
            conn.executescript(_SCHEMA+SCHEMA)
            conn.execute("CREATE TRIGGER fail_proof BEFORE INSERT ON pro_ticker_bracket_exit_provenance BEGIN SELECT RAISE(ABORT,'proof unavailable'); END");conn.commit()
        with self.assertRaises(Exception):self.monitor()
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM pro_ticker_paper_exit_links').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM pro_ticker_bracket_exit_audit').fetchone()[0],0)

    def test_http_auth_origin_injected_evidence_default_off_and_transport_failure(self):
        def owner(request):
            if request.headers.get('X-Owner')!='yes':raise HTTPException(401)
        app=FastAPI();app.include_router(build_paper_execution_router(require_session=owner,context=self.context))
        disabled=FastAPI();disabled.include_router(build_paper_execution_router(require_session=owner))
        async def run():
            path='/auto-trader/pro-ticker/order-intents/'+self.intent['id']+'/monitor';headers={'X-Owner':'yes','Origin':'https://test'}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as client:
                self.assertEqual((await client.post(path,json={})).status_code,401)
                self.assertEqual((await client.post(path,json={},headers={**headers,'Origin':'https://evil'})).status_code,403)
                self.assertEqual((await client.post(path,json={'legs':self.legs},headers=headers)).status_code,422)
                self.gateway.read_order.side_effect=PaperGatewayUnavailable('private account key')
                result=await client.post(path,json={},headers=headers);self.assertEqual(result.status_code,503);self.assertNotIn('private account key',result.text)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=disabled),base_url='https://test') as client:self.assertEqual((await client.post(path,json={},headers=headers)).status_code,403)
        asyncio.run(run())
