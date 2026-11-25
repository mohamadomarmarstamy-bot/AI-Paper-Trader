import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import database
import jarvis_test_bot as api
import jarvis_codex_feed as feed

NOW=1800000006
ROW={'mint':'1'*31+'2','pair':'1'*31+'3','first':NOW-60}
OTHER='1'*31+'4'

class CodexTests(unittest.TestCase):
    def setUp(self):
        tmp=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database,'DATABASE_PATH',Path(tmp)/'db'))
        self.enterContext(patch.dict(os.environ,{'JARVIS_CODEX_API_KEY':'private','JARVIS_CODEX_MODE':'trial'}))
        with api.connection():pass
    def metadata(self):
        return {'pairMetadata':{'id':ROW['pair']+':'+str(feed.NETWORK),'networkId':feed.NETWORK,
            'token0':{'address':ROW['mint'],'networkId':feed.NETWORK},'token1':{'address':OTHER,'networkId':feed.NETWORK}}}
    def bars(self):
        times=[NOW-6-(20-i)*60 for i in range(20)]
        return {'getBars':{'s':'ok','pair':{'address':ROW['pair'],'networkId':feed.NETWORK,'token0':ROW['mint'],'token1':OTHER},
            't':times,'o':[1]*20,'h':[2]*20,'l':[.5]*20,'c':[1]*20,'buyVolume':['40']*20,'sellVolume':['60']*20}}
    def response(self,data):
        r=Mock(status_code=200,content=b'{}');r.json.return_value={'data':data};return r
    def test_usd_orientation_and_volume_are_explicit(self):
        side,addresses=feed.parse_metadata(self.metadata(),ROW)
        self.assertEqual(side,'token0')
        v=feed.parse_bars(self.bars(),ROW,addresses,NOW)
        self.assertEqual(v['currency'],'USD');self.assertEqual(v['volume_currency'],'USD')
        self.assertEqual(v['volume_ratio'],1)
        self.assertIn('currencyCode:"USD"',feed.BARS)
        self.assertIn('symbolType:POOL',feed.BARS)
    def test_wrong_identity_stale_and_gapped_data_are_rejected(self):
        for mode in ('identity','stale','gap','null','duplicate'):
            d=self.bars();b=d['getBars']
            if mode=='identity':b['pair']['address']='wrong'
            if mode=='stale':b['t']=[t-3600 for t in b['t']]
            if mode=='gap':b['t'][3]-=30
            if mode=='null':b['c'][2]=None
            if mode=='duplicate':b['t'][2]=b['t'][1]
            with self.assertRaises(feed.Unavailable):feed.parse_bars(d,ROW,[ROW['mint'],OTHER],NOW)
    def test_trial_does_not_feed_trading_and_saves_result(self):
        post=Mock(side_effect=[self.response(self.metadata()),self.response(self.bars())])
        self.assertIsNone(feed.attempt(api,ROW,clock=lambda:NOW,post=post))
        with api.connection() as conn:
            self.assertEqual(feed.status(conn)['samples'],{'validated':1})
            self.assertEqual(feed.status(conn)['calls'],2)
        self.assertIsNone(feed.attempt(api,ROW,clock=lambda:NOW+60,post=post))
        self.assertEqual(post.call_count,2)
        self.assertNotIn('private',json.dumps(self.bars()))
    def test_fallback_only_returns_validated_chart(self):
        with patch.dict(os.environ,{'JARVIS_CODEX_MODE':'fallback'}):
            post=Mock(side_effect=[self.response(self.metadata()),self.response(self.bars())])
            self.assertEqual(feed.attempt(api,ROW,clock=lambda:NOW,post=post)['provider'],'codex')
    def test_graphql_errors_never_use_partial_data_or_echo_secrets(self):
        r=self.response(self.metadata());r.json.return_value={'data':self.metadata(),'errors':[{'message':'private','extensions':{'code':'NOT_AUTHORIZED'}}]}
        post=Mock(return_value=r)
        self.assertIsNone(feed.attempt(api,ROW,clock=lambda:NOW,post=post))
        with api.connection() as conn:
            s=feed.status(conn);self.assertEqual(s['samples'],{'authentication_or_plan':1})
            self.assertNotIn('private',json.dumps(s))
        self.assertEqual(post.call_count,1)
    def test_reservations_survive_restart_and_cap_trial_calls(self):
        for i in range(6):feed.reserve(api,NOW+i*2,11)
        with self.assertRaises(feed.Unavailable):feed.reserve(api,NOW+20,5)
        with api.connection() as conn:self.assertEqual(feed.status(conn)['calls'],6)
    def test_daily_budget_and_rate_limit_are_reserved_before_network(self):
        with patch.dict(os.environ,{'JARVIS_CODEX_MODE':'fallback'}):
            for i in range(4):feed.reserve(api,NOW,5)
            with self.assertRaises(feed.Unavailable):feed.reserve(api,NOW,5)
            for i in range(16):feed.reserve(api,NOW+2+i,11)
            with self.assertRaises(feed.Unavailable):feed.reserve(api,NOW+30,5)
    def test_disabled_or_missing_key_does_not_request(self):
        with patch.dict(os.environ,{'JARVIS_CODEX_API_KEY':''}):
            post=Mock();self.assertIsNone(feed.attempt(api,ROW,clock=lambda:NOW,post=post));post.assert_not_called()


