from datetime import datetime,timezone
import unittest
from unittest.mock import Mock
import requests
import jarvis_research as research
import jarvis_usd_feed_trial as trial

NOW=1800000000
ROW={'mint':'1'*31+'2','pair':'1'*31+'3'}

class USDTrialTests(unittest.TestCase):
    def pool(self):return {'id':ROW['pair'],'chain':'solana','base_token_id':ROW['mint'],'quote_token_id':'1'*31+'4'}
    def bars(self):
        iso=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat()
        return [{'time_open':iso(NOW-(20-i)*60),'time_close':iso(NOW-(19-i)*60),
                 'open':1,'high':2,'low':.5,'close':1,'volume':100} for i in range(20)]
    def test_usd_price_and_orientation_never_enable_trading(self):
        for flipped in (False,True):
            pool=self.pool()
            if flipped:pool['base_token_id'],pool['quote_token_id']=pool['quote_token_id'],pool['base_token_id']
            fetch=Mock(side_effect=[pool,self.bars()])
            result=trial.usd_chart(ROW,NOW,'private',fetch)
            self.assertEqual(result['price_currency'],'USD')
            self.assertFalse(result['trading_enabled'])
            self.assertEqual(result['volume_equivalence'],'unverified')
            self.assertEqual(fetch.call_args.kwargs['params']['inversed'],str(flipped).lower())
            self.assertNotIn('private',str(result))
    def test_wrong_pool_or_orientation_stops_before_candle_request(self):
        for field in ('id','chain','base_token_id'):
            pool=self.pool();pool[field]='wrong'
            fetch=Mock(return_value=pool)
            with self.assertRaises(research.ChartUnavailable):trial.usd_chart(ROW,NOW,'key',fetch)
            self.assertEqual(fetch.call_count,1)
    def test_gaps_wrong_intervals_and_staleness_are_rejected(self):
        for mode in ('gap','interval','stale','duplicate'):
            bars=self.bars()
            if mode=='gap':bars.pop(5)
            if mode=='interval':bars[0]['time_close']=bars[0]['time_open']
            if mode=='duplicate':bars.append(bars[0])
            if mode=='stale':
                for b in bars:
                    for k in ('time_open','time_close'):b[k]=b[k].replace('2027','2025')
            fetch=Mock(side_effect=[self.pool(),bars])
            with self.assertRaises(research.ChartUnavailable):trial.usd_chart(ROW,NOW,'key',fetch)
    def test_missing_key_and_invalid_batch_send_nothing(self):
        fetch=Mock()
        for rows,key in [([ROW],''),([ROW,ROW],'key'),([], 'key')]:
            with self.assertRaises(ValueError):trial.benchmark(rows,key,fetch=fetch)
        fetch.assert_not_called()
    def test_entitlement_failure_does_not_retry_or_continue(self):
        fetch=Mock(side_effect=requests.HTTPError(response=Mock(status_code=403)))
        result=trial.benchmark([ROW,{**ROW,'pair':'1'*31+'5'}],'key',fetch=fetch)
        self.assertEqual(result['sampled'],1)
        self.assertEqual(fetch.call_count,1)
        self.assertFalse(result['trading_enabled'])

if __name__=='__main__':unittest.main()
