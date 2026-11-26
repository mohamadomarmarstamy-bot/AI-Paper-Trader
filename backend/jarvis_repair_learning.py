"""Durable evidence from verified repair attempts, never self-awarded success."""
import ast
import hashlib
import json
import math

SCHEMA='''CREATE TABLE IF NOT EXISTS repair_lessons(
 run TEXT PRIMARY KEY,at REAL NOT NULL,fingerprint TEXT NOT NULL,base TEXT NOT NULL,
 file TEXT NOT NULL,helper TEXT NOT NULL,kind TEXT NOT NULL,outcome TEXT NOT NULL,
 stage TEXT NOT NULL,evidence TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS repair_lesson_patch ON repair_lessons(fingerprint,base);'''


def fingerprint(patch):
    # Whitespace and regression-test renaming cannot turn a failed fix into a new fix.
    body=ast.dump(ast.parse(patch['replacement']),include_attributes=False)
    return hashlib.sha256(json.dumps([patch['file'],patch['function'],body]).encode()).hexdigest()


def blocked(conn,patch,now):
    for row in conn.execute('SELECT outcome,at FROM repair_lessons WHERE fingerprint=? AND base=? ORDER BY at DESC',
                            (fingerprint(patch),patch['base_sha256'])):
        if row['outcome'] in ('tests_failed','rolled_back','rollback_blocked'):return 'same_patch_failed_on_same_source'
        if row['outcome'] in ('runner_error','expired') and now-row['at']<3600:return 'runner_retry_cooldown'
    return None


def remember(conn,run,patch,report,now):
    outcome=report['outcome'];checks=report.get('checks',{})
    stage={'deployed':'live_recovery_verified','rolled_back':'live_recovery_not_verified',
           'rollback_blocked':'rollback_needs_attention','runner_error':'runner_unavailable','expired':'runner_lease_expired'}.get(outcome)
    if outcome=='tests_failed':
        stage=next((label for key,label in (('baseline_failed','baseline_not_reproduced'),
            ('regression_passed','regression_not_fixed'),('suite_passed','existing_suite_failed')) if not checks.get(key)), 'test_runner_incomplete')
    if not stage:return
    # Only fixed aggregate health fields and booleans; never source, tokens or arbitrary errors.
    keys=('paper_enabled','engine_error_present','eligible_candidates','complete_candidates','cycle_age','oldest_quote_wait')
    evidence={key:{k:v for k,v in report.get(key,{}).items() if k in keys} for key in ('baseline','after')}
    evidence['checks']={k:checks.get(k) is True for k in ('baseline_failed','regression_passed','suite_passed','canary_passed')}
    conn.execute('INSERT OR IGNORE INTO repair_lessons VALUES (?,?,?,?,?,?,?,?,?,?)',
        (run,now,fingerprint(patch),patch['base_sha256'],patch['file'],patch['function'],report.get('incident_kind','unknown'),outcome,stage,json.dumps(evidence)))


def lessons(conn,kind=None):
    rows=conn.execute('SELECT * FROM repair_lessons '+('WHERE kind=? ' if kind else '')+'ORDER BY at DESC LIMIT 8',() if kind is None else (kind,))
    return [{'at':r['at'],'file':r['file'],'function':r['helper'],'fingerprint':r['fingerprint'],
             'source_digest':r['base'],'incident_kind':r['kind'],'outcome':r['outcome'],
             'failure_or_verification_stage':r['stage'],'evidence':json.loads(r['evidence'])} for r in rows]


def regression_examples(kind):
    """Curated, fixed paper regressions; separate from verified live repair outcomes."""
    if kind not in ('chart_data_gap', 'data_coverage'):
        return []
    return [{
        'reference': 'PR #177',
        'verification': 'Offline regression examples, not proof of live coverage recovery.',
        'fixtures': [
            {'test': 'test_chart_refresh_cannot_replace_fresh_evidence_with_older_or_weaker_chart',
             'symptom': 'A successful primary response is older than the cached closed candle, '
                        'or is equally timed but missing a usable indicator.',
             'expected': 'Retain the valid chart and its original observation time. The retained '
                         'chart must still expire normally. A newer valid chart can replace it.'},
            {'test': 'test_successful_responses_without_candles_recover_pace_not_trade_evidence',
             'symptom': 'Valid responses with unusable history interrupt pacing recovery after an HTTP 429.',
             'expected': 'Count valid responses toward gradual pacing recovery without treating '
                         'their candles as trade evidence. HTTP 429 and transport failures still reset recovery.'},
        ],
        'apply': 'Distinguish expired cache, upstream data gaps and rate limits before proposing a fix. '
                 'If a suspected behavior can be reproduced, require a failing baseline, a passing '
                 'regression and the existing paper suite. If not, measure or escalate; do not invent a patch.',
        'limits': 'Do not expand repair scope, change provider rights or quotas, relax freshness, '
                  'or touch real-money trading. This case did not add automatically editable functions.',
    }]


def chart_cases(api,conn,state,now):
    """A bounded sample of actual inputs, with identifiers and provider text removed."""
    cases=[]
    for row in state['watch'].values():
        if row.get('monitor_only') or not 0<=now-row['last']<=90:continue
        found=conn.execute('SELECT evidence FROM research WHERE mint=? AND pair=?',(row['mint'],row['pair'])).fetchone()
        record=(json.loads(found['evidence']).get('chart') or {}) if found else {}
        record=record if isinstance(record,dict) else {}
        value=record.get('value')
        value=value if isinstance(value,dict) else {}
        finite=lambda v:v if type(v) in (int,float) and math.isfinite(v) else None
        observed,closed=finite(record.get('at')),finite(value.get('closed_at'))
        cases.append({'record':{'at':observed,'value':{'closed_at':closed}},'now':now,
                      'pool':{'age_minutes':finite(row.get('age_minutes'))},
                      'rsi_available':finite(value.get('rsi')) is not None,
                      'volume_ratio_available':finite(value.get('volume_ratio')) is not None,
                      'observed_age_seconds':None if observed is None else now-observed,
                      'closed_candle_age_seconds':None if closed is None else now-closed})
        if len(cases)>=6:break
    return {'chart_inputs':cases,'scope':'Observed helper inputs only; no expected answers, profit or causal fix claim. Never make old or missing data fresh by relaxing limits.'}
