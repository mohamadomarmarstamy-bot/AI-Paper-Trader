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
        self.assertFalse(self.state()['bots']['instant-step']['positions'])
        self.assertIn(MINT, self.state()['bots']['instant-trim']['positions'])
        self.assertFalse(jarvis.history(True)['coins'])
        self.assertFalse(database.DATABASE_PATH.exists())

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

    def test_review_at_500_activates_jarvis_only_on_independent_holdout(self):
        with jarvis.connection() as conn, conn:
            state = jarvis.initial()
            for index in range(500):
                bot = jarvis.FIXED[index % 4]
                mint = f'test-{index // 4}'
                pnl = 1_000_000 if bot == 'patient-step' else -1_000_000
                row = {'bot': bot, 'mint': mint, 'liquidity': 150_000, 'age': 200, 'pnl': pnl}
                if index == 499:
                    jarvis.evaluate(conn, state)
                    self.assertIsNone(state['strategy'])
                conn.execute('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',
                             (bot, mint, index, index, '2026-11-19', pnl, jarvis.BUY_COST, json.dumps(row)))
            jarvis.evaluate(conn, state)
            self.assertEqual(state['strategy']['version'], 500)
            self.assertEqual(state['strategy']['entry'], 'pullback')
            self.assertEqual(state['strategy']['exit'], 'step')
            self.assertEqual(state['bots']['jarvis']['cash'], jarvis.JARVIS_BUDGET)
            self.assertEqual([jarvis.BOTS[bot][1] for bot in jarvis.FIXED],
                             ['instant', 'instant', 'pullback', 'pullback'])

    def test_jarvis_cannot_auto_fund(self):
        state = jarvis.initial()
        state['bots']['jarvis']['cash'] = jarvis.BUY_COST - 1
        row = {'mint': MINT, 'symbol': 'Example', 'liquidity_usd': 150_000, 'age_minutes': 300}
        jarvis.open_trade(state, 'jarvis', row, 100, self.now)
        self.assertFalse(state['bots']['jarvis']['positions'])
        self.assertEqual(state['bots']['jarvis']['funding'], jarvis.JARVIS_BUDGET)


if __name__ == '__main__':
    unittest.main()
