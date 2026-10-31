from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import database
import solana_real_budget as budget
from solana_meme_bot import Conflict


class RealBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(self.temp.name)/'budget.db');p.start();self.addCleanup(p.stop)
        budget._cache.clear();self.addCleanup(budget._cache.clear)

    def observation(self,amount=1_000_000_000,price=100,at=1000):
        return {'address':'wallet','lamports':amount,'observed_at':at,'price':{'usd':price,'observed_at':at},'slot':123,'error':None}

    def test_separate_persistent_settings_and_revision_conflicts(self):
        self.assertIsNone(budget.settings()['config']['buy_usd'])
        result=budget.configure({'buy_usd':10},0)
        self.assertEqual(result['revision'],1);self.assertEqual(result['config']['buy_usd'],10)
        self.assertFalse(result['execution_enabled']);self.assertEqual(result['budget_basis'],'available_native_sol')
        self.assertEqual(budget.settings(),result)
        with self.assertRaises(Conflict):budget.configure({'buy_usd':20},0)
        with closing(database.get_connection()) as conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='solana_copy_state'").fetchone())

    def test_validation_never_accepts_execution_or_invalid_amounts(self):
        for changes in ({'enabled':True},{'buy_usd':0},{'buy_usd':-1},{'buy_usd':True},{'buy_usd':float('nan')},{'buy_usd':float('inf')},{'buy_mode':'balance_percent','buy_percent':101},{'buy_usd':10,'fee_reserve_sol':.0000000001},{'buy_usd':10,'fee_reserve_sol':None}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):budget.configure(changes,0)
        self.assertEqual(budget.settings()['revision'],0)
        self.assertEqual(budget.configure({'buy_usd':1000000,'fee_reserve_sol':0},0)['config']['buy_usd'],1000000)

    def test_fixed_dollars_uses_real_balance_and_reserve(self):
        config={**budget.DEFAULTS,'buy_usd':10}
        result=budget.preview(self.observation(),config,1001)
        self.assertEqual(result['balance_sol'],1);self.assertEqual(result['spendable_sol'],.99)
        self.assertEqual(result['spendable_usd'],99);self.assertEqual(result['next_buy_sol'],.1)
        self.assertTrue(result['funding_fits'])
        self.assertEqual(budget.preview(self.observation(),{**config,'buy_usd':100},1001)['next_buy_sol'],None)

    def test_percentage_resizes_after_withdrawals_and_works_without_usd_price(self):
        config={**budget.DEFAULTS,'buy_mode':'balance_percent','buy_percent':10}
        self.assertEqual(budget.preview(self.observation(),config,1001)['next_buy_sol'],.099)
        observation=self.observation(amount=500_000_000);observation['price']=None
        result=budget.preview(observation,config,1001)
        self.assertEqual(result['next_buy_sol'],.049);self.assertIsNone(result['next_buy_usd'])
        result=budget.preview(self.observation(amount=1),config,1001)
        self.assertEqual(result['spendable_sol'],0);self.assertFalse(result['funding_fits'])

    def test_zero_stale_future_and_missing_balance_never_invent_funds(self):
        config={**budget.DEFAULTS,'buy_usd':10}
        for observation,now in ((self.observation(amount=0),1001),(self.observation(),1046),(self.observation(),999)):
            result=budget.preview(observation,config,now);self.assertIsNone(result['next_buy_sol']);self.assertFalse(result['funding_fits'])
        observation=self.observation();observation['price']['observed_at']=900
        result=budget.preview(observation,config,1001)
        self.assertEqual(result['balance_sol'],1);self.assertIsNone(result['spendable_usd']);self.assertFalse(result['funding_fits'])

    def test_readonly_cache_is_per_wallet_and_failure_drops_old_balance(self):
        with patch('solana_copy_rpc.request',return_value={'value':1_000_000_000,'context':{'slot':1}}) as rpc,patch.object(budget,'sol_price',return_value={'usd':100,'observed_at':1000}),patch.object(budget.time,'time',return_value=1000):
            first=budget.observe('wallet-a',1000);again=budget.observe('wallet-a',1001)
            self.assertEqual(first,again);self.assertEqual(rpc.call_count,1)
            rpc.assert_called_with('getBalance',['wallet-a',{'commitment':'confirmed'}])
            budget.observe('wallet-b',1002);self.assertEqual(rpc.call_count,2)
            rpc.side_effect=TimeoutError();failed=budget.observe('wallet-b',1033)
            self.assertIsNone(failed['lamports']);self.assertIsNotNone(failed['error'])

    def test_malformed_balance_or_price_does_not_become_available_money(self):
        with patch('solana_copy_rpc.request',return_value={'value':True,'context':{'slot':1}}):
            self.assertIsNone(budget.observe('wallet-a',1000)['lamports'])
        with patch('solana_copy_rpc.request',return_value={'value':0,'context':{'slot':1}}),patch.object(budget,'sol_price',side_effect=ValueError()):
            result=budget.observe('wallet-b',1000)
            self.assertEqual(result['lamports'],0);self.assertIsNone(result['error']);self.assertIsNotNone(result['price_error'])

    def test_sol_reference_requires_exact_mint_active_stable_pool(self):
        row={'chainId':'solana','baseToken':{'address':budget.SOL},'quoteToken':{'address':next(iter(budget.STABLES))},'priceUsd':'100','liquidity':{'usd':1000000},'volume':{'m5':100},'txns':{'m5':{'buys':1,'sells':1}},'pairAddress':'exact-pool'}
        wrong={**row,'baseToken':{'address':'wrong'},'priceUsd':'1000'}
        inactive={**row,'volume':{'m5':0},'priceUsd':'1000'}
        with patch('solana_meme_bot.ReadOnlyProviders.pairs',return_value=[wrong,inactive,row]):
            self.assertEqual(budget.sol_price()['usd'],100)
        with patch('solana_meme_bot.ReadOnlyProviders.pairs',return_value=[wrong,inactive]):
            with self.assertRaises(ValueError):budget.sol_price()


if __name__=='__main__':unittest.main()
