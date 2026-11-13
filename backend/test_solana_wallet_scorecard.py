from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import database
from solana_copy_performance import report
import solana_wallet_scorecard as cards

START = 1793552400


def trade(ident, pnl, day=0, source='leader'):
    buy = {'id': ident, 'wallet': 'owner', 'source_wallet': source, 'mint': 'coin:'+ident,
           'side': 'buy', 'status': 'confirmed', 'created_at': START+day*86400,
           'prepared': {'decimals': 0, 'wire': 'PRIVATE-WIRE'},
           'result': {'token_delta': 100, 'native_delta': -1000, 'fee_lamports': 5, 'block_time': START+day*86400},
           'source_entry': {'mint': 'coin:'+ident, 'quote_mint': 'So11111111111111111111111111111111111111112',
                            'quote_amount_raw': '1000', 'token_amount_raw': '110', 'token_decimals': 0}}
    sale = {'id': 'sale:'+ident, 'position_id': ident, 'wallet': 'owner', 'source_wallet': source, 'mint': buy['mint'],
            'side': 'sell', 'status': 'confirmed', 'created_at': START+day*86400+100,
            'realized_pnl_lamports': pnl, 'result': {'token_delta': -100, 'native_delta': 1000+pnl,
                                                   'fee_lamports': 5, 'block_time': START+day*86400+100}}
    return [buy, sale]


