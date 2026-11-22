import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
import jarvis_experiments as lab
import jarvis_test_bot as jarvis

NOW = 1_800_000_000
MINT = 'example-mint'


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database, 'DATABASE_PATH', Path(temp) / 'paper.db'))
        self.row = {'mint': MINT, 'pair': 'example-pool', 'first': NOW, 'last': NOW, 'pullback': None}

    def test_quotes_are_delayed_and_chart_arms_share_a_quote(self):
        evidence = {'chart': {'pattern': 'breakout_retest'}, 'bluesky': {'mint_authors': 2}}
        coin = lab.new_coin(self.row, NOW)
        self.assertFalse(lab.advance(coin, self.row, evidence, NOW, jarvis.TRADE, jarvis.BUY_COST))
        self.assertFalse(lab.advance(coin, self.row, evidence, NOW + 7, jarvis.TRADE, jarvis.BUY_COST))
        jobs = lab.advance(coin, self.row, evidence, NOW + 8, jarvis.TRADE, jarvis.BUY_COST)
        arms = jobs['buy', jarvis.TRADE]
        self.assertEqual(len(arms), 4)
        lab.quotes(arms, 'buy', 100, NOW + 8, jarvis.BUY_COST, jarvis.SELL_FEE, jarvis.exit_floor)
        self.assertEqual(coin['arms']['chart_social']['entry_wait'], 8)
        jobs = lab.advance(coin, self.row, evidence, NOW + 16, jarvis.TRADE, jarvis.BUY_COST)
        self.assertEqual(len(jobs['sell', 100]), 4)
        lab.quotes(jobs['sell', 100], 'sell', None, NOW + 16, jarvis.BUY_COST, jarvis.SELL_FEE, jarvis.exit_floor)
        lab.advance(coin, self.row, evidence, NOW + 8 + lab.HOLD_SECONDS + lab.ROUTE_GRACE, jarvis.TRADE, jarvis.BUY_COST)
        score = lab.table([coin])
        self.assertEqual(score['chart_social']['per_opportunity'], -50.25)
        self.assertEqual(score['chart_social']['unpriced'], 1)
        self.assertEqual(score['chart_social']['failed_exits'], 1)
        self.assertEqual(score['pullback']['per_opportunity'], 0)

    def test_trailing_arm_differs_only_after_arming_gain(self):
        coin = lab.new_coin(self.row, NOW)
        instant, trailing = (coin['arms'][mode] for mode in ('instant', 'instant_trailing_20'))
        arms = [instant, trailing]
        for arm in arms:
            arm['signal_at'] = NOW
        lab.quotes(arms, 'buy', 100, NOW + 8, jarvis.BUY_COST, jarvis.SELL_FEE, jarvis.exit_floor)
        lab.quotes(arms, 'sell', 65_000_000, NOW + 16, jarvis.BUY_COST, jarvis.SELL_FEE, jarvis.exit_floor)
        lab.quotes(arms, 'sell', 54_000_000, NOW + 24, jarvis.BUY_COST, jarvis.SELL_FEE, jarvis.exit_floor)
        self.assertEqual(instant['status'], 'closed')
        self.assertEqual(trailing['status'], 'open')
        lab.quotes([trailing], 'sell', 50_000_000, NOW + 32, jarvis.BUY_COST, jarvis.SELL_FEE, jarvis.exit_floor)
        self.assertEqual(trailing['status'], 'closed')

    def test_legacy_coins_are_excluded_from_new_arm_denominator(self):
        old = lab.new_coin(self.row, NOW)
        del old['arms']['instant_trailing_20']
        fresh = lab.new_coin(self.row, NOW + 60)
        self.assertEqual(lab.table([old, fresh])['instant_trailing_20']['opportunities'], 1)
        self.assertIsNone(lab.score([old, fresh], 'instant_trailing_20'))

    def test_promotion_needs_two_fresh_batches_then_rolls_back(self):
        def batch(best):
            return [{'arms': {mode: {'status': 'closed', 'pnl': 100 if mode == best else 10,
                                             'misses': 0, 'entry_misses': 0}
                              for mode in lab.MODES}} for _ in range(lab.WINDOW)]

        with jarvis.connection() as conn, conn:
            for batch_no, mode in enumerate(('chart_social', 'chart_social', 'instant')):
                for i, coin in enumerate(batch(mode)):
                    conn.execute('INSERT INTO experiment_coins(mint,opened,done,data) VALUES (?,?,?,?)',
                                 (f'{batch_no}-{i}', NOW, NOW + 1, json.dumps(coin)))
                lab.update_policy(conn)
                current = json.loads(conn.execute('SELECT policy FROM experiment_state').fetchone()[0])
                if batch_no == 0:
                    self.assertEqual(current['streak'], 1)
                    self.assertNotIn('mode', current)
                elif batch_no == 1:
                    self.assertEqual(current['mode'], 'chart_social')
                else:
                    self.assertEqual(current['mode'], 'instant')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM experiment_reviews').fetchone()[0], 3)

    def test_experiment_persists_fills_without_counting_toward_first_review(self):
        with jarvis.connection() as conn, conn:
            state, _ = jarvis.read(conn)
            state['enabled'] = True
            state['watch'][MINT] = self.row
            conn.execute('UPDATE run SET state=?', (json.dumps(state),))
        calls = []

        def quote(source, target, size):
            calls.append((source, target, size))
            return 100 if source == jarvis.USDC else 40_000_000

        for offset in (0, 8, 16):
            jarvis.experiment_tick(get_quote=quote, clock=lambda: NOW + offset)
        with jarvis.connection() as conn:
            data = json.loads(conn.execute('SELECT data FROM experiment_coins').fetchone()[0])
            self.assertEqual(data['arms']['instant']['status'], 'closed')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0], 0)
        self.assertEqual(calls, [(jarvis.USDC, MINT, jarvis.TRADE), (MINT, jarvis.USDC, 100)])

    def test_guard_pauses_jarvis_after_matched_losses_and_keeps_exit_eligible(self):
        state = jarvis.initial()
        state['strategy'] = {'source': 'instant-step', 'version': 1000, 'activated_at': NOW - 10,
                             'entry': 'instant', 'exit': 'step', 'min_liquidity': 0, 'min_age': 0}
        row = {**self.row, 'liquidity_usd': 10000, 'age_minutes': 30, 'symbol': 'Example'}
        state['watch'][MINT] = row
        with jarvis.connection() as conn, conn:
            for i in range(25):
                for bot, pnl in (('jarvis', -300_000), ('instant-step', 0)):
                    data = json.dumps({'policy': {'version': 1000}})
                    conn.execute('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',
                                 (bot, f'matched-{i}', NOW, NOW + 1, 'test-day', pnl, jarvis.BUY_COST, data))
            jarvis.guard_entries(conn, state)
            self.assertIn('entry_guard', state)
            self.assertEqual(jarvis.entry_jobs(conn, state, NOW)[MINT], ['instant-step', 'instant-trim', 'lab-five-minute', 'lab-breakeven', 'lab-tp5-sl10'])
        jarvis.open_trade(state, 'jarvis', row, 100, NOW)
        self.assertIn('jarvis', [bot for bot, _ in jarvis.quote_jobs(state, NOW + 3)[MINT, 100]])
