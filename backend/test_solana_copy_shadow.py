from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import database
import solana_copy_shadow as shadow
import solana_copy_trader as paper
import solana_real_budget as budget
import solana_real_copy as link
import solana_real_worker as live
from solana_jupiter import Deferred, Unavailable
from solana_live_hours import ALL_DAY_SOURCES


class Reader:
    def balance(self, owner): return 1_000_000_000
    def price(self): return 100
    def network(self): pass
    def age(self, mint): return {'minimum_age_seconds': 7200}
    def mint(self, mint): return {'decimals': 6}
    def rent(self, mint): return 2_000_000


class ShadowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        p = patch.object(database, 'DATABASE_PATH', Path(temp.name)/'shadow.sqlite3'); p.start(); self.addCleanup(p.stop)
        # Any accidental HTTP, signing or submission makes this test fail.
        for target in ('solana_real_execution.signer', 'solana_real_execution.Provider.prepare',
                       'solana_real_execution.Provider.send', 'requests.sessions.Session.request'):
            p = patch(target, side_effect=AssertionError('Comparison cannot execute or make unmocked network calls'))
            p.start(); self.addCleanup(p.stop)
        self.now = 1_800_000_000.; self.source_wallet = sorted(ALL_DAY_SOURCES)[0]; self.calls = []; self.sell_value = 130_000_000
        self.reader = Reader(); self.source = paper.initial()
        self.source['config'].update(enabled=True, profit_take_enabled=False)
        self.source.update(last_cycle=self.now, traders=[{'wallet': self.source_wallet, 'name': 'Followed source', 'active': True, 'selected_at': self.now-100}])
        with closing(database.get_connection()) as conn:
            paper.initialize(conn); live.initialize(conn); link.initialize(conn)
            conn.execute('UPDATE solana_real_copy_wallet SET wallet_address=?,verified_at=? WHERE id=1', ('owner', self.now))
            conn.execute('UPDATE solana_copy_state SET state_json=? WHERE id=1', (json.dumps(self.source),)); conn.commit()
        budget.configure({'buy_mode': 'fixed_usd', 'buy_usd': 10}, 0)
        self.tick()

    def tick(self, seconds=0, quote=None):
        self.now += seconds; self.source['last_cycle'] = self.now
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE solana_copy_state SET state_json=? WHERE id=1', (json.dumps(self.source),)); conn.commit()
        shadow.tick(reader=self.reader, quote=quote or self.quote, clock=lambda: self.now)

    def event(self, ident='signal', side='buy', **changes):
        row = {'id': ident, 'side': side, 'mint': 'mint', 'wallet': self.source_wallet,
               'source_at': self.now, 'status': 'paper_buy' if side == 'buy' else 'sell_queued',
               'source': 'solana_rpc_pump', 'source_entry': {'mint': 'mint', 'quote_mint': budget.SOL,
                   'quote_amount_raw': '100000000', 'token_amount_raw': '100000', 'token_decimals': 6}, **changes}
        with closing(database.get_connection()) as conn:
            conn.execute('INSERT INTO solana_copy_events VALUES(?,?,?)', (ident, self.now, json.dumps(row))); conn.commit()
        return row

    def quote(self, owner, input_mint, output_mint, amount, policy, **kwargs):
        self.calls.append((input_mint, output_mint, amount, kwargs))
        return {'otherAmountThreshold': '100000' if input_mint == budget.SOL else str(self.sell_value), '_observed_at': self.now}

    def portfolio(self):
        with closing(database.get_connection()) as conn:
            return json.loads(conn.execute('SELECT state_json FROM solana_copy_shadow_run WHERE wallet=?', ('owner',)).fetchone()[0])

    def entry(self, ident='signal'):
        with closing(database.get_connection()) as conn:
            return json.loads(conn.execute('SELECT details_json FROM solana_copy_shadow_entries WHERE source_id=?', (ident,)).fetchone()[0])

    def public(self, orders=()):
        with closing(database.get_connection()) as conn: return shadow.public(conn, orders, self.now)

    def open(self):
        self.event(); self.tick(6)
        self.assertEqual(self.entry()['status'], 'open')

    def test_prospective_bootstrap_is_idempotent_and_live_state_is_untouched(self):
        self.assertEqual(self.portfolio()['initial_lamports'], 1_000_000_000)
        before = live.load()
        self.open(); self.tick(5)
        self.assertEqual(live.load(), before)
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(c[3] == {'research': True} for c in self.calls))
        self.assertEqual(self.portfolio()['initial_lamports'], 1_000_000_000)

    def test_minimum_output_fees_and_rent_are_charged_and_sale_delay_is_observed(self):
        self.open(); row = self.entry()
        self.assertEqual(row['entry_cost_lamports'], 102_105_000)
        self.assertEqual(self.portfolio()['cash_lamports'], 897_895_000)
        self.event('sale', 'sell'); self.tick(5)
        self.assertEqual(self.entry()['status'], 'open')
        self.tick(5)
        row = self.entry(); self.assertEqual(row['status'], 'closed')
        self.assertEqual(row['realized_lamports'], 27_790_000)
        self.assertEqual(self.portfolio()['cash_lamports'], 1_027_790_000)
        self.assertEqual(self.public()['net_lamports'], row['realized_lamports'])

    def test_source_sell_with_no_quote_preserves_holding_and_does_not_invent_profit(self):
        self.open(); self.event('sale', 'sell'); self.tick(5)
        def missing(*a, **k): raise Unavailable('No route')
        self.tick(5, missing)
        self.assertEqual(self.entry()['status'], 'open')
        self.assertEqual(self.portfolio()['realized_lamports'], 0)
        self.assertEqual(self.public()['unpriced_positions'], 1)
        self.assertIsNone(self.public()['net_lamports'])
        self.assertEqual(self.portfolio()['positions']['signal']['pending_reason'], 'source_sell')
        self.tick(5); self.assertEqual(self.entry()['status'], 'closed')

    def test_deferred_quotes_expire_without_replaying_the_buy(self):
        self.event()
        def busy(*a, **k): raise Deferred('Live first')
        self.tick(6, busy)
        self.assertEqual(self.entry()['status'], 'queued')
        self.assertEqual(self.portfolio()['cash_lamports'], 1_000_000_000)
        self.tick(90)
        self.assertEqual(self.entry()['status'], 'skipped')
        self.assertFalse(self.calls)

    def test_price_cap_and_token_restrictions_apply_to_simulated_buys(self):
        self.event()
        def costly(*a, **k): return {'otherAmountThreshold': '80000', '_observed_at': self.now}
        self.tick(6, costly)
        self.assertNotIn('signal', self.portfolio()['positions'])
        self.assertIn('more than 5%', self.entry()['reason'])
        with patch.object(self.reader, 'mint', side_effect=Unavailable('Active freeze authority')):
            self.tick(5)
        self.assertIn('freeze authority', self.entry()['reason'])
        self.assertEqual(self.portfolio()['cash_lamports'], 1_000_000_000)

    def test_live_stop_is_independent_and_keeps_losing_positions_in_results(self):
        with closing(database.get_connection()) as conn:
            import solana_copy_exit_settings as stops
            conn.execute(stops.SCHEMA)
            conn.execute('INSERT INTO solana_live_stop_settings VALUES(1,1,40,?,?)', (self.now, '{}')); conn.commit()
        self.source['config']['stop_loss_percent'] = 0
        self.open(); self.assertEqual(self.entry()['settings']['protection']['stop_loss_percent'], 40)
        self.sell_value = 50_000_000; self.tick(5)
        self.assertEqual(self.entry()['status'], 'open')
        self.tick(5)
        self.assertEqual(self.entry()['status'], 'closed')
        self.assertLess(self.entry()['realized_lamports'], 0)
        self.assertEqual(self.public()['wallets'][0]['wins'], 0)

    def test_half_sale_uses_its_own_quote_and_does_not_count_as_completed_win(self):
        self.source['config']['profit_take_enabled'] = True
        self.open(); self.tick(5)
        self.assertEqual(self.portfolio()['positions']['signal']['pending_reason'], 'profit_take_half')
        self.sell_value = 65_000_000; self.tick(5)
        self.assertEqual(self.calls[-1][2], 50000)
        self.assertEqual(self.entry()['status'], 'open')
        self.assertEqual(self.public()['wallets'][0]['closed'], 0)
        self.assertEqual(self.public()['wallets'][0]['wins'], 0)
        self.assertEqual(self.entry()['exits'][0]['quantity_raw'], 50000)
        self.assertIsNone(self.public()['net_lamports'])

    def test_live_pending_order_preempts_all_comparison_quotes(self):
        self.event()
        with closing(database.get_connection()) as conn:
            order = {'id': 'live', 'wallet': 'owner'}
            conn.execute('INSERT INTO solana_real_orders VALUES(?,?,?,?)', ('live', self.now, 'unknown', json.dumps(order))); conn.commit()
        self.tick(6)
        self.assertFalse(self.calls); self.assertEqual(self.entry()['status'], 'queued')

    def test_duplicate_signal_channels_and_repeated_coin_do_not_double_buy(self):
        self.open()
        with closing(database.get_connection()) as conn:
            import solana_live_source as direct
            direct.initialize(conn)
            row = self.entry()['event']
            conn.execute('INSERT INTO solana_live_source_events VALUES(?,?,?)', (row['id'], self.now, json.dumps(row))); conn.commit()
        self.event('repeat'); self.tick(5)
        self.assertEqual(len(self.portfolio()['positions']), 1)
        self.assertEqual(self.entry('repeat')['status'], 'skipped')
        self.assertEqual(self.public()['signals'], 2)

    def test_source_sale_in_same_batch_skips_entry_before_any_quote(self):
        self.event(); self.event('sale', 'sell'); self.tick(6)
        self.assertFalse(self.calls); self.assertEqual(self.entry()['status'], 'skipped')

    def test_measured_delay_does_not_add_detection_time_twice(self):
        from solana_execution_timing import PROFILE
        with closing(database.get_connection()) as conn:
            o = {'id': 'prior', 'wallet': 'owner', 'side': 'buy', 'source_at': self.now-120, 'execution_profile': PROFILE, 'timing_ms': {'source_to_finalized_ms': 20000}}
            conn.execute('INSERT INTO solana_real_orders VALUES(?,?,?,?)', ('prior', self.now-100, 'confirmed', json.dumps(o))); conn.commit()
        self.event(); self.tick(6)
        self.assertFalse(self.calls); self.assertEqual(self.entry()['delay_model']['samples'], 1)
        self.tick(15)
        self.assertEqual(self.entry()['status'], 'open')
        self.assertEqual(self.entry()['source_to_quote_seconds'], 21)

    def test_report_matches_partial_paper_and_actual_exits_without_currency_conversion(self):
        self.open()
        with closing(database.get_connection()) as conn:
            close = {'lot_id': 'signal', 'pnl_usd': 12, 'partial_exit': True}
            conn.execute('INSERT INTO solana_copy_closes VALUES(?,?,?)', ('partial', self.now, json.dumps(close))); conn.commit()
        orders = [{'id': 'buy:signal', 'wallet': 'owner', 'side': 'buy', 'status': 'confirmed'},
                  {'id': 'sell1', 'position_id': 'buy:signal', 'wallet': 'owner', 'side': 'sell', 'status': 'confirmed', 'realized_pnl_lamports': -50},
                  {'id': 'failed1', 'position_id': 'buy:signal', 'wallet': 'owner', 'side': 'sell', 'status': 'failed', 'result': {'fee_lamports': 5}}]
        result = self.public(orders)['entries'][0]
        self.assertEqual(result['paper_realized_usd'], 12); self.assertEqual(result['paper_status'], 'Partial')
        self.assertEqual(result['actual_realized_lamports'], -55)
        orders[-1]['result'] = {}
        self.assertIsNone(self.public(orders)['entries'][0]['actual_realized_lamports'])

    def test_paused_source_and_paper_only_wallet_never_enter(self):
        self.source['config']['enabled'] = False; self.event(); self.tick(6)
        self.assertEqual(self.entry()['status'], 'skipped'); self.assertFalse(self.calls)
        self.source['config']['enabled'] = True; self.source['traders'][0]['paper_only'] = True
        self.event('paperonly'); self.tick(5)
        self.assertEqual(self.entry('paperonly')['status'], 'skipped'); self.assertFalse(self.calls)

    def test_historical_source_events_are_not_backfilled(self):
        self.event('old', source_at=self.now-10); self.tick(6)
        self.assertEqual(self.public()['signals'], 0)

    def test_simulated_holdings_keep_source_sell_monitoring_interested_while_paused(self):
        self.assertEqual(shadow.held_sources(), set())
        self.open(); self.source['config']['enabled'] = False; self.tick(5)
        self.assertEqual(shadow.held_sources(), {self.source_wallet})
        self.event('sale', 'sell'); self.tick(5); self.tick(5)
        self.assertEqual(self.entry()['status'], 'closed')
        self.assertEqual(shadow.held_sources(), set())

    def test_simulated_cash_limits_and_balance_percentage_use_the_live_sizing_formula(self):
        budget.configure({'buy_mode': 'balance_percent', 'buy_percent': 50}, 1)
        self.event()
        def proportional(owner, input_mint, output_mint, amount, policy, **kwargs):
            self.calls.append(amount)
            return {'otherAmountThreshold': str(amount//1000 if input_mint == budget.SOL else amount*1000), '_observed_at': self.now}
        self.tick(6, proportional)
        available = 1_000_000_000-10_000_000-105_000-2_000_000
        self.assertEqual(self.entry()['input_lamports'], available//2)
        self.assertGreaterEqual(self.portfolio()['cash_lamports'], 10_000_000)
        budget.configure({'buy_mode': 'fixed_usd', 'buy_usd': 10000}, 2)
        self.event('too-large', mint='othermint'); self.tick(5, proportional); self.tick(5, proportional)
        self.assertEqual(self.entry('too-large')['status'], 'skipped')
        self.assertIn('does not fit', self.entry('too-large')['reason'])

    def test_forward_model_excludes_its_own_fill_and_receipts_observed_after_signal(self):
        from solana_execution_timing import PROFILE
        with closing(database.get_connection()) as conn:
            rows = [
                {'id': 'self', 'source_at': self.now, 'timing_ms': {'source_to_finalized_ms': 1000}},
                {'id': 'late-receipt', 'source_at': self.now-20, 'timing_ms': {'source_to_finalized_ms': 30000}},
                {'id': 'prior-receipt', 'source_at': self.now-40, 'timing_ms': {'source_to_finalized_ms': 10000}},
            ]
            for entry in rows:
                entry.update(wallet='owner', side='buy', execution_profile=PROFILE)
                conn.execute('INSERT INTO solana_real_orders VALUES(?,?,?,?)', (entry['id'], self.now, 'confirmed', json.dumps(entry)))
            conn.commit()
        self.event(); self.tick(6)
        model = self.entry()['delay_model']
        self.assertEqual(model['samples'], 1); self.assertEqual(model['seconds'], 10)
        self.assertEqual(model['training_cutoff_at'], self.now-6)


if __name__ == '__main__': unittest.main()