class ScorecardTests(unittest.TestCase):
    def build(self, orders=(), paper=(), shadow=(), traders=None, positions=None, runtime_changes=None):
        traders = [{'wallet': 'leader', 'name': 'Leader', 'active': True}] if traders is None else traders
        own = [o for o in orders if o.get('wallet') == 'owner']
        ledger = sum(o['realized_pnl_lamports'] for o in own if o.get('side') == 'sell' and o.get('status') == 'confirmed' and type(o.get('realized_pnl_lamports')) is int)
        ledger -= sum((o.get('result') or {}).get('fee_lamports', 0) for o in own if o.get('status') == 'failed')
        runtime = {'wallet': 'owner', 'positions': positions or {}, 'realized_lamports': ledger, **(runtime_changes or {})}
        base = report(traders, runtime, own)
        return cards.build(traders, orders, runtime, paper, {}, shadow, START, base)

    def test_single_real_win_with_huge_paper_gain_still_has_insufficient_evidence(self):
        paper = [{'id': 'paper', 'wallet': 'leader', 'pnl_usd': 100000, 'closed_at': START}]
        row = self.build(trade('one', 20), paper)['wallets'][0]
        self.assertEqual(row['evidence'], 'Insufficient data')
        self.assertEqual(row['actual']['net_realized'], 20)
        self.assertEqual(row['paper']['net_realized'], 100000)
        self.assertFalse(row['enough_history'])

    def test_evidence_requires_both_completed_positions_and_distinct_eastern_dates(self):
        same_day = [o for i in range(20) for o in trade(str(i), 10)]
        self.assertEqual(self.build(same_day)['wallets'][0]['evidence'], 'Insufficient data')
        spread = [o for i in range(20) for o in trade(str(i), 10, i%7)]
        row = self.build(spread)['wallets'][0]
        self.assertEqual(row['evidence'], 'Observed positive net')
        self.assertEqual(row['actual']['active_close_dates'], 7)
        self.assertTrue(row['enough_history'])
        self.assertIsNone(row['actual']['profit_factor'])
        self.assertTrue(row['actual']['no_completed_losses'])

    def test_realized_drawdown_loss_streak_and_failed_buy_fees_are_counted_once(self):
        orders = [o for i, pnl in enumerate((100, -30, -40, 5, -10)) for o in trade(str(i), pnl, i)]
        orders.append({'id': 'failed-buy', 'wallet': 'owner', 'source_wallet': 'leader', 'side': 'buy',
                       'status': 'failed', 'result': {'fee_lamports': 10, 'block_time': START+5*86400}})
        row = self.build(orders)['wallets'][0]['actual']
        self.assertEqual(row['net_realized'], 15)
        self.assertEqual(row['paid_failed_fees'], 10)
        self.assertEqual(row['max_realized_drawdown'], 85)
        self.assertEqual(row['max_completed_loss_streak'], 2)
        self.assertEqual(row['winning_positions'], 2)
        self.assertAlmostEqual(row['profit_factor'], 105/80)

    def test_partial_exits_group_into_one_net_position_and_do_not_create_a_win(self):
        buy, first = trade('entry', 10)
        first['result']['token_delta'] = -50
        failed = {**first, 'id': 'failed-sell', 'status': 'failed', 'result': {'fee_lamports': 5, 'block_time': START+150}}
        actual = self.build([buy, first, failed])['wallets'][0]['actual']
        self.assertEqual(actual['completed_positions'], 0)
        self.assertIsNone(actual['win_rate_percent'])
        last = {**first, 'id': 'last', 'realized_pnl_lamports': -20,
                'result': {'token_delta': -50, 'native_delta': 480, 'block_time': START+200}}
        actual = self.build([buy, first, failed, last])['wallets'][0]['actual']
        self.assertEqual(actual['completed_positions'], 1)
        self.assertEqual(actual['winning_positions'], 0)
        self.assertEqual(actual['net_realized'], -15)
        self.assertEqual(actual['max_completed_loss_streak'], 1)

    def test_missing_fees_and_unallocated_sales_block_evidence_and_net_totals(self):
        buy, sale = trade('entry', 10)
        sale['realized_pnl_lamports'] = None
        row = self.build([buy, sale])['wallets'][0]
        self.assertEqual(row['evidence'], 'Accounting incomplete')
        self.assertIsNone(row['actual']['net_realized'])
        self.assertIsNone(row['actual']['max_realized_drawdown'])
        sale['realized_pnl_lamports'] = 10
        failed = {**sale, 'id': 'failed', 'status': 'failed', 'result': {}}
        row = self.build([buy, sale, failed])['wallets'][0]
        self.assertEqual(row['actual']['unknown_fee_records'], 1)
        self.assertEqual(row['evidence'], 'Accounting incomplete')

    def test_global_reconciliation_gap_does_not_turn_into_a_positive_evidence_label(self):
        orders = [o for i in range(20) for o in trade(str(i), 10, i%7)]
        row = self.build(orders, runtime_changes={'realized_lamports': 201})['wallets'][0]
        self.assertEqual(row['evidence'], 'Reconciliation pending')

    def test_paid_failed_buy_fees_can_make_wallet_negative_despite_all_closed_trades_winning(self):
        orders = [o for i in range(20) for o in trade(str(i), 10, i%7)]
        orders.append({'id': 'costly-failed-buy', 'wallet': 'owner', 'source_wallet': 'leader', 'side': 'buy',
                       'status': 'failed', 'result': {'fee_lamports': 300, 'block_time': START+8*86400}})
        row = self.build(orders)['wallets'][0]
        self.assertEqual(row['actual']['winning_positions'], 20)
        self.assertEqual(row['actual']['net_realized'], -100)
        self.assertEqual(row['evidence'], 'Observed negative net')

    def test_manual_intervention_is_not_labelled_as_observed_unattended_copy_performance(self):
        orders = [o for i in range(20) for o in trade(str(i), 10, i%7)]
        orders[1]['reason'] = 'manual_external'
        row = self.build(orders)['wallets'][0]
        self.assertEqual(row['evidence'], 'Manual exits included')
        self.assertEqual(row['actual']['manual_external_sales'], 1)
        self.assertEqual(row['actual']['net_realized'], 200)

    def test_unknown_settlement_time_preserves_net_but_withholds_risk_ordering(self):
        orders = trade('entry', -20)
        del orders[1]['result']['block_time']
        row = self.build(orders)['wallets'][0]['actual']
        self.assertEqual(row['net_realized'], -20)
        self.assertIsNone(row['max_realized_drawdown'])
        self.assertIsNone(row['max_completed_loss_streak'])
        self.assertEqual(row['active_close_dates'], 0)

    def test_archived_repair_is_in_net_but_does_not_get_an_invented_trade_date(self):
        buy, sale = trade('entry', 20); sale['result']['token_delta'] = -50
        repair = {'repair_id': 'reviewed', 'source_wallet': 'leader', 'position_id': 'entry', 'quantity_raw': 50, 'pnl_lamports': -40}
        summary = {'net_realized_lamports': -20, 'failed_fees_lamports': 0}
        result = cards.real_metrics('leader', [buy, sale], {}, [repair], summary)
        self.assertEqual(result['net_realized'], -20)
        self.assertEqual(result['completed_positions'], 1)
        self.assertEqual(result['winning_positions'], 0)
        self.assertIsNone(result['max_realized_drawdown'])
        self.assertIsNone(result['max_completed_loss_streak'])
        self.assertEqual(result['manual_adjustments'], 1)

    def test_paper_only_sources_are_not_promoted_by_simulated_profits(self):
        traders = [{'wallet': 'candidate', 'name': 'Candidate', 'paper_only': True}]
        paper = [{'id': 'one', 'wallet': 'candidate', 'pnl_usd': 1000000, 'closed_at': START}]
        row = self.build(paper=paper, traders=traders)['wallets'][0]
        self.assertEqual(row['evidence'], 'Paper only')
        self.assertIsNone(row['actual']['net_realized'])
        self.assertEqual(row['paper']['net_realized'], 1000000)

    def test_execution_counts_separate_failed_rejected_and_pending_attempts(self):
        buy, sale = trade('entry', 10)
        failed = {**sale, 'id': 'failed', 'status': 'failed', 'result': {'fee_lamports': 5, 'block_time': START+200}}
        rejected = {**buy, 'id': 'rejected-buy', 'status': 'rejected', 'result': {}}
        rejected_sell = {**sale, 'id': 'rejected-sell', 'status': 'rejected', 'result': {}}
        pending = {**sale, 'id': 'pending', 'status': 'unknown', 'result': {}}
        actual = self.build([buy, sale, failed, rejected, rejected_sell, pending])['wallets'][0]['actual']
        self.assertEqual(actual['failed_sells'], 1); self.assertEqual(actual['rejected_sells'], 1)
        self.assertEqual(actual['rejected_buys'], 1); self.assertEqual(actual['pending_orders'], 1)
        self.assertEqual(actual['sell_attempts'], 3); self.assertEqual(actual['buy_attempts'], 2)
        self.assertAlmostEqual(actual['entry_premium']['median'], 10)

    def test_other_execution_wallets_are_excluded_and_inputs_remain_unchanged(self):
        own = trade('own', 10); foreign = trade('foreign', 100000)
        for row in foreign: row['wallet'] = 'other-owner'
        before = deepcopy(own+foreign)
        result = self.build(own+foreign)
        self.assertEqual(result['wallets'][0]['actual']['net_realized'], 10)
        self.assertEqual(own+foreign, before)
        self.assertFalse(result['automatic_promotion'])
        self.assertNotIn('PRIVATE-WIRE', json.dumps(result))

    def test_established_evidence_precedes_one_large_win_without_a_numeric_score(self):
        orders = [o for i in range(20) for o in trade(str(i), 10, i%7, source='established')]
        orders += trade('lucky', 100000, source='new')
        traders = [{'wallet': 'established', 'name': 'Established'}, {'wallet': 'new', 'name': 'New'}]
        result = self.build(orders, traders=traders)
        self.assertEqual(result['wallets'][0]['wallet'], 'established')
        self.assertEqual(result['wallets'][1]['evidence'], 'Insufficient data')
        self.assertNotIn('score', result['wallets'][0])

    def test_original_paper_groups_partial_exits_and_ignores_voided_phantom_profits(self):
        rows = [{'id': 'half', 'lot_id': 'lot', 'wallet': 'leader', 'partial_exit': True, 'pnl_usd': 10, 'closed_at': START},
                {'id': 'rest', 'lot_id': 'lot', 'wallet': 'leader', 'partial_exit': False, 'pnl_usd': -20, 'closed_at': START+100},
                {'id': 'bad', 'wallet': 'leader', 'pnl_usd': 100000, 'closed_at': START, 'voided': True}]
        result = cards.paper_metrics('leader', rows, {})
        self.assertEqual(result['net_realized'], -10)
        self.assertEqual(result['completed_positions'], 1)
        self.assertEqual(result['winning_positions'], 0)
        self.assertEqual(result['max_realized_drawdown'], 20)

    def test_realistic_positions_are_separate_and_inconsistent_sums_are_unavailable(self):
        row = {'id': 'signal', 'source_wallet': 'leader', 'status': 'closed', 'realized_lamports': -20,
               'closed_at': START+100, 'exits': [{'pnl_lamports': 10, 'quote_at': START}, {'pnl_lamports': -30, 'quote_at': START+100}]}
        result = cards.simulated_metrics('leader', [row])
        self.assertEqual(result['net_realized'], -20); self.assertEqual(result['completed_positions'], 1)
        self.assertEqual(result['winning_positions'], 0)
        row['realized_lamports'] = 500
        result = cards.simulated_metrics('leader', [row])
        self.assertFalse(result['accounting_complete']); self.assertIsNone(result['net_realized'])

    def test_equal_time_mixed_sign_closes_have_no_invented_loss_streak(self):
        result = cards.series([('a', START, 10), ('b', START, -10)],
                              [{'id': 'a', 'at': START, 'pnl': 10}, {'id': 'b', 'at': START, 'pnl': -10}])
        self.assertEqual(result['max_realized_drawdown'], 0)
        self.assertIsNone(result['max_completed_loss_streak'])
        self.assertTrue(result['ambiguous_streak_order'])

    def test_empty_history_and_invalid_dates_do_not_become_zero_risk_claims(self):
        result = self.build()['wallets'][0]
        self.assertIsNone(result['actual']['net_realized'])
        self.assertIsNone(result['actual']['max_realized_drawdown'])
        self.assertEqual(result['evidence'], 'Insufficient data')
        risk = cards.series([('a', 1e300, -10)], [{'id': 'a', 'at': 1e300, 'pnl': -10}])
        self.assertIsNone(risk['max_realized_drawdown']); self.assertEqual(risk['active_close_dates'], 0)


class SnapshotTests(unittest.TestCase):
    def test_empty_scorecard_read_creates_no_tables_and_calls_no_network(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        with patch.object(database, 'DATABASE_PATH', Path(temp.name)/'read.db'), patch('requests.sessions.Session.request', side_effect=AssertionError('Read only')):
            from solana_copy_performance import public
            result = public()
            self.assertEqual(result['scorecard']['wallets'], [])
            with closing(database.get_connection()) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0], 0)


if __name__ == '__main__': unittest.main()
