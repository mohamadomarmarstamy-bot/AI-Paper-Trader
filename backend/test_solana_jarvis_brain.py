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
