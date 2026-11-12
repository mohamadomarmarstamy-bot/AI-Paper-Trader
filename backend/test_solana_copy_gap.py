from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import database
from solana_copy_gap import analyze, read_context


def prediction(ident='signal', **changes):
    return {'id': ident, 'mint': 'coin', 'source_wallet': 'leader', 'source_at': 100,
            'status': 'closed', 'input_lamports': 1000, 'entry_cost_lamports': 1100,
            'quantity_raw': 100, 'entry_quote_at': 105, 'estimated_fee_lamports': 10,
            'estimated_rent_lamports': 90, 'realized_lamports': 200,
            'delay_model': {'seconds': 5, 'training_cutoff_at': 100},
            'exits': [{'quantity_raw': 100, 'reason': 'source_sell', 'quote_at': 120,
                       'net_lamports': 1300, 'estimated_fee_lamports': 10}], **changes}


def buy(ident='signal', **changes):
    return {'id': 'buy:'+ident, 'wallet': 'owner', 'source_wallet': 'leader', 'mint': 'coin',
            'side': 'buy', 'status': 'confirmed', 'source_at': 100, 'created_at': 102,
            'prepared': {'input_amount': 1000, 'minimum_output': 100, 'fee_cap': 10},
            'result': {'native_delta': -1050, 'token_delta': 105, 'fee_lamports': 5, 'block_time': 106},
            'timing_ms': {'source_to_finalized_ms': 7000}, **changes}


def sell(ident='exit', position='signal', **changes):
    return {'id': ident, 'position_id': 'buy:'+position, 'wallet': 'owner', 'source_wallet': 'leader',
            'mint': 'coin', 'side': 'sell', 'status': 'confirmed', 'reason': 'source_sell', 'created_at': 119,
            'prepared': {'input_amount': 105, 'minimum_output': 1300, 'fee_cap': 10},
            'result': {'native_delta': 1345, 'token_delta': -105, 'fee_lamports': 5, 'block_time': 121},
            'realized_pnl_lamports': 295, **changes}


def metric(result, key, *, historical=False):
    return next(r for r in result['calibration']['historical_metrics' if historical else 'metrics'] if r['key'] == key)


