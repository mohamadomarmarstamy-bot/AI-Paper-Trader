import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import database
import jarvis_autonomy as auto
import jarvis_test_bot as api

RULE = {'name': 'Deep pool', 'hypothesis': 'Test deeper pools.', 'source': 'instant-step',
        'conditions': [{'field': 'liquidity', 'op': '>=', 'value': 100000}]}


class AutonomyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database, 'DATABASE_PATH', Path(self.tmp) / 'main.db'))

    def test_does_not_execute_code_or_accept_unbounded_fields(self):
        for field in ('__import__', 'wallet', 'pnl'):
            bad = copy.deepcopy(RULE)
            bad['conditions'][0]['field'] = field
            with self.assertRaises(ValueError):
                auto.validate(bad)
        bad = copy.deepcopy(RULE)
        bad['conditions'][0]['value'] = float('nan')
        with self.assertRaises(ValueError):
            auto.validate(bad)
        self.assertFalse(auto.allows(RULE, None, 10, {}))
        self.assertTrue(auto.allows(RULE, 200000, 10, {}))

    def test_search_required_and_sources_recorded(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'test'}):
            response = Mock()
            response.json.return_value = {'status': 'completed', 'output': [
                {'type': 'web_search_call', 'status': 'completed', 'action': {'sources': [
                    {'url': 'https://example.com/research', 'title': 'Research'}]}},
                {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(RULE)}]}]}
            post = Mock(return_value=response)
            rule, sources = auto.propose([], post)
            self.assertEqual(rule, RULE)
            self.assertEqual(len(sources), 1)
            self.assertEqual(post.call_args.kwargs['json']['tools'][0]['type'], 'web_search')
            response.json.return_value['output'].pop(0)
            with self.assertRaises(ValueError):
                auto.propose([], post)

    def test_disabled_and_interval_prevent_calls(self):
        researcher = Mock(return_value=(RULE, []))
        auto.tick(api, clock=lambda: 100, researcher=researcher)
        researcher.assert_not_called()
        api.set_enabled(True)
        auto.tick(api, clock=lambda: 100, researcher=researcher)
        auto.tick(api, clock=lambda: 101, researcher=researcher)
        self.assertEqual(researcher.call_count, 1)
        with api.connection() as conn:
            self.assertEqual(auto.status(conn)['rules'][0]['status'], 'shadow')

    def test_only_future_distinct_profitable_coins_can_promote(self):
        api.set_enabled(True)
        auto.tick(api, clock=lambda: 100, researcher=lambda _: (RULE, []))
        def insert(conn, mint, opened, pnl=1000000):
            trade = {'bot': 'instant-step', 'mint': mint, 'opened': opened,
                     'liquidity': 200000, 'age': 120, 'pnl': pnl}
            conn.execute('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',
                         ('instant-step', mint, opened, 200, 'day', pnl, 50250000, json.dumps(trade)))
        with api.connection() as conn, conn:
            state, _ = api.read(conn)
            for i in range(30):
                insert(conn, 'old' + str(i), 50)
            auto.review(conn, state, 210)
            self.assertIsNone(state['strategy'])
            for i in range(100):
                insert(conn, 'new' + str(i), 150 + i)
            auto.review(conn, state, 220)
            self.assertEqual(state['strategy']['autonomous_rule'], RULE)
            self.assertEqual(state['strategy']['activated_at'], 220)
            self.assertEqual(state['strategy']['exit'], 'step')

    def test_single_lucky_winner_cannot_pass(self):
        trades = [{'bot': 'instant-step', 'mint': str(i), 'liquidity': 200000,
                   'age': 100, 'pnl': 100000000 if i == 0 else -1000000} for i in range(30)]
        self.assertFalse(auto.passed(auto.assess(RULE, trades)))

    def test_invalid_research_keeps_strategy_and_records_failure(self):
        api.set_enabled(True)
        auto.tick(api, clock=lambda: 100, researcher=lambda _: ({'code': 'bad'}, []))
        with api.connection() as conn:
            self.assertEqual(auto.status(conn)['last_error_at'], 100)
            self.assertIsNone(api.read(conn)[0]['strategy'])

    def test_loss_guard_pauses_autonomous_entries(self):
        with api.connection() as conn, conn:
            state, _ = api.read(conn)
            state['strategy'] = {'autonomous_rule': RULE, 'version': 'research-test'}
            for i in range(100):
                conn.execute('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',
                             ('jarvis', str(i), 100, 200, 'day', -1000000, 50250000,
                              json.dumps({'policy': {'version': 'research-test'}})))
            auto.guard(conn, state)
            self.assertEqual(state['entry_guard']['version'], 'research-test')
