import json
import sqlite3
import unittest
from solana_activity_details import enrich_orders


class ActivityDetailsTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        self.conn.execute('CREATE TABLE solana_real_orders(id TEXT PRIMARY KEY,created_at REAL,status TEXT,details_json TEXT)')

    def add(self, ident, side, qty, delta, at, **extra):
        row = dict(id=ident, side=side, mint='coin', wallet='owner', status='confirmed', created_at=at,
                   result=dict(state='confirmed', token_delta=qty, native_delta=delta), **extra)
        self.conn.execute('INSERT INTO solana_real_orders VALUES(?,?,?,?)', (ident, at, row['status'], json.dumps(row)))
        return row

    def test_partial_slices_match_ledger_rounding_and_find_entry_outside_latest_page(self):
        self.add('buy', 'buy', 3, -100, 1, source_wallet='Johnson')
        first = self.add('trim', 'sell', -1, 50, 2, position_id='buy')
        self.add('failed', 'sell', 0, -5, 3, position_id='buy')
        self.conn.execute("UPDATE solana_real_orders SET status='failed' WHERE id='failed'")
        last = self.add('exit', 'sell', -2, 60, 4, position_id='buy')
        rows = enrich_orders(self.conn, [last, first], {})
        self.assertEqual([r['realized_pnl_lamports'] for r in rows], [-7, 17])
        self.assertEqual([r['source_wallet'] for r in rows], ['Johnson', 'Johnson'])
        self.assertNotIn('realized_pnl_lamports', last)
        self.assertNotIn('source_wallet', last)

    def test_missing_entry_and_external_adjustment_do_not_invent_profit(self):
        row = self.add('exit', 'sell', -2, 60, 3, position_id='buy')
        self.assertNotIn('realized_pnl_lamports', enrich_orders(self.conn, [row], {})[0])
        self.add('buy', 'buy', 3, -100, 1, source_wallet='Iceman')
        state = {'balance_adjustments': [{'position_id': 'buy'}]}
        result = enrich_orders(self.conn, [row], state)[0]
        self.assertEqual(result['source_wallet'], 'Iceman')
        self.assertNotIn('realized_pnl_lamports', result)

    def test_reviewed_manual_sale_uses_reviewed_allocated_cost(self):
        self.add('buy', 'buy', 3, -100, 1, source_wallet='Numero')
        row = self.add('manual', 'sell', -2, 60, 3, position_id='buy')
        state = {'accounting_reviews': [{'position_id': 'buy', 'order_id': 'manual',
                 'unreconciled_cost_lamports': 70, 'proceeds_lamports': 60}]}
        self.assertEqual(enrich_orders(self.conn, [row], state)[0]['realized_pnl_lamports'], -10)

    def test_other_lot_same_coin_and_wrong_owner_never_supply_cost_or_attribution(self):
        self.add('buy', 'buy', 3, -100, 1, source_wallet='Johnson')
        self.add('other', 'buy', 9, -900, 1, source_wallet='Numero')
        row = self.add('exit', 'sell', -3, 105, 3, position_id='buy')
        self.assertEqual(enrich_orders(self.conn, [row], {})[0]['realized_pnl_lamports'], 5)
        row['wallet'] = 'other-owner'
        self.conn.execute('UPDATE solana_real_orders SET details_json=? WHERE id=?', (json.dumps(row), row['id']))
        result = enrich_orders(self.conn, [row], {})[0]
        self.assertNotIn('source_wallet', result)
        self.assertNotIn('realized_pnl_lamports', result)
