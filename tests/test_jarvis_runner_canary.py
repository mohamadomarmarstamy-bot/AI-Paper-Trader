import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

NOW=1800000000


class CanaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('jarvis_runner_canary_test',Path(__file__).resolve().parents[1]/'scripts/jarvis_paper_runner.py')
        cls.runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.runner)
    def run_canary(self,transform=None,kind='engine_error',limit=300):
        elapsed=[0];calls=[]
        def health(_):
            value={'source_sha':'a'*40,'paper_enabled':True,'cycle_age':1,'oldest_quote_wait':0,
                   'engine_error_present':False,'observed_at':NOW+elapsed[0]}
            if transform:value=transform(value,len(calls))
            calls.append(value);return value
        def advance(seconds):elapsed[0]+=seconds
        with patch.object(self.runner,'api',side_effect=health),patch.object(self.runner.time,'monotonic',side_effect=lambda:elapsed[0]),\
             patch.object(self.runner.time,'time',side_effect=lambda:NOW+elapsed[0]),patch.object(self.runner.time,'sleep',side_effect=advance):
            result=self.runner.canary('a'*40,{'engine_error_present':True},limit=limit,kind=kind)
        return result,calls
    def test_five_distinct_healthy_samples_over_two_minutes_succeed(self):
        ok,calls=self.run_canary();self.assertTrue(ok);self.assertEqual(len(calls),5)
        self.assertEqual(calls[-1]['observed_at']-calls[0]['observed_at'],120)
    def test_unhealthy_sample_resets_consecutive_check_counter(self):
        ok,calls=self.run_canary(lambda v,i:{**v,'engine_error_present':i==3})
        self.assertTrue(ok);self.assertEqual(len(calls),9)
    def test_duplicate_or_stale_observations_never_verify_recovery(self):
        self.assertFalse(self.run_canary(lambda v,i:{**v,'observed_at':NOW})[0])
        self.assertFalse(self.run_canary(lambda v,i:{**v,'observed_at':v['observed_at']-60})[0])
    def test_owner_pause_does_not_count_as_incident_recovery_but_allows_rollback(self):
        paused=lambda v,i:{**v,'paper_enabled':False,'cycle_age':300}
        self.assertFalse(self.run_canary(paused)[0]);self.assertTrue(self.run_canary(paused,kind=None)[0])
    def test_runner_outage_is_not_reported_as_a_bad_code_fix(self):
        for error,expected in ((RuntimeError('Image unavailable'),'runner_error'),(self.runner.TestFailure('Regression failed'),'tests_failed')):
            reports=[]
            def coordinator(path,payload=None):
                if path=='health':return {'source_sha':'a'*40,'enabled':True}
                if path=='claim':return {'status':'claimed','id':'run','token':'private','patch':{},'proposal':'p'}
                reports.append(payload);return {}
            with self.subTest(expected=expected),patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main'}),patch.object(self.runner,'api',side_effect=coordinator),patch.object(self.runner,'git',return_value='a'*40),patch.object(self.runner,'prove',side_effect=error):
                self.runner.run()
            self.assertEqual(reports[-1]['outcome'],expected)
    def test_wrong_deployed_commit_and_unresolved_original_issue_never_pass(self):
        self.assertFalse(self.run_canary(lambda v,i:{**v,'source_sha':'b'*40})[0])
        self.assertFalse(self.run_canary(lambda v,i:{**v,'engine_error_present':True})[0])

if __name__=='__main__':unittest.main()
