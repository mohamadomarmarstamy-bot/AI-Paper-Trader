"""GitHub-OIDC authenticated paper-only test/deployment coordinator."""
import base64
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import threading
import time

from cryptography.hazmat.primitives.asymmetric import padding,rsa
from cryptography.hazmat.primitives import hashes
import solana_http
import jarvis_repair_policy as policy

REPOSITORY='mohamadomarmarstamy-bot/AI-Paper-Trader'
REPOSITORY_ID='1306820068'
WORKFLOW=REPOSITORY+'/.github/workflows/jarvis-paper-repair.yml@refs/heads/main'
ISSUER='https://token.actions.githubusercontent.com'
AUDIENCE='jarvis-paper-repair'
SCHEMA='''CREATE TABLE IF NOT EXISTS repair_runs(
 id TEXT PRIMARY KEY,proposal TEXT NOT NULL,run_id TEXT NOT NULL,token_hash TEXT NOT NULL,
 started REAL NOT NULL,expires REAL NOT NULL,status TEXT NOT NULL,report TEXT NOT NULL DEFAULT '{}');
CREATE UNIQUE INDEX IF NOT EXISTS repair_active ON repair_runs(proposal) WHERE status IN ('testing','deploying');
CREATE TABLE IF NOT EXISTS repair_runner(id INTEGER PRIMARY KEY CHECK(id=1),seen REAL NOT NULL,run_id TEXT NOT NULL);'''
_KEYS={'at':0,'keys':[]}
_LOCK=threading.Lock()


def enabled():return os.getenv('JARVIS_PAPER_REPAIR_ENABLED','').strip().lower() in ('1','true','yes','on')


def decode(value):return base64.urlsafe_b64decode(value+'='*((-len(value))%4))


def jwks(now,fetch=solana_http.get):
    with _LOCK:
        if now-_KEYS['at']<300 and _KEYS['keys']:return _KEYS['keys']
        response=fetch(ISSUER+'/.well-known/jwks',timeout=5,allow_redirects=False)
        if response.status_code!=200 or len(response.content)>100000:raise ValueError('OIDC keys unavailable')
        keys=response.json().get('keys')
        if not isinstance(keys,list) or len(keys)>20:raise ValueError('Invalid OIDC keys')
        _KEYS.update(at=now,keys=keys)
        return keys


def authenticate(header,now=None,keys=None):
    now=time.time() if now is None else now
    try:
        if not isinstance(header,str) or not header.startswith('Bearer ') or len(header)>20000:raise ValueError()
        encoded=header[7:].split('.')
        if len(encoded)!=3:raise ValueError()
        head,claims=[json.loads(decode(value)) for value in encoded[:2]]
        if head.get('alg')!='RS256' or not isinstance(head.get('kid'),str):raise ValueError()
        key=next(k for k in (jwks(now) if keys is None else keys) if k.get('kid')==head['kid'] and k.get('kty')=='RSA')
        public=rsa.RSAPublicNumbers(int.from_bytes(decode(key['e']),'big'),int.from_bytes(decode(key['n']),'big')).public_key()
        public.verify(decode(encoded[2]),('.'.join(encoded[:2])).encode(),padding.PKCS1v15(),hashes.SHA256())
        if (claims.get('iss')!=ISSUER or claims.get('aud')!=AUDIENCE or claims.get('repository')!=REPOSITORY
            or claims.get('repository_id')!=REPOSITORY_ID or claims.get('ref')!='refs/heads/main'
            or claims.get('workflow_ref')!=WORKFLOW or claims.get('runner_environment')!='github-hosted'
            or claims.get('event_name') not in ('schedule','workflow_dispatch','push')
            or not isinstance(claims.get('run_id'),str) or not claims['run_id'].isdigit()):raise ValueError()
        if any(type(claims.get(k)) not in (int,float) for k in ('iat','exp','nbf')):raise ValueError()
        if not now-600<=claims['iat']<=now+30 or claims['nbf']>now+30 or claims['exp']<=now or claims['exp']-claims['iat']>900:raise ValueError()
        return claims
    except Exception:raise ValueError('Invalid repair-runner identity') from None


