from pathlib import Path
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

import database
import jarvis_test_bot as jarvis
import jarvis_strategies as lab
import jarvis_research as research
import solana_jupiter

NOW = 1800000000
MINT = '11111111111111111111111111111112'
PAIR = '11111111111111111111111111111113'


class MoreStrategiesTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database, 'DATABASE_PATH', Path(directory) / 'paper.db'))
        self.row = {'mint': MINT, 'pair': PAIR, 'symbol': 'Example', 'first': NOW, 'last': NOW,
                    'pullback': None, 'price_usd': 1, 'liquidity_usd': 100000,
                    'age_minutes': 200, 'buys_m5': 40, 'sells_m5': 10}

    def position(self, bot, evidence=None):
        state = jarvis.initial()
        jarvis.open_trade(state, bot, self.row, 1000000, NOW, evidence)
        return state, state['bots'][bot], state['bots'][bot]['positions'][MINT]

    def test_upgrading_existing_lab_adds_only_five_empty_portfolios(self):
        state = jarvis.initial()
        state['reviewed'] = 2000
        for bot in lab.NEW_BOTS:
            del state['bots'][bot]
        state['bots']['lab-pressure']['cash'] = 123456789
        state['bots']['lab-pressure']['funding'] = 100000000
        state['bots']['lab-pressure']['positions'][MINT] = {'kept': True}
        before = copy.deepcopy(state)
        with jarvis.connection() as conn, conn:
            conn.execute('UPDATE run SET state=?', (json.dumps(state),))
            migrated, _ = jarvis.read(conn)
        self.assertEqual(migrated['reviewed'], 2000)
        for bot, portfolio in before['bots'].items():
            self.assertEqual(migrated['bots'][bot], portfolio)
        self.assertEqual(set(migrated['bots']) - set(before['bots']), lab.NEW_BOTS)
        for bot in lab.NEW_BOTS:
            self.assertEqual(migrated['bots'][bot]['cash'], 0)
            self.assertEqual(migrated['bots'][bot]['positions'], {})

    def test_new_brackets_hold_until_exact_boundary_and_sell_fully_after_fees(self):
        for bot, target in [('lab-tp20-sl10', 20), ('lab-rsi', 15)]:
            _, portfolio, p = self.position(bot)
            closed = []
            threshold = jarvis.TRADE * (100 + target) // 100
            jarvis.apply_exit(closed, portfolio, bot, p, p['quantity'], threshold - 1, NOW + 2)
            self.assertFalse(closed)
            jarvis.apply_exit(closed, portfolio, bot, p, p['quantity'], threshold, NOW + 3)
            self.assertEqual(len(closed), 1)
            self.assertNotIn(MINT, portfolio['positions'])
            self.assertEqual(closed[0]['pnl'], threshold - jarvis.SELL_FEE - jarvis.BUY_COST)
            self.assertEqual(closed[0]['strategy_version'], 'strategy-lab-v2')
            self.assertIn(f'+{target}%', closed[0]['reason'])
            _, portfolio, p = self.position(bot)
            closed = []
            jarvis.apply_exit(closed, portfolio, bot, p, p['quantity'], 45000001, NOW + 2)
            self.assertFalse(closed)
            jarvis.apply_exit(closed, portfolio, bot, p, p['quantity'], 45000000, NOW + 3)
            self.assertEqual(closed[0]['quantity'], 0)
            self.assertEqual(lab.version('lab-tp5-sl10'), 'strategy-lab-v1')

    def test_liquidity_drain_uses_frozen_entry_liquidity_and_keeps_ladder_stop(self):
        _, portfolio, p = self.position('lab-drain')
        closed = []
        jarvis.apply_exit(closed, portfolio, 'lab-drain', p, p['quantity'], 50000000, NOW + 2,
                          market={'liquidity_usd': 85000.01})
        self.assertFalse(closed)
        jarvis.apply_exit(closed, portfolio, 'lab-drain', p, p['quantity'], 50000000, NOW + 3,
                          market={'liquidity_usd': 85000})
        self.assertEqual(closed[0]['quantity'], 0)
        self.assertIn('liquidity fell 15%', closed[0]['reason'])
        self.assertEqual(lab.floor('drain', 25, jarvis.exit_floor), jarvis.exit_floor(25))
        _, portfolio, p = self.position('lab-drain')
        closed = []
        jarvis.apply_exit(closed, portfolio, 'lab-drain', p, p['quantity'], 50000000, NOW + 2)
        self.assertFalse(closed)

    def test_squeeze_uses_completed_breakout_and_frozen_range_for_exit(self):
        chart = {'closed_at': NOW, 'squeeze_breakout': True, 'squeeze_level': 1}
        row = {**self.row, 'price_usd': 1.01}
        self.assertEqual(lab.entry_signal('squeeze', row, {'chart': chart}, NOW), NOW)
        self.assertIsNone(lab.entry_signal('squeeze', self.row, {'chart': chart}, NOW))
        self.assertIsNone(lab.entry_signal('squeeze', row, {'chart': chart}, NOW - 1))
        _, portfolio, p = self.position('lab-squeeze', {'chart': chart})
        closed = []
        jarvis.apply_exit(closed, portfolio, 'lab-squeeze', p, p['quantity'], 50000000, NOW + 2,
                          evidence={'chart': {'squeeze_level': .5}}, market={'price_usd': .99})
        self.assertIn('original', lab.DESCRIPTIONS['squeeze_exit'])
        self.assertIn('squeeze range', closed[0]['reason'])
        self.assertNotIn(MINT, portfolio['positions'])

    def test_rsi_and_squeeze_cannot_use_unfinished_or_missing_candles(self):
        closes = list(range(100, 81, -1)) + [90]
        bars = [[NOW - (20 - i) * 60, close, close + .1, close - .1, close, 100]
                for i, close in enumerate(closes)]
        chart = research.chart_signal(bars, NOW)
        self.assertTrue(chart['rsi_recovery'])
        self.assertAlmostEqual(chart['rsi'], 100 - 100 / (1 + 8 / 13))
        self.assertEqual(lab.entry_signal('rsi', self.row, {'chart': chart}, NOW), NOW)
        future = [NOW, 90, 5000, 1, 4999, 100000]
        self.assertEqual(chart, research.chart_signal(bars + [future], NOW))
        self.assertIsNone(research.chart_signal(bars[:8] + bars[9:], NOW))
        stable = [[NOW - (20 - i) * 60, 100, 101, 99, 100, 100] for i in range(19)]
        stable.append([NOW - 60, 100, 105, 100, 104, 200])
        breakout = research.chart_signal(stable, NOW)
        self.assertTrue(breakout['squeeze_breakout'])
        stable[-1][-1] = 100
        self.assertFalse(research.chart_signal(stable, NOW)['squeeze_breakout'])

    def test_relative_strength_needs_aligned_fresh_sol_data_and_buy_confirmation(self):
        row = copy.deepcopy(self.row)
        benchmark = {'samples': []}
        for offset, coin, sol in [(0, 1, 100), (300, 1.05, 101), (310, 1.06, 101)]:
            row['price_usd'] = coin
            lab.observe(row, NOW + offset)
            benchmark['samples'].append({'at': NOW + offset, 'price': sol})
        evidence = {'benchmark': benchmark}
        metrics = lab.relative_metrics(row, benchmark, NOW + 310)
        self.assertAlmostEqual(metrics['excess_points'], 5)
        self.assertEqual(lab.entry_signal('relative', row, evidence, NOW + 310), NOW + 310)
        self.assertIsNone(lab.entry_signal('relative', row, {}, NOW + 310))
        self.assertIsNone(lab.relative_metrics(row, benchmark, NOW + 401))
        self.assertIsNone(lab.relative_metrics(row, {'samples': benchmark['samples'][:1]}, NOW + 310))
        row['lab_samples'][-1]['buys'] = 10
        self.assertIsNone(lab.entry_signal('relative', row, evidence, NOW + 310))
        _, portfolio, p = self.position('lab-relative')
        closed = []
        row['lab_samples'][-1]['price'] = 1
        jarvis.apply_exit(closed, portfolio, 'lab-relative', p, p['quantity'], 50000000, NOW + 310,
                          evidence=evidence, market=row)
        self.assertIn('Relative strength', closed[0]['reason'])

    def test_sol_is_observed_as_benchmark_without_becoming_a_paper_candidate(self):
        row = self.row
        class Provider:
            def profiles(self):
                return [MINT, lab.SOL]
            def pairs(self, addresses):
                return [{'chainId': 'solana', 'pairAddress': PAIR,
                         'baseToken': {'address': mint, 'symbol': 'SOL' if mint == lab.SOL else 'COIN'},
                         'priceUsd': '100' if mint == lab.SOL else '1',
                         'pairCreatedAt': (NOW - 7200) * 1000,
                         'liquidity': {'usd': 100000}, 'volume': {'m5': 20000},
                         'txns': {'m5': {'buys': 40, 'sells': 10}}, 'priceChange': {'m5': 1}}
                        for mint in addresses]
        state = jarvis.initial()
        jarvis.observe(state, Provider(), NOW)
        self.assertIn(MINT, state['watch'])
        self.assertNotIn(lab.SOL, state['watch'])
        self.assertEqual(state['benchmark']['samples'][0]['price'], 100)
        state['benchmark']['pair'] = 'old-pool'
        jarvis.observe(state, Provider(), NOW + 10)
        self.assertEqual(len(state['benchmark']['samples']), 1)

    def test_more_buys_yield_to_near_stops_late_checks_and_backlogs(self):
        state, _, p = self.position('instant-step')
        p.update(due=NOW, mark=jarvis.BUY_COST)
        jobs = {(MINT, p['quantity']): [('instant-step', p)]}
        self.assertEqual(jarvis.entry_quote_budget(jobs, NOW), 1)
        p['mark'] = jarvis.BUY_COST * .94
        self.assertEqual(jarvis.entry_quote_budget(jobs, NOW), 2)
        p['mark'] = jarvis.BUY_COST
        self.assertEqual(jarvis.entry_quote_budget(jobs, NOW + 16), 2)
        self.assertEqual(jarvis.entry_quote_budget(jobs, NOW + 100), 6)
        p.update(mark_stale=True, quote_due_since=NOW - 1000)
        self.assertEqual(jarvis.entry_quote_budget(jobs, NOW), 1)

    def test_healthy_queue_makes_more_buys_with_same_request_budget(self):
        def run(budget_rule):
            state = jarvis.initial()
            state.update(enabled=True, next_discovery=NOW + 1000)
            for i in range(6):
                jarvis.open_trade(state, 'instant-step', {**self.row, 'mint': f'old-{i}'}, 10, NOW - 2)
            for i in range(100):
                state['watch'][f'candidate-{i}'] = {**self.row, 'mint': f'candidate-{i}'}
            clock = [NOW]
            budget = solana_jupiter.Budget()
            starts = []
            def quote(source, target, size):
                with budget.permit(research=True):
                    starts.append(clock[0])
                    return 10 if source == jarvis.USDC else 50000000
            with jarvis.connection() as conn, patch.object(jarvis, 'entry_quote_budget', budget_rule), patch.object(solana_jupiter.time, 'monotonic', side_effect=lambda: clock[0]), patch.dict('os.environ', {'JUPITER_BUILD_RPS': '1'}):
                for i in range(11):
                    clock[0] = NOW + i * jarvis.WORKER_INTERVAL
                    jarvis.step(conn, state, None, quote, clock[0])
            self.assertTrue(all(b - a >= 1.049 for a, b in zip(starts, starts[1:])))
            self.assertEqual(state['quote_activity']['deferrals'], 0)
            return state['quote_activity'], len(starts)
        baseline, old_requests = run(lambda jobs, now: 2)
        improved, new_requests = run(jarvis.entry_quote_budget)
        self.assertEqual(old_requests, new_requests)
        self.assertGreater(improved['entry_quotes'], baseline['entry_quotes'])
        self.assertGreater(improved['paper_entries'], baseline['paper_entries'])
        self.assertGreater(improved['exit_quotes'], 0)
        print(f"Healthy-queue simulation: {baseline['entry_quotes']} -> {improved['entry_quotes']} buy quotes, {new_requests} total requests each")
