from collections import OrderedDict
from datetime import datetime,timezone
import unittest
from unittest.mock import Mock,patch
import execution_quotes as quotes

NOW=datetime(2026,10,27,17,tzinfo=timezone.utc).timestamp()
def stamp(age=0):return datetime.fromtimestamp(NOW-age,timezone.utc).isoformat()
def snapshot():return {'latestQuote':{'bp':3.56,'ap':3.9,'t':stamp()},'latestTrade':{'p':3.77,'t':stamp()}}

class ExecutionQuoteTests(unittest.TestCase):
    def test_ask_bid_and_last_trade_are_distinct_with_source_and_timestamps(self):
        q=quotes.parse_snapshot('BRAI',snapshot(),now=NOW)
        self.assertEqual(q['ask'],3.9);self.assertEqual(q['bid'],3.56)
        self.assertEqual(q['price'],3.77);self.assertEqual(q['price_kind'],'last_trade')
        self.assertTrue(q['quote_fresh']);self.assertTrue(q['estimate_only'])
        self.assertEqual(q['source'],'alpaca_snapshot');self.assertEqual(q['feed'],'iex')

    def test_old_future_missing_crossed_or_invalid_quotes_never_supply_a_buy_estimate(self):
        for patch_quote in [{'t':stamp(31)},{'t':stamp(-1)},{'t':'bad'},{'bp':4.0},{'ap':'NaN'},{'ap':True}]:
            s=snapshot();s['latestQuote'].update(patch_quote)
            result=quotes.parse_snapshot('BRAI',s,now=NOW)
            self.assertFalse(result['quote_fresh']);self.assertIsNone(result['ask'])

    def test_stale_trade_not_substituted_for_a_current_trade(self):
        s=snapshot();s['latestTrade']['t']=stamp(31)
        result=quotes.parse_snapshot('BRAI',s,now=NOW)
        self.assertIsNone(result['last_trade']);self.assertEqual(result['price_kind'],'quote_midpoint')
        s['latestQuote']['t']=stamp(31)
        result=quotes.parse_snapshot('BRAI',s,now=NOW)
        self.assertIsNone(result['price']);self.assertIsNone(result['ask'])

    def test_short_raw_cache_coalesces_reads_but_recomputes_freshness(self):
        request=Mock(return_value={'BRAI':snapshot()})
        with patch.object(quotes,'_caches',OrderedDict()),patch('execution_quotes.time.time',return_value=NOW) as clock:
            self.assertTrue(quotes.read_quote('BRAI',request)['quote_fresh'])
            clock.return_value=NOW+31
            self.assertFalse(quotes.read_quote('BRAI',request)['quote_fresh'])
        request.assert_called_once_with('GET','/v2/stocks/snapshots',params={'symbols':'BRAI','feed':'iex'},timeout=5.0)

    def test_unavailable_or_unentitled_feed_does_not_fall_back_to_another_source(self):
        request=Mock(side_effect=RuntimeError('403 subscription required'))
        with patch.object(quotes,'_caches',OrderedDict()),self.assertRaises(RuntimeError):quotes.read_quote('BRAI',request,feed='sip')
        self.assertEqual(request.call_count,1)
