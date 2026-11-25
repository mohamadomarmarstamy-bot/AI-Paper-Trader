import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import database
import jarvis_test_bot as api
import jarvis_brain as brain
import jarvis_repair as repair
import jarvis_repair_learning as learning
import jarvis_evidence as evidence
from test_solana_jarvis_repair import fixture

NOW=1800000000


class RepairLearningTests(unittest.TestCase):
    def setUp(self):
        tmp=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database,'DATABASE_PATH',Path(tmp)/'db'))
        self.enterContext(patch.dict(os.environ,{'JARVIS_BRAIN_ENABLED':'true','JARVIS_PAPER_REPAIR_ENABLED':'true'}))
        _,self.value=fixture();self.value['incident_id']='incident'
        with api.connection() as conn,conn:
            state,_=api.read(conn);state.update(enabled=True,last_cycle=NOW,error='Provider unavailable')
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
            conn.execute("INSERT INTO brain_incidents(id,kind,opened,last_seen,status) VALUES ('incident','engine_error',?,?,'investigating')",(NOW,NOW))
    def remember(self,outcome='tests_failed',checks=None,ident='run'):
        report={'outcome':outcome,'incident_kind':'engine_error','checks':checks or {},
                'baseline':{'cycle_age':2,'secret':'do-not-copy'},'after':{'engine_error_present':False,'token':'do-not-copy'}}
        with api.connection() as conn,conn:learning.remember(conn,ident,self.value,report,NOW)
    def test_exact_failure_stages_and_sanitized_persistent_memory(self):
        for i,(checks,stage) in enumerate((({},'baseline_not_reproduced'),
                    ({'baseline_failed':True},'regression_not_fixed'),
                    ({'baseline_failed':True,'regression_passed':True},'existing_suite_failed'))):
            self.remember(checks=checks,ident=str(i))
            with api.connection() as conn:
                item=next(r for r in learning.lessons(conn) if r['failure_or_verification_stage']==stage)
                self.assertNotIn('do-not-copy',json.dumps(item))
        with api.connection() as conn:self.assertEqual(len(learning.lessons(conn)),3)
    def test_failed_replacement_cannot_bypass_memory_with_whitespace_or_test_rename(self):
        self.remember()
        changed={**self.value,'replacement':self.value['replacement']+'\n','regression_test':'renamed test'}
        with api.connection() as conn:
            self.assertEqual(learning.blocked(conn,changed,NOW+86400),'same_patch_failed_on_same_source')
            self.assertIsNone(learning.blocked(conn,{**changed,'base_sha256':'new-source'},NOW))
            self.assertIsNone(learning.blocked(conn,{**changed,'replacement':'def chart_fresh_for(record, now):\n    return 0'},NOW))
    def test_runner_failure_is_cooldown_not_permanent_code_failure(self):
        self.remember('runner_error')
        with api.connection() as conn:
            self.assertEqual(learning.blocked(conn,self.value,NOW+60),'runner_retry_cooldown')
            self.assertIsNone(learning.blocked(conn,self.value,NOW+3600))
    def test_verified_success_is_recorded_without_promoting_strategy(self):
        self.remember('deployed',dict.fromkeys(('baseline_failed','regression_passed','suite_passed','canary_passed'),True))
        with api.connection() as conn:
            self.assertEqual(learning.lessons(conn)[0]['failure_or_verification_stage'],'live_recovery_verified')
            self.assertIsNone(learning.blocked(conn,self.value,NOW))
            self.assertEqual(conn.execute('SELECT count(*) FROM local_strategies').fetchone()[0],0)
    def test_failed_run_teaches_next_job_and_blocks_duplicate_repair_claim(self):
        with api.connection() as conn,conn:
            conn.execute("INSERT INTO brain_proposals(id,created,kind,status,data) VALUES ('p',?,'code','needs_functional_tests',?)",(NOW,json.dumps(self.value)))
        job=repair.claim(api,{'run_id':'1'},NOW)
        payload={'id':job['id'],'token':job['token'],'outcome':'tests_failed','commit':'',
                 'checks':{'baseline_failed':True,'regression_passed':True,'suite_passed':False,'canary_passed':False}}
        repair.report(api,{'run_id':'1'},payload,NOW+1)
        with api.connection() as conn,conn:
            self.assertEqual(learning.lessons(conn)[0]['failure_or_verification_stage'],'existing_suite_failed')
            self.assertEqual(conn.execute('SELECT next_attempt FROM brain_incidents').fetchone()[0],NOW+301)
            conn.execute("INSERT INTO brain_proposals(id,created,kind,status,data) VALUES ('rename',?,'code','needs_functional_tests',?)",(NOW+2,json.dumps(self.value)))
        self.assertEqual(repair.claim(api,{'run_id':'2'},NOW+2)['status'],'empty')
        job=brain.build_job(api,NOW+302)
        self.assertEqual(job['repair_learning'][0]['failure_or_verification_stage'],'existing_suite_failed')
        self.assertEqual(job['helper_constants']['jarvis_research.py']['FRESH_SECONDS'],180)
        self.assertIn('def chart_is_fresh',job['code']['jarvis_research.py'])
    def test_same_patch_plan_is_retained_as_blocked_artifact(self):
        self.remember()
        plan={'summary':'Try a repair','hypothesis':'Test hypothesis','action':'monitor','checks':[],
              'strategy':None,'patch':self.value,'sources':[]}
        brain.apply_plan(api,{'kind':'engine_error','id':'incident'},plan,None,NOW)
        with api.connection() as conn:self.assertEqual(conn.execute('SELECT status FROM brain_proposals').fetchone()[0],'repeat_blocked')
    def test_temporary_runner_failure_can_rearm_same_proposal_after_cooldown(self):
        self.remember('runner_error')
        plan={'summary':'Try a repair','hypothesis':'Test hypothesis','action':'monitor','checks':[],
              'strategy':None,'patch':self.value,'sources':[]}
        brain.apply_plan(api,{'kind':'engine_error','id':'incident'},plan,None,NOW+60)
        with api.connection() as conn:self.assertEqual(conn.execute('SELECT status FROM brain_proposals').fetchone()[0],'repeat_blocked')
        brain.apply_plan(api,{'kind':'engine_error','id':'incident'},plan,None,NOW+3601)
        with api.connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM brain_proposals').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT status FROM brain_proposals').fetchone()[0],'needs_functional_tests')
    def test_observed_chart_cases_are_bounded_numeric_and_identifier_free(self):
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            for i in range(10):
                state['watch']['private-mint-'+str(i)]={'mint':'private-mint-'+str(i),'pair':'private-pool-'+str(i),'last':NOW,'first':NOW-5,'age_minutes':3}
                conn.execute('INSERT INTO research VALUES (?,?,?,?)',('private-mint-'+str(i),'private-pool-'+str(i),NOW,json.dumps({'chart':{'at':NOW,'value':{'closed_at':NOW-60,'rsi':45,'volume_ratio':None},'token':'do-not-copy'}})))
            cases=learning.chart_cases(api,conn,state,NOW)
        self.assertEqual(len(cases['chart_inputs']),6)
        self.assertEqual(cases['chart_inputs'][0]['closed_candle_age_seconds'],60)
        self.assertFalse(cases['chart_inputs'][0]['volume_ratio_available'])
        self.assertNotIn('private-',json.dumps(cases));self.assertNotIn('do-not-copy',json.dumps(cases))
    def test_partial_data_coverage_gets_measurements_without_claiming_code_failure(self):
        diagnosis=evidence.diagnose({'chart_coverage':{'eligible':30,'reasons':{'complete':2,'zero_volume_baseline':4}}})
        self.assertIn('low_complete_indicator_coverage',diagnosis['observed_facts'])
        self.assertTrue(any('unknown' in s for s in diagnosis['next_measurements']))
        self.assertNotIn('no_complete_candidate_indicators',diagnosis['observed_facts'])
    def test_investigation_retries_stop_at_three_without_changing_reservation_budget(self):
        for at in (NOW,NOW+3601,NOW+7202):self.assertEqual(brain.build_job(api,at)['kind'],'engine_error')
        job=brain.build_job(api,NOW+10803)
        self.assertNotEqual(job and job['kind'],'engine_error')
        self.assertEqual(brain.MONTHLY_CENTS,2500);self.assertEqual(brain.DAILY_CENTS,200)


if __name__=='__main__':unittest.main()
