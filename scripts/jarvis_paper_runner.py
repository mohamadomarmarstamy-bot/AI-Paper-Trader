"""Trusted GitHub-hosted controller. Generated code runs only inside Docker.

No model-supplied shell commands, workflow files or tests are pushed to main.
"""
import argparse
import ast
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib import request,error,parse

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('repair_policy',ROOT/'backend/jarvis_repair_policy.py')
policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)
BASE='https://ai-paper-trader-production-7465.up.railway.app'
IMAGE='jarvis-paper-tests:isolated'
DEBUG=False
TEST_DRIVER='''import importlib,io,json,sys,unittest
stream=io.StringIO()
suite=unittest.defaultTestLoader.loadTestsFromName('test_jarvis_generated_regression')
r=unittest.TextTestRunner(stream=stream,verbosity=0).run(suite)
print(json.dumps({'run':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skipped':len(r.skipped),'expected':len(r.expectedFailures),'unexpected':len(r.unexpectedSuccesses)}))
'''


def command(args,cwd=None,timeout=240):
    return subprocess.run(args,cwd=cwd or ROOT,capture_output=True,text=True,timeout=timeout)


def git(*args):
    r=command(['git',*args])
    if r.returncode:raise RuntimeError('Git operation failed')
    return r.stdout.strip()


def container_args(folder):
    return ['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL',
        '--security-opt','no-new-privileges','--pids-limit','128','--memory','2g','--cpus','2',
        '--user','65534:65534','--tmpfs','/tmp:rw,noexec,nosuid,size=256m',
        '--mount','type=bind,source='+str(folder.resolve())+',target=/app,readonly',
        '--workdir','/app/backend','--env','PYTHONDONTWRITEBYTECODE=1','--env','DATABASE_PATH=/tmp/jarvis-isolated.db',IMAGE]


def image():
    if command(['docker','image','inspect',IMAGE],timeout=15).returncode==0:return
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)
        (path/'Dockerfile').write_text("FROM python:3.12-slim\nRUN python -m pip install --no-cache-dir 'fastapi>=0.115,<1' 'httpx>=0.27,<1' 'requests>=2.32,<3' 'cryptography>=46,<51' 'solders==0.29.0' 'websockets>=16,<17'\n")
        built=command(['docker','build','--tag',IMAGE,str(path)],timeout=300)
        if built.returncode:
            if DEBUG:print(built.stderr[-6000:],flush=True)
            raise RuntimeError('Isolated test image unavailable')


def regression(folder):
    r=command([*container_args(folder),'python','-c',TEST_DRIVER],timeout=60)
    if r.returncode:
        if DEBUG:print(r.stderr[-6000:],flush=True)
        raise RuntimeError('Regression sandbox failed')
    try:value=json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError,IndexError):raise RuntimeError('Invalid regression result') from None
    if value['run']<1 or any(value[k] for k in ('errors','skipped','expected','unexpected')):raise RuntimeError('Regression must run without errors, skips or expected failures')
    return value['failures']


def prove(patch,baseline_source=None,evidence=None):
    evidence={} if evidence is None else evidence
    file,name=patch.get('file'),patch.get('function')
    if file not in policy.TARGETS or name not in policy.TARGETS[file]:raise ValueError('Outside automatic scope')
    original=(ROOT/'backend'/file).read_text() if baseline_source is None else baseline_source
    changed=policy.apply(original,patch)
    policy.check_test(patch.get('regression_test'),file,name)
    image()
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);baseline=root/'baseline';candidate=root/'candidate'
        # No .git, GitHub credentials, CI controller or host environment mounted.
        shutil.copytree(ROOT/'backend',baseline/'backend',ignore=shutil.ignore_patterns('__pycache__','*.sqlite3','*.db','.env*','.venv'))
        shutil.copytree(ROOT/'local-worker',baseline/'local-worker',ignore=shutil.ignore_patterns('__pycache__','.venv','.env*','config.json','worker-state.json','*.log'))
        (baseline/'backend'/file).write_text(original)
        (baseline/'backend'/'test_jarvis_generated_regression.py').write_text(patch['regression_test'])
        shutil.copytree(baseline,candidate)
        (candidate/'backend'/file).write_text(changed)
        # tempfile's 0700 parent prevents the unprivileged sandbox from reading
        # it on some Docker hosts; bind-mount contents need read/search access.
        for folder in (root,baseline,candidate):folder.chmod(0o755)
        if regression(baseline)<1:raise RuntimeError('No demonstrated baseline failure')
        evidence['baseline_failed']=True
        if regression(candidate)!=0:raise RuntimeError('Repair did not fix regression')
        evidence['regression_passed']=True
        full=command([*container_args(candidate),'python','-m','unittest','discover','-s','.','-p','test_solana*.py','-q'],timeout=240)
        if full.returncode:
            if DEBUG:print(full.stderr[-10000:],flush=True)
            raise RuntimeError('Existing paper tests failed')
        evidence['suite_passed']=True
        print('Baseline regression failed; candidate regression and existing Solana suite passed.',flush=True)
    return changed


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RuntimeError('Unexpected authentication redirect')


def fetch(url,headers,payload=None):
    raw=None if payload is None else json.dumps(payload).encode()
    r=request.Request(url,data=raw,headers={**headers,**({'Content-Type':'application/json'} if raw else {})})
    with request.build_opener(NoRedirect).open(r,timeout=20) as response:
        value=response.read(250001)
        if len(value)>250000:raise RuntimeError('Oversized coordinator response')
        return json.loads(value)


