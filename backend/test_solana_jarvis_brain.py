import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

import database
import jarvis_test_bot as api
import jarvis_brain as brain
import jarvis_backlog as backlog
import jarvis_local_lab as local

REAL_MAIL_TICK=brain.mail_tick
NOW=1800000000
SOURCE={'url':'https://docs.python.org/3/library/asyncio.html','title':'Asyncio','snippet':''}

class BrainTests(unittest.TestCase):
    def setUp(self):
        tmp=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database,'DATABASE_PATH',Path(tmp)/'db'))
        self.enterContext(patch.dict(os.environ,{'JARVIS_BRAIN_ENABLED':'true','OPENAI_API_KEY':'private',
            'RESEND_API_KEY':'mail-private','HEALTH_ALERT_EMAIL_FROM':'jarvis@example.com','HEALTH_ALERT_EMAIL_TO':'owner@example.com'}))
        self.enterContext(patch.object(brain,'mail_tick'))
        with api.connection() as conn,conn:
            state,_=api.read(conn);state.update(enabled=True,last_cycle=NOW)
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
    def proposal_id(self):
        with api.connection() as conn:return conn.execute('SELECT id FROM brain_proposals').fetchone()[0]

    def state(self):
        with api.connection() as conn:return api.read(conn)[0]
    def plan(self,**changes):
        return dict({'summary':'Check the scheduler','hypothesis':'Provider pressure may delay checks',
            'action':'monitor','checks':['Measure overdue checks'],'strategy':None,'patch':None,'sources':[SOURCE]},**changes)
    def confirm(self,kind,start=NOW):
        with patch.object(brain,'symptoms',return_value=({kind},{})):
            for at in range(start,start+121,30):brain.observe(api,at)
        with api.connection() as conn:return dict(conn.execute('SELECT * FROM brain_incidents').fetchone())
    def test_incident_confirmation_pause_and_deduplicated_mail(self):
        incident=self.confirm('exit_backlog')
        self.assertEqual(incident['status'],'investigating')
        self.assertTrue(backlog.paused(self.state(),NOW+121))
        with patch.object(brain,'symptoms',return_value=({'exit_backlog'},{})):
            brain.observe(api,NOW+150)
        with api.connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM brain_incidents').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT count(*) FROM brain_mail').fetchone()[0],1)
    def test_multiple_healthy_checks_resolve_and_preserve_owner_pause(self):
        self.confirm('exit_backlog')
        with api.connection() as conn,conn:
            state,_=api.read(conn);state['entry_pause_until_all']=True
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
        with patch.object(brain,'symptoms',return_value=(set(),{})):
            for at in (NOW+150,NOW+180,NOW+210,NOW+240):brain.observe(api,at)
            self.assertIn('brain_pause',self.state())
            brain.observe(api,NOW+270)
        state=self.state();self.assertNotIn('brain_pause',state)
        self.assertTrue(state['entry_pause_until_all']);self.assertTrue(backlog.paused(state,NOW+300))
        with api.connection() as conn:
            self.assertEqual(conn.execute('SELECT status FROM brain_incidents').fetchone()[0],'resolved')
            self.assertEqual(conn.execute('SELECT count(*) FROM brain_mail').fetchone()[0],2)
    def test_chart_observer_requires_eligible_samples_and_emails_chart_counts(self):
        def observe(start, duration, eligible, complete, problems=()):
            snapshot = {'paper_enabled':True, 'cycle_age':1, 'chart_coverage':{
                'eligible':eligible,'reasons':{'complete':complete}}}
            with patch.object(brain,'symptoms',return_value=(set(problems),snapshot)):
                for offset in range(0,duration+1,30):
                    brain.observe(api,start+offset)
        observe(NOW,600,23,2,['chart_data_gap'])
        observe(NOW+630,180,4,3)
        with api.connection() as conn:
            self.assertIsNone(conn.execute('SELECT resolved FROM brain_incidents').fetchone()[0])
            self.assertEqual(conn.execute("SELECT count(*) FROM improvement_tasks WHERE status='observed_recovery'").fetchone()[0],0)
        observe(NOW+840,120,20,5)
        with api.connection() as conn:
            body=json.loads(conn.execute("SELECT payload FROM brain_mail WHERE id LIKE '%/resolved'").fetchone()[0])['text']
        self.assertIn('2/23 to 5/20',body)
        self.assertNotIn('exit-check delay',body)

    def test_chart_review_requires_measured_usefulness_sources_and_binding_cap(self):
        options = [dict(name=name,source=url,price='fixture price',quota='fixture quota',
                        coverage='same-pool candles',unknown='eligible overlap',trial='compare six pools')
                   for name,url in [('Codex','https://www.codex.io/pricing'),
                                    ('GeckoTerminal','https://support.coingecko.com/articles/fixture')]]
        sources=[{'url':o['source']} for o in options]
        self.assertEqual(brain.provider_options(options,sources[:1]),[])
        verified=brain.provider_options(options,sources)
        incident={'id':'fixture','kind':'chart_data_gap','opened':NOW-600,
                  'plan':json.dumps({'provider_options':verified,'researched_at':NOW})}
        snapshot={'chart_coverage':{'eligible':23,'reasons':{'complete':2}},
                  'fallback_allowance':{'remaining_monthly_cu':0}}
        with api.connection() as conn,conn:
            brain.provider_decision(conn,incident,snapshot,NOW)
            self.assertFalse(conn.execute('SELECT 1 FROM brain_mail').fetchone())
            conn.executemany('INSERT INTO codex_outcomes(at,complete_rules,matching_rules) VALUES (?,1,0)',[(NOW-i,) for i in range(3)])
            for _ in range(2):brain.provider_decision(conn,incident,snapshot,NOW)
            self.assertEqual(conn.execute('SELECT count(*) FROM brain_mail').fetchone()[0],1)
        post=Mock(return_value=Mock(status_code=202))
        REAL_MAIL_TICK(api,NOW,post=post)
        payload=post.call_args.kwargs['json']
        self.assertEqual(payload['to'],['owner@example.com'])
        self.assertIn('2/23',payload['text'])
        self.assertIn('3 fallback observations',payload['text'])
        self.assertIn('fixture price',payload['html'])

    def test_healthy_monitoring_does_not_invalidate_trading_revision(self):
        with api.connection() as conn:before=api.read(conn)[1]
        with patch.object(brain,'symptoms',return_value=(set(),{})):brain.observe(api,NOW)
        with api.connection() as conn:self.assertEqual(api.read(conn)[1],before)
    def test_short_spike_does_not_alert_and_owner_stop_does_not_resolve(self):
        with patch.object(brain,'symptoms',return_value=({'worker_stalled'},{})):brain.observe(api,NOW)
        with patch.object(brain,'symptoms',return_value=(set(),{})):brain.observe(api,NOW+30)
        with api.connection() as conn:self.assertEqual(conn.execute('SELECT count(*) FROM brain_incidents').fetchone()[0],0)
        self.confirm('exit_backlog',NOW+60)
        with api.connection() as conn,conn:
            state,_=api.read(conn);state['enabled']=False;conn.execute('UPDATE run SET state=?',(json.dumps(state),))
        with patch.object(brain,'symptoms',return_value=(set(),{})):
            for at in range(NOW+210,NOW+600,30):brain.observe(api,at)
        with api.connection() as conn:self.assertIsNone(conn.execute('SELECT resolved FROM brain_incidents').fetchone()[0])
    def test_persistent_issue_escalates_once(self):
        self.confirm('exit_backlog')
        with patch.object(brain,'symptoms',return_value=({'exit_backlog'},{})):
            brain.observe(api,NOW+4000);brain.observe(api,NOW+4100)
        with api.connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM brain_mail').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT status FROM brain_incidents').fetchone()[0],'needs_attention')
    def test_budget_persists_caps_daily_and_monthly_and_keeps_failed_reservations(self):
        for i in range(4):self.assertIsNotNone(brain.reserve(api,NOW+i,'test'))
        self.assertIsNone(brain.reserve(api,NOW+20,'test'))
        count=4
        for day in range(1,28):
            for i in range(4):
                if brain.reserve(api,NOW+day*86400+i,'test'):count+=1
                if count==50:break
            if count==50:break
        self.assertEqual(count,50)
        self.assertIsNone(brain.reserve(api,NOW+14*86400,'test'))
        with api.connection() as conn:
            self.assertEqual(conn.execute('SELECT sum(reserved_cents) FROM brain_calls').fetchone()[0],2500)
    def response(self,plan):
        data={'status':'completed','usage':{'input_tokens':100,'output_tokens':200},'output':[
            {'type':'web_search_call','status':'completed','action':{'sources':[SOURCE]}},
            {'type':'message','content':[{'type':'output_text','text':json.dumps(plan)}]}]}
        r=Mock(status_code=200,content=b'{}');r.json.return_value=data;return r
    def provider_episodes(self):
        with api.connection() as conn,conn:
            for i,age in enumerate((7200,1800)):
                conn.execute("""INSERT INTO brain_incidents(id,kind,opened,last_seen,status,resolved)
                    VALUES (?,'chart_data_gap',?,?,'resolved',?)""",(str(i),NOW-age,NOW-age+60,NOW-age+180))
            conn.execute("INSERT OR REPLACE INTO research_tasks VALUES ('brain-strategy',?)",(NOW+86400,))

    def test_provider_followup_survives_short_recoveries_and_is_read_only(self):
        with api.connection() as conn,conn:
            conn.execute("INSERT INTO research_tasks VALUES ('brain-strategy',?)",(NOW+86400,))
        for start in (NOW,NOW+1800):
            def observe(duration,at,complete,problems):
                snap={'paper_enabled':True,'cycle_age':1,'chart_coverage':{'eligible':20,'reasons':{'complete':complete}}}
                with patch.object(brain,'symptoms',return_value=(problems,snap)):
                    for delta in range(0,duration+1,30):brain.observe(api,at+delta)
            observe(600,start,1,{'chart_data_gap'})
            self.assertTrue(brain.build_job(api,start+600)['local_reasoning'])
            observe(120,start+630,5,set())
        when=NOW+4300
        options=[dict(name=n,price='fixture',quota='fixture',coverage='pool candles',unknown='overlap',
            trial='compare matched pools',source=u) for n,u in [('Codex','https://www.codex.io/pricing'),
            ('GeckoTerminal','https://support.coingecko.com/articles/fixture')]]
        response=self.response({'provider_options':options+[{'untrusted-field':'PRIVATE-RAW-MARKER'}]})
        output=response.json.return_value['output']
        output[1]['content'][0]['annotations']=[{'type':'url_citation','url':options[0]['source']}]
        output[0]['action']['sources']=[{'url':f'https://docs.codex.io/fixture/{i}'} for i in range(12)]
        output[0]['action']['sources'].append({'url':options[1]['source']})
        post=Mock(return_value=response)
        before=self.state()
        with patch.object(brain,'symptoms',return_value=(set(),{})),patch.object(brain,'admit_proposal') as admit:
            brain.cloud_tick(api,clock=lambda:when,researcher=lambda a,j,t:brain.research_job(a,j,t,post))
            self.assertIsNone(brain.build_job(api,when+181))
            admit.assert_not_called()
        with api.connection() as conn:
            self.assertEqual(brain.status(conn,when)['provider_research']['options'],options)
            self.assertEqual(conn.execute("SELECT count(*) FROM brain_incidents WHERE resolved IS NOT NULL AND attempts=1").fetchone()[0],2)
            self.assertEqual(conn.execute("SELECT count(*) FROM brain_calls WHERE purpose='provider_followup'").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT count(*) FROM brain_mail WHERE id LIKE '%/provider-review'").fetchone()[0],0)
        self.assertEqual(self.state(),before)
        self.assertEqual(post.call_count,1)
        for target in brain.PROVIDER_DOCS.values():
            self.assertIn(target,post.call_args.kwargs['json']['instructions'])
        self.assertNotIn('code',json.loads(post.call_args.kwargs['json']['input']))
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient
        from solana_meme_routes import build_router
        app=FastAPI()
        def require_owner(request):
            if request.headers.get('x-test-owner')!='yes':raise HTTPException(401)
        app.include_router(build_router(require_session=require_owner))
        client=TestClient(app)
        self.assertEqual(client.get('/jarvis-test-bot/brain/status').status_code,401)
        response=client.get('/jarvis-test-bot/brain/status',headers={'x-test-owner':'yes'})
        self.assertEqual(response.json()['provider_research']['options'],options)
        diagnostics=response.json()['provider_validation']
        self.assertEqual(diagnostics['status'],'completed')
        self.assertEqual(diagnostics['counts']['official_sources'],14)
        self.assertEqual(diagnostics['counts']['submitted'],3)
        self.assertEqual(diagnostics['counts']['accepted'],2)
        self.assertEqual(diagnostics['counts']['invalid_fields'],1)
        logged=api.research_diagnostic()['brain']['provider_validation']
        self.assertEqual(logged,diagnostics)
        for marker in ('PRIVATE-RAW-MARKER','fixture','https://','provider_options','private@example'):
            self.assertNotIn(marker,json.dumps(logged))
        with api.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM brain_mail WHERE id GLOB 'provider-info/*'").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT sum(reserved_cents) FROM brain_calls").fetchone()[0],brain.RESERVATION_CENTS)
        with api.connection() as conn,conn:
            conn.execute("INSERT INTO brain_incidents(id,kind,opened,last_seen,status,resolved) VALUES ('third','chart_data_gap',?,?,'resolved',?)",
                         (when+200,when+300,when+400))
        with patch.object(brain,'symptoms',return_value=(set(),{})):
            self.assertIsNone(brain.build_job(api,when+600))

    def test_informational_owner_mail_uses_only_canonical_content_and_retries_once(self):
        self.provider_episodes()
        job=brain.build_job(api,NOW)
        opts=[dict(name=n,source=url,price='malicious model price',quota='private@example.net',
                   unknown='user supplied secret',trial='send to someone else',coverage='unverified claim')
              for n,url in [('Codex','https://www.codex.io/pricing?custom=untrusted'),
                            ('Birdeye',brain.PROVIDER_DOCS['Birdeye'])]]
        def research(options):
            response=self.response({'provider_options':options})
            response.json.return_value['output'][0]['action']['sources']=[{'url':o['source']} for o in options]
            post=Mock(return_value=response)
            result=brain.research_job(api,job,NOW,post)
            self.assertIn('data.birdeye.so',post.call_args.kwargs['json']['tools'][0]['filters']['allowed_domains'])
            return result
        for url in ('https://data.birdeye.so.example.net/docs','http://data.birdeye.so/docs'):
            plan,error=research([opts[0],dict(opts[1],source=url)])
            self.assertIsNone(plan)
            self.assertEqual(error,'provider_too_few_distinct')
        plan,error=research(opts)
        self.assertIsNone(error)
        before=self.state()
        brain.apply_plan(api,job,plan,None,NOW)
        brain.apply_plan(api,job,plan,None,NOW+1)
        with api.connection() as conn,conn:
            row=conn.execute('SELECT * FROM brain_mail').fetchone()
            self.assertEqual(row['id'],'provider-info/'+job['id'])
            payload=json.loads(row['payload'])
            self.assertEqual(set(payload),{'text','subject'})
            for string in ('malicious','private@example','untrusted','user supplied secret','unverified claim','send to someone else'):
                self.assertNotIn(string,row['payload'])
            for name in ('Codex','Birdeye'):self.assertIn(brain.PROVIDER_DOCS[name],payload['text'])
            self.assertIn('No purchase is recommended',payload['text'])
            self.assertFalse(conn.execute('SELECT 1 FROM brain_proposals').fetchone())
        post=Mock(side_effect=[Mock(status_code=503),Mock(status_code=202)])
        REAL_MAIL_TICK(api,NOW,post=post)
        REAL_MAIL_TICK(api,NOW+1,post=post)
        REAL_MAIL_TICK(api,NOW+61,post=post)
        REAL_MAIL_TICK(api,NOW+120,post=post)
        self.assertEqual(post.call_count,2)
        self.assertEqual(post.call_args_list[0],post.call_args_list[1])
        sent=post.call_args.kwargs['json']
        self.assertEqual(sent['to'],['owner@example.com'])
        self.assertEqual(set(sent),{'subject','text','from','to'})
        with api.connection() as conn,conn:
            conn.execute("UPDATE brain_incidents SET resolved=NULL,status='investigating' WHERE id='1'")
            brain.queue_provider_information(conn,'different-claim',opts,NOW+121)
            self.assertEqual(conn.execute('SELECT count(*) FROM brain_mail').fetchone()[0],1)
        self.assertEqual(self.state(),before)

    def test_shortlist_mail_rechecks_measured_recurrence_at_completion(self):
        self.provider_episodes()
        job=brain.build_job(api,NOW)
        with api.connection() as conn,conn:
            conn.execute("DELETE FROM brain_incidents WHERE id='1'")
        opts=[dict(name=n,price='unknown',quota='unknown',coverage='unknown',unknown='overlap',trial='paper',source=u)
              for n,u in list(brain.PROVIDER_DOCS.items())[:2]]
        plan={'provider_options':opts,'sources':[{'url':o['source']} for o in opts]}
        brain.apply_plan(api,dict(job,confirmed_episodes=900),plan,None,NOW)
        with api.connection() as conn:
            self.assertEqual(brain.status(conn,NOW)['provider_research']['status'],'completed')
            self.assertFalse(conn.execute('SELECT 1 FROM brain_mail').fetchone())

    def test_provider_candidate_rejection_counts_and_nonprovider_validator_is_strict(self):
        opts=[dict(name=n,source=url,price='unknown',quota='unknown',coverage='unknown',
                   unknown='pool overlap',trial='paper') for n,url in brain.PROVIDER_DOCS.items()]
        sources=[{'url':o['source']} for o in opts]
        cases=[
            ([opts[0]],'accepted',1),
            ([opts[0],opts[0]],'duplicates',1),
            ([opts[0],dict(opts[1],source='https://docs.codex.io/missing')],'unattested',1),
            ([opts[0],dict(opts[1],source='https://docs.codex.io.example.com/pricing')],'invalid_identity',1),
            ([opts[0],dict(opts[1],name='Other')],'invalid_identity',1),
            ([opts[0],dict(opts[1],quota={})],'invalid_fields',1)]
        for options,key,expected in cases:
            counts=dict.fromkeys(('duplicates','unattested','invalid_identity','invalid_fields'),0)
            result,status=brain.validate_provider_followup({'provider_options':options},sources,counts)
            self.assertEqual((result,status),([],'provider_too_few_distinct'))
            self.assertEqual(counts[key],expected)
        self.assertEqual(brain.provider_options([opts[0],opts[1],{}],sources),[])

    def test_provider_request_schema_and_fail_closed_responses(self):
        self.provider_episodes()
        job=brain.build_job(api,NOW)
        for index,(case,expected) in enumerate((('unsupported','http_400'),('refusal','provider_refused'),
                                                ('incomplete','incomplete'))):
            response=self.response({})
            if case=='unsupported':response.status_code=400
            elif case=='refusal':
                response.json.return_value['output'][1]['content']=[{'type':'refusal','refusal':'private fixture'}]
            else:response.json.return_value['status']='incomplete'
            post=Mock(return_value=response)
            plan,error=brain.research_job(api,job,NOW+index,post)
            self.assertEqual((plan,error),(None,expected))
            post.assert_called_once()
            req=post.call_args.kwargs['json']
            fmt=req['text']['format']
            self.assertEqual((fmt['type'],fmt['strict'],req['tools'][0]['type']),('json_schema',True,'web_search'))
            root=fmt['schema']
            self.assertFalse(root['additionalProperties'])
            options=root['properties']['provider_options']
            self.assertEqual(options['maxItems'],3)
            candidate=options['items']
            self.assertFalse(candidate['additionalProperties'])
            self.assertEqual(set(candidate['required']),set(candidate['properties']))
            self.assertEqual(set(candidate['required']),{'name','price','quota','coverage','unknown','trial','source'})
            self.assertEqual(set(candidate['properties']['name']['enum']),set(brain.PROVIDER_DOCS))
            for key in set(candidate['required'])-{'name'}:
                self.assertEqual(candidate['properties'][key],{'type':'string','pattern':'^.{1,500}$'})
        post=Mock(return_value=self.response({}))
        brain.research_job(api,{'kind':'strategy'},NOW+10,post)
        self.assertNotIn('text',post.call_args.kwargs['json'])
        with api.connection() as conn:
            self.assertFalse(conn.execute('SELECT 1 FROM brain_mail').fetchone())

    def test_provider_parse_failure_is_private_and_preserves_six_hour_retry(self):
        self.provider_episodes()
        job=brain.build_job(api,NOW)
        response=self.response({})
        action,message=response.json.return_value['output']
        action['action']['sources']=[{'url':brain.PROVIDER_DOCS['Birdeye']}]
        message['content'][0]['text']='PRIVATE-INVALID-JSON'
        plan,error=brain.research_job(api,job,NOW,Mock(return_value=response))
        self.assertEqual((plan,error),(None,'provider_invalid_json'))
        brain.apply_plan(api,job,plan,error,NOW+30)
        with api.connection() as conn:
            review=brain.status(conn,NOW+30)
            self.assertEqual(review['provider_research']['next_attempt'],NOW+30+6*3600)
            self.assertEqual(review['provider_validation']['counts']['accepted'],0)
            self.assertNotIn('PRIVATE-INVALID-JSON',json.dumps(review))
            self.assertFalse(conn.execute('SELECT 1 FROM brain_mail').fetchone())

    def test_provider_watch_expires_and_yields_to_strategy_urgent_work_and_global_lease(self):
        self.provider_episodes()
        with api.connection() as conn,conn:
            conn.execute("DELETE FROM research_tasks WHERE name='brain-strategy'")
        self.assertEqual(brain.build_job(api,NOW)['kind'],'strategy')
        self.assertIsNone(brain.build_job(api,NOW+60))
        with patch.object(brain,'symptoms',return_value=({'exit_backlog'},{})):
            self.assertIsNone(brain.build_job(api,NOW+190))
        with patch.object(brain,'symptoms',return_value=(set(),{})):
            self.assertIsNone(brain.build_job(api,NOW+14400))
        with api.connection() as conn:
            self.assertIsNone(brain.status(conn,NOW)['provider_research'])

    def test_provider_budget_denial_failure_and_deduplication(self):
        self.provider_episodes()
        job=brain.build_job(api,NOW)
        self.assertEqual(job['kind'],'provider_followup')
        for i in range(3):self.assertTrue(brain.reserve(api,NOW,'fixture'))
        post=Mock()
        result,error=brain.research_job(api,job,NOW,post)
        post.assert_not_called()
        self.assertEqual(error,'budget_exhausted')
        brain.apply_plan(api,job,result,error,NOW)
        self.assertIsNone(brain.build_job(api,NOW+180))
        with api.connection() as conn:
            self.assertEqual(brain.status(conn,NOW)['provider_research']['status'],'budget_exhausted')
        self.assertTrue(brain.reserve(api,NOW,'strategy'))

    def test_provider_rejects_action_payload_and_ignores_stale_result(self):
        self.provider_episodes()
        job=brain.build_job(api,NOW)
        response=self.response({'action':'pause_entries','provider_options':[]})
        response.json.return_value['output'][0]['action']['sources']=[{'url':'https://www.codex.io/pricing'}]
        result,error=brain.research_job(api,job,NOW,Mock(return_value=response))
        self.assertIsNone(result)
        self.assertEqual(error,'provider_response_shape')
        brain.apply_plan(api,{**job,'id':'old-claim'},None,error,NOW)
        with api.connection() as conn:
            self.assertEqual(brain.status(conn,NOW)['provider_research']['status'],'researching')
        brain.apply_plan(api,job,None,error,NOW)
        self.assertIsNone(self.state().get('brain_pause'))

    def test_local_investigation_reasons_without_search(self):
        plan=self.plan()
        plan.pop('sources')
        response=self.response(plan)
        response.json.return_value['output'].pop(0)
        post=Mock(return_value=response)
        result,error=brain.research_job(api,{'kind':'chart_data_gap','local_reasoning':True},NOW,post)
        self.assertIsNone(error)
        self.assertEqual(result['sources'],[])
        self.assertNotIn('tools',post.call_args.kwargs['json'])

    def test_research_requires_sources_and_never_returns_credentials(self):
        plan=self.plan();plan.pop('sources')
        post=Mock(return_value=self.response(plan))
        result,error=brain.research_job(api,{'kind':'engine_error'},NOW,post)
        self.assertIsNone(error);self.assertEqual(result['sources'],[SOURCE])
        request=post.call_args.kwargs
        self.assertNotIn('private',json.dumps(request['json']))
        self.assertFalse(request['allow_redirects'])
        self.assertEqual(request['json']['max_tool_calls'],2)
        self.assertIn('filters',request['json']['tools'][0])
        with api.connection() as conn:
            self.assertEqual(conn.execute('SELECT status FROM brain_calls').fetchone()[0],'completed')
        r=self.response(plan);r.json.return_value['output'][0]['action']['sources']=[]
        result,error=brain.research_job(api,{'kind':'strategy'},NOW+2,Mock(return_value=r))
        self.assertIsNone(result);self.assertEqual(error,'no_research_sources')
    def test_cited_response_without_search_action_sources_runs_through_paper_worker(self):
        rule={'name':'Pressure trial','hypothesis':'Test order pressure with available market fields',
              'entry':'liquidity >= 50000 and buys_m5 > sells_m5 * 1.5','exit':'gain >= 15'}
        plan=self.plan(strategy=rule);plan.pop('sources')
        r=self.response(plan)
        action,message=r.json.return_value['output']
        action['action']={'type':'search'}
        message['content'][0]['annotations']=[
            {'type':'url_citation','url':SOURCE['url'],'title':SOURCE['title']},
            {'type':'url_citation','url':SOURCE['url'],'title':'Duplicate'},
            {'type':'url_citation','url':'https:///missing-host','title':'Invalid'},
            {'type':'url_citation','url':'https://user:pass@example.org','title':'Invalid'}]
        post=Mock(return_value=r)
        brain.cloud_tick(api,clock=lambda:NOW,
                         researcher=lambda a,j,t:brain.research_job(a,j,t,post))
        with api.connection() as conn:
            row=conn.execute('SELECT * FROM brain_proposals').fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['status'],'waiting_slot')
            self.assertEqual(json.loads(row['data'])['sources'],[SOURCE])
            self.assertEqual(conn.execute('SELECT count(*) FROM local_strategies').fetchone()[0],0)
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            state['watch']={f'm{i}':{'mint':f'm{i}','pair':f'p{i}','first':NOW+1,'last':NOW+61,
                           'liquidity_usd':75000,'age_minutes':8,'buys_m5':20,'sells_m5':8}
                           for i in range(10)}
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
        with patch.object(api,'entry_evidence',side_effect=lambda conn,row,now:{'market':row}):
            brain.monitor_tick(api,clock=lambda:NOW+61)
        with api.connection() as conn:
            rule_row=conn.execute('SELECT * FROM local_strategies').fetchone()
            self.assertEqual(rule_row['status'],'testing')
            self.assertEqual(rule_row['created'],NOW+61)
            self.assertEqual(conn.execute('SELECT status FROM brain_proposals').fetchone()[0],'testing')
            self.assertEqual(conn.execute('SELECT count(*) FROM brain_calls').fetchone()[0],1)
        self.assertIsNone(self.state()['strategy'])

    def test_citations_require_completed_search_and_diagnostic_domain(self):
        plan=self.plan();plan.pop('sources')
        for offset,status,quote in [(0,'in_progress',SOURCE),(1,'completed',{'url':'https://docs.python.org.example.com/','title':'Lookalike'})]:
            with self.subTest(status=status):
                r=self.response(plan)
                action,message=r.json.return_value['output']
                action.update(status=status,action={'type':'search'})
                message['content'][0]['annotations']=[{'type':'url_citation',**quote}]
                result,error=brain.research_job(api,{'kind':'engine_error'},NOW+offset,Mock(return_value=r))
                self.assertIsNone(result)
                self.assertEqual(error,'no_research_sources')

    def test_ready_proposal_waits_during_paper_pause_and_skips_unfunded_vacancy(self):
        rule={'name':'Pressure trial','hypothesis':'Test order pressure',
              'entry':'liquidity >= 50000 and buys_m5 > sells_m5 * 1.5','exit':'gain >= 15'}
        brain.apply_plan(api,{'kind':'strategy'},self.plan(strategy=rule),None,NOW)
        ident=self.proposal_id()
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            state['bots']['research-01'].update(local_funded=True,cash=1)
            state['entry_pause_until_all']=True
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
        with patch.object(brain.insight,'scouting_status',return_value=[{'id':ident,'data_ready':True}]):
            brain.admit_proposal(api,NOW+1)
            with api.connection() as conn,conn:
                state,_=api.read(conn)
                self.assertEqual(brain.status(conn,NOW+1)['admission_checks'],
                                 [{'reason':'paper_entries_paused','at':NOW+1}])
                self.assertFalse(state.get('local_rules'))
                state.pop('entry_pause_until_all')
                conn.execute('UPDATE run SET state=?',(json.dumps(state),))
            brain.admit_proposal(api,NOW+62)
        self.assertEqual(self.state()['bots']['research-01']['cash'],1)
        with api.connection() as conn:
            row=conn.execute('SELECT slot,status FROM local_strategies').fetchone()
            self.assertEqual(tuple(row),('research-02','testing'))

    def test_failed_cloud_call_consumes_reservation_without_logging_body(self):
        r=Mock(status_code=401,content=b'private')
        result,error=brain.research_job(api,{'kind':'strategy'},NOW,Mock(return_value=r))
        self.assertEqual(error,'authentication_or_access');self.assertIsNone(result)
        with api.connection() as conn:
            value=brain.status(conn,NOW)
            self.assertEqual(value['reserved_this_month_usd'],.5)
            self.assertNotIn('private',json.dumps(value))
    def test_invalid_action_and_code_path_are_rejected(self):
        plan=self.plan(action='erase_losses');plan.pop('sources')
        with self.assertRaises(ValueError):brain.validate_plan(plan,'engine_error')
        with self.assertRaises(ValueError):brain.validate_patch({'file':'../main.py','function':'login','replacement':'pass'})
        with self.assertRaises(ValueError):brain.validate_patch({'file':'jarvis_test_bot.py','function':'exit_queue','replacement':'import os\ndef exit_queue(): pass'})
    def test_patch_is_syntax_checked_and_never_executed_or_written(self):
        before=(Path(brain.__file__).parent/'jarvis_test_bot.py').read_bytes()
        value=brain.validate_patch({'file':'jarvis_test_bot.py','function':'exit_queue','replacement':
            'def exit_queue(jobs, now):\n    raise RuntimeError("must never execute")\n'})
        self.assertEqual(value['validation'],'syntax_checked_only')
        self.assertIn('must never execute',value['diff'])
        self.assertEqual((Path(brain.__file__).parent/'jarvis_test_bot.py').read_bytes(),before)
    def test_strategy_failure_status_from_scheduled_review(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from solana_meme_routes import build_router
        app=FastAPI()
        app.include_router(build_router(require_session=lambda request: None))
        client=TestClient(app)
        cases=('strategy_transport_error','strategy_invalid_response','strategy_api_failure','strategy_refused',
               'incomplete','strategy_invalid_json','strategy_plan_rejected','no_research_sources','completed')
        for day,expected in enumerate(cases):
            with self.subTest(expected=expected):
                now=NOW+day*86401
                plan=self.plan()
                del plan['sources']
                if expected=='strategy_plan_rejected':plan['strategy']={'private fixture':'bad rule'}
                response=self.response(plan)
                post=Mock(return_value=response)
                data=response.json.return_value
                if expected=='strategy_transport_error':post.side_effect=TimeoutError('private fixture')
                if expected=='strategy_invalid_response':response.json.side_effect=ValueError('private fixture')
                if expected=='strategy_api_failure':data['status']='failed'
                if expected=='incomplete':data['status']='incomplete'
                if expected=='strategy_refused':data['output'][1]['content']=[{'type':'refusal','refusal':'private fixture'}]
                if expected=='strategy_invalid_json':data['output'][1]['content'][0]['text']='private fixture'
                if expected=='no_research_sources':data['output'][0]['action']['sources']=[]
                with patch.object(brain,'symptoms',return_value=(set(),{})):
                    brain.cloud_tick(api,clock=lambda:now,researcher=lambda a,j,t:brain.research_job(a,j,t,post))
                post.assert_called_once()
                view=client.get('/jarvis-test-bot/brain/status').json()
                self.assertEqual(view['last_call'],{'at':now,'purpose':'strategy','status':expected})
                self.assertNotIn('private fixture',json.dumps(view))
                self.assertEqual(view['strategy_due_at'],now+86400)
                with api.connection() as conn:
                    call=conn.execute('SELECT * FROM brain_calls WHERE at=?',(now,)).fetchone()
                    self.assertEqual(call['reserved_cents'],brain.RESERVATION_CENTS)
                    tokens=(None,None) if day<2 else (100,200)
                    self.assertEqual((call['input_tokens'],call['output_tokens']),tokens)
                    self.assertFalse(conn.execute('SELECT 1 FROM brain_proposals').fetchone())

    def test_daily_strategy_jobs_and_incident_retry_are_bounded(self):
        with patch.object(brain,'symptoms',return_value=(set(),{})):
            job=brain.build_job(api,NOW);self.assertEqual(job['kind'],'strategy')
            self.assertIsNone(brain.build_job(api,NOW+181))
        incident=self.confirm('engine_error')
        with patch.object(brain,'symptoms',return_value=({'engine_error'},{})):
            self.assertEqual(brain.build_job(api,NOW+200)['id'],incident['id'])
            self.assertIsNone(brain.build_job(api,NOW+400))
            self.assertEqual(brain.build_job(api,NOW+4000)['id'],incident['id'])
            self.assertEqual(brain.build_job(api,NOW+8000)['id'],incident['id'])
            self.assertIsNone(brain.build_job(api,NOW+12000))
    def test_model_cannot_pause_healthy_engine_or_change_accounting(self):
        incident=self.confirm('engine_error')
        state=self.state()
        with patch.object(brain,'symptoms',return_value=({'engine_error'},{})):
            brain.apply_plan(api,{'kind':'engine_error','id':incident['id']},self.plan(action='pause_entries'),None,NOW+150)
        self.assertEqual(self.state(),state)
    def test_strategy_proposal_uses_forward_slot_validation_and_preserves_promotion_rules(self):
        rule={'name':'Pressure test','hypothesis':'Test pressure without unavailable RSI',
              'entry':'liquidity >= 50000 and buys_m5 > sells_m5 * 1.5','exit':'gain >= 15'}
        plan=self.plan(strategy=rule)
        brain.apply_plan(api,{'kind':'strategy'},plan,None,NOW)
        with patch.object(brain.insight,'scouting_status',return_value=[{'id':self.proposal_id(),'data_ready':True}]):
            brain.admit_proposal(api,NOW+30)
        with api.connection() as conn:
            row=conn.execute('SELECT * FROM local_strategies').fetchone()
            self.assertEqual(row['status'],'testing');self.assertEqual(row['created'],NOW+30)
            self.assertEqual(conn.execute('SELECT status FROM brain_proposals').fetchone()[0],'testing')
        self.assertEqual(local.MIN_COINS,100);self.assertEqual(local.MAX_OPEN,4)
    def test_incident_mail_uses_stable_idempotency_and_retries_without_duplicate_outbox(self):
        incident=self.confirm('exit_backlog')
        post=Mock(return_value=Mock(status_code=503))
        REAL_MAIL_TICK(api,NOW+121,post)
        with api.connection() as conn:
            row=conn.execute('SELECT * FROM brain_mail').fetchone()
            self.assertIsNone(row['sent']);self.assertEqual(row['attempts'],1)
        post.return_value.status_code=200
        REAL_MAIL_TICK(api,NOW+200,post)
        self.assertEqual(post.call_args.kwargs['headers']['Idempotency-Key'],'jarvis-brain/'+incident['id']+'/opened')
        REAL_MAIL_TICK(api,NOW+300,post)
        self.assertEqual(post.call_count,2)
    def test_investigation_routes_require_owner_session(self):
        from fastapi import FastAPI,HTTPException
        from fastapi.testclient import TestClient
        from solana_meme_routes import build_router
        def require(request):
            if request.headers.get('x-test-owner')!='yes':raise HTTPException(401)
        app=FastAPI();app.include_router(build_router(require_session=require));client=TestClient(app)
        for path in ('/jarvis-test-bot/brain','/jarvis-test-bot/brain/status'):
            self.assertEqual(client.get(path).status_code,401)
            self.assertEqual(client.get(path,headers={'x-test-owner':'yes'}).status_code,200)
        result=client.get('/jarvis-test-bot/brain/status',headers={'x-test-owner':'yes'})
        self.assertEqual(result.headers['cache-control'],'no-store')
        self.assertNotIn('private',result.text)

    def test_disabled_worker_does_nothing(self):
        researcher=Mock()
        with patch.dict(os.environ,{'JARVIS_BRAIN_ENABLED':'false'}):brain.tick(api,clock=lambda:NOW,researcher=researcher)
        researcher.assert_not_called()

if __name__=='__main__':unittest.main()
