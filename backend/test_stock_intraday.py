from datetime import datetime, timedelta
from copy import deepcopy
from unittest import TestCase, main
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
import stock_intraday as intraday
import stock_momentum as momentum
import app_controls

ET=ZoneInfo('America/New_York')

class IntradayTests(TestCase):
    def setUp(self):
        self.now=datetime(2026,10,29,10,0,tzinfo=ET)
        self.bars=[]
        dates=[];day=self.now.date()
        while len(dates)<6:
            if day.weekday()<5:dates.append(day)
            day-=timedelta(days=1)
        for day in reversed(dates):
            for i in range(6):
                at=datetime(day.year,day.month,day.day,9,30,tzinfo=ET)+timedelta(minutes=5*i)
                close=10.2 if i==5 and day==self.now.date() else 10
                self.bars.append({'t':at.isoformat(),'o':10,'c':close,'h':close+.01,'l':9.99,'v':200 if day==self.now.date() else 100,'vw':10})

    def test_same_time_comparison_ignores_incomplete_and_future_volume_and_detects_breakout(self):
        extra={'t':self.now.isoformat(),'o':10,'c':50,'h':50,'l':10,'v':1000000,'vw':20}
        result=intraday.calculate(self.bars+[extra],now=self.now)
        self.assertTrue(result['available']);self.assertEqual(result['time_volume_ratio'],2)
        self.assertEqual(result['comparison_sessions'],5);self.assertEqual(result['vwap'],10)
        self.assertEqual(result['setup'],'breakout')

    def test_old_stale_gapped_duplicate_or_too_few_sessions_never_invent_evidence(self):
        missing=self.bars[:-1];self.assertFalse(intraday.calculate(missing,now=self.now)['available'])
        duplicate=self.bars+[self.bars[-1]];self.assertFalse(intraday.calculate(duplicate,now=self.now)['available'])
        result=intraday.calculate(self.bars[-24:],now=self.now)
        self.assertIsNone(result['time_volume_ratio']);self.assertEqual(result['comparison_sessions'],3)
        result=intraday.calculate(self.bars,now=self.now.replace(hour=9,minute=40))
        self.assertFalse(result['available'])

    def test_paginated_provider_cache_and_failure_do_not_fabricate_volume(self):
        request=Mock(side_effect=[{'bars':self.bars[:15],'next_page_token':'page2'},{'bars':self.bars[15:]}])
        with patch.object(intraday,'_cache',{}):
            result=intraday.fetch('TEST',request,clock=lambda:self.now.timestamp())
            self.assertEqual(result['time_volume_ratio'],2)
            again=intraday.fetch('TEST',request,clock=lambda:self.now.timestamp()+30)
            self.assertEqual(again,result);self.assertEqual(request.call_count,2)
            self.assertEqual(request.call_args.kwargs['params']['page_token'],'page2')
        with patch.object(intraday,'_cache',{}):
            result=intraday.fetch('TEST',Mock(return_value={'bars':[],'next_page_token':'loop'}),clock=lambda:self.now.timestamp())
            self.assertFalse(result['available'])

    def test_current_quote_vwap_chase_and_time_volume_actually_gate_order_evidence(self):
        config={**app_controls.ALL_DEFAULTS,'stock_min_float_millions':0,'stock_max_float_millions':0}
        data=intraday.calculate(self.bars,now=self.now)
        candidate={'volume_ratio':.05,'average_volume':300000,'momentum_move_percent':3,'atr_percent':4}
        features=momentum.features(candidate,{'ask':10.2,'spread_percent':.1},config,symbol='TEST',clock=lambda:self.now.timestamp(),intraday_fetch=lambda _:data)
        self.assertEqual(momentum.evaluate(features,config,now=self.now.timestamp()),[])
        chased={**features,'vwap_distance_percent':8}
        self.assertIn('stock_max_vwap_distance_percent',momentum.evaluate(chased,config,now=self.now.timestamp()))
        self.assertIn('intraday_evidence_missing_or_stale',momentum.evaluate(features,config,now=self.now.timestamp()+120))
        self.assertIn('completed_breakout_or_vwap_pullback_missing',momentum.evaluate({**features,'entry_setup':None},config,now=self.now.timestamp()))
        self.assertIn('stock_min_time_volume_ratio',momentum.evaluate({**features,'time_volume_ratio':1},config,now=self.now.timestamp()))

if __name__=='__main__':main()
