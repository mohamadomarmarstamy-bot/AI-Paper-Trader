import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa,padding
from cryptography.hazmat.primitives import hashes
import database
import jarvis_test_bot as api
import jarvis_brain as brain
import jarvis_repair as repair
import jarvis_repair_policy as policy

NOW=1800000000
TEST='''import unittest
import jarvis_research as research
class Regression(unittest.TestCase):
    def test_fresh_record(self):
        self.assertEqual(research.chart_fresh_for({'at':1800,'value':{'closed_at':1800}},1800),180)
'''


def fixture():
    path=Path(__file__).parent/'jarvis_research.py';source=path.read_text();lines=source.splitlines(True)
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='chart_fresh_for')
    return source,{'file':path.name,'function':node.name,'replacement':''.join(lines[node.lineno-1:node.end_lineno]),
        'base_sha256':hashlib.sha256(source.encode()).hexdigest(),'regression_test':TEST}


class PolicyTests(unittest.TestCase):
    def test_original_helpers_pass_and_other_functions_do_not(self):
        for file,names in policy.TARGETS.items():
            source=(Path(__file__).parent/file).read_text();lines=source.splitlines(True)
            for name in names:
                node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name==name)
                policy.check_function(''.join(lines[node.lineno-1:node.end_lineno]),file,name,node)
        with self.assertRaises(ValueError):policy.check_function('def quote(): return 1','jarvis_test_bot.py','quote',ast.parse('def quote(): pass').body[0])
    def test_unsafe_helpers_cannot_import_mutate_inspect_recurse_or_rebind(self):
        source,value=fixture()
        for body in ('import os\n    return os.environ','return __import__("os")','record["at"] = now\n    return 0',
                     'return record.__class__','return chart_fresh_for(record, now)','return record.update({})',
                     'min = chart_fresh_for\n    return min(record,now)','while True: pass',
                     'return [a for a in record for b in record for c in record]'):
            with self.subTest(body=body),self.assertRaises(ValueError):
                policy.apply(source,{**value,'replacement':'def chart_fresh_for(record, now):\n    '+body+'\n'})
    def test_stale_source_and_changed_signature_are_rejected(self):
        source,value=fixture()
        with self.assertRaises(ValueError):policy.apply(source+'\n',value)
        with self.assertRaises(ValueError):policy.apply(source,{**value,'replacement':'def chart_fresh_for(record, now, extra=0):\n    return 0\n'})
        self.assertEqual(policy.apply(source,value),source)
    def test_regression_test_requires_assertions_without_runtime_introspection(self):
        self.assertTrue(policy.check_test(TEST,'jarvis_research.py','chart_fresh_for'))
        for bad in (TEST.replace('import unittest','import os'),TEST.replace('self.assertEqual','self.skipTest'),
                    TEST.replace('research.chart_fresh_for','research.__dict__.get'),TEST+'\nraise SystemExit(0)',
                    TEST.replace('self.assertEqual','unittest.TestCase.assertEqual')):
            with self.assertRaises(ValueError):policy.check_test(bad,'jarvis_research.py','chart_fresh_for')
    def test_brain_retains_replacement_and_regression_for_isolated_testing(self):
        _,value=fixture()
        result=brain.validate_patch({k:value[k] for k in ('file','function','replacement','regression_test')})
        self.assertEqual(result['regression_test'],TEST);self.assertIn('def chart_fresh_for',result['replacement'])


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        tmp=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database,'DATABASE_PATH',Path(tmp)/'db'))
        self.enterContext(patch.dict(os.environ,{'JARVIS_PAPER_REPAIR_ENABLED':'true','RAILWAY_GIT_COMMIT_SHA':'a'*40}))
        self.claims={'run_id':'123'}
        with api.connection() as conn,conn:
            state,_=api.read(conn);state.update(enabled=True,last_cycle=NOW,error='Provider unavailable')
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
            conn.execute("INSERT INTO brain_incidents(id,kind,opened,last_seen,status) VALUES ('incident','engine_error',?,?, 'investigating')",(NOW,NOW))
            _,value=fixture();value['incident_id']='incident'
            conn.execute("INSERT INTO brain_proposals(id,created,kind,status,data) VALUES ('proposal',?,'code','needs_functional_tests',?)",(NOW,json.dumps(value)))
    def test_claim_is_single_run_bound_and_status_does_not_expose_token(self):
        job=repair.claim(api,self.claims,NOW)
        self.assertEqual(job['status'],'claimed')
        self.assertEqual(repair.claim(api,{'run_id':'456'},NOW+1)['status'],'busy')
        with api.connection() as conn:
            self.assertNotIn(job['token'],json.dumps(repair.status(conn,NOW)))
    def payload(self,job,outcome='deploying'):
        return {'id':job['id'],'token':job['token'],'outcome':outcome,'commit':'b'*40,
            'checks':{'baseline_failed':True,'regression_passed':True,'suite_passed':True,'canary_passed':False}}
    def test_reports_require_tests_valid_lease_and_actual_deployed_health(self):
        job=repair.claim(api,self.claims,NOW);payload=self.payload(job)
        with self.assertRaises(ValueError):repair.report(api,{'run_id':'456'},payload,NOW+1)
        with self.assertRaises(ValueError):repair.report(api,self.claims,{**payload,'checks':{**payload['checks'],'suite_passed':False}},NOW+1)
        self.assertEqual(repair.report(api,self.claims,payload,NOW+1)['status'],'deploying')
        deployed={**payload,'outcome':'deployed','checks':{**payload['checks'],'canary_passed':True}}
        with self.assertRaises(ValueError):repair.report(api,self.claims,deployed,NOW+2)
        with api.connection() as conn,conn:
            state,_=api.read(conn);state['error']=None;conn.execute('UPDATE run SET state=?',(json.dumps(state),))
        with patch.dict(os.environ,{'RAILWAY_GIT_COMMIT_SHA':'b'*40}):
            self.assertEqual(repair.report(api,self.claims,deployed,NOW+3)['status'],'deployed')
        with self.assertRaises(ValueError):repair.report(api,self.claims,payload,NOW+4)
    def test_missing_regression_and_resolved_incidents_never_deploy(self):
        with api.connection() as conn,conn:
            conn.execute("UPDATE brain_incidents SET resolved=?",(NOW,))
        self.assertEqual(repair.claim(api,self.claims,NOW)['status'],'empty')
        with api.connection() as conn:self.assertEqual(conn.execute('SELECT status FROM brain_proposals').fetchone()[0],'obsolete')
    def test_expired_runner_is_flagged_and_does_not_reclaim_patch(self):
        job=repair.claim(api,self.claims,NOW)
        self.assertEqual(repair.claim(api,{'run_id':'456'},NOW+1300)['status'],'empty')
        with api.connection() as conn:
            self.assertEqual(conn.execute('SELECT status FROM repair_runs').fetchone()[0],'expired')
            self.assertEqual(conn.execute('SELECT status FROM brain_proposals').fetchone()[0],'needs_review')
    def test_health_is_independent_of_generated_scheduler_functions(self):
        with patch.object(api,'exit_queue',side_effect=RuntimeError('must not call')):
            self.assertEqual(repair.health(api,NOW)['cycle_age'],0)


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        public=cls.key.public_key().public_numbers()
        def enc(n):return base64.urlsafe_b64encode(n.to_bytes((n.bit_length()+7)//8,'big')).decode().rstrip('=')
        cls.keys=[{'kid':'test','kty':'RSA','e':enc(public.e),'n':enc(public.n)}]
    def token(self,changes=None):
        claims={'iss':repair.ISSUER,'aud':repair.AUDIENCE,'repository':repair.REPOSITORY,'repository_id':repair.REPOSITORY_ID,
            'workflow_ref':repair.WORKFLOW,'ref':'refs/heads/main','event_name':'schedule','runner_environment':'github-hosted',
            'run_id':'123','iat':NOW-10,'nbf':NOW-10,'exp':NOW+290,**(changes or {})}
        def enc(v):return base64.urlsafe_b64encode(json.dumps(v).encode()).decode().rstrip('=')
        message=enc({'alg':'RS256','kid':'test'})+'.'+enc(claims)
        signature=base64.urlsafe_b64encode(self.key.sign(message.encode(),padding.PKCS1v15(),hashes.SHA256())).decode().rstrip('=')
        return 'Bearer '+message+'.'+signature
    def test_signature_repository_workflow_and_expiry_are_verified(self):
        self.assertEqual(repair.authenticate(self.token(),NOW,self.keys)['run_id'],'123')
        for changes in ({'aud':'other'},{'repository':'attacker/repo'},{'repository_id':'1'},{'workflow_ref':'other'},
                        {'ref':'refs/heads/feature'},{'event_name':'pull_request'},{'runner_environment':'self-hosted'},
                        {'exp':NOW-1},{'iat':NOW-900},{'nbf':NOW+120}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):repair.authenticate(self.token(changes),NOW,self.keys)
        with self.assertRaises(ValueError):repair.authenticate(self.token()[:-3]+'abc',NOW,self.keys)
    def test_runner_endpoints_reject_owner_session_without_runner_identity(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from solana_meme_routes import build_router
        app=FastAPI();app.include_router(build_router(require_session=lambda _:None));client=TestClient(app)
        self.assertEqual(client.get('/jarvis-test-bot/repair-runner/health').status_code,401)
        self.assertEqual(client.post('/jarvis-test-bot/repair-runner/claim',json={}).status_code,401)
        self.assertEqual(client.post('/jarvis-test-bot/repair-runner/report',json={}).status_code,401)

if __name__=='__main__':unittest.main()
