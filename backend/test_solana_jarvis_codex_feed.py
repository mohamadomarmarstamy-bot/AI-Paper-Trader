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
            for i in range(20):feed.reserve(api,NOW+2+i,11)
            feed.reserve(api,NOW+30,10)
            with self.assertRaises(feed.Unavailable):feed.reserve(api,NOW+32,1)
    def test_monthly_cap_stays_8000_independent_of_daily_headroom(self):
        from datetime import datetime,timezone
        month=datetime.fromtimestamp(NOW,timezone.utc).strftime('%Y-%m')
        with api.connection() as conn,conn:
            conn.execute('INSERT INTO codex_calls(at,day,month,reserved) VALUES (?,?,?,?)',(NOW-86400,'earlier',month,7800))
        with patch.dict(os.environ,{'JARVIS_CODEX_MODE':'fallback'}):
            feed.reserve(api,NOW,200)
            with self.assertRaises(feed.Unavailable):feed.reserve(api,NOW+2,1)
        with api.connection() as conn:
            status=feed.status(conn)
            self.assertEqual(status['daily_reserved_cu_limit'],250)
            self.assertEqual(status['monthly_reserved_cu_limit'],8000)
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
    def test_excluded_only_consumer_does_not_start_fallback(self):
        row,state=self.prepare()
        original=state['local_rules']['research-01']
        for overrides in ({'entry_paused':True},{'challenger_ends':NOW},{'resumed_at':row['first']+1},
                          {'exit':'(gain >= 15 or (peak >= 10 and gain <= peak - 5)) and gain > 15'}):
            state['local_rules']['research-01']=dict(original,**overrides)
            with api.connection() as conn,conn:
                conn.execute('UPDATE run SET state=?',(json.dumps(state),))
            with patch.object(feed,'attempt') as attempt:
                feed.tick(api,clock=lambda:NOW)
                attempt.assert_not_called()
        state['local_rules']['research-02']=dict(original,resumed_at=row['first'])
        state['bots']['research-02']['cash']=api.BUY_COST
        with api.connection() as conn,conn:
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
            self.assertEqual(feed.blocked_rules(api,conn,state,row,NOW),['research-02'])
            self.assertEqual(feed.status(conn)['calls'],0)

    def test_pause_during_requests_keeps_only_incurred_reservations(self):
        row,state=self.prepare()
        original_attempt=feed.attempt
        def pause():
            state['local_rules']['research-01']['entry_paused']=True
            with api.connection() as conn,conn:
                conn.execute('UPDATE run SET state=?',(json.dumps(state),))
        for stage,expected in enumerate((0,11,16)):
            with self.subTest(stage=stage),api.connection() as conn,conn:
                state['local_rules']['research-01']['entry_paused']=False
                conn.execute('UPDATE run SET state=?',(json.dumps(state),))
                for table in ('research_tasks','codex_calls','codex_samples'):conn.execute('DELETE FROM '+table)
            responses=[CodexTests.metadata(self),CodexTests.bars(self)]
            def post(*args,**kwargs):
                data=responses.pop(0)
                if len(responses)==2-stage:pause()
                return CodexTests.response(self,data)
            def attempt(*args,**kwargs):
                if stage==0:pause()
                return original_attempt(*args,post=post,**kwargs)
            with patch.dict(os.environ,{'JARVIS_CODEX_MODE':'fallback'}),patch.object(feed,'attempt',side_effect=attempt):
                feed.tick(api,clock=lambda:NOW)
            with api.connection() as conn:
                info=feed.status(conn,NOW)
                self.assertEqual(info['calls'],stage)
                self.assertEqual(info['reserved_cu_today'],expected)
                self.assertEqual(info['impact_since_tracking']['fallback_charts'],0)
                self.assertNotIn('chart',api.entry_evidence(conn,row,NOW))

    def test_ready_candle_expires_before_recent_primary_attempt_and_racing_refresh_wins(self):
        row,state=self.prepare()
        def save(record):
            with api.connection() as conn,conn:
                conn.execute('UPDATE research SET evidence=?',(json.dumps({'chart':record,'other':{'value':True,'at':NOW}}),))
        old={'status':'ready','at':NOW,'attempt_at':NOW,'value':{'closed_at':NOW-150,'rsi':25,'volume_ratio':1}}
        with patch.dict(os.environ,{'JARVIS_CODEX_MODE':'fallback'}):
            with patch.object(feed,'attempt') as attempt:
                for record,elapsed in ((old,0),(old,30),({**old,'attempt_at':None},31),
                                       ({**old,'value':dict(old['value'],provider='codex')},31)):
                    save(record)
                    feed.tick(api,clock=lambda:NOW+elapsed)
                attempt.assert_not_called()
            save(old)
            fresh={'status':'ready','at':NOW+31,'attempt_at':NOW+31,'value':{'closed_at':NOW,'volume_ratio':1}}
            def refresh(*args,**kwargs):
                save(fresh)
                return {'closed_at':NOW,'rsi':20,'volume_ratio':2,'provider':'codex'}
            with patch.object(feed,'attempt',side_effect=refresh) as attempt:
                feed.tick(api,clock=lambda:NOW+31)
                attempt.assert_called_once()
            with api.connection() as conn:
                saved=json.loads(conn.execute('SELECT evidence FROM research').fetchone()[0])
                self.assertEqual(saved,{'chart':fresh,'other':{'value':True,'at':NOW}})
            save(old)
            with api.connection() as conn,conn:
                conn.execute("DELETE FROM research_tasks WHERE name='codex-priority'")
            with patch.object(feed,'attempt',return_value={'closed_at':NOW,'rsi':20,'volume_ratio':2,'provider':'codex'}):
                feed.tick(api,clock=lambda:NOW+31)
            with api.connection() as conn:
                self.assertEqual(api.entry_evidence(conn,row,NOW+31)['chart']['provider'],'codex')
                self.assertTrue(json.loads(conn.execute('SELECT evidence FROM research').fetchone()[0])['other']['value'])

    def test_healthy_primary_does_not_spend(self):
        row,state=self.prepare()
        with api.connection() as conn,conn:
            conn.execute('UPDATE research SET evidence=?',(json.dumps({'chart':{'at':NOW,'value':None,'status':'ready','attempt_at':NOW}}),))
        with patch.object(feed,'attempt') as attempt:
            feed.tick(api,clock=lambda:NOW);attempt.assert_not_called()
    def test_scorecard_matches_same_episode_through_recurrence_and_reservations(self):
        import jarvis_brain as brain
        row,state=self.prepare()
        attempt=feed.attempt
        post=Mock(side_effect=[CodexTests.response(self,CodexTests.metadata(self)),
                               CodexTests.response(self,CodexTests.bars(self))])
        with patch.dict(os.environ,{'JARVIS_CODEX_MODE':'fallback'}), patch.object(feed,'attempt',
                side_effect=lambda *args,**kwargs:attempt(*args,post=post,**kwargs)):
            feed.tick(api,clock=lambda:NOW)
        for seconds in (60,240,360):
            with api.connection() as conn,conn:
                state,_=api.read(conn)
                state['watch'][row['mint']]['last']=NOW+seconds
                state['last_cycle']=NOW+seconds
                conn.execute('UPDATE run SET state=?',(json.dumps(state),))
            brain.observe(api,NOW+seconds)
        with api.connection() as conn:
            score=brain.status(conn,NOW+360)['fallback_scorecard']
            self.assertEqual(score['series']['candles'],[0,1,1,0,0])
            self.assertEqual(score['matched'],1)
            self.assertEqual(score['recurrences'],1)
            self.assertEqual(score['durable'],0)
            self.assertEqual(score['verdict'],'change_approach')
            self.assertEqual(score['reserved_cu'],16)
            self.assertIsNone(score['billed_cu'])
            self.assertEqual(post.call_count,2)

    def test_repeated_fresh_observations_keep_fixed_denominators_and_separate_indicators(self):
        row,state=self.prepare()
        with api.connection() as conn,conn:
            for i in range(3):
                pool={**row,'mint':str(i),'pair':'pool-'+str(i)}
                state['watch'][pool['mint']]=pool
                chart={'at':NOW,'value':{'closed_at':NOW,'rsi':45,'volume_ratio':None}}
                def record(at,ratio):
                    chart['at']=chart['value']['closed_at']=at
                    chart['value']['volume_ratio']=ratio
                    conn.execute('INSERT OR REPLACE INTO research VALUES (?,?,?,?)',
                        (pool['mint'],pool['pair'],at,json.dumps({'chart':chart})))
                record(NOW,None)
                ident=feed.scorecard.start(conn,pool,NOW)
                record(NOW,2)
                feed.scorecard.finish(conn,state,ident,NOW,True,api.ENTRY_WINDOW)
            for delay in feed.scorecard.WINDOWS:
                for key in range(3):
                    pool=state['watch'][str(key)]
                    pool['last']=NOW+delay
                    record(NOW+delay,2)
                feed.scorecard.observe(conn,state,NOW+delay,api.ENTRY_WINDOW)
            score=feed.scorecard.status(conn,NOW+360)
            self.assertEqual(score['series']['rsi'],[3,3,3,3,3])
            self.assertEqual(score['series']['volume_ratio'],[0,3,3,3,3])
            self.assertEqual(score['durable'],3)
            self.assertEqual(score['verdict'],'sustained_in_measured_window')

    def test_changing_population_cannot_establish_a_durable_fallback_gain(self):
        import jarvis_brain as brain
        row,state=self.prepare()
        with api.connection() as conn,conn:
            ident=feed.scorecard.start(conn,row,NOW)
            feed.scorecard.finish(conn,state,ident,NOW,False,api.ENTRY_WINDOW)
            for offset,eligible,complete in [(60,24,2),(240,20,5),(360,25,2)]:
                # Same mint moves to another pool; even a usable replacement is unmatched.
                state['watch'][row['mint']]={**row,'pair':OTHER,'last':NOW+offset}
                feed.scorecard.observe(conn,state,NOW+offset,api.ENTRY_WINDOW)
                brain.improvement.observe(conn,set(),{'paper_enabled':True,'cycle_age':1,
                    'chart_coverage':{'eligible':eligible,'reasons':{'complete':complete}}},NOW+offset)
            score=brain.status(conn,NOW+360)['fallback_scorecard']
            self.assertEqual((score['post_matched'],score['matched'],score['durable']),(1,0,0))
            self.assertEqual(score['verdict'],'retry_measurement')

    def test_attribution_requires_chart_dependency(self):
        row,state=self.prepare()
        evidence={'chart':{'provider':'codex','rsi':20,'volume_ratio':2}}
        api.open_trade(state,'research-01',row,100,NOW,evidence)
        self.assertTrue(state['bots']['research-01']['positions'][row['mint']]['codex_required'])
        state['local_rules']['research-01']['entry']='liquidity >= 50000'
        api.open_trade(state,'research-01',row,100,NOW,evidence)
        self.assertFalse(state['bots']['research-01']['positions'][row['mint']]['codex_required'])

if __name__=='__main__':unittest.main()
