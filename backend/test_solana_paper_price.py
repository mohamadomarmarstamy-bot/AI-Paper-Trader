from copy import deepcopy
from contextlib import closing
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import database
import solana_copy_trader as paper
import solana_paper_price as guard
import solana_paper_price_review as review
import solana_trailing_comparison as comparison


class PriceTests(unittest.TestCase):
    def row(self):
        return {'chainId': 'solana', 'dexId': 'pumpswap',
                'pairAddress': 'BZvN52YuR6sxptrfPQR6zdxXsRKnkKz4M9wU7TB7KoiA',
                'baseToken': {'address': review.MINT, 'symbol': 'FEAR'},
                'quoteToken': {'address': guard.SOL}, 'priceUsd': '.000037',
                'priceNative': str(74 / 222000000),
                'liquidity': {'base': 222000000, 'quote': 74, 'usd': 13500}}

    def quotes(self, row):
        class Market:
            def pairs(self, addresses): return [row]
        return paper.prices([review.MINT], Market(), lambda: 100)

    def test_actual_dust_rounding_spike_cannot_mark_trim_or_fill(self):
        row = self.row(); row.update(priceUsd='.01082', priceNative='.0001176')
        self.assertEqual(self.quotes(row), {})
        p = {'id': review.LOT, 'mint': review.MINT, 'pool': row['pairAddress'],
             'quantity': 1230854.0650186352, 'cost_usd': 50.1}
        state = {'config': deepcopy(paper.DEFAULTS), 'positions': {review.LOT: p},
                 'cash_usd': 99949.9, 'stop_loss_cooldowns': {}}
        closes = []
        paper.mark_and_protect(state, self.quotes(row), 100, closes, allow_profit_takes=True)
        self.assertFalse(closes); self.assertIsNone(p['mark_usd'])
        self.assertNotIn('profit_take_stages', p)
        p.update(pending_sell='source')
        paper.mark_and_protect(state, self.quotes(row), 100, closes, allow_profit_takes=True)
        self.assertFalse(closes); self.assertEqual(state['cash_usd'], 99949.9)
        paper.mark_and_protect(state, self.quotes(self.row()), 100, closes, allow_profit_takes=True)
        self.assertEqual(len(closes), 1); self.assertLess(closes[0]['proceeds_usd'], 50)

    def test_real_price_move_supported_by_reserves_has_no_gain_cap(self):
        row = self.row()
        row.update(priceUsd='.0111', priceNative='.0001')
        row['liquidity'].update(base=1000000, quote=100)
        self.assertEqual(len(self.quotes(row)), 1)

    def test_missing_zero_nonfinite_or_inconsistent_pump_evidence_rejected(self):
        for key, value in [('priceNative', None), ('priceNative', 'nan'),
                           ('priceNative', '-1'), ('priceNative', True), ('liquidity', None),
                           ('liquidity', {'base': 0, 'quote': 10}),
                           ('liquidity', {'base': 10, 'quote': float('inf')})]:
            with self.subTest(key=key, value=value):
                row = self.row(); row[key] = value
                self.assertEqual(self.quotes(row), {})
        row = self.row(); row['priceNative'] = str(74 / 222000000 / 100)
        self.assertEqual(self.quotes(row), {})

    def test_other_pool_types_are_not_given_a_constant_product_model(self):
        row = self.row(); row.update(dexId='raydium', liquidity=None)
        self.assertEqual(len(self.quotes(row)), 1)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        p = patch.object(database, 'DATABASE_PATH', Path(self.temp.name) / 'paper.sqlite3')
        p.start(); self.addCleanup(p.stop)
        self.rows = []
        for i, (ident, (pnl, partial)) in enumerate(review.EXPECTED.items()):
            self.rows.append({'id': ident, 'lot_id': review.LOT, 'mint': review.MINT,
                              'wallet': review.WALLET, 'quantity': 615427.0325093176,
                              'cost_usd': 25.05, 'closed_at': 1793656730.68093 + i * 30,
                              'opened_at': 1793656660.7932367,
                              'pnl_usd': pnl, 'proceeds_usd': 25.05 + pnl,
                              'partial_exit': partial})
        self.delta = sum(r['pnl_usd'] for r in self.rows)
        state = paper.initial(); state['cash_usd'] = 100000 + self.delta + 12
        state['history'] = [{'at': 1793656000}, {'at': 1793656740}, {'at': 1793658000}]
        self.experiment = comparison.initial(100, paper.DEFAULTS)
        for arm in self.experiment['arms'].values():
            arm['closed'] = deepcopy(self.rows); arm['cash_usd'] = 100000 + self.delta
            arm['max_drawdown_usd'] = 6600
        with closing(database.get_connection()) as conn:
            paper.initialize(conn)
            conn.execute('UPDATE solana_copy_state SET state_json=? WHERE id=1', (json.dumps(state),))
            for row in self.rows:
                conn.execute('INSERT INTO solana_copy_closes VALUES(?,?,?)', (row['id'], row['closed_at'], json.dumps(row)))
            conn.execute('CREATE TABLE solana_trailing_comparison(id INTEGER PRIMARY KEY,state_json TEXT NOT NULL)')
            conn.execute('INSERT INTO solana_trailing_comparison VALUES(1,?)', (json.dumps(self.experiment),))
            conn.commit()

    def test_atomic_idempotent_void_retains_originals_and_excludes_from_metrics(self):
        with closing(database.get_connection()) as conn:
            self.assertTrue(review.repair(conn, 1793660000))
            self.assertFalse(review.repair(conn, 1793660010))
        state, rev = paper.load()
        self.assertAlmostEqual(state['cash_usd'], 100012)
        self.assertEqual(rev, 1); self.assertEqual(len(state['history']), 1)
        self.assertEqual(state['paper_price_reviews'][0]['original_exits'], self.rows)
        with closing(database.get_connection()) as conn:
            rows = [json.loads(r[0]) for r in conn.execute('SELECT details_json FROM solana_copy_closes')]
            self.assertEqual(len(rows), 2); self.assertTrue(all(r['voided'] for r in rows))
            self.assertEqual([r['pnl_usd'] for r in rows], [r['pnl_usd'] for r in self.rows])
            exp = comparison.read(conn)
        for arm in exp['arms'].values():
            self.assertAlmostEqual(arm['cash_usd'], 100000)
            result = comparison.arm_summary(arm, 1793660000)
            self.assertEqual(result['realized_pnl_usd'], 0)
            self.assertEqual(result['completed_positions'], 0)
            self.assertEqual(result['recent_exits'], [])
            self.assertIsNone(result['max_observed_drawdown_usd'])
        with patch.object(paper, 'regular_metrics', return_value={'equity_usd': 100000, 'net_usd': 0}):
            data = paper.public()
        self.assertEqual(data['closed'], []); self.assertEqual(data['closed_count'], 0)
        self.assertEqual(data['wins'], 0); self.assertEqual(data['realized_pnl_usd'], 0)
        self.assertEqual(data['completed_lot_count'], 0)

    def test_active_worker_defers_review_and_never_changes_settings(self):
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE solana_copy_state SET lease_until=1793660100'); conn.commit()
            self.assertFalse(review.repair(conn, 1793660000))
            self.assertTrue(review.repair(conn, 1793660200))
        state, _ = paper.load(); self.assertEqual(state['config'], paper.DEFAULTS)

    def test_unexpected_receipt_is_never_overwritten(self):
        with closing(database.get_connection()) as conn:
            conn.execute("UPDATE solana_copy_closes SET details_json=json_set(details_json,'$.pnl_usd',123) WHERE id=?", (self.rows[0]['id'],)); conn.commit()
            self.assertFalse(review.repair(conn, 1793660000))
            self.assertAlmostEqual(json.loads(conn.execute('SELECT state_json FROM solana_copy_state').fetchone()[0])['cash_usd'], 100012+self.delta)

    def test_comparison_mismatch_rolls_back_entire_repair(self):
        self.experiment['arms']['baseline']['closed'].pop()
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE solana_trailing_comparison SET state_json=?', (json.dumps(self.experiment),)); conn.commit()
            with self.assertRaises(ValueError): review.repair(conn, 1793660000)
            self.assertFalse(any(json.loads(r[0]).get('voided') for r in conn.execute('SELECT details_json FROM solana_copy_closes')))
