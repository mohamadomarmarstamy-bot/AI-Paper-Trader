import unittest
from solana_wallet_review import paper_results, real_results


class WalletReviewTests(unittest.TestCase):
    def test_partial_exits_grouped_and_voided_excluded(self):
        rows = [dict(wallet='a', lot_id='1', pnl_usd=10, partial_exit=True),
                dict(wallet='a', lot_id='1', pnl_usd=-3, partial_exit=False),
                dict(wallet='a', lot_id='2', pnl_usd=-5, partial_exit=False),
                dict(wallet='a', lot_id='3', pnl_usd=999, voided=True)]
        result = paper_results([dict(wallet='a', name='A')], rows, [dict(wallet='a', cost_usd=50, mark_usd=None)])[0]
        self.assertEqual(result['realized_pnl_usd'], 2)
        self.assertEqual(result['completed_positions'], 2)
        self.assertEqual(result['winning_positions'], 1)
        self.assertEqual(result['profit_factor'], 1.4)
        self.assertIsNone(result['open_mark_usd'])

    def test_real_failed_fees_and_unknown_allocation(self):
        rows = [dict(source_wallet='a', side='sell', status='confirmed', realized_pnl_lamports=2000000),
                dict(source_wallet='a', side='sell', status='failed', result={'fee_lamports':100000}),
                dict(source_wallet='b', side='sell', status='confirmed')]
        a, b = real_results([], rows)
        self.assertEqual(a['net_realized_sol'], .0019)
        self.assertIsNone(b['net_realized_sol'])
        self.assertEqual(b['unallocated_sell_records'], 1)