def token():
    url=os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']
    if parse.urlsplit(url).scheme!='https' or not parse.urlsplit(url).hostname.endswith('.actions.githubusercontent.com'):
        raise RuntimeError('Unexpected identity endpoint')
    return fetch(url+('&' if '?' in url else '?')+'audience=jarvis-paper-repair',
        {'Authorization':'Bearer '+os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})['value']


def api(path,payload=None):
    return fetch(BASE+'/jarvis-test-bot/repair-runner/'+path,{'Authorization':'Bearer '+token()},payload)


def canary(sha,baseline,limit=480,kind=None):
    deadline=time.monotonic()+limit;healthy=0
    while time.monotonic()<deadline:
        try:
            value=api('health')
            healthy=(value['cycle_age'] is not None and value['cycle_age']<=45 and value['oldest_quote_wait']<=120)
            # An intentional owner pause is compatible with verified code rollback,
            # but can never count as recovery of the incident that motivated a fix.
            ok=value['source_sha']==sha and ((not value['paper_enabled'] or healthy) if kind is None
                else policy.goal_met(kind,baseline,value))
            healthy=healthy+1 if ok else 0
            if healthy>=5:return True
        except Exception:healthy=0
        time.sleep(30)
    return False


def selftest():
    file='jarvis_research.py';name='chart_fresh_for'
    source=(ROOT/'backend'/file).read_text();root=ast.parse(source)
    node=next(n for n in root.body if isinstance(n,ast.FunctionDef) and n.name==name)
    lines=source.splitlines(keepends=True)
    replacement=''.join(lines[node.lineno-1:node.end_lineno])
    broken=''.join(lines[:node.lineno-1])+'def chart_fresh_for(record, now):\n    return 0\n'+''.join(lines[node.end_lineno:])
    regression_source='''import unittest
import jarvis_research as research
class FreshnessRegression(unittest.TestCase):
    def test_fresh_evidence_has_remaining_lifetime(self):
        self.assertEqual(research.chart_fresh_for({'at':1800,'value':{'closed_at':1800}},1800),180)
'''
    patch={'file':file,'function':name,'replacement':replacement,'base_sha256':hashlib.sha256(broken.encode()).hexdigest(),'regression_test':regression_source}
    prove(patch,baseline_source=broken)
    print('Isolated repair rehearsal passed. No production data or deployment was used.',flush=True)


def run():
    # Checkout and controller come from trusted main, never from a model branch.
    if os.environ.get('GITHUB_REF')!='refs/heads/main':raise RuntimeError('Repairs require main')
    # A merge can start this job while Railway is still replacing the server.
    deadline=time.monotonic()+120
    while True:
        try:
            api('health')
            break
        except Exception:
            if time.monotonic()>=deadline:raise RuntimeError('Coordinator not ready') from None
            time.sleep(15)
    job=api('claim',{})
    if job['status']!='claimed':
        print('Paper repair coordinator: '+job['status'],flush=True);return
    checks=dict.fromkeys(('baseline_failed','regression_passed','suite_passed','canary_passed'),False)
    def report(outcome,commit=''):
        return api('report',{'id':job['id'],'token':job['token'],'outcome':outcome,'commit':commit,'checks':checks})
    pushed=False;new_sha='';before=''
    try:
        patch=job['patch'];base=git('rev-parse','HEAD')
        before=api('health')
        if before['source_sha']!=base:raise RuntimeError('Main and deployed source differ; wait for deployment')
        changed=prove(patch,evidence=checks)
        checks.update(baseline_failed=True,regression_passed=True,suite_passed=True)
        if not api('health')['enabled']:raise RuntimeError('Owner disabled automatic repair')
        if git('ls-remote','origin','refs/heads/main').split()[0]!=base:raise RuntimeError('Main advanced during tests')
        path=ROOT/'backend'/patch['file'];path.write_text(changed)
        git('config','user.name','Jarvis paper repair')
        git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
        git('add','--','backend/'+patch['file'])
        if git('diff','--cached','--name-only')!='backend/'+patch['file']:raise RuntimeError('Unexpected staged change')
        git('commit','-m','Jarvis paper repair '+job['proposal'])
        new_sha=git('rev-parse','HEAD')
        # Normal fast-forward push rejects concurrent main changes. Never force.
        git('push','origin','HEAD:refs/heads/main');pushed=True
        report('deploying',new_sha)
        if canary(new_sha,job['baseline'],kind=job['incident_kind']):
            checks['canary_passed']=True;report('deployed',new_sha)
            print('Paper repair deployed and passed independent live checks.',flush=True);return
        raise RuntimeError('Live checks did not verify recovery')
    except Exception:
        if pushed:
            try:
                if git('ls-remote','origin','refs/heads/main').split()[0]!=new_sha:
                    report('rollback_blocked',new_sha);return
                git('revert','--no-edit',new_sha)
                rollback_sha=git('rev-parse','HEAD')
                git('push','origin','HEAD:refs/heads/main')
                if not canary(rollback_sha,before,limit=240):
                    report('rollback_blocked',rollback_sha);return
                report('rolled_back',rollback_sha)
                print('Paper repair rolled back; incident monitoring continues.',flush=True)
            except Exception:
                try:report('rollback_blocked',new_sha)
                except Exception:pass
                raise RuntimeError('Rollback needs owner attention') from None
        else:
            report('tests_failed' if not checks['suite_passed'] else 'runner_error')
            print('Paper repair not deployed; proposal retained for review.',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--self-test',action='store_true');args=parser.parse_args()
    DEBUG=args.self_test
    try:selftest() if args.self_test else run()
    except Exception as exc:
        print('Paper repair runner could not complete; no recovery is claimed.'+((' '+str(exc)) if DEBUG else ''),file=sys.stderr,flush=True)
        raise SystemExit(1) from None