def health(api,now):
    with api.connection() as conn:
        state,_=api.read(conn)
        # Independent of generated scheduler/coverage helpers.
        positions=[p for b in state['bots'].values() for p in b['positions'].values()]
        regular=[p for p in positions if not (p.get('mark_stale') and 'quote_due_since' in p)]
        oldest=max([0,*[now-p['due'] for p in regular if p['due']<=now]])
        starts=[r['created'] for r in state.get('local_rules',{}).values()]
        rows=[r for r in state['watch'].values() if starts and not r.get('monitor_only') and 0<=now-r['last']<=90
              and 0<=now-r['first']<=900 and r['first']>=min(starts) and now>=r.get('retry',0)]
        complete=0
        for candidate in rows:
            found=conn.execute('SELECT evidence FROM research WHERE mint=? AND pair=?',(candidate['mint'],candidate['pair'])).fetchone()
            record=(json.loads(found['evidence']).get('chart') or {}) if found else {}
            value=record.get('value') or {}
            observed,closed=record.get('at'),value.get('closed_at')
            fresh=all(type(v) in (int,float) and math.isfinite(v) and 0<=now-v<=180 for v in (observed,closed))
            if fresh and all(type(value.get(f)) in (int,float) and math.isfinite(value[f]) for f in ('rsi','volume_ratio')):complete+=1
        return {'enabled':enabled(),'paper_enabled':state['enabled'],'engine_error_present':bool(state.get('error')),
            'eligible_candidates':len(rows),'complete_candidates':complete,
            'source_sha':os.getenv('RAILWAY_GIT_COMMIT_SHA',''),
            'cycle_age':max(0,now-state['last_cycle']) if state.get('last_cycle') else None,
            'oldest_quote_wait':oldest,'observed_at':now}


def claim(api,claims,now):
    baseline=health(api,now)
    with api.connection() as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('INSERT OR REPLACE INTO repair_runner VALUES (1,?,?)',(now,claims['run_id']))
        if not enabled():return {'status':'disabled'}
        # A crashed runner cannot silently leave a half-deployed repair behind.
        for expired in conn.execute("SELECT * FROM repair_runs WHERE expires<=? AND status IN ('testing','deploying')",(now,)).fetchall():
            conn.execute("UPDATE repair_runs SET status='expired' WHERE id=?",(expired['id'],))
            conn.execute("UPDATE brain_proposals SET status='needs_review' WHERE id=?",(expired['proposal'],))
            escalate(api,conn,expired['proposal'],now,'The isolated repair runner timed out. No successful recovery has been verified; review the code proposal and deployment status.')
        if conn.execute("SELECT 1 FROM repair_runs WHERE status IN ('testing','deploying')").fetchone():return {'status':'busy'}
        day_count=conn.execute('SELECT count(*) FROM repair_runs WHERE started>?',(now-86400,)).fetchone()[0]
        if day_count>=2:return {'status':'daily_limit'}
        for row in conn.execute("SELECT * FROM brain_proposals WHERE kind='code' AND status='needs_functional_tests' ORDER BY created LIMIT 10").fetchall():
            patch=json.loads(row['data'])
            incident=conn.execute('SELECT resolved,kind FROM brain_incidents WHERE id=?',(patch.get('incident_id',''),)).fetchone()
            if incident is None or incident['resolved'] is not None:
                conn.execute("UPDATE brain_proposals SET status='obsolete' WHERE id=?",(row['id'],));continue
            if not policy.symptom_present(incident['kind'],baseline):continue
            try:
                if patch.get('file') not in policy.TARGETS:raise ValueError('Outside automatic scope')
                source=(Path(__file__).parent/patch['file']).read_text()
                policy.apply(source,patch)
                policy.check_test(patch.get('regression_test'),patch['file'],patch['function'])
            except (ValueError,TypeError,SyntaxError):
                conn.execute("UPDATE brain_proposals SET status='needs_review' WHERE id=?",(row['id'],));continue
            ident=secrets.token_hex(16);token=secrets.token_urlsafe(32)
            conn.execute('INSERT INTO repair_runs(id,proposal,run_id,token_hash,started,expires,status,report) VALUES (?,?,?,?,?,?,?,?)',
                (ident,row['id'],claims['run_id'],hashlib.sha256(token.encode()).hexdigest(),now,now+1200,'testing',json.dumps({'baseline':baseline,'incident_kind':incident['kind']})))
            conn.execute("UPDATE brain_proposals SET status='isolated_testing' WHERE id=?",(row['id'],))
            return {'status':'claimed','id':ident,'token':token,'proposal':row['id'],'patch':patch,'expires':now+1200,'baseline':baseline,'incident_kind':incident['kind']}
        return {'status':'empty'}


