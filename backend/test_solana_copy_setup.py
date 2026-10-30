from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
import database
import solana_copy_setup as setup
import solana_copy_trader as copy
import solana_copy_solscan as solscan
import solana_copy_rpc as rpc
from solana_meme_bot import Conflict
from solana_meme_routes import build_router


class SetupTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'test.sqlite3');p.start();self.addCleanup(p.stop)

    def test_research_seeds_are_attributed_prospective_and_idempotent(self):
        with patch.object(solscan,'configured',return_value=True),patch.object(setup.time,'time',return_value=1000):
            first=setup.seed_wallets(0)
            self.assertEqual(len(first['traders']),2);self.assertEqual(first['positions'],[]);self.assertEqual(first['cash_usd'],100000)
            for trader in first['traders']:
                self.assertEqual(trader['selected_at'],1000);self.assertEqual(trader['trade_provider'],'solscan')
                self.assertEqual(trader['qualification'],'owner_requested_research_seed')
                self.assertIsNone(trader['win_rate_percent']);self.assertIsNone(trader['historical_realized_usd'])
                self.assertTrue(trader['research_evidence']['source_url'].startswith('https://kolexplorer.com/kol/'))
            state,_=copy.load();state['cursors'][first['traders'][0]['wallet']]['at']=1050
            with closing(database.get_connection()) as conn:
                import json
                conn.execute('UPDATE solana_copy_state SET state_json=? WHERE id=1',(json.dumps(state),));conn.commit()
            setup.seed_wallets(0)
            self.assertEqual(copy.load()[0]['cursors'][first['traders'][0]['wallet']]['at'],1050)
            self.assertEqual(len(copy.load()[0]['traders']),2)
            with self.assertRaises(Conflict):setup.seed_wallets(9)

    def test_connection_checks_persist_safe_results_without_account_mutation(self):
        with closing(database.get_connection()) as conn:copy.initialize(conn)
        before=copy.load()
        with patch.object(copy.Birdeye,'request',side_effect=copy.DataUnavailable('Birdeye /trader/gainers-losers: HTTP 403; access denied.')),patch.object(solscan,'request',return_value=[]),patch.object(rpc,'request',return_value=[]):
            report=setup.check_connections()
        self.assertEqual(len(report['checks']),6);self.assertEqual(report['orders_submitted'],0)
        self.assertFalse(report['checks'][0]['ok']);self.assertTrue(report['checks'][-1]['ok'])
        self.assertEqual(setup.connection_status(),report);self.assertEqual(copy.load(),before)

    def test_test_and_seed_routes_require_owner_origin_and_exact_body(self):
        allowed=[False]
        def auth(request):
            if not allowed[0]:raise HTTPException(401)
        app=FastAPI();app.include_router(build_router(require_session=auth));client=TestClient(app)
        with patch.object(setup,'check_connections',return_value={'orders_submitted':0}) as check:
            self.assertEqual(client.post('/meme-coins/copy/connections',json={}).status_code,401)
            allowed[0]=True
            self.assertEqual(client.post('/meme-coins/copy/connections',json={}).status_code,403)
            headers={'Origin':'http://testserver'}
            self.assertEqual(client.post('/meme-coins/copy/connections',json={'api_key':'untrusted'},headers=headers).status_code,422)
            self.assertEqual(client.post('/meme-coins/copy/connections',json={},headers=headers).status_code,200)
            check.assert_called_once()
        self.assertEqual(client.post('/meme-coins/copy/seeds',json={'wallet':'untrusted'},headers=headers).status_code,422)
        self.assertEqual(client.post('/meme-coins/copy/seeds',json={'expected_revision':0},headers=headers).status_code,200)
        self.assertEqual(client.post('/meme-coins/copy/public-rpc',json={'expected_revision':0}).status_code,403)
        self.assertEqual(client.post('/meme-coins/copy/public-rpc',json={'expected_revision':0,'url':'untrusted'},headers=headers).status_code,422)
        self.assertEqual(client.post('/meme-coins/copy/public-rpc',json={'expected_revision':0},headers=headers).status_code,200)


if __name__=='__main__':unittest.main()
