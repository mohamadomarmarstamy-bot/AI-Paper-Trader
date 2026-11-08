from copy import deepcopy
import asyncio
import unittest
from unittest.mock import patch

from solana_copy_entry import SOL, source_entry, check_quote
from solana_real_execution import Unavailable


class EntryPriceTests(unittest.TestCase):
    def reference(self):
        return {'mint': 'coin', 'quote_mint': SOL, 'quote_amount_raw': '1000000000',
                'token_amount_raw': '1000000', 'token_decimals': 6}

    def test_worst_case_slippage_bound_instead_of_expected_output(self):
        reference = self.reference()
        check_quote(reference, 'coin', 6, 105000000, 100000)
        with self.assertRaisesRegex(Unavailable, 'more than 5%'):
            check_quote(reference, 'coin', 6, 105000001, 100000)
        with self.assertRaisesRegex(Unavailable, 'more than 5%'):
            check_quote(reference, 'coin', 6, 104000000, 98000)
        huge=10**30
        reference.update(quote_amount_raw=str(huge),token_amount_raw=str(huge))
        with self.assertRaises(Unavailable):
            check_quote(reference,'coin',6,105*huge+1,100*huge)

    def test_missing_wrong_mint_wrong_currency_and_decimal_mismatch_reject(self):
        cases = [None, {}, {**self.reference(), 'mint': 'other'},
                 {**self.reference(), 'quote_mint': 'USDC'},
                 {**self.reference(), 'token_decimals': 9},
                 {**self.reference(), 'token_amount_raw': '0'}]
        for reference in cases:
            with self.subTest(reference=reference), self.assertRaises(Unavailable):
                check_quote(reference, 'coin', 6, 100000000, 100000)

    def test_source_native_and_existing_wrapped_sol_are_counted_once(self):
        def balance(mint, amount, decimals):
            return {'owner': 'owner', 'mint': mint,
                    'uiTokenAmount': {'amount': str(amount), 'decimals': decimals}}
        transaction = {'transaction': {'message': {'accountKeys': [{'pubkey': 'owner'}]}},
                       'meta': {'preBalances': [2000000000], 'postBalances': [1999900000],
                                'preTokenBalances': [balance(SOL, 1000000000, 9)],
                                'postTokenBalances': [balance('coin', 1000000, 6)]}}
        result = source_entry(transaction, 'owner', 'coin')
        self.assertEqual(result['quote_amount_raw'], '1000100000')
        self.assertEqual(result['token_amount_raw'], '1000000')
        self.assertIn('fees and rent', result['basis'])
        broken = deepcopy(transaction)
        broken['meta']['postBalances'] = [3000000000]
        self.assertIsNone(source_entry(broken, 'owner', 'coin'))

    def test_live_sources_get_every_cycle_with_background_fairness(self):
        import solana_copy_trader as paper
        state = {'traders': [{'wallet': f'live{i}', 'active': True} for i in range(4)] +
                            [{'wallet': f'paper{i}', 'active': True, 'paper_only': True} for i in range(5)],
                 'config': {'enabled': True, 'poll_seconds': 10}}
        watched = {t['wallet'] for t in state['traders']}
        polled = {w: 0 for w in watched}
        with patch('solana_real_worker.load', return_value=({'enabled': True, 'positions': {}}, 0)):
            self.assertEqual(paper.cycle_interval(state), 5)
            background = set()
            for now in range(1, 6):
                chosen = paper.select_wallets(state, watched, polled)
                self.assertEqual(len(chosen), 5)
                self.assertTrue({f'live{i}' for i in range(4)} <= set(chosen))
                background.update(w for w in chosen if w.startswith('paper'))
                for w in chosen:
                    polled[w] = now
            self.assertEqual(len(background), 5)

    def test_unrelated_cash_flow_does_not_supply_entry_reference(self):
        transaction={'transaction':{'message':{'accountKeys':[{'pubkey':'owner'}]}},
                     'meta':{'preBalances':[2000000000],'postBalances':[1000000000],
                             'preTokenBalances':[], 'postTokenBalances':[
                                 {'owner':'owner','mint':'coin','uiTokenAmount':{'amount':'100','decimals':6}},
                                 {'owner':'owner','mint':'USDC','uiTokenAmount':{'amount':'1','decimals':6}}]}}
        self.assertIsNone(source_entry(transaction, 'owner', 'coin'))
        transaction['meta']['preTokenBalances']=[deepcopy(transaction['meta']['postTokenBalances'][1])]
        self.assertIsNotNone(source_entry(transaction,'owner','coin'))

    def test_committed_signal_wakes_execution_without_waiting_for_poll_timeout(self):
        import solana_real_worker as live
        async def scenario():
            loop=asyncio.get_running_loop();first=asyncio.Event();second=asyncio.Event();calls=[]
            def tick():
                calls.append(1)
                loop.call_soon_threadsafe(first.set if len(calls)==1 else second.set)
            with patch.object(live,'tick',side_effect=tick),patch.object(live,'cycle_delay',return_value=60):
                task=asyncio.create_task(live.execution_loop())
                try:
                    await asyncio.wait_for(first.wait(),1)
                    live.notify_source_events()
                    await asyncio.wait_for(second.wait(),1)
                    self.assertEqual(len(calls),2)
                finally:
                    task.cancel()
                    try:await task
                    except asyncio.CancelledError:pass
            self.assertIsNone(live._source_wakeup)
        asyncio.run(scenario())
