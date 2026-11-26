from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import database
import jarvis_test_bot as api
import jarvis_brain as brain
import jarvis_evidence as evidence
import jarvis_repair as repair
import jarvis_repair_policy as policy

NOW=1800000000
RULE={'name':'Pressure candidate','hypothesis':'Test current buying pressure','entry':'liquidity >= 50000 and buys_m5 > sells_m5','exit':'gain >= 15'}

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        tmp=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database,'DATABASE_PATH',Path(tmp)/'db'))
        self.enterContext(patch.dict(os.environ,{'JARVIS_BRAIN_ENABLED':'true'}))
        self.enterContext(patch.object(brain,'mail_tick'))
        with api.connection() as conn,conn:
            state,_=api.read(conn);state.update(enabled=True,last_cycle=NOW,local_rules={'research-01':{
                'id':'active','created':NOW-200,'entry':'rsi < 30','exit':'gain >= 15'}})
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
    def proposal(self,ident='candidate',created=NOW-100,rule=RULE):
        with api.connection() as conn,conn:
            conn.execute("INSERT INTO brain_proposals(id,created,kind,status,data) VALUES (?,?,'strategy','waiting_slot',?)",
                (ident,created,json.dumps({'rule':rule,'sources':[{'url':'https://example.org','title':'Research','snippet':''}]})))
    def watch(self,count=1):
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            rows={str(i):{'mint':str(i),'pair':'pool'+str(i),'first':NOW-60,'last':NOW,'symbol':'Test',
                  'liquidity_usd':80000,'buys_m5':10,'sells_m5':5,'age_minutes':60} for i in range(count)}
            state['watch']=rows;conn.execute('UPDATE run SET state=?',(json.dumps(state),))
        return rows
    def snapshot(self):
        return {'cycle_age':1,'queue':{'oldest_wait':0},'chart_coverage':{'eligible':31,'reasons':{
            'complete':0,'stale_candles':25},'provider':{'median_returned_lag_seconds':500}},
            'fallback_allowance':{'remaining_daily_cu':8,'remaining_monthly_cu':7808}}
    def test_diagnosis_distinguishes_stale_provider_and_exhausted_fallback(self):
        facts=evidence.diagnose(self.snapshot())['observed_facts']
        self.assertIn('provider_returned_stale_candles',facts)
        self.assertIn('fallback_allowance_exhausted',facts)
        self.assertNotIn('exit_checks_overdue',facts)
        self.assertEqual(evidence.diagnose({'chart_coverage':{'provider':{'median_returned_lag_seconds':None}}})['observed_facts'],[])
    def test_chart_investigation_receives_regression_examples_not_authority(self):
        with api.connection() as conn,conn:
            conn.execute("INSERT INTO brain_incidents(id,kind,opened,last_seen,status,next_attempt) "
                         "VALUES ('chart','chart_data_gap',?,?,'investigating',?)",(NOW,NOW,NOW))
        job=brain.build_job(api,NOW)
        lessons=job['repair_regression_examples']
        self.assertEqual(lessons[0]['reference'],'PR #177')
        self.assertEqual(len(lessons[0]['fixtures']),2)
        self.assertIn('not proof of live coverage',lessons[0]['verification'])
        self.assertIn('Do not expand repair scope',lessons[0]['limits'])
        self.assertNotIn('choose_chart_record',policy.TARGETS['jarvis_research.py'])
        self.assertNotIn('record_chart_result',policy.TARGETS['jarvis_research.py'])

    def test_diagnosis_separates_expired_cache_stale_provider_and_retained_refresh(self):
        snapshot={'chart_coverage':{'eligible':12,'reasons':{'complete':4,'stale_evidence':8},
                   'provider':{'cached_pools_last_refresh_retained':1}}}
        result=evidence.diagnose(snapshot)
        self.assertIn('cached_chart_evidence_expired',result['observed_facts'])
        self.assertIn('inferior_chart_refresh_retained',result['observed_facts'])
        self.assertNotIn('provider_returned_stale_candles',result['observed_facts'])
        self.assertTrue(any('not proof of a defect' in x for x in result['next_measurements']))

    def test_incident_evidence_preserves_detection_and_updates_latest(self):
        with api.connection() as conn,conn:
            before=self.snapshot();after={**before,'queue':{'oldest_wait':2}}
            evidence.record(conn,'incident',NOW,before);evidence.record(conn,'incident',NOW+60,after)
            value=evidence.incident(conn,'incident')
            self.assertEqual(value['before']['metrics']['queue']['oldest_wait'],0)
            self.assertEqual(value['latest']['metrics']['queue']['oldest_wait'],2)
            self.assertEqual(value['first_at'],NOW)
    def test_scout_is_prospective_unique_and_does_not_invent_novelty(self):
        self.proposal();self.watch()
        evidence.scout(api,NOW);evidence.scout(api,NOW+60)
        with api.connection() as conn:
            value=evidence.scouting(conn,NOW+60)[0]
            self.assertEqual(value['distinct_coin_pools'],1);self.assertEqual(value['matched'],1)
            self.assertEqual(value['comparable_matches'],0);self.assertIsNone(value['measured_signal_overlap'])
            self.assertNotIn('profit',value)
    def test_discoveries_before_proposal_do_not_count(self):
        self.proposal(created=NOW-30);self.watch()
        evidence.scout(api,NOW)
        with api.connection() as conn:self.assertEqual(evidence.scouting(conn,NOW)[0]['distinct_coin_pools'],0)
    def test_missing_required_data_does_not_count_as_complete_or_matching(self):
        self.proposal(rule={**RULE,'entry':'rsi < 30'});self.watch()
        evidence.scout(api,NOW)
        with api.connection() as conn:
            value=evidence.scouting(conn,NOW)[0]
            self.assertEqual(value['complete'],0);self.assertEqual(value['matched'],0)
    def test_overlap_is_measured_only_when_active_signal_is_known(self):
        self.proposal();rows=self.watch()
        with api.connection() as conn,conn:
            for row in rows.values():conn.execute('INSERT INTO research VALUES (?,?,?,?)',
                (row['mint'],row['pair'],NOW,json.dumps({'chart':{'at':NOW,'value':{'closed_at':NOW,'rsi':20}}})))
        evidence.scout(api,NOW)
        with api.connection() as conn:self.assertEqual(evidence.scouting(conn,NOW)[0]['measured_signal_overlap'],1)
    def test_old_matches_do_not_stay_in_window_when_coin_reappears(self):
        self.proposal()
        with api.connection() as conn,conn:
            conn.execute('INSERT INTO brain_scout_samples VALUES (?,?,?,?,?,?,?,?)',
                ('candidate','coin','pool',NOW,NOW-90000,NOW-90000,NOW-90000,NOW-90000))
            sample=evidence.scouting(conn,NOW)[0]
            self.assertEqual(sample['distinct_coin_pools'],1);self.assertEqual(sample['matched'],0)
    def test_feasible_candidate_can_go_first_without_maximizing_trade_count(self):
        self.proposal('old',NOW-1000,{**RULE,'entry':'rsi < 30'})
        self.proposal('ready',NOW-100);self.watch(12)
        evidence.scout(api,NOW)
        with api.connection() as conn:
            rows=conn.execute("SELECT * FROM brain_proposals ORDER BY created").fetchall()
            self.assertEqual(evidence.choose(conn,rows,NOW)['id'],'ready')
            self.assertTrue(evidence.scouting(conn,NOW)[1]['data_ready'])
    def test_cloud_investigation_does_not_block_local_monitor(self):
        entered=threading.Event();release=threading.Event()
        def researcher(*args):
            entered.set();release.wait(3);return None,'test_unavailable'
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(brain.cloud_tick,api,lambda:NOW,researcher)
            try:
                self.assertTrue(entered.wait(1))
                brain.monitor_tick(api,clock=lambda:NOW+1)
                with api.connection() as conn:
                    self.assertIsNotNone(conn.execute("SELECT due FROM research_tasks WHERE name='brain-observe'").fetchone())
            finally:release.set()
            future.result(timeout=2)
    def test_canonical_health_does_not_use_generated_freshness_or_scheduler(self):
        rows=self.watch()
        with api.connection() as conn,conn:
            row=rows['0'];conn.execute('INSERT INTO research VALUES (?,?,?,?)',
                (row['mint'],row['pair'],NOW,json.dumps({'chart':{'at':NOW,'value':{'closed_at':NOW-500,'rsi':20,'volume_ratio':2}}})))
        with patch.object(api,'chart_coverage',side_effect=RuntimeError('must not call')),patch.object(api,'exit_queue',side_effect=RuntimeError('must not call')):
            value=repair.health(api,NOW)
        self.assertEqual(value['eligible_candidates'],1);self.assertEqual(value['complete_candidates'],0)
    def test_healthy_server_alone_does_not_pass_data_repair_goal(self):
        before={'complete_candidates':0,'oldest_quote_wait':0,'cycle_age':1,'paper_enabled':True}
        after={**before,'source_sha':'a'*40}
        self.assertFalse(policy.goal_met('chart_data_gap',before,after))
        self.assertFalse(policy.goal_met('chart_data_gap',before,{**after,'complete_candidates':1}))
        self.assertTrue(policy.goal_met('chart_data_gap',before,{**after,'eligible_candidates':10,'complete_candidates':2}))
        self.assertFalse(policy.goal_met('chart_data_gap',before,{**after,'complete_candidates':1,'paper_enabled':False}))
    def test_queue_repair_must_improve_queue_and_engine_error_must_clear(self):
        before={'oldest_quote_wait':300,'cycle_age':1,'paper_enabled':True}
        self.assertFalse(policy.goal_met('exit_backlog',before,before))
        self.assertTrue(policy.goal_met('exit_backlog',before,{**before,'oldest_quote_wait':10}))
        self.assertFalse(policy.goal_met('engine_error',before,{**before,'oldest_quote_wait':0,'engine_error_present':True}))
        self.assertFalse(policy.symptom_present('exit_backlog',{**before,'oldest_quote_wait':0}))

if __name__=='__main__':unittest.main()
