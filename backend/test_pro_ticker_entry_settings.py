from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import database
from pro_ticker_entry_settings import read,save,Conflict
import test_pro_ticker_manual_scanner as fixtures
import test_pro_ticker_manual_scanner_adapters as adapters

class EntrySettingsTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
  p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'test.db');p.start();self.addCleanup(p.stop)
 def test_defaults_and_revisioned_save(self):
  current=read();self.assertEqual(current['settings']['dollars_per_stock'],2000)
  values={**current['settings'],'dollars_per_stock':1000,'stop_loss_percent':1,'take_profit_percent':3}
  result=save({'expected_revision':0,'settings':values});self.assertEqual(result['settings'],values);self.assertEqual(result['revision'],1)
  with self.assertRaises(Conflict):save({'expected_revision':0,'settings':values})
 def test_validation_rejects_unknown_booleans_nonfinite_and_negative(self):
  for extra in ({'dollars_per_stock':True},{'dollars_per_stock':0},{'dollars_per_stock':1000.001},{'stop_loss_percent':float('nan')},{'take_profit_percent':101},{'live_trading':True}):
   with self.assertRaises(ValueError):save({'expected_revision':0,'settings':{**read()['settings'],**extra}})
  self.assertEqual(read()['revision'],0)
 def test_default_stop_and_profit_can_inherit_current_main_settings_without_persisting(self):
  inherited=read({'dollars_per_stock':2000,'stop_loss_percent':3,'take_profit_percent':6})
  self.assertEqual(inherited['settings']['stop_loss_percent'],3);self.assertEqual(inherited['revision'],0)
 def test_custom_pro_ticker_protection_reaches_native_bracket_body(self):
  fn,ns=adapters.adapter('submit_alpaca_auto_bracket_buy')
  with patch.dict('os.environ',{'PRO_TICKER_MANUAL_SCANNER_ENABLED':'true'}),patch('pro_ticker_manual_scanner.assert_dispatch',return_value='ptm-'+'a'*32):
   fn(symbol='TEST',shares=10,reference_price=100,scanner_result={},manual_request_id='claim',manual_settings={'dollars_per_stock':1000,'stop_loss_percent':1,'take_profit_percent':3})
  body=ns['alpaca_paper_request'].call_args.kwargs['json_body'];self.assertEqual(body['stop_loss']['stop_price'],'99.00');self.assertEqual(body['take_profit']['limit_price'],'103.00')

class EntrySizingTests(unittest.TestCase):
 setUp=fixtures.ManualScannerTests.setUp
 broker=fixtures.ManualScannerTests.broker
 stamp=fixtures.ManualScannerTests.stamp
 review=fixtures.ManualScannerTests.review
 buy=fixtures.ManualScannerTests.buy
 sell=fixtures.ManualScannerTests.sell
 tick=fixtures.ManualScannerTests.tick
 submit=fixtures.ManualScannerTests.submit
 def test_configured_amount_sizes_whole_shares_and_freezes_request_settings(self):
  self.context.target_usd=1000;self.context.stop_loss_percent=1;self.context.take_profit_percent=3
  result=self.submit();self.assertEqual(result['shares'],21);self.assertEqual(result['target_usd'],'1000')
  from pro_ticker_manual_scanner import _row
  row=_row(self.request_id);self.assertEqual(float(row['stop_loss_percent']),1)
  self.context.target_usd=3000;self.context.stop_loss_percent=5
  retry=self.submit();self.assertEqual(retry['target_usd'],'1000');self.assertEqual(retry['shares'],21)
