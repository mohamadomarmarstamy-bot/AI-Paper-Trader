import unittest
from unittest.mock import Mock

import requests

from jarvis_candle_benchmark import benchmark, birdeye_chart
from jarvis_research import ChartUnavailable

NOW = 1_800_000_000
ROW = {'mint': '1' * 31 + '2', 'pair': '1' * 31 + '3'}


class CandleBenchmarkTests(unittest.TestCase):
    def test_exact_pool_and_usd_volume(self):
        items = [{'unix_time': NOW - (20 - i) * 60, 'type': '1m',
                  'o': 100, 'h': 101, 'l': 99, 'c': 100,
                  'v': 500, 'v_usd': 200 if i == 19 else 100} for i in range(20)]
        fetch = Mock(return_value={'success': True, 'data': {'items': items}})
        signal = birdeye_chart(ROW, NOW, 'test-key', fetch)
        self.assertEqual(signal['volume_ratio'], 2)
        self.assertEqual(signal['closed_at'], NOW)
        self.assertEqual(fetch.call_args.kwargs['params']['address'], ROW['pair'])
        self.assertEqual(fetch.call_args.kwargs['headers']['x-chain'], 'solana')
        items.pop(3)
        with self.assertRaises(ChartUnavailable):
            birdeye_chart(ROW, NOW, 'test-key', fetch)

    def test_same_pool_and_time_and_no_key_in_results(self):
        primary = Mock(return_value={'rsi': 50, 'volume_ratio': 2, 'closed_at': NOW})
        alt = Mock(return_value={'rsi': 55, 'volume_ratio': 1, 'closed_at': NOW})
        result = benchmark([ROW], 'test-key', clock=lambda: NOW, primary=primary, alternate=alt)
        primary.assert_called_once_with(ROW, NOW)
        alt.assert_called_once_with(ROW, NOW, 'test-key')
        self.assertEqual(result['coverage'], {'geckoterminal': {'complete': 1}, 'birdeye': {'complete': 1}})
        self.assertTrue(result['rows'][0]['same_close_minute'])
        self.assertNotIn('test-key', str(result))

    def test_invalid_rows_send_no_requests_and_rate_limit_stops_run(self):
        primary = Mock(return_value={'rsi': 50, 'volume_ratio': 2, 'closed_at': NOW})
        response = requests.Response()
        response.status_code = 429
        alt = Mock(side_effect=requests.HTTPError(response=response))
        with self.assertRaises(ValueError):
            benchmark([ROW, ROW], 'test-key', primary=primary, alternate=alt)
        primary.assert_not_called()
        report = benchmark([ROW, {**ROW, 'pair': '1' * 31 + '4'}], 'test-key',
                           clock=lambda: NOW, primary=primary, alternate=alt)
        self.assertEqual(report['sampled'], 1)
        self.assertEqual(report['coverage']['birdeye'], {'http_429': 1})
