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

NOW = 1_800_000_000
MINT = '11111111111111111111111111111112'
PAIR = '11111111111111111111111111111113'


class StrategyTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database, 'DATABASE_PATH', Path(directory) / 'paper.db'))
        self.row = {'mint': MINT, 'pair': PAIR, 'symbol': 'Example', 'price_usd': 1,
                    'first': NOW, 'last': NOW, 'pullback': None,
                    'liquidity_usd': 100000, 'age_minutes': 200, 'buys_m5': 40, 'sells_m5': 10}

    def position(self, bot):
        state = jarvis.initial()
        jarvis.open_trade(state, bot, self.row, 1000000, NOW)
        return state, state['bots'][bot], state['bots'][bot]['positions'][MINT]

    def apply(self, bot, portfolio, position, amount, when=NOW + 2, **kwargs):
        closed = []
        jarvis.apply_exit(closed, portfolio, bot, position, position['quantity'], amount, when, **kwargs)
        return closed

    def test_legacy_state_migration_preserves_money_positions_and_reviews(self):
        state = jarvis.initial()
        state['reviewed'] = 1500
        state['bots']['instant-step']['cash'] = 123
        state['bots']['instant-step']['positions'][MINT] = {'original': True}
        for bot in lab.BOTS:
            del state['bots'][bot]
        with jarvis.connection() as conn, conn:
            conn.execute('UPDATE run SET state=?', (json.dumps(state),))
            migrated, _ = jarvis.read(conn)
        self.assertEqual(migrated['reviewed'], 1500)
        self.assertEqual(migrated['bots']['instant-step'], state['bots']['instant-step'])
        self.assertEqual(len(lab.BOTS), 9)
        self.assertTrue(all(bot in jarvis.FIXED for bot in lab.BOTS))
        self.assertTrue(all(not migrated['bots'][bot]['positions'] for bot in lab.BOTS))

    def test_buy_pressure_needs_two_separate_fresh_scans(self):
        row = copy.deepcopy(self.row)
        lab.observe(row, NOW)
        self.assertIsNone(lab.entry_signal('pressure', row, {}, NOW))
        lab.observe(row, NOW + 10)
        self.assertEqual(lab.entry_signal('pressure', row, {}, NOW + 10), NOW + 10)
        row.update(buys_m5=20, sells_m5=30)
        lab.observe(row, NOW + 20)
        self.assertIsNone(lab.entry_signal('pressure', row, {}, NOW + 20))
        self.assertIsNone(lab.entry_signal('pressure', self.row, {}, NOW))

    def test_liquidity_needs_six_minutes_continuity_and_sustained_growth(self):
        row = copy.deepcopy(self.row)
        for offset in range(0, 361, 10):
            row['liquidity_usd'] = 125000 if offset >= 300 else 100000
            lab.observe(row, NOW + offset)
            signal = lab.entry_signal('liquidity', row, {}, NOW + offset)
            if offset < 360:
                self.assertIsNone(signal)
        self.assertEqual(signal, NOW + 360)
        row['liquidity_usd'] = 124000
        lab.observe(row, NOW + 370)
        self.assertIsNone(lab.entry_signal('liquidity', row, {}, NOW + 370))
        sparse = copy.deepcopy(self.row)
        lab.observe(sparse, NOW)
        sparse['liquidity_usd'] = 150000
        lab.observe(sparse, NOW + 300)
        lab.observe(sparse, NOW + 360)
        self.assertIsNone(lab.entry_signal('liquidity', sparse, {}, NOW + 360))

    def test_chart_entries_require_fresh_completed_signals(self):
        for entry, fields in (
            ('volume', {'pattern': 'volume_breakout', 'volume_ratio': 2}),
            ('vwap', {'vwap_reclaim': True}),
            ('trend', {'ema_cross_up': True, 'volume_rising': True}),
        ):
            evidence = {'chart': {'closed_at': NOW, **fields}}
            self.assertEqual(lab.entry_signal(entry, self.row, evidence, NOW), NOW)
            self.assertIsNone(lab.entry_signal(entry, self.row, evidence, NOW - 1))
            self.assertIsNone(lab.entry_signal(entry, self.row, evidence, NOW + 91))
            self.assertIsNone(lab.entry_signal(entry, self.row, {}, NOW))

    def test_completed_candle_indicators_ignore_future_spikes(self):
        bars = [[NOW - (22 - i) * 60, 100, 101, 99, 100, 100] for i in range(21)]
        bars.append([NOW - 60, 100, 111, 99, 110, 300])
        signal = research.chart_signal(bars, NOW)
        self.assertTrue(signal['ema_cross_up'])
        self.assertTrue(signal['volume_rising'])
        self.assertEqual(signal['pattern'], 'volume_breakout')
        self.assertEqual(signal, research.chart_signal(bars + [[NOW, 110, 1000, 1, 999, 99999]], NOW))
        self.assertIsNotNone(signal['vwap'])
        # Reclaim requires two closes above their separate rolling VWAPs.
        bars[-2] = [NOW - 120, 100, 111, 99, 110, 200]
        self.assertTrue(research.chart_signal(bars, NOW)['vwap_reclaim'])

    def test_tp5_and_sl10_close_the_entire_position_at_exact_token_value_boundaries(self):
        for boundary, adjacent, reason in ((52500000, 52499999, 'Take profit'), (45000000, 45000001, 'Stop loss')):
            state, portfolio, position = self.position('lab-tp5-sl10')
            self.assertFalse(self.apply('lab-tp5-sl10', portfolio, position, adjacent))
            closed = self.apply('lab-tp5-sl10', portfolio, position, boundary)
            self.assertNotIn(MINT, portfolio['positions'])
            self.assertEqual(position['quantity'], 0)
            self.assertEqual(len(closed), 1)
            self.assertIn(reason, closed[0]['reason'])
            self.assertEqual(closed[0]['pnl'], boundary - jarvis.SELL_FEE - jarvis.BUY_COST)
            self.assertEqual(closed[0]['strategy_version'], lab.VERSION)

    def test_time_exit_and_stall_exit_wait_for_their_deadlines(self):
        for bot, deadline in (('lab-five-minute', 300), ('lab-stalled', 180)):
            _, portfolio, position = self.position(bot)
            self.assertFalse(self.apply(bot, portfolio, position, 50000000, NOW + deadline - 1))
            self.assertTrue(self.apply(bot, portfolio, position, 50000000, NOW + deadline))
        _, portfolio, position = self.position('lab-stalled')
        self.assertFalse(self.apply('lab-stalled', portfolio, position, 52000000, NOW + 100))
        self.assertFalse(self.apply('lab-stalled', portfolio, position, 51000000, NOW + 200))
        self.assertTrue(self.apply('lab-stalled', portfolio, position, 51000000, NOW + 280))

    def test_breakeven_runner_covers_costs_after_arming(self):
        _, portfolio, position = self.position('lab-breakeven')
        self.assertFalse(self.apply('lab-breakeven', portfolio, position, jarvis.BUY_COST * 115 // 100 + jarvis.SELL_FEE))
        closed = self.apply('lab-breakeven', portfolio, position, jarvis.BUY_COST + jarvis.SELL_FEE)
        self.assertEqual(closed[0]['pnl'], 0)
        self.assertIn('Break-even', closed[0]['reason'])

    def test_indicator_exits_use_current_evidence_and_always_retain_stop(self):
        for bot, chart, market in (
            ('lab-vwap', {'vwap': 2}, {'price_usd': 1}),
            ('lab-trend', {'ema_fast': 1, 'ema_slow': 2}, {}),
        ):
            _, portfolio, position = self.position(bot)
            self.assertFalse(self.apply(bot, portfolio, position, 50000000))
            self.assertTrue(self.apply(bot, portfolio, position, 50000000, evidence={'chart': chart}, market=market))
            _, portfolio, position = self.position(bot)
            self.assertTrue(self.apply(bot, portfolio, position, 40000000))

    def test_jarvis_can_validate_and_adopt_a_new_lab_policy(self):
        state = jarvis.initial()
        with jarvis.connection() as conn, conn:
            for index in range(1000):
                data = {'mint': f'new-{index}', 'bot': 'lab-tp5-sl10', 'pnl': 1000000,
                        'liquidity': 200000, 'age': 200, 'opened': NOW + index}
                conn.execute('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',
                             ('lab-tp5-sl10', data['mint'], NOW + index, NOW + 2000, 'test', 1000000,
                              jarvis.BUY_COST, json.dumps(data)))
            jarvis.evaluate(conn, state)
        self.assertEqual(state['strategy']['source'], 'lab-tp5-sl10')
        self.assertEqual(state['strategy']['exit'], 'tp5')
        self.assertGreater(state['strategy']['validation_pnl'], 0)

    def test_indicator_exit_cannot_use_research_from_a_different_pool(self):
        state, portfolio, position = self.position('lab-vwap')
        row = {**self.row, 'pair': 'different-pool', 'last': NOW + 2}
        state['watch'][MINT] = row
        with jarvis.connection() as conn, conn:
            evidence = {'chart': {'at': NOW + 2, 'value': {'closed_at': NOW + 2, 'vwap': 2}}}
            conn.execute('INSERT INTO research(mint,pair,checked,evidence) VALUES (?,?,?,?)',
                         (MINT, 'different-pool', NOW + 2, json.dumps(evidence)))
            self.assertFalse(jarvis.step(conn, state, None, lambda *args: 50000000, NOW + 2))
            self.assertIn(MINT, portfolio['positions'])
            row.update(pair=PAIR, last=NOW + 18)
            conn.execute('UPDATE research SET pair=?', (PAIR,))
            self.assertTrue(jarvis.step(conn, state, None, lambda *args: 50000000, NOW + 18))
            self.assertNotIn(MINT, portfolio['positions'])

    def test_per_bot_history_stays_separate_and_rejects_unknown_bot(self):
        with jarvis.connection() as conn, conn:
            for bot in ('instant-step', 'lab-tp5-sl10'):
                _, portfolio, position = self.position(bot)
                closed = self.apply(bot, portfolio, position, 53000000 if bot.startswith('lab') else 40000000)[0]
                conn.execute('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',
                             (bot, MINT, NOW, NOW + 2, 'test', closed['pnl'], jarvis.BUY_COST, json.dumps(closed)))
        history = jarvis.history(False, bot='lab-tp5-sl10')
        self.assertEqual(len(history['coins'][0]['trades']), 1)
        self.assertEqual(history['coins'][0]['trades'][0]['bot'], lab.BOTS['lab-tp5-sl10'][0])
        with self.assertRaises(ValueError):
            jarvis.history(False, bot="' OR 1=1")
