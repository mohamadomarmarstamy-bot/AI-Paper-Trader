from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import database
import solana_copy_trader as paper
import solana_paper_trials as trials
import solana_real_worker as live


class PaperTrialsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = patch.object(database, 'DATABASE_PATH', Path(self.temp.name)/'main.sqlite3')
        p.start(); self.addCleanup(p.stop)
        with closing(database.get_connection()) as conn:
            paper.initialize(conn)
            state, _ = paper.load()
            state['config'].update(enabled=True, trade_usd=50, stop_loss_percent=20)
            state['traders'] = [{'name': 'Existing live source', 'wallet': 'existing', 'active': True}]
            conn.execute('UPDATE solana_copy_state SET state_json=? WHERE id=1', (json.dumps(state),))
            conn.commit()
        self.original = Path(database.DATABASE_PATH).read_bytes()

    def test_start_is_isolated_prospective_and_idempotent(self):
        self.assertFalse(trials.status()['started'])
        self.assertFalse(trials.exists())
        result = trials.start()
        self.assertTrue(result['started']); self.assertFalse(result['live_execution_enabled'])
        self.assertFalse(result['automatic_promotion'])
        self.assertEqual([r['name'] for r in result['traders']], ['Jijo', 'Tech', 'Kadenox'])
        self.assertFalse(result['config']['auto_discover'])
        for row in result['traders']:
            self.assertTrue(row['paper_only'])
            self.assertEqual(row['selected_at'], result['trial_started_at'])
        self.assertEqual(trials.start()['trial_started_at'], result['trial_started_at'])
        self.assertEqual(Path(database.DATABASE_PATH).read_bytes(), self.original)

    def test_trial_events_are_invisible_to_live_and_results_group_partial_sales(self):
        trials.start()
        wallet = trials.CANDIDATES[0][1]
        with database.paper_trial_context(), closing(database.get_connection()) as conn:
            for i, pnl in enumerate((12, -5)):
                row = dict(wallet=wallet, lot_id='one-lot', pnl_usd=pnl, partial_exit=i == 0)
                conn.execute('INSERT INTO solana_copy_closes VALUES(?,?,?)', (str(i), i+1, json.dumps(row)))
            conn.execute('INSERT INTO solana_copy_events VALUES(?,?,?)', ('trial-buy', 1, json.dumps(dict(id='trial-buy', status='paper_buy', side='buy', wallet=wallet))))
            conn.commit()
        self.assertEqual(live.source_events(0), [])
        row = trials.status()['wallet_results'][0]
        self.assertEqual(row['realized_pnl_usd'], 7)
        self.assertEqual(row['completed_positions'], 1)
        self.assertEqual(row['winning_completed_positions'], 1)
        self.assertEqual(row['exit_count'], 2)
        self.assertEqual(Path(database.DATABASE_PATH).read_bytes(), self.original)

    def test_tick_context_restores_after_failure_and_does_not_leak_to_other_threads(self):
        trials.start()
        def synthetic_tick(**kwargs):
            self.assertIsInstance(kwargs['provider'], trials.TrialProvider)
            self.assertEqual(paper.load()[0]['traders'][0]['name'], 'Jijo')
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(lambda: paper.load()[0]['traders'][0]['name']).result(), 'Existing live source')
            raise RuntimeError('Synthetic failure')
        with patch.object(paper, 'tick', side_effect=synthetic_tick), self.assertRaises(RuntimeError):
            trials.tick()
        self.assertEqual(paper.load()[0]['traders'][0]['name'], 'Existing live source')
        self.assertEqual(Path(database.DATABASE_PATH).read_bytes(), self.original)

    def test_real_simulator_records_only_future_trial_buys_in_trial_ledger(self):
        from test_solana_copy_trader import trade, Market, TOKEN
        trials.start()
        started = trials.status()['trial_started_at']
        wallet = trials.CANDIDATES[0][1]
        def swaps(owner, cursor, now, **kwargs):
            rows = [dict(trade(started+1, wallet=owner), source='solana_rpc_pump')] if owner == wallet else []
            return rows, {'at': now, 'rpc_coverage': {'checked_at': now}}
        original_tick = paper.tick
        def deterministic_tick(**kwargs):
            return original_tick(**kwargs, market=Market(), clock=lambda: started+2)
        with patch.object(trials.TrialProvider, 'trades', side_effect=swaps), patch.object(paper, 'tick', side_effect=deterministic_tick):
            trials.tick()
        data = trials.status()
        self.assertEqual(len(data['positions']), 1)
        self.assertEqual(data['positions'][0]['wallet'], wallet)
        self.assertEqual(data['events'][0]['status'], 'paper_buy')
        self.assertEqual(live.source_events(0), [])
        self.assertEqual(Path(database.DATABASE_PATH).read_bytes(), self.original)

    def test_authenticated_routes_and_same_origin_start(self):
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient
        from solana_meme_routes import build_router
        def session(request):
            if request.headers.get('x-test-owner') != 'yes':
                raise HTTPException(401, 'Owner required')
        app = FastAPI(); app.include_router(build_router(require_session=session))
        with TestClient(app) as client:
            self.assertEqual(client.get('/meme-coins/copy/trials/status').status_code, 401)
            headers = {'x-test-owner': 'yes', 'origin': 'http://testserver'}
            self.assertEqual(client.get('/meme-coins/copy/trials/status', headers=headers).status_code, 200)
            self.assertEqual(client.post('/meme-coins/copy/trials/start', json={}, headers={**headers, 'origin': 'http://evil.test'}).status_code, 403)
            self.assertFalse(trials.exists())
            self.assertEqual(client.post('/meme-coins/copy/trials/start', json={'wallet':'unapproved'}, headers=headers).status_code, 422)
            self.assertEqual(client.post('/meme-coins/copy/trials/start', json={}, headers=headers).status_code, 200)
        self.assertEqual(Path(database.DATABASE_PATH).read_bytes(), self.original)

    def test_discovery_cannot_expand_trial_roster(self):
        state = {'traders': []}
        trials.TrialProvider().discover(state, lambda: 1)
        self.assertEqual(state['traders'], [])


if __name__ == '__main__': unittest.main()
