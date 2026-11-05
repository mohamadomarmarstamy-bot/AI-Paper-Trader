import unittest
from unittest.mock import Mock, patch
import requests
import solana_token_labels as labels

MINT='BDXUaN4Bx6yaCBKAmCyGfdXd3UkRobiQwbV8mB1Kpump'
OTHER='ETnx5pPnt6tdUw1xsT4cbqC43gtYmpuCyDfgTzmGpump'


class TokenLabelsTests(unittest.TestCase):
    def setUp(self):
        labels._cache.clear();self.addCleanup(labels._cache.clear)

    def test_exact_chain_and_mint_match_with_cached_name_and_symbol(self):
        response=Mock(content=b'[]')
        response.json.return_value=[
            {'chainId':'ethereum','baseToken':{'address':MINT,'name':'Wrong chain'}},
            {'chainId':'solana','baseToken':{'address':OTHER,'name':'Wrong coin'}},
            {'chainId':'solana','baseToken':{'address':MINT,'name':'Chihuahua','symbol':'CHI'},'liquidity':{'usd':100}},
            {'chainId':'solana','baseToken':{'address':MINT,'name':'Low liquidity label'},'liquidity':{'usd':1}},
        ]
        with patch.object(labels.requests,'get',return_value=response) as get:
            first=labels.labels([MINT]);again=labels.labels([MINT])
        self.assertEqual(first,{MINT:{'name':'Chihuahua','symbol':'CHI'}})
        self.assertEqual(first,again);self.assertEqual(get.call_count,1)
        self.assertEqual(get.call_args.kwargs['timeout'],3)

    def test_provider_failure_does_not_invent_names_and_is_negative_cached(self):
        with patch.object(labels.requests,'get',side_effect=requests.Timeout()) as get:
            self.assertEqual(labels.labels([MINT]),{MINT:None})
            self.assertEqual(labels.labels([MINT]),{MINT:None})
        self.assertEqual(get.call_count,1)

    def test_batch_validation_happens_before_provider_access(self):
        with patch.object(labels.requests,'get') as get:
            for addresses in ([],[MINT]*31,['https://example.com'],['CHI']):
                with self.assertRaises(ValueError):labels.labels(addresses)
        get.assert_not_called()


if __name__=='__main__':unittest.main()
