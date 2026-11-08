import os
import unittest
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor

import solana_real_execution as ex


class CacheTests(unittest.TestCase):
    def setUp(self):
        for p in (patch.dict(os.environ,{'SOLANA_REAL_RPC_URL':'https://one.test/rpc'},clear=True),patch.object(ex,'_network_check',{})):
            p.start();self.addCleanup(p.stop)

    def test_mainnet_check_is_shared_but_expires_and_tracks_endpoint(self):
        p=ex.Provider();p.rpc=Mock(return_value=ex.MAINNET_GENESIS)
        with patch.object(ex.time,'monotonic',return_value=100) as clock:
            p.network();p.network();self.assertEqual(p.rpc.call_count,1)
            clock.return_value=130;p.network();self.assertEqual(p.rpc.call_count,2)
            os.environ['SOLANA_REAL_RPC_URL']='https://two.test/rpc'
            p.network();self.assertEqual(p.rpc.call_count,3)
            clock.return_value=160;p.rpc.side_effect=ex.Unavailable('offline')
            with self.assertRaises(ex.Unavailable):p.network()
            self.assertFalse(ex.connection_status()['mainnet_verified_recently'])
            p.rpc.side_effect=None;p.rpc.return_value='wrong network'
            with self.assertRaises(ex.Unavailable):p.network()
            self.assertFalse(ex.connection_status()['mainnet_verified_recently'])

    def test_parallel_network_checks_share_one_successful_read(self):
        p=ex.Provider();p.rpc=Mock(return_value=ex.MAINNET_GENESIS)
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda _:p.network(),range(12)))
        p.rpc.assert_called_once()

    def test_mint_cache_is_short_lived_endpoint_scoped_and_returns_copies(self):
        p=ex.Provider();info={'program':ex.TOKEN,'decimals':6,'account_size':165}
        p._read_mint=Mock(return_value=info)
        with patch.object(ex.time,'monotonic',return_value=100) as clock:
            a=p.mint('mint');a['decimals']=99
            self.assertEqual(p.mint('mint')['decimals'],6);p._read_mint.assert_called_once()
            os.environ['SOLANA_REAL_RPC_URL']='https://two.test/rpc'
            p.mint('mint');self.assertEqual(p._read_mint.call_count,2)
            clock.return_value=115;p._read_mint.side_effect=ex.Unavailable('mint unavailable')
            with self.assertRaises(ex.Unavailable):p.mint('mint')

    def test_mutable_token2022_configuration_and_failures_are_never_cached(self):
        p=ex.Provider();p._read_mint=Mock(return_value={'program':ex.TOKEN2022,'decimals':6,'account_size':170})
        p.mint('mint');p.mint('mint');self.assertEqual(p._read_mint.call_count,2)
        p._read_mint.side_effect=ex.Unavailable('active authority')
        for _ in range(2):
            with self.assertRaises(ex.Unavailable):p.mint('bad')
        self.assertEqual(p._read_mint.call_count,4)

    def test_mint_cache_is_bounded_and_does_not_cache_balances(self):
        p=ex.Provider();p._read_mint=Mock(return_value={'program':ex.TOKEN,'decimals':6,'account_size':165})
        for i in range(140):p.mint(str(i))
        self.assertEqual(len(p._mint_cache),128)
        p.rpc=Mock(side_effect=[{'value':100},{'value':200}])
        self.assertEqual(p.balance('owner'),100);self.assertEqual(p.balance('owner'),200)
