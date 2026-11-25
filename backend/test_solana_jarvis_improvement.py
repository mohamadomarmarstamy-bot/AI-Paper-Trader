import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import database
import jarvis_test_bot as api
import jarvis_brain as brain
import jarvis_improvement as improvement
import jarvis_repair as repair
import jarvis_repair_policy as policy

NOW=1800000000
class ImprovementTests(unittest.TestCase):
    def setUp(self):
        tmp=self.enterContext(tempfile.TemporaryDirectory());self.enterContext(patch.object(database,'DATABASE_PATH',Path(tmp)/'db'))
        with api.connection() as conn,conn:
            state,_=api.read(conn);state.update(enabled=True,last_cycle=NOW)
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
    def snap(self,wait=0,complete=20):return {'paper_enabled':True,'cycle_age':1,'queue':{'oldest_wait':wait},'chart_coverage':{'eligible':30,'reasons':{'complete':complete}}}
    def observe(self,problems,now,snapshot=None):
        with api.connection() as conn,conn:improvement.observe(conn,set(problems),snapshot or self.snap(),now)
    def task(self,kind):
        with api.connection() as conn:return next(r for r in improvement.status(conn)['tasks'] if r['kind']==kind)
    def test_priorities_deduplication_and_original_baseline_survive_observations(self):
        self.observe(['exit_backlog','engine_error'],NOW,self.snap(300));self.observe(['exit_backlog'],NOW+30,self.snap(200))
        with api.connection() as conn:
            self.assertEqual(improvement.selected(conn)['kind'],'exit_backlog')
            self.assertEqual(len(improvement.status(conn)['tasks']),2)
        self.assertEqual(self.task('exit_backlog')['evidence']['baseline']['oldest_quote_wait'],300)
        self.assertEqual(self.task('exit_backlog')['evidence']['latest']['oldest_quote_wait'],200)
    def test_recovery_requires_sustained_current_observations_and_recurrence_reopens(self):
        self.observe(['engine_error'],NOW)
        for at in (30,60,90,120):self.observe([],NOW+at)
        self.assertNotEqual(self.task('engine_error')['status'],'observed_recovery')
        self.observe([],NOW+150);self.assertEqual(self.task('engine_error')['status'],'observed_recovery')
        self.observe(['engine_error'],NOW+180)
        self.assertEqual(self.task('engine_error')['status'],'queued');self.assertEqual(self.task('engine_error')['recurrences'],1)
    def test_pause_missing_heartbeat_and_sampling_gap_do_not_establish_recovery(self):
        self.observe(['engine_error'],NOW)
        for at in (30,180):self.observe([],NOW+at,{**self.snap(),'paper_enabled':False})
        self.assertIsNone(self.task('engine_error')['healthy_since'])
        self.observe([],NOW+210);self.observe([],NOW+600)
        self.assertNotEqual(self.task('engine_error')['status'],'observed_recovery')
    def test_partial_data_coverage_is_a_goal_and_independent_repair_requires_recovery(self):
        self.observe([],NOW,self.snap(complete=2));self.assertEqual(self.task('data_coverage')['status'],'queued')
        baseline={'paper_enabled':True,'eligible_candidates':30,'complete_candidates':2,'cycle_age':1,'oldest_quote_wait':0}
        self.assertTrue(policy.symptom_present('chart_data_gap',baseline))
        self.assertFalse(policy.goal_met('chart_data_gap',baseline,{**baseline,'complete_candidates':3}))
        self.assertTrue(policy.goal_met('chart_data_gap',baseline,{**baseline,'complete_candidates':6}))
    def test_rechecks_are_hourly_and_report_recurrence_without_rolling_back(self):
        report={'incident_kind':'engine_error','baseline':{},'commit':'a'*40}
        with api.connection() as conn,conn:
            conn.execute("INSERT INTO repair_runs(id,proposal,run_id,token_hash,started,expires,status,report) VALUES ('run','p','1','private',?,?,'deployed',?)",(NOW,NOW+500,json.dumps(report)))
        actual={'paper_enabled':True,'cycle_age':1,'oldest_quote_wait':0,'engine_error_present':True,'complete_candidates':2,'source_sha':'b'*40}
        with patch.object(repair,'health',return_value=actual) as health:
            improvement.recheck(api,NOW);improvement.recheck(api,NOW+30);self.assertEqual(health.call_count,1)
            actual['engine_error_present']=False;improvement.recheck(api,NOW+3600)
        with api.connection() as conn:
            result=improvement.status(conn)['rechecks'][0]
            self.assertEqual(result['status'],'recovery_still_observed');self.assertFalse(result['evidence']['same_release'])
            self.assertEqual(conn.execute('SELECT status FROM repair_runs').fetchone()[0],'deployed')
    def test_brain_job_receives_backlog_without_granting_new_permissions(self):
        self.observe(['engine_error'],NOW)
        with api.connection() as conn,conn:
            conn.execute("INSERT INTO brain_incidents(id,kind,opened,last_seen,status) VALUES ('confirmed','engine_error',?,?,'investigating')",(NOW,NOW))
        job=brain.build_job(api,NOW)
        self.assertEqual(job['selected_improvement']['kind'],'engine_error')
        self.assertEqual(brain.MONTHLY_CENTS,2500)
        self.assertNotIn('jarvis_improvement.py',policy.TARGETS)

if __name__=='__main__':unittest.main()
