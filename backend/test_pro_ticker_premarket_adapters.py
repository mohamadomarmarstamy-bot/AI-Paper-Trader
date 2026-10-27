"""Production premarket risk/quote adapters with no broker transport."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import test_buy_asset_eligibility as eligibility
import test_pro_ticker_manual_scanner_adapters as adapters


class PremarketAdapterTests(unittest.TestCase):
    def test_manual_premarket_flag_does_not_bypass_disabled_server_mode(self):
        fn,broker,account=eligibility.load_risk_validator({'status':'active','tradable':True})
        account.side_effect=None;account.return_value={'equity':'100000','buying_power':'100000'}
        fn.__globals__.update(os=os,fetch_alpaca_paper_positions=Mock(return_value=[]))
        with patch.dict(os.environ,{'PRO_TICKER_PREMARKET_ENABLED':'false'}):
            result=fn(symbol='TEST',shares=43,side='buy',manual_premarket=True)
        self.assertFalse(result['approved'])
        self.assertIn('premarket session',result['error'])
        self.assertEqual(broker.call_count,1)

    def test_data_feed_choice_is_explicit_and_no_delayed_feed_is_accepted(self):
        fn,ns=adapters.adapter('fetch_alpaca_risk_quote',get_alpaca_credentials=Mock())
        with self.assertRaises(ValueError):fn('TEST',feed='delayed_sip')
        ns['get_alpaca_credentials'].assert_not_called()

    def test_current_sip_quote_does_not_fall_back_when_provider_denies_it(self):
        response=SimpleNamespace(ok=False,json=lambda:{'message':'subscription required'})
        request=Mock(return_value=response)
        fn,ns=adapters.adapter('fetch_alpaca_risk_quote',
            get_alpaca_credentials=lambda:('synthetic-key','synthetic-secret'),
            requests=SimpleNamespace(get=request),extract_alpaca_error=lambda p:p['message'])
        with self.assertRaisesRegex(RuntimeError,'subscription required'):fn('TEST',feed='sip')
        self.assertEqual(request.call_count,1)
        self.assertEqual(request.call_args.kwargs['params'],{'feed':'sip'})