class GapTests(unittest.TestCase):
    def report(self, rows=(), orders=(), decisions=None):
        return analyze(rows, orders, 'owner', decisions=decisions, started_at=90)

    def test_closed_gap_counts_equal_input_full_positions_and_failed_fees_once(self):
        failed = sell('failure', status='failed', result={'fee_lamports': 7})
        result = self.report([prediction()], [buy(), sell(), failed, failed])
        self.assertEqual(result['closed_pair_count'], 1)
        self.assertEqual(result['actual_minus_simulated_lamports'], 88)
        self.assertEqual(result['known_failed_fees_lamports'], 7)
        self.assertEqual(next(r for r in result['conditions'] if r['label'] == 'Failed on-chain sell attempts')['count'], 1)

    def test_missed_estimates_include_winners_and_losers_and_never_mix_with_actual_fees(self):
        a, b = prediction('win', realized_lamports=200), prediction('loss', realized_lamports=-80)
        rejected = buy('win', status='rejected', error='Buy skipped: executable entry exceeds source cost by more than 5%', result={})
        result = self.report([a, b], [rejected], {'loss': 'Real buys paused.'})
        self.assertEqual(result['hypothetical_missed_net_lamports'], 120)
        self.assertEqual(result['hypothetical_missed_winners'], 1); self.assertEqual(result['hypothetical_missed_losers'], 1)
        self.assertEqual(result['known_failed_fees_lamports'], 0)
        self.assertEqual({r['key'] for r in result['entry_outcomes']}, {'price', 'paused'})
        self.assertIsNone(result['actual_minus_simulated_lamports'])

    def test_unknown_or_pending_entries_are_not_called_missed_profits(self):
        for orders, decisions in (([], {}), ([buy(status='unknown')], {}), ([], {'signal': 'Selected fresh admitted copy signal.'})):
            result = self.report([prediction()], orders, decisions)
            self.assertEqual(result['hypothetical_missed_count'], 0)
            self.assertIsNone(result['hypothetical_missed_net_lamports'])

    def test_unknown_failed_fee_and_manual_sales_exclude_closed_net_gap(self):
        for extra in (sell('failed', status='failed', result={}), sell(reason='manual_external')):
            orders = [buy(), sell(), extra] if extra['status'] == 'failed' else [buy(), extra]
            result = self.report([prediction()], orders)
            self.assertEqual(result['closed_pair_count'], 0)
            self.assertIsNone(result['actual_minus_simulated_lamports'])
        result = self.report([prediction()], [buy(), sell(), sell('failed', status='failed', result={})])
        self.assertEqual(result['unknown_failed_fees'], 1)

    def test_partial_positions_and_different_sizing_do_not_enter_closed_gap(self):
        for sale, entry in ((sell(result={'token_delta': -50}), buy()),
                            (sell(), buy(prepared={'input_amount': 2000}))):
            result = self.report([prediction()], [entry, sale])
            self.assertEqual(result['closed_pair_count'], 0)
            self.assertTrue(result['closed_pair_exclusions'])

    def test_same_amount_near_time_calibrates_all_in_price_tokens_fee_and_other_debit(self):
        result = self.report([prediction()], [buy()])
        self.assertEqual(result['calibration']['exact_entry_pairs'], 1)
        self.assertAlmostEqual(metric(result, 'entry_cost_error_percent')['median'], -9.090909, places=6)
        self.assertEqual(metric(result, 'entry_output_error_percent')['median'], 5)
        self.assertEqual(metric(result, 'fee_error_lamports')['median'], -5)
        self.assertEqual(metric(result, 'other_debit_error_lamports')['median'], -45)
        self.assertEqual(metric(result, 'entry_delay_error_seconds')['median'], 2)
        self.assertFalse(result['calibration']['automatic_setting_changes'])

    def test_size_time_missing_fields_and_identity_mismatches_exclude_price_samples(self):
        cases = [buy(prepared={'input_amount': 2000}),
                 buy(result={'native_delta': -1050, 'token_delta': 105, 'block_time': 111}),
                 buy(result={'native_delta': -1050}), buy(source_wallet='other'), buy(mint='other'), buy(wallet='other')]
        for entry in cases:
            result = self.report([prediction()], [entry])
            self.assertEqual(metric(result, 'entry_cost_error_percent')['count'], 0)
        result = self.report([prediction()], [buy(result={'native_delta': -1050, 'token_delta': 105, 'block_time': 110})])
        self.assertEqual(result['calibration']['exact_entry_pairs'], 1)  # Five-second boundary included.

    def test_prediction_without_past_only_training_cutoff_is_not_scored(self):
        for model in ({'seconds': 5}, {'seconds': 5, 'training_cutoff_at': 101}, {'seconds': float('nan'), 'training_cutoff_at': 100}):
            result = self.report([prediction(delay_model=model)], [buy()])
            self.assertEqual(metric(result, 'entry_delay_error_seconds')['count'], 0)

    def test_observed_finalization_time_fallback_is_labelled_and_legacy_missing_times_excluded(self):
        entry = buy(); del entry['result']['block_time']
        result = self.report([prediction()], [entry])
        self.assertEqual(result['calibration']['polling_time_matches'], 1)
        entry['timing_ms'] = {}
        self.assertEqual(self.report([prediction()], [entry])['calibration']['exact_entry_pairs'], 0)

    def test_exact_exit_quantity_reason_and_near_time_match_one_to_one(self):
        first = {'quantity_raw': 50, 'reason': 'profit_take_half', 'quote_at': 120, 'net_lamports': 700, 'estimated_fee_lamports': 10}
        row = prediction(exits=[deepcopy(first), deepcopy(first)])
        sale = sell(reason='profit_take_half', result={'token_delta': -50, 'native_delta': 735, 'fee_lamports': 5, 'block_time': 121})
        result = self.report([row], [buy(), sale])
        self.assertEqual(result['calibration']['exact_exit_pairs'], 1)
        self.assertEqual(metric(result, 'exit_net_error_percent')['median'], 5)
        self.assertEqual(metric(result, 'exit_net_error_percent')['eligible'], 2)
        for bad in (sell(reason='stop_loss', result=sale['result']), sell(result={'token_delta': -51, 'native_delta': 735, 'block_time': 121})):
            self.assertEqual(self.report([row], [buy(), bad])['calibration']['exact_exit_pairs'], 0)

    def test_historical_checks_use_stored_quote_floor_and_paid_fees_without_shadow_history(self):
        result = self.report([], [buy(), sell()])
        self.assertEqual(metric(result, 'tokens_above_minimum_percent', historical=True)['median'], 5)
        self.assertEqual(metric(result, 'unused_fee_allowance_lamports', historical=True)['median'], 5)
        self.assertEqual(metric(result, 'other_native_debit_lamports', historical=True)['median'], 45)
        self.assertEqual(result['signals'], 0)
        self.assertEqual(result['calibration']['exact_entry_pairs'], 0)

    def test_missing_values_are_not_zero_samples_and_inputs_are_not_modified(self):
        rows = [prediction()]; orders = [buy(prepared={}, result={})]
        before = deepcopy((rows, orders)); result = self.report(rows, orders)
        for row in result['calibration']['historical_metrics']:
            self.assertEqual(row['count'], 0); self.assertIsNone(row['median'])
        self.assertEqual((rows, orders), before)
        self.assertNotIn('prepared', json.dumps(result))

    def test_primary_outcome_counts_use_all_rows_beyond_display_page(self):
        rows = [prediction(str(i), status='skipped') for i in range(80)]
        result = self.report(rows, [], {str(i): 'Real buys paused.' for i in range(80)})
        self.assertEqual(result['signals'], 80)
        self.assertEqual(sum(r['count'] for r in result['entry_outcomes']), 80)
        self.assertEqual(result['hypothetical_missed_count'], 0)

    def test_opposite_errors_do_not_look_perfect_in_absolute_calibration(self):
        predictions = [prediction('a', entry_cost_lamports=2000), prediction('b', entry_cost_lamports=2000)]
        first = buy('a', result={'native_delta': -1800, 'token_delta': 100, 'block_time': 106})
        second = buy('b', result={'native_delta': -2200, 'token_delta': 100, 'block_time': 106})
        result = self.report(predictions, [first, second])
        cost = metric(result, 'entry_cost_error_percent')
        self.assertEqual(cost['median'], 0)
        self.assertEqual(cost['absolute_median'], 10)