def escalate(api,conn,proposal,now,detail):
    import jarvis_brain as brain
    row=conn.execute('SELECT data FROM brain_proposals WHERE id=?',(proposal,)).fetchone()
    if not row:return
    incident=conn.execute('SELECT * FROM brain_incidents WHERE id=? AND resolved IS NULL',
                         (json.loads(row['data']).get('incident_id',''),)).fetchone()
    if incident:brain.queue_mail(conn,incident,'escalated',now,detail)


def report(api,claims,payload,now):
    if not isinstance(payload,dict) or set(payload)!={'id','token','outcome','commit','checks'}:raise ValueError('Invalid runner report')
    outcome=payload['outcome']
    if outcome not in ('tests_failed','deploying','deployed','rolled_back','rollback_blocked','runner_error'):raise ValueError('Unknown runner outcome')
    commit=payload['commit']
    if not isinstance(commit,str) or (commit and (len(commit)!=40 or any(c not in '0123456789abcdef' for c in commit))):raise ValueError('Invalid commit')
    checks=payload['checks']
    if not isinstance(checks,dict) or set(checks)!={'baseline_failed','regression_passed','suite_passed','canary_passed'} or any(type(v) is not bool for v in checks.values()):raise ValueError('Invalid checks')
    if outcome in ('deployed','rolled_back'):
        actual=health(api,now)
        if actual['source_sha']!=commit:raise ValueError('Deployed source not verified')
        if outcome=='deployed' and (actual['cycle_age'] is None or actual['cycle_age']>45 or actual['oldest_quote_wait']>120):
            raise ValueError('Deployed health not verified')
    with api.connection() as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM repair_runs WHERE id=?',(payload['id'],)).fetchone()
        if (not row or row['run_id']!=claims['run_id'] or row['expires']<=now or row['status'] not in ('testing','deploying')
            or not isinstance(payload['token'],str) or not hmac.compare_digest(row['token_hash'],hashlib.sha256(payload['token'].encode()).hexdigest())):
            raise ValueError('Invalid repair lease')
        if outcome in ('deploying','deployed') and (not commit or not all(checks[k] for k in ('baseline_failed','regression_passed','suite_passed'))):raise ValueError('Missing test evidence')
        if outcome=='deployed' and (row['status']!='deploying' or not checks['canary_passed']):raise ValueError('Missing deployment verification')
        prior=json.loads(row['report'])
        if outcome=='deployed' and not policy.goal_met(prior.get('incident_kind'),prior.get('baseline',{}),actual):
            raise ValueError('Original incident metric has not improved')
        result={**prior,'outcome':outcome,'commit':commit,'checks':checks,'at':now,'run_id':claims['run_id']}
        if outcome=='deployed':result['after']=actual
        conn.execute('UPDATE repair_runs SET status=?,report=?,expires=? WHERE id=?',
            (outcome,json.dumps(result),max(row['expires'],now+900) if outcome=='deploying' else row['expires'],row['id']))
        conn.execute('UPDATE brain_proposals SET status=?,report=? WHERE id=?',(outcome,json.dumps(result),row['proposal']))
        if outcome in ('runner_error','rollback_blocked','rolled_back'):
            escalate(api,conn,row['proposal'],now,'The proposed paper code repair was not kept. Outcome: '+outcome.replace('_',' ')+'. Existing incident monitoring continues; review is needed.')
        return {'status':outcome}


def status(conn,now):
    runner=conn.execute('SELECT * FROM repair_runner WHERE id=1').fetchone()
    return {'enabled':enabled(),'runner_seen':runner['seen'] if runner else None,
        'runner_recent':bool(runner and 0<=now-runner['seen']<=3600),
        'scope':{k:sorted(v) for k,v in policy.TARGETS.items()},'max_repairs_per_day':2,
        'runs':[dict(r)|{'report':json.loads(r['report'])} for r in conn.execute('SELECT id,proposal,started,status,report FROM repair_runs ORDER BY started DESC LIMIT 5')]}