class PriorityTests(unittest.TestCase):
    setUp = CodexTests.setUp
    def prepare(self):
        row={**ROW,'last':NOW,'symbol':'Test','liquidity_usd':80000,'age_minutes':60,
             'volume_m5_usd':15000,'buys_m5':20,'sells_m5':10}
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            state.update(enabled=True,watch={row['mint']:row},local_rules={'research-01':{
                'created':NOW-120,'entry':'liquidity >= 50000 and rsi < 30','id':'test'}})
            state['bots']['research-01']['cash']=2000_000000
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
            conn.execute('INSERT INTO research VALUES (?,?,?,?)',(row['mint'],row['pair'],NOW,json.dumps({
                'chart':{'at':NOW,'value':None,'status':'stale_candles','attempt_at':NOW}})))
        return row,state
    def test_partial_expression_preserves_unknown_and_or_not(self):
        self.assertIs(feed.possible('liquidity > 50000 and rsi < 30',{'liquidity':100}),False)
        self.assertIsNone(feed.possible('liquidity > 50000 or rsi < 30',{'liquidity':100}))
        self.assertIs(feed.possible('liquidity > 50000 or rsi < 30',{'liquidity':90000}),True)
        self.assertIsNone(feed.possible('not (rsi < 30) and liquidity > 100',{'liquidity':80000}))
        self.assertIsNone(feed.possible('buys_m5 / sells_m5 > 1.5',{'buys_m5':10,'sells_m5':0}))
    def test_filters_full_stale_and_known_rejections(self):
        row,state=self.prepare()
        with api.connection() as conn:
            self.assertEqual(feed.blocked_rules(api,conn,state,row,NOW),['research-01'])
            for changed in ({'liquidity_usd':100},{'last':NOW-91},{'monitor_only':True},{'first':NOW-1000}):
                self.assertEqual(feed.blocked_rules(api,conn,state,{**row,**changed},NOW),[])
            state['bots']['research-01']['positions']={str(i):{} for i in range(4)}
            self.assertEqual(feed.blocked_rules(api,conn,state,row,NOW),[])
    def test_prioritizes_volume_and_persists_impact(self):
        row,state=self.prepare();other={**row,'mint':OTHER,'volume_m5_usd':25000}
        state['watch'][OTHER]=other
        with api.connection() as conn,conn:
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
            conn.execute('INSERT INTO research VALUES (?,?,?,?)',(OTHER,row['pair'],NOW,json.dumps({
                'chart':{'at':NOW,'value':None,'status':'stale_candles','attempt_at':NOW}})))
        value={'closed_at':NOW-6,'rsi':20,'volume_ratio':2,'provider':'codex'}
        with patch.object(feed,'attempt',return_value=value) as attempt:
            feed.tick(api,clock=lambda:NOW)
            self.assertEqual(attempt.call_args.args[1]['mint'],OTHER)
            feed.tick(api,clock=lambda:NOW)
            self.assertEqual(attempt.call_count,1)
        with api.connection() as conn:
            self.assertEqual(api.entry_evidence(conn,other,NOW)['chart'],value)
            self.assertEqual(feed.status(conn)['impact_since_tracking'],{'fallback_charts':1,'charts_completing_rules':1,'charts_matching_rules':1})
    def test_healthy_primary_does_not_spend(self):
        row,state=self.prepare()
        with api.connection() as conn,conn:
            conn.execute('UPDATE research SET evidence=?',(json.dumps({'chart':{'at':NOW,'value':None,'status':'ready','attempt_at':NOW}}),))
        with patch.object(feed,'attempt') as attempt:
            feed.tick(api,clock=lambda:NOW);attempt.assert_not_called()
    def test_attribution_requires_chart_dependency(self):
        row,state=self.prepare()
        evidence={'chart':{'provider':'codex','rsi':20,'volume_ratio':2}}
        api.open_trade(state,'research-01',row,100,NOW,evidence)
        self.assertTrue(state['bots']['research-01']['positions'][row['mint']]['codex_required'])
        state['local_rules']['research-01']['entry']='liquidity >= 50000'
        api.open_trade(state,'research-01',row,100,NOW,evidence)
        self.assertFalse(state['bots']['research-01']['positions'][row['mint']]['codex_required'])

if __name__=='__main__':unittest.main()