class ContextTests(unittest.TestCase):
    def test_empty_context_is_read_only_and_owner_scoped(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        with patch.object(database, 'DATABASE_PATH', Path(temp.name)/'empty.db'), closing(database.get_connection()) as conn:
            before = conn.total_changes
            self.assertEqual(read_context(conn, 'owner')['rows'], [])
            self.assertEqual(conn.total_changes, before)
            self.assertEqual(conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0], 0)

    def test_context_reads_only_the_requested_wallet_without_changing_existing_rows(self):
        import solana_copy_shadow as shadow
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        with patch.object(database, 'DATABASE_PATH', Path(temp.name)/'scoped.db'), closing(database.get_connection()) as conn:
            conn.executescript(shadow.SCHEMA)
            for owner in ('owner', 'other'):
                conn.execute('INSERT INTO solana_copy_shadow_run(wallet,state_json) VALUES(?,?)', (owner, json.dumps({'started_at': 90})))
                shadow.write_entry(conn, owner, {**prediction(owner), 'created_at': 100})
            conn.commit(); before = conn.total_changes
            context = read_context(conn, 'owner')
            self.assertEqual([r['id'] for r in context['rows']], ['owner'])
            self.assertEqual(conn.total_changes, before)


if __name__ == '__main__': unittest.main()
