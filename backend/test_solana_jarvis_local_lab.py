import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import database
import jarvis_test_bot as api
import jarvis_local_lab as lab

RULE = {'name':'Pressure test','hypothesis':'Buying pressure in liquid pools.',
        'entry':'liquidity >= 50000 and buys_m5 > sells_m5 * 1.5',
        'exit':'gain >= 15 or (peak >= 10 and gain <= peak - 5)'}
SOURCES = [{'url':'https://example.com/research','title':'Research','snippet':'Market microstructure.'}]


class LocalLabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database,'DATABASE_PATH',Path(self.tmp)/'main.db'))
        api.set_enabled(True)

    def submit(self, rule=None, now=100):
        return lab.submit(api, {'rule':rule or RULE,'sources':SOURCES}, now=now)

    def test_expression_rejects_python_execution_and_future_fields(self):
        for text in ["__import__('os').system('whoami')",'gain > 1','liquidity.__class__ == 1',
                     '[x for x in range(10)]','liquidity ** 100 > 0','liquidity[0] > 1','True',
                     'liquidity > float("inf")']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                lab.parse(text)
        self.assertFalse(lab.run('liquidity / sells_m5 > 1',{'liquidity':10,'sells_m5':0}))
        self.assertTrue(lab.run('sells_m5 > 0 and buys_m5 / sells_m5 > 2',{'buys_m5':10,'sells_m5':2}))
        self.assertFalse(lab.run('rsi > 50',{}))

    def test_auth_disabled_by_default_and_scoped_token(self):
        with patch.dict(os.environ, {'JARVIS_LOCAL_WORKER_TOKEN':''}):
            self.assertFalse(lab.authorized('Bearer anything'))
        with patch.dict(os.environ, {'JARVIS_LOCAL_WORKER_TOKEN':'x'*32}):
            self.assertTrue(lab.authorized('Bearer '+'x'*32))
            self.assertFalse(lab.authorized('Bearer '+'y'*32))

    def test_ten_slots_duplicate_and_fixed_funding(self):
        first = self.submit()
        self.assertEqual(self.submit()['status'],'duplicate')
        for number in range(1,10):
            rule = {**RULE,'entry':f'liquidity >= {50000+number}'}
            self.assertEqual(self.submit(rule)['status'],'testing')
        self.assertEqual(self.submit({**RULE,'entry':'liquidity >= 60000'})['status'],'full')
        with api.connection() as conn:
            state,_ = api.read(conn)
            self.assertEqual(state['bots'][first['slot']]['cash'],lab.CAPITAL)
            self.assertEqual(len(state['local_rules']),10)

    def test_paper_entry_freezes_code_and_custom_exit(self):
        result = self.submit()
        with api.connection() as conn:
            state,_ = api.read(conn)
        row = {'mint':'mint','pair':'pair','symbol':'Coin','first':101,'last':101,
               'liquidity_usd':100000,'age_minutes':120,'buys_m5':40,'sells_m5':10,
               'volume_m5_usd':20000,'price_usd':1,'monitor_only':False,'pullback':None}
        self.assertTrue(lab.entry_allowed(state,result['slot'],row,{},101))
        self.assertFalse(lab.entry_allowed(state,result['slot'],{**row,'first':99},{},101))
        api.open_trade(state,result['slot'],row,1000,101)
        p = state['bots'][result['slot']]['positions']['mint']
        del state['local_rules'][result['slot']]
        self.assertEqual(p['policy']['local_rule']['exit'],RULE['exit'])
        completed=[]
        api.apply_exit(completed,state['bots'][result['slot']],result['slot'],p,1000,60_000000,102,market=row)
        self.assertEqual(len(completed),1)
        self.assertEqual(completed[0]['reason'],'Research strategy exit')
        self.assertEqual(completed[0]['pnl'],9_650000)

    def test_research_uses_shared_entry_queue_when_regular_exits_are_overdue(self):
        result=self.submit()
        with api.connection() as conn:
            state,_=api.read(conn)
            row={'mint':'new','pair':'pair','symbol':'New','first':499,'last':500,
                 'liquidity_usd':100000,'age_minutes':120,'buys_m5':40,'sells_m5':10,
                 'volume_m5_usd':20000,'price_usd':1,'monitor_only':False,'pullback':None}
            held={**row,'mint':'old','first':101}
            api.open_trade(state,'instant-step',held,1000,101)
            state['watch']={'new':row}
            exits=api.quote_jobs(state,500)
            self.assertTrue(api.exit_queue(exits,500)['backed_up'])
            self.assertEqual(api.entry_quote_budget(exits,500),api.BACKLOG_EXIT_QUOTES_PER_ENTRY)
            self.assertIn(result['slot'],api.entry_jobs(conn,state,500)['new'])
            status=api.local_entry_status(conn,state,500)[result['slot']]
            self.assertEqual(status['matching_coins'],1)
            self.assertIn('shared quote capacity',status['label'])
            state['entry_pause_until']=600
            self.assertIn('Buy pause active',api.local_entry_status(conn,state,500)[result['slot']]['label'])
            state['enabled']=False
            self.assertIn('Paper entries paused',api.local_entry_status(conn,state,500)[result['slot']]['label'])

    def test_entry_status_distinguishes_missing_chart_from_unmet_signal(self):
        result=self.submit({**RULE,'entry':'volume_ratio >= 1.5 and rsi <= 30'})
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            state['watch']={'new':{'mint':'new','pair':'pair','symbol':'New','first':499,'last':500,
                 'liquidity_usd':100000,'age_minutes':120,'buys_m5':40,'sells_m5':10,
                 'volume_m5_usd':20000,'price_usd':1,'monitor_only':False,'pullback':None}}
            status=api.local_entry_status(conn,state,500)[result['slot']]
            self.assertIn('Waiting for fresh data',status['label'])
            self.assertEqual(status['missing_observations']['rsi'],1)
            evidence={'chart':{'at':500,'value':{'closed_at':480,'rsi':50,'volume_ratio':2}}}
            conn.execute('INSERT INTO research VALUES (?,?,?,?)',('new','pair',500,json.dumps(evidence)))
            status=api.local_entry_status(conn,state,500)[result['slot']]
            self.assertIn('No matching signal',status['label'])
            self.assertEqual(status['complete_observations'],1)
            evidence['chart']['value']['rsi']=20
            conn.execute('UPDATE research SET evidence=?',(json.dumps(evidence),))
            self.assertEqual(api.local_entry_status(conn,state,500)[result['slot']]['matching_coins'],1)
            state['bots']['instant-step']['positions']={'old':{'mint':'old','quantity':1000,'due':100,'mark_stale':False,'exit_priority':0}}
            self.assertIn('urgent exits',api.local_entry_status(conn,state,500)[result['slot']]['label'])

    def test_preset_pause_keeps_research_entries_and_existing_exits(self):
        result=self.submit()
        with api.connection() as conn:
            state,_=api.read(conn)
            row={'mint':'new','pair':'pair','symbol':'New','first':499,'last':500,
                 'liquidity_usd':100000,'age_minutes':120,'buys_m5':40,'sells_m5':10,
                 'volume_m5_usd':20000,'price_usd':1,'monitor_only':False,'pullback':None}
            held={**row,'mint':'held'}
            api.open_trade(state,'instant-step',held,1000,101)
            before=copy.deepcopy(state['bots']['instant-step'])
            state['watch']={'new':row};state['preset_entries_paused']=True
            jobs=api.entry_jobs(conn,state,500)
            self.assertIn(result['slot'],jobs['new'])
            self.assertFalse(set(jobs['new'])&set(api.FIXED))
            self.assertTrue(api.quote_jobs(state,500))
            self.assertEqual(state['bots']['instant-step'],before)
            state['preset_entries_paused']=False
            self.assertIn('instant-step',api.entry_jobs(conn,state,500)['new'])

    def test_preset_startup_pause_is_durable_but_respects_explicit_resume(self):
        self.submit()
        with patch.dict(os.environ,{'JARVIS_FOCUS_MODE':'true'}):
            api.apply_preset_pause()
            self.assertTrue(api.status()['preset_entries_paused'])
            api.set_preset_pause(False)
            api.apply_preset_pause()
            self.assertFalse(api.status()['preset_entries_paused'])
        with self.assertRaises(ValueError):api.set_preset_pause('yes')
        report=api.research_diagnostic()
        self.assertEqual(len(report['slots']),10)
        self.assertNotIn('sources',json.dumps(report))

    def test_archive_retains_versions_and_pages_without_changing_trading_state(self):
        first=self.submit()
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            self.insert(conn,first['slot'],first['id'],2,-1_000000)
            conn.execute("UPDATE local_strategies SET status='failed' WHERE id=?",(first['id'],))
            for index in range(26):
                conn.execute('INSERT INTO local_strategies(id,slot,created,rule,sources,status) VALUES (?,?,?,?,?,?)',
                    ('archived-'+str(index),first['slot'],200+index,json.dumps(RULE),json.dumps(SOURCES),'inactive'))
            before=conn.execute('SELECT state FROM run').fetchone()[0]
        page=api.archive()
        self.assertEqual(len(page['strategies']),25)
        older=api.archive(page['next'])
        self.assertEqual(len(older['strategies']),2)
        item=next(row for row in older['strategies'] if row['id']==first['id'])
        self.assertEqual(item['closed'],2);self.assertEqual(item['net'],-2)
        self.assertEqual(item['status'],'failed');self.assertIsNone(older['next'])
        with api.connection() as conn:
            self.assertEqual(conn.execute('SELECT state FROM run').fetchone()[0],before)

    def test_reused_slot_shows_only_current_trial_and_preserves_pbh(self):
        from datetime import datetime
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from solana_meme_routes import build_router

        old = self.submit()
        slot = old['slot']
        with api.connection() as conn, conn:
            state, _ = api.read(conn)
            self.insert(conn, slot, old['id'], 100, -6_000000)
            lab.review(conn, state, 2000)
            self.assertNotIn(slot, state['local_rules'])
            conn.execute('UPDATE run SET state=?', (json.dumps(state),))
        replacement = self.submit({**RULE, 'name': 'New trial', 'entry': 'liquidity >= 70000'}, now=2100)
        self.assertEqual(replacement['slot'], slot)
        app = FastAPI()
        app.include_router(build_router(require_session=lambda request: None))
        client = TestClient(app)
        status = lambda: next(b for b in client.get('/jarvis-test-bot/status').json()['bots'] if b['id'] == slot)
        new = status()
        self.assertEqual(new['current_trial']['id'], replacement['id'])
        self.assertEqual(new['current_trial']['name'], 'New trial')
        self.assertEqual((new['current_trial']['closed'], new['current_trial']['realized'], new['current_trial']['open']), (0, 0, 0))
        self.assertEqual((new['closed'], new['realized']), (100, -600))
        self.assertEqual(new['funding'], lab.CAPITAL / api.DOLLAR)
        self.assertEqual(client.get('/jarvis-test-bot/history', params={'bot': slot, 'strategy_id': replacement['id']}).json()['coins'], [])
        day = datetime.now(api.ET).date().isoformat()
        with api.connection() as conn, conn:
            for coin, pnl, trade_day in [('winner', 5_000000, day), ('loser', -1_000000, 'earlier')]:
                trade = {'bot': slot, 'symbol': coin, 'policy': {'local_rule': {'id': replacement['id']}},
                         'pnl': pnl, 'entry_cost': 50_250000, 'banked': 50_250000 + pnl,
                         'opened': 2200, 'closed': 2210, 'reason': 'Paper test'}
                conn.execute('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',
                             (slot, coin, 2200, 2210, trade_day, pnl, 50_250000, json.dumps(trade)))
        updated = status()
        trial = updated['current_trial']
        self.assertEqual((trial['closed'], trial['realized'], trial['wins'], trial['losses'], trial['today']['realized']), (2, 4, 1, 1, 5))
        self.assertEqual(updated['cash'], new['cash'])
        items = client.get('/jarvis-test-bot/history', params={'bot': slot, 'strategy_id': replacement['id']}).json()['coins']
        self.assertEqual({item['mint'] for item in items}, {'winner', 'loser'})
        self.assertEqual(client.get('/jarvis-test-bot/history', params={'bot': 'jarvis', 'strategy_id': replacement['id']}).status_code, 422)
        archive = client.get('/jarvis-test-bot/archive').json()['strategies']
        self.assertEqual([(item['id'], item['closed'], item['net']) for item in archive], [(old['id'], 100, -600)])

    def insert(self, conn, slot, ident, count, pnl):
        for i in range(count):
            data={'policy':{'local_rule':{'id':ident}},'pnl':pnl}
            conn.execute('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',
                         (slot,str(i),101+i,1000+i,'day',pnl,50_250000,json.dumps(data)))

    def test_waits_for_100_and_promotes_real_paper_results(self):
        result=self.submit()
        with api.connection() as conn, conn:
            state,_=api.read(conn)
            self.insert(conn,result['slot'],result['id'],99,1_000000)
            lab.review(conn,state,2000)
            self.assertIsNone(state['strategy'])
            conn.execute('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',
                         (result['slot'],'last',300,2000,'day',1_000000,50_250000,
                          json.dumps({'policy':{'local_rule':{'id':result['id']}}})))
            lab.review(conn,state,2001)
            self.assertEqual(state['strategy']['local_rule']['id'],result['id'])
            self.assertIn(result['slot'],state['local_rules'])
            self.assertEqual(conn.execute('SELECT status FROM local_strategies').fetchone()[0],'proven')

    def test_failed_strategy_releases_slot_preserves_positions_and_capital(self):
        result=self.submit()
        with api.connection() as conn, conn:
            state,_=api.read(conn)
            self.insert(conn,result['slot'],result['id'],100,-1_000000)
            state['bots'][result['slot']]['positions']['holding']={'policy':{'local_rule':RULE}}
            lab.review(conn,state,2000)
            self.assertNotIn(result['slot'],state['local_rules'])
            self.assertIn('holding',state['bots'][result['slot']]['positions'])
            self.assertEqual(conn.execute('SELECT status FROM local_strategies').fetchone()[0],'failed')
            conn.execute('UPDATE run SET state=? WHERE id=1',(json.dumps(state),))
        self.assertNotIn(result['slot'],lab.context(api)['available_slots'])

    def test_no_entries_is_inactive_not_losing(self):
        result=self.submit()
        with api.connection() as conn, conn:
            state,_=api.read(conn)
            lab.review(conn,state,100+24*3600-1)
            self.assertEqual(conn.execute('SELECT status FROM local_strategies').fetchone()[0],'testing')
            self.assertIn(result['slot'],state['local_rules'])
            lab.review(conn,state,100+24*3600)
            self.assertEqual(conn.execute('SELECT status FROM local_strategies').fetchone()[0],'inactive')
            self.assertNotIn(result['slot'],state['local_rules'])
            self.assertIsNone(state['strategy'])

    def test_24_hour_inactivity_does_not_retire_a_strategy_with_trades_or_holdings(self):
        result=self.submit()
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            state['bots'][result['slot']]['positions']['held']={'policy':{'local_rule':{'id':result['id']}}}
            lab.review(conn,state,100+24*3600)
            self.assertEqual(conn.execute('SELECT status FROM local_strategies').fetchone()[0],'testing')
            state['bots'][result['slot']]['positions'].clear()
            self.insert(conn,result['slot'],result['id'],1,-1_000000)
            lab.review(conn,state,100+25*3600)
            self.assertEqual(conn.execute('SELECT status FROM local_strategies').fetchone()[0],'testing')

    def test_routes_require_token_and_reject_invalid_strategy(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from solana_meme_routes import build_router
        app=FastAPI()
        app.include_router(build_router(require_session=lambda _:None))
        client=TestClient(app)
        with patch.dict(os.environ,{'JARVIS_LOCAL_WORKER_TOKEN':'x'*32}):
            self.assertEqual(client.get('/jarvis-test-bot/local-worker/context').status_code,401)
            headers={'Authorization':'Bearer '+'x'*32}
            response=client.get('/jarvis-test-bot/local-worker/context',headers=headers)
            self.assertEqual(response.status_code,200)
            self.assertEqual(len(response.json()['available_slots']),10)
            response=client.post('/jarvis-test-bot/local-worker/strategy',headers=headers,json={'rule':RULE,'sources':SOURCES})
            self.assertEqual(response.json()['status'],'testing')
            self.assertEqual(client.post('/jarvis-test-bot/local-worker/strategy',headers=headers,json={}).status_code,422)
            self.assertEqual(client.post('/jarvis-test-bot/local-worker/strategy',headers=headers,content='x'*20001).status_code,413)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('local_worker',Path(__file__).resolve().parents[1]/'local-worker'/'worker.py')
        self.module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.tmp=self.enterContext(tempfile.TemporaryDirectory())
        self.config={'app_url':'https://example.com','worker_token':'x'*32,'model':'qwen2.5-coder:3b','sessions_per_day':10}

    def test_worker_research_generate_submit_and_no_paid_ai(self):
        http=Mock()
        context={'enabled':True,'available_slots':['research-01'],'strategies':[]}
        responses=[]
        for data in (context,{'status':'testing'}):
            r=Mock(status_code=200,content=b'{}');r.json.return_value=data;responses.append(r)
        http.request.side_effect=responses
        search=Mock(status_code=200,content=b'{}')
        search.json.return_value={'query':{'search':[{'pageid':1,'title':'Market','snippet':'Research'}]}}
        http.get.return_value=search
        generated=Mock(status_code=200,content=b'{}')
        generated.json.return_value={'done':True,'done_reason':'stop','response':json.dumps(RULE),'eval_count':100,'eval_duration':10**9}
        http.post.return_value=generated
        worker=self.module.Worker(self.config,Path(self.tmp)/'state.json',session=http)
        self.assertEqual(worker.cycle(),'testing')
        self.assertEqual(http.post.call_args.args[0],'http://127.0.0.1:11434/api/generate')
        self.assertNotIn('Authorization',http.post.call_args.kwargs)
        self.assertEqual(worker.cycle(),'waiting')
        self.assertEqual(http.request.call_count,2)

    def test_quota_reserved_before_failure(self):
        worker=self.module.Worker(self.config,Path(self.tmp)/'state.json',session=Mock())
        worker.api=Mock(return_value={'enabled':True,'available_slots':['slot']})
        worker.search=Mock(side_effect=ValueError('Unavailable'))
        with self.assertRaises(ValueError):worker.cycle()
        self.assertEqual(worker.state['sessions'],1)
        self.assertEqual(worker.cycle(),'waiting')

    def test_config_rejects_cloud_and_insecure_origin(self):
        path=Path(self.tmp)/'config.json'
        for updates in ({'app_url':'http://example.com'},{'model':'model:cloud'},{'worker_token':''}):
            path.write_text(json.dumps({**self.config,**updates}))
            with self.assertRaises(ValueError):self.module.load_config(path)

    def test_full_slots_skip_search(self):
        worker=self.module.Worker(self.config,Path(self.tmp)/'state.json',session=Mock())
        worker.api=Mock(return_value={'enabled':True,'available_slots':[]})
        worker.search=Mock()
        self.assertEqual(worker.cycle(),'paused_or_full')
        worker.search.assert_not_called()

    def test_cloud_model_rejected_even_in_benchmark(self):
        worker=self.module.Worker({**self.config,'model':'qwen:cloud'},Path(self.tmp)/'state.json',session=Mock())
        with self.assertRaises(ValueError):worker.generate({},SOURCES)
        worker.http.post.assert_not_called()

class StrategyQualityTests(unittest.TestCase):
    def test_rejects_benchmark_volume_units(self):
        with self.assertRaisesRegex(ValueError,'Divide matching'):
            lab.validate({**RULE,'entry':'liquidity >= 50000 and volume_m5_usd / volume_ratio > 1'})

    def test_rejects_shadowed_trailing_branch(self):
        with self.assertRaisesRegex(ValueError,'Profit target'):
            lab.validate({**RULE,'exit':'gain >= 10 or (peak >= 10 and gain <= peak - 5)'})

    def test_accepts_direct_volume_ratio_and_working_trailing_exit(self):
        rule={**RULE,'entry':'liquidity >= 50000 and volume_ratio >= 1.5 and rsi > 30'}
        lab.validate(rule)
        self.assertTrue(lab.run(rule['exit'],{'gain':7,'peak':12},True))
        self.assertFalse(lab.run(rule['exit'],{'gain':11,'peak':12},True))
        self.assertTrue(lab.run(rule['exit'],{'gain':15,'peak':15},True))

    def test_rejects_other_mixed_units_but_accepts_buy_sell_ratio(self):
        for entry in ('age > liquidity','buys_m5 + volume_m5_usd > 10','rsi * liquidity > 100'):
            with self.assertRaises(ValueError):lab.validate({**RULE,'entry':entry})
        lab.validate({**RULE,'entry':'sells_m5 > 0 and buys_m5 / sells_m5 > 1.5'})

    def test_rejects_exact_second_benchmark_with_nested_time_condition(self):
        rule={'name':'Dynamic Volume RSI Strategy','hypothesis':'Untested oversold hypothesis',
              'entry':'liquidity >= 50000 and volume_m5_usd >= 10000 and volume_ratio >= 1.5 and rsi < 20',
              'exit':'gain >= 10 or (peak >= 10 and gain <= peak - 5) and held_seconds <= 3600'}
        with self.assertRaisesRegex(ValueError,'Profit target'):
            lab.validate(rule)

    def test_conflict_detected_inside_shared_outer_conditions(self):
        with self.assertRaisesRegex(ValueError,'Profit target'):
            lab.validate({**RULE,'exit':'held_seconds < 1800 and (gain >= 10 or (peak >= 10 and gain <= peak - 5))'})
        lab.validate({**RULE,'exit':'held_seconds < 1800 and (gain >= 20 or (peak >= 10 and gain <= peak - 5))'})

    def test_different_profit_time_gate_does_not_imply_shadow(self):
        lab.validate({**RULE,'exit':'(gain >= 10 and held_seconds > 1800) or (peak >= 10 and gain <= peak - 5)'})
