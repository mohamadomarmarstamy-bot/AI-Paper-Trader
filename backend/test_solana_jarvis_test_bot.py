from contextlib import closing
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import database
import jarvis_test_bot as jarvis

MINT = '11111111111111111111111111111112'
PAIR = '11111111111111111111111111111113'


class Provider:
    def __init__(self, clock):
        self.clock = clock
        self.price = 1

    def profiles(self):
        return [MINT]

    def pairs(self, addresses):
        return [{
            'chainId': 'solana', 'pairAddress': PAIR, 'baseToken': {'address': MINT, 'name': 'Example'},
            'priceUsd': self.price, 'pairCreatedAt': (self.clock() - 7200) * 1000,
            'liquidity': {'usd': 150_000}, 'volume': {'m5': 20_000},
            'txns': {'m5': {'buys': 50, 'sells': 30}}, 'priceChange': {'m5': 2},
        }]


class JarvisTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database, 'DATABASE_PATH', Path(directory) / 'original.db'))
        self.now = 1795110000
        self.market = Provider(lambda: self.now)
        self.sell = 45_000_000

    def advance(self, seconds=5):
        self.now += seconds
        jarvis.tick(providers=self.market, get_quote=self.quote, clock=lambda: self.now)

    def quote(self, input_mint, output_mint, amount):
        return 1_000_000 if input_mint == jarvis.USDC else self.sell

    def state(self):
        with jarvis.connection() as conn:
            return jarvis.read(conn)[0]

    def test_step_floor_and_quote_exit_are_isolated(self):
        self.assertEqual([jarvis.exit_floor(x) for x in (0, 9.99, 10, 20, 29.99, 30)],
                         [-10, -10, 0, 10, 10, 20])
        jarvis.set_enabled(True)
        self.advance()
        state = self.state()
        self.assertIn(MINT, state['bots']['instant-step']['positions'])
        self.assertIn(MINT, state['bots']['instant-trim']['positions'])
        self.assertFalse(state['bots']['patient-step']['positions'])
        self.assertFalse(state['bots']['jarvis']['positions'])
        self.assertIsNone(state['strategy'])
        self.assertEqual(state['bots']['jarvis']['cash'], jarvis.JARVIS_BUDGET)

        jarvis.set_enabled(False)
        self.advance()
        report = jarvis.history(False)
        self.assertEqual(report['coins'][0]['mint'], MINT)
        self.assertAlmostEqual(report['coins'][0]['trades'][0]['pnl'], -5.35)
        self.assertEqual(report['coins'][0]['trades'][0]['held_seconds'], 5)
        self.assertFalse(self.state()['bots']['instant-step']['positions'])
        self.assertFalse(self.state()['bots']['instant-trim']['positions'])
        self.assertEqual(len(report['coins'][0]['trades']), 2)
        self.assertFalse(jarvis.history(True)['coins'])
        self.assertFalse(database.DATABASE_PATH.exists())

    def test_estimated_totals_keep_last_sell_quote_and_value_unquoted_holdings_at_cost(self):
        jarvis.set_enabled(True)
        self.advance()
        self.sell = 50_000_000
        self.advance()
        with jarvis.connection() as conn, conn:
            state, _ = jarvis.read(conn)
            quoted = state['bots']['instant-step']['positions'][MINT]
            self.assertEqual(quoted['mark'], 49_900_000)
            jarvis.open_trade(state, 'instant-step', {
                'mint': 'new-holding', 'symbol': 'Unquoted', 'liquidity_usd': 120_000, 'age_minutes': 300,
            }, 10, self.now)
            conn.execute('UPDATE run SET state=? WHERE id=1', (json.dumps(state),))

        def missing_quote(*_args):
            raise ValueError('No sell route')

        self.now += 16
        jarvis.tick(providers=self.market, get_quote=missing_quote, clock=lambda: self.now)
        self.now += jarvis.POLL
        jarvis.tick(providers=self.market, get_quote=missing_quote, clock=lambda: self.now)
        with patch.object(jarvis.time, 'time', return_value=self.now):
            bot = jarvis.status()['bots'][0]
        self.assertIsNone(bot['balance'])
        self.assertAlmostEqual(bot['estimated_balance'], 100.15)
        self.assertAlmostEqual(bot['estimated_pnl'], -.35)
        self.assertEqual(bot['valuation'], {'at_cost': 1, 'last_quote': 1})
        self.assertEqual([item['valuation_basis'] for item in bot['holdings']], ['last_quote', 'cost'])

    def test_quote_capacity_reserves_exits_and_deferral_does_not_spend_a_slot(self):
        with jarvis.connection() as conn:
            state = jarvis.initial()
            state.update(enabled=True, next_discovery=self.now + 60)
            for mint in ('a', 'b', 'c'):
                jarvis.open_trade(state, 'instant-step', {
                    'mint': mint, 'symbol': mint, 'liquidity_usd': 1, 'age_minutes': 1,
                }, 10, self.now - 20)
            state['watch']['candidate'] = {'mint': 'candidate', 'symbol': 'Candidate',
                                           'first': self.now, 'last': self.now, 'pullback': None,
                                           'liquidity_usd': 1, 'age_minutes': 1}
            calls = []

            def quote(input_mint, output_mint, _amount):
                calls.append((input_mint, output_mint))
                if len(calls) == 1:
                    raise jarvis.Deferred('Quote slot reserved upstream')
                return 10 if input_mint == jarvis.USDC else 50_000_000

            for offset in range(0, 8, 2):
                jarvis.step(conn, state, self.market, quote, self.now + offset)

            self.assertEqual([source for source, _ in calls], ['a', 'a', 'b', jarvis.USDC])
            self.assertIn('candidate', state['bots']['instant-step']['positions'])
            self.assertIn('candidate', state['bots']['instant-trim']['positions'])

    def test_discovery_checks_two_provider_batches_and_rotates_without_skipping(self):
        mints = [f'mint-{i}' for i in range(60)]
        batches = []

        class BatchProvider:
            def profiles(self):
                return mints

            def pairs(self, addresses):
                batches.append(addresses)
                return []

        state = jarvis.initial()
        jarvis.observe(state, BatchProvider(), self.now)
        jarvis.observe(state, BatchProvider(), self.now + 20)
        self.assertEqual([len(batch) for batch in batches], [20, 20, 20, 20])
        self.assertEqual(batches[:3], [mints[:20], mints[20:40], mints[40:]])

    def test_pullback_reuses_the_same_discovery_and_waits_for_rebound(self):
        state = jarvis.initial()
        jarvis.observe(state, self.market, self.now)
        self.market.price = .93
        jarvis.observe(state, self.market, self.now + 20)
        self.assertIsNone(state['watch'][MINT]['pullback'])
        self.market.price = .96
        jarvis.observe(state, self.market, self.now + 40)
        self.assertEqual(state['watch'][MINT]['pullback'], self.now + 40)

    def test_trim_requires_size_specific_quote_and_banks_partial_proceeds(self):
        state = jarvis.initial()
        row = {'mint': MINT, 'symbol': 'Example', 'liquidity_usd': 120_000, 'age_minutes': 300}
        bot = 'instant-trim'
        jarvis.open_trade(state, bot, row, 100, self.now)
        portfolio = state['bots'][bot]
        position = portfolio['positions'][MINT]
        completed = []
        jarvis.apply_exit(completed, portfolio, bot, position, 100, 65_000_000, self.now)
        self.assertEqual(position['pending'], 'trim')
        self.assertEqual(position['quantity'], 100)
        jarvis.apply_exit(completed, portfolio, bot, position, 50, 32_000_000, self.now)
        self.assertEqual(position['quantity'], 50)
        self.assertEqual(position['target'], 40)
        self.assertEqual(position['banked'], 31_900_000)
        self.assertFalse(completed)

    def test_trim_steps_up_to_ten_percent_and_closes_the_remainder(self):
        state = jarvis.initial()
        row = {'mint': MINT, 'symbol': 'Example', 'liquidity_usd': 120_000, 'age_minutes': 300}
        bot = 'patient-trim'
        jarvis.open_trade(state, bot, row, 100, self.now)
        portfolio = state['bots'][bot]
        position = portfolio['positions'][MINT]
        completed = []

        # A net +20% full-size quote raises the floor to +10% and queues a trim.
        jarvis.apply_exit(completed, portfolio, bot, position, 100, 60_400_000, self.now)
        self.assertEqual(position['peak'], 20)
        self.assertEqual(position['pending'], 'trim')
        jarvis.apply_exit(completed, portfolio, bot, position, 50, 30_250_000, self.now + 2)
        self.assertEqual(position['quantity'], 50)
        self.assertEqual(position['target'], 40)

        # A +12% quote stays above the ratcheted stop. A +9% quote closes it.
        jarvis.apply_exit(completed, portfolio, bot, position, 50, 28_240_000, self.now + 4)
        self.assertFalse(completed)
        self.assertEqual(position['peak'], 20)
        jarvis.apply_exit(completed, portfolio, bot, position, 50, 27_486_250, self.now + 6)
        self.assertNotIn(MINT, portfolio['positions'])
        self.assertEqual(completed[0]['reason'], 'Step profit protection')
        self.assertEqual(completed[0]['pnl'], 7_286_250)

    def test_reviews_start_at_1000_then_use_only_new_batches_of_500(self):
        self.assertEqual(jarvis.status()['review_every'], 1000)
        with jarvis.connection() as conn, conn:
            state = jarvis.initial()

            def close_batch(start, end, winner):
                for index in range(start, end):
                    bot = jarvis.FIXED[index % 4]
                    mint = f'test-{index // 4}'
                    pnl = 1_000_000 if bot == winner else -1_000_000
                    row = {'bot': bot, 'mint': mint, 'liquidity': 150_000, 'age': 200, 'pnl': pnl}
                    conn.execute(
                        'INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',
                        (bot, mint, index, index, '2026-11-19', pnl, jarvis.BUY_COST, json.dumps(row)),
                    )
                jarvis.evaluate(conn, state)

            close_batch(0, 500, 'patient-step')
            self.assertIsNone(state['strategy'])
            close_batch(500, 999, 'patient-step')
            self.assertIsNone(state['strategy'])
            close_batch(999, 1000, 'patient-step')
            self.assertEqual(state['strategy']['version'], 1000)
            self.assertEqual(state['strategy']['entry'], 'pullback')
            self.assertEqual(state['strategy']['exit'], 'step')

            close_batch(1000, 1499, 'instant-trim')
            self.assertEqual(state['strategy']['version'], 1000)
            close_batch(1499, 1500, 'instant-trim')
            self.assertEqual(state['strategy']['version'], 1500)
            self.assertEqual(state['strategy']['entry'], 'instant')
            self.assertEqual(state['strategy']['exit'], 'trim')

            close_batch(1500, 2000, None)
            self.assertEqual(state['reviewed'], 2000)
            self.assertEqual(state['strategy']['version'], 1500)
            self.assertIn('kept its current strategy', state['review']['result'])
            self.assertEqual(state['bots']['jarvis']['cash'], jarvis.JARVIS_BUDGET)
            self.assertEqual([jarvis.BOTS[bot][1] for bot in jarvis.FIXED],
                             ['instant', 'instant', 'pullback', 'pullback'])

            conn.execute('UPDATE run SET state=? WHERE id=1', (json.dumps(state),))
        self.assertEqual(jarvis.status()['review_every'], 500)

    def test_jarvis_cannot_auto_fund(self):
        state = jarvis.initial()
        state['bots']['jarvis']['cash'] = jarvis.BUY_COST - 1
        row = {'mint': MINT, 'symbol': 'Example', 'liquidity_usd': 150_000, 'age_minutes': 300}
        jarvis.open_trade(state, 'jarvis', row, 100, self.now)
        self.assertFalse(state['bots']['jarvis']['positions'])
        self.assertEqual(state['bots']['jarvis']['funding'], jarvis.JARVIS_BUDGET)


if __name__ == '__main__':
    unittest.main()
