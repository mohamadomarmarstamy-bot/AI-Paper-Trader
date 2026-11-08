from contextlib import closing
from pathlib import Path
import json
import os
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

import database
import solana_jupiter as jupiter
import solana_quote_benchmark as benchmark
import solana_real_execution as execution
import solana_real_worker as live


class BudgetTests(unittest.TestCase):
    def setUp(self):
        p=patch.dict(os.environ,{'JUPITER_API_KEY':'synthetic'},clear=True);p.start();self.addCleanup(p.stop)

    def test_live_requests_are_spaced_and_research_yields(self):
        budget=jupiter.Budget();clock=[100.0];starts=[]
        def wait(timeout):clock[0]+=timeout
        with patch.object(jupiter.time,'monotonic',side_effect=lambda:clock[0]),patch.object(budget.condition,'wait',side_effect=wait):
            with budget.permit():starts.append(clock[0])
            with budget.permit():starts.append(clock[0])
            self.assertAlmostEqual(starts[1]-starts[0],1.05)
            with self.assertRaises(jupiter.Deferred):
                with budget.permit(research=True):self.fail('Research must yield')
            self.assertEqual(budget.live_waiters,0);self.assertEqual(budget.active,0)
            clock[0]+=31
            with budget.permit(research=True):self.assertEqual(budget.active,1)

    def test_rate_limit_cooldown_sends_no_request_and_expires(self):
        budget=jupiter.Budget();clock=[100.0]
        with patch.object(jupiter.time,'monotonic',side_effect=lambda:clock[0]):
            with budget.permit():pass
            budget.limited({'Retry-After':'10'})
            with self.assertRaises(jupiter.RateLimited):
                with budget.permit():self.fail('Cooldown must prevent the request')
            clock[0]=111
            with budget.permit():pass
        self.assertEqual(budget.active,0)

    def test_http_date_retry_after_is_honored(self):
        from email.utils import formatdate
        budget=jupiter.Budget()
        with patch.object(jupiter.time,'time',return_value=1000),patch.object(jupiter.time,'monotonic',return_value=100):
            with budget.permit():pass
            budget.limited({'Retry-After':formatdate(1010,usegmt=True)})
            self.assertEqual(budget.cooldown,110)

    def test_key_change_invalidates_old_cooldown(self):
        budget=jupiter.Budget()
        with budget.permit():pass
        budget.limited({'Retry-After':'60'})
        os.environ['JUPITER_API_KEY']='other-synthetic'
        with budget.permit():pass
        self.assertEqual(budget.cooldown,0)

    def test_fast_mode_cannot_be_requested_by_live_execution(self):
        with patch.object(jupiter.http,'get') as get:
            with self.assertRaises(ValueError):jupiter.build_quote('owner','input','output',1,{},mode='fast')
            get.assert_not_called()

    def test_identical_normal_and_fast_policy_and_redacted_failures(self):
        policy={'slippage_bps':100,'max_price_impact_percent':5}
        data={'inputMint':'input','outputMint':'output','inAmount':'100','outAmount':'1000','otherAmountThreshold':'990',
              'swapMode':'ExactIn','slippageBps':100,'priceImpactPct':'0.01'}
        response=Mock(status_code=200,content=b'{}',headers={});response.json.side_effect=lambda:dict(data)
        from contextlib import nullcontext
        with patch.object(jupiter._budget,'permit',side_effect=lambda **_:nullcontext()),patch.object(jupiter.http,'get',return_value=response) as get:
            for mode in ('standard','fast'):jupiter.build_quote('owner','input','output',100,policy,mode=mode,research=True)
            normal,fast=[c.kwargs['params'] for c in get.call_args_list]
            self.assertEqual({**normal,'mode':'fast'},fast)
            self.assertEqual(get.call_args.kwargs['timeout'],3)
            get.side_effect=RuntimeError('synthetic-private-key')
            with self.assertRaises(jupiter.Unavailable) as caught:jupiter.build_quote('owner','input','output',100,policy,research=True)
            self.assertNotIn('synthetic-private-key',str(caught.exception))

    def test_429_is_not_retried_and_cooldown_is_shared_with_live(self):
        budget=jupiter.Budget();response=Mock(status_code=429,headers={'Retry-After':'5'})
        with patch.object(jupiter,'_budget',budget),patch.object(jupiter.http,'get',return_value=response) as get:
            with self.assertRaises(jupiter.RateLimited):jupiter.build_quote('owner','input','output',100,{'slippage_bps':100},research=True)
            with self.assertRaises(jupiter.RateLimited):execution.Provider().build('owner','input','output',100,{'slippage_bps':100})
            self.assertEqual(get.call_count,1)


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        for p in (patch.object(database,'DATABASE_PATH',Path(temp.name)/'bench.db'),patch.dict(os.environ,{'JUPITER_API_KEY':'synthetic'},clear=True)):
            p.start();self.addCleanup(p.stop)
        self.now=time.time();self.run=live.initial();self.run.update(wallet='owner')
        with closing(database.get_connection()) as conn:
            live.initialize(conn)
            conn.execute('UPDATE solana_real_run SET state_json=? WHERE id=1',(json.dumps(self.run),))
            order={'id':'old-buy','wallet':'owner','mint':'mint','side':'buy','status':'confirmed','created_at':self.now-100,
                   'prepared':{'input_amount':1000,'wire':'PRIVATE-WIRE'}}
            conn.execute('INSERT INTO solana_real_orders VALUES(?,?,?,?)',(order['id'],order['created_at'],order['status'],json.dumps(order)));conn.commit()
        self.calls=[]

    def quote(self,owner,input_mint,output_mint,amount,policy,**kwargs):
        self.calls.append((owner,input_mint,output_mint,amount,dict(policy),kwargs))
        mode=kwargs['mode']
        return {'_request_ms':40 if mode=='fast' else 80,'outAmount':'1010','otherAmountThreshold':'990' if mode=='fast' else '1000','_observed_at':self.now}

    def samples(self):
        with closing(database.get_connection()) as conn:
            return [json.loads(r[0]) for r in conn.execute('SELECT details_json FROM solana_quote_benchmark ORDER BY id')]

    def test_quote_only_pairs_persist_separately_and_never_touch_trades(self):
        before=live.load()
        with patch.object(execution,'signer',side_effect=AssertionError('No signing')),patch.object(execution.Provider,'send',side_effect=AssertionError('No sending')):
            benchmark.tick(quote=self.quote,clock=lambda:self.now,pause=lambda _:None)
        sample=self.samples()[0]
        self.assertTrue(sample['comparable']);self.assertEqual(sample['fast_latency_change_ms'],-40)
        self.assertAlmostEqual(sample['fast_minimum_output_change_percent'],-1)
        self.assertEqual(self.calls[0][0:5],self.calls[1][0:5])
        self.assertEqual([c[5]['mode'] for c in self.calls],['standard','fast'])
        self.assertTrue(all(c[5]['research'] for c in self.calls));self.assertEqual(live.load(),before)
        self.assertNotIn('PRIVATE-WIRE',json.dumps(sample))

    def test_persistent_cadence_and_alternating_order(self):
        for stamp in (self.now,self.now+10,self.now+61):benchmark.tick(quote=self.quote,clock=lambda:stamp,pause=lambda _:None)
        self.assertEqual(len(self.calls),4)
        self.assertEqual([s['first_mode'] for s in self.samples()],['standard','fast'])

    def test_positions_pending_orders_and_recent_source_events_prevent_sampling(self):
        with closing(database.get_connection()) as conn:
            run=dict(self.run);run['positions']={'p':{'mint':'mint'}}
            conn.execute('UPDATE solana_real_run SET state_json=?',(json.dumps(run),));conn.commit()
        benchmark.tick(quote=self.quote,clock=lambda:self.now,pause=lambda _:None);self.assertFalse(self.calls)
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE solana_real_run SET state_json=?',(json.dumps(self.run),))
            conn.execute("UPDATE solana_real_orders SET status='unknown'");conn.commit()
        benchmark.tick(quote=self.quote,clock=lambda:self.now,pause=lambda _:None);self.assertFalse(self.calls)
        with closing(database.get_connection()) as conn:
            conn.execute("UPDATE solana_real_orders SET status='confirmed'")
            conn.execute('CREATE TABLE solana_live_source_events(id TEXT,observed_at REAL,details_json TEXT)')
            conn.execute('INSERT INTO solana_live_source_events VALUES(?,?,?)',('new-source',self.now,'{}'));conn.commit()
        benchmark.tick(quote=self.quote,clock=lambda:self.now,pause=lambda _:None);self.assertFalse(self.calls)

    def test_live_work_between_modes_cancels_second_quote(self):
        def new_work(_):
            with closing(database.get_connection()) as conn:
                conn.execute("UPDATE solana_real_orders SET status='unknown'");conn.commit()
        benchmark.tick(quote=self.quote,clock=lambda:self.now,pause=new_work)
        self.assertEqual(len(self.calls),1);self.assertFalse(self.samples()[0]['comparable'])

    def test_rate_limit_stops_pair_and_never_auto_retries(self):
        quote=Mock(side_effect=jupiter.RateLimited('synthetic'))
        benchmark.tick(quote=quote,clock=lambda:self.now,pause=lambda _:None)
        quote.assert_called_once();self.assertEqual(self.samples()[0]['results']['standard']['status'],'rate_limited')

    def test_stale_pair_excluded_and_failures_remain_in_denominator(self):
        original=self.quote
        def stale(*args,**kwargs):
            q=original(*args,**kwargs)
            if kwargs['mode']=='fast':q['_observed_at']+=6
            return q
        benchmark.tick(quote=stale,clock=lambda:self.now,pause=lambda _:None)
        self.assertFalse(self.samples()[0]['comparable'])
        self.now+=61
        def partial(*args,**kwargs):
            if kwargs['mode']=='fast':raise jupiter.Unavailable('No supported route')
            return original(*args,**kwargs)
        benchmark.tick(quote=partial,clock=lambda:self.now,pause=lambda _:None)
        with closing(database.get_connection()) as conn:status=benchmark.public(conn)
        self.assertEqual(status['comparable_pairs'],0)
        fast=next(m for m in status['modes'] if m['mode']=='fast')
        self.assertEqual(fast['attempts'],2);self.assertEqual(fast['successful'],1);self.assertEqual(fast['unavailable'],1)

    def test_old_or_other_wallet_orders_are_not_sampled(self):
        with closing(database.get_connection()) as conn:
            order=json.loads(conn.execute('SELECT details_json FROM solana_real_orders').fetchone()[0])
            order['wallet']='someone-else'
            conn.execute('UPDATE solana_real_orders SET details_json=?',(json.dumps(order),));conn.commit()
            self.assertEqual(benchmark.candidates(conn,'owner',self.now),[])
            order['wallet']='owner'
            conn.execute('UPDATE solana_real_orders SET created_at=?,details_json=?',(self.now-90000,json.dumps(order)));conn.commit()
            self.assertEqual(benchmark.candidates(conn,'owner',self.now),[])
        benchmark.tick(quote=self.quote,clock=lambda:self.now,pause=lambda _:None)
        self.assertFalse(self.calls)

    def test_report_status_has_no_network_or_schema_side_effects(self):
        with patch.object(jupiter.http,'get',side_effect=AssertionError('Read-only report')):
            with closing(database.get_connection()) as conn:
                before=conn.total_changes;status=benchmark.public(conn)
                self.assertEqual(conn.total_changes,before)
        self.assertEqual(status['samples'],0);self.assertEqual(status['live_mode'],'standard')
