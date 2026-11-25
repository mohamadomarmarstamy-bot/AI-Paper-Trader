import unittest
from datetime import datetime,timezone
from unittest.mock import Mock
import requests
import jarvis_alternate_feed_check as trial

NOW=1800000000
ROW={'mint':'1'*31+'2','pair':'1'*31+'3'}

class TrialTests(unittest.TestCase):
    def bars(self):
        return [{'timestamp':datetime.fromtimestamp(NOW-(20-i)*60,timezone.utc).isoformat(),
            'pair_address':ROW['pair'],'token_address':ROW['mint'],'timeframe':'1m',
            'base_mint':'So11111111111111111111111111111111111111112','open':1,'high':2,'low':.5,'close':1,
            'volume_sol':1000000000,'trade_count':1} for i in range(20)]
    def test_valid_sample_is_explicitly_not_trading_compatible(self):
        fetch=Mock(return_value={'success':True,'data':self.bars()})
        result=trial.inspect(ROW,NOW,'secret',fetch)
        self.assertEqual(result['status'],'fresh_contiguous')
        self.assertFalse(result['trading_compatible'])
        self.assertNotIn('secret',str(result))
        self.assertEqual(fetch.call_args.kwargs['params']['timeframe'],'1m')
    def test_missing_key_and_invalid_batch_do_not_call_provider(self):
        fetch=Mock()
        for rows,key in [([ROW],''),([ROW,ROW],'key'),([], 'key')]:
            with self.assertRaises(ValueError):trial.benchmark(rows,key,fetch=fetch)
        fetch.assert_not_called()
    def test_no_fabricated_gaps_wrong_identity_or_currencies(self):
        for mode,expected in [('gap','insufficient_history'),('identity','identity_mismatch'),('currency','unsupported_price_currency'),('stale','stale_candles')]:
            bars=self.bars()
            if mode=='gap':bars.pop(5)
            if mode=='identity':bars[0]['token_address']='wrong'
            if mode=='currency':bars[0]['base_mint']='wrong'
            if mode=='stale':
                for b in bars:b['timestamp']=datetime.fromisoformat(b['timestamp']).replace(year=2025).isoformat()
            result=trial.inspect(ROW,NOW,'key',lambda *_a,**_k:{'success':True,'data':bars})
            self.assertEqual(result['status'],expected)
    def test_access_failure_stops_bounded_trial(self):
        response=Mock(status_code=403)
        fetch=Mock(side_effect=requests.HTTPError(response=response))
        result=trial.benchmark([ROW,{**ROW,'pair':'1'*31+'4'}],'key',fetch=fetch)
        self.assertEqual(result['sampled'],1)
        self.assertFalse(result['trading_enabled'])

if __name__=='__main__':unittest.main()
