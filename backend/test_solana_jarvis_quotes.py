from contextlib import nullcontext
import copy
import unittest
from unittest.mock import Mock, patch

import requests
import jarvis_test_bot as jarvis

SOL = 'So11111111111111111111111111111111111111112'


class QuoteTests(unittest.TestCase):
    def setUp(self):
        self.data = {'inputMint': SOL, 'outputMint': jarvis.USDC, 'inAmount': '100000000',
                     'outAmount': '10000000', 'otherAmountThreshold': '9900000',
                     'swapMode': 'ExactIn', 'slippageBps': 100,
                     'routePlan': [{'swapInfo': {'label': 'Metis'}}], 'transaction': None}
        self.response = Mock(status_code=200, content=b'{}', headers={})
        self.response.json.side_effect = lambda: copy.deepcopy(self.data)
        self.get = self.enterContext(patch.object(jarvis.solana_http, 'get', return_value=self.response))
        self.enterContext(patch.object(jarvis._budget, 'permit', side_effect=lambda **kwargs: nullcontext()))

    def quote(self):
        return jarvis.quote(SOL, jarvis.USDC, 100000000)

    def test_standard_quote_is_exact_size_and_read_only(self):
        self.assertEqual(self.quote(), 9900000)
        request = self.get.call_args.kwargs
        self.assertEqual(request['params']['amount'], '100000000')
        self.assertNotIn('taker', request['params'])
        self.assertFalse(request['allow_redirects'])
        self.assertEqual(request['timeout'], 8)

    def test_zero_and_tighter_slippage_quotes_keep_the_provider_floor(self):
        for bps in (0, 25, 50, 100):
            with self.subTest(bps=bps):
                self.data.update(slippageBps=bps, otherAmountThreshold=str(10000000 * (10000 - bps) // 10000))
                self.assertEqual(self.quote(), int(self.data['otherAmountThreshold']))

    def test_invalid_quotes_never_become_fills(self):
        cases = [
            {'slippageBps': 101}, {'slippageBps': -1}, {'slippageBps': True},
            {'slippageBps': 0, 'otherAmountThreshold': '9999999'},
            {'otherAmountThreshold': '9899999'}, {'otherAmountThreshold': '10000001'},
            {'outAmount': 10.5}, {'otherAmountThreshold': True}, {'outAmount': '0'},
            {'inAmount': '99999999'}, {'inputMint': jarvis.USDC}, {'outputMint': SOL},
            {'transaction': 'unsigned-transaction'}, {'taker': SOL}, {'swapMode': 'ExactOut'},
            {'errorCode': 1}, {'error': 'quote failed'}, {'routePlan': []}, {'routePlan': 'invalid'},
        ]
        original = copy.deepcopy(self.data)
        for changes in cases:
            with self.subTest(changes=changes):
                self.data = {**original, **changes}
                with self.assertRaises(jarvis.QuoteUnavailable):
                    self.quote()

    def test_http_errors_are_classified_without_echoing_provider_content(self):
        for status, error, category in (
            (400, 'Failed to get quotes', 'no_route'),
            (400, 'COULD_NOT_FIND_ANY_ROUTE', 'no_route'),
            (400, 'invalid request secret=do-not-log', 'request'),
            (401, 'secret=do-not-log', 'access'),
            (403, 'secret=do-not-log', 'access'),
            (503, 'secret=do-not-log', 'temporary'),
        ):
            with self.subTest(status=status, category=category):
                self.response.status_code = status
                self.data = {'error': error}
                with self.assertRaises(jarvis.QuoteUnavailable) as caught:
                    self.quote()
                self.assertEqual(caught.exception.category, category)
                self.assertNotIn('do-not-log', str(caught.exception))
                self.assertEqual(self.get.call_count, 1)
                self.get.reset_mock()

    def test_rate_limit_uses_shared_cooldown_without_immediate_retry(self):
        self.response.status_code = 429
        with patch.object(jarvis._budget, 'limited') as limited:
            with self.assertRaises(jarvis.RateLimited):
                self.quote()
            limited.assert_called_once_with(self.response.headers)
        self.get.assert_called_once()

    def test_timeout_and_invalid_json_are_temporary_and_never_echo_details(self):
        self.get.side_effect = requests.Timeout('secret=do-not-log')
        with self.assertRaises(jarvis.QuoteUnavailable) as caught:
            self.quote()
        self.assertEqual(caught.exception.category, 'temporary')
        self.assertNotIn('do-not-log', str(caught.exception))
        self.get.side_effect = None
        self.response.json.side_effect = ValueError('invalid JSON')
        with self.assertRaises(jarvis.QuoteUnavailable) as caught:
            self.quote()
        self.assertEqual(caught.exception.category, 'temporary')
