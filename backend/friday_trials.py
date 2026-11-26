"""Prospective Friday-owned simulated positions using existing frozen stock charts."""
from contextlib import closing
from datetime import datetime
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo

import database
from day_trader_strategy import POLICY, VERSION, stamp
import friday_phase
import friday_replay
from friday_research import COHORT, classify, ledger_path

TRIAL_VERSION = 'friday-breakout-v1'
MAX_OPEN = 2
ET = ZoneInfo('America/New_York')
SCHEMA = """
CREATE TABLE IF NOT EXISTS friday_trials (
    chart_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, admitted REAL NOT NULL,
    phase TEXT NOT NULL, plan TEXT NOT NULL, expires REAL NOT NULL,
    closed REAL, outcome TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS friday_one_open_symbol
    ON friday_trials(symbol) WHERE closed IS NULL;
CREATE TABLE IF NOT EXISTS friday_trial_evidence (
    chart_id TEXT PRIMARY KEY REFERENCES friday_trials(chart_id), scan_finished REAL NOT NULL,
    snapshot TEXT NOT NULL, chart TEXT NOT NULL, alert_id TEXT, alert_received TEXT
);
CREATE TABLE IF NOT EXISTS friday_trial_health (
    id INTEGER PRIMARY KEY CHECK(id=1), checked REAL NOT NULL, reason TEXT NOT NULL
);
"""


def source_reader(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    return db


def number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def decoded(value, expected):
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, expected) else None


def entry_plan(chart, now):
    """Friday independently gates the completed-bar breakout from the stock evaluator."""
    if not isinstance(chart, dict):
        return None
    if chart.get('version') != VERSION or chart.get('feed') != 'iex' or chart.get('status') != 'evaluated':
        return None
    try:
        frozen, opened, closed, as_of, ask, rvol, spread = [chart[k] for k in
            ('observed_at','session_open','session_close','bar_as_of','reference_ask','opening_rvol','spread_percent')]
        if not all(map(number,(frozen,opened,closed,as_of,ask,rvol,spread))):
            return None
        current = datetime.fromtimestamp(now, ET)
        opening = datetime.fromtimestamp(opened, ET)
        closing = datetime.fromtimestamp(closed, ET)
        if not (current.weekday() < 5 and current.date() == opening.date() == closing.date()
                and (opening.hour, opening.minute) == (9,30)
                and (closing.hour,closing.minute) in ((13,0),(16,0))
                and opened+360 <= frozen <= now < closed-1800
                and int(now//60) == int(frozen//60) and 0 <= now-frozen <= 15 and opened < as_of <= frozen and now-as_of <= 75
                and 0 <= now-stamp(chart['quote_timestamp']) <= 15
                and 0 <= frozen-stamp(chart['quote_timestamp'])
                and 0 <= spread <= .15 and rvol >= 1.5 and ask > 0):
            return None
        plan = next((p for p in chart['setups'] if p.get('strategy') == 'opening_range_breakout'), None)
        if not plan or plan.get('status') != 'candidate':
            return None
        stop,target,expiry = [plan[k] for k in ('stop','target','expires_at')]
        shares = plan['shares']
        if not (all(map(number,(stop,target,expiry))) and plan['entry_ask'] == ask and 0 < stop < ask < target
                and now < expiry <= closed-300 and type(shares) is int and shares > 0
                and shares*ask*1.001 <= POLICY['notional_cap_usd']
                and shares*(ask*1.001-stop*.999+.01) <= POLICY['risk_cap_usd']):
            return None
        return plan
    except (ValueError, KeyError, TypeError, OverflowError, OSError, AttributeError):
        return None


def validated_outcome(rows, plan):
    if not isinstance(rows, list):
        return None
    result = next((r for r in rows if isinstance(r,dict) and r.get('strategy') == plan['strategy']), None)
    if not result or result.get('status') != 'modeled':
        return None
    scenarios = result.get('scenarios')
    if not isinstance(scenarios,list) or len(scenarios) != len(POLICY['cost_bps_per_side']):
        return None
    for scenario, bps in zip(scenarios,POLICY['cost_bps_per_side']):
        if not isinstance(scenario,dict) or scenario.get('bps_per_side') != bps:
            return None
        if not all(number(scenario.get(k)) for k in ('modeled_pnl_usd','matched_hold_pnl_usd')):
            return None
    return result


def advance(*, source=None, target=None, clock=time.time):
    """No provider calls. Reconcile owned trial IDs first, then admit timely evidence."""
    path = target or ledger_path()
    now = clock()
    if Path(path).resolve() == Path(source or database.DATABASE_PATH).resolve():
        raise ValueError('Friday requires a separate ledger')
    with closing(sqlite3.connect(path, timeout=2)) as db, db:
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA + friday_replay.SCHEMA)
        db.execute('BEGIN IMMEDIATE')
        decisions = {}
        def health(reason):
            friday_phase.regular_replay(db,now,reason,decisions)
            db.execute('INSERT OR REPLACE INTO friday_trial_health VALUES(1,?,?)',(now,reason))
            return reason
        source = source or database.DATABASE_PATH
        if not Path(source).exists():
            return health('stock_chart_source_unavailable')
        with closing(source_reader(source)) as stocks:
            stocks.execute('BEGIN')
            if not stocks.execute("SELECT 1 FROM sqlite_master WHERE name='day_trader_experiments'").fetchone():
                return health('stock_chart_source_unavailable')
            pending = db.execute('SELECT * FROM friday_trials WHERE closed IS NULL ORDER BY expires,chart_id').fetchall()
            waiting = False
            for owned in pending:
                if now < owned['expires'] + 60:
                    continue
                evidence = stocks.execute('SELECT * FROM day_trader_experiments WHERE request_id=? AND symbol=?',
                                          (owned['chart_id'],owned['symbol'])).fetchone()
                plan = json.loads(owned['plan'])
                outcome = None
                if evidence and evidence['outcome_json']:
                    recorded = decoded(evidence['decision_json'],dict) or {}
                    if plan in (recorded.get('setups') or []):
                        outcome = validated_outcome(decoded(evidence['outcome_json'],list),plan)
                if outcome:
                    db.execute('UPDATE friday_trials SET closed=?,outcome=? WHERE chart_id=? AND closed IS NULL',
                               (now,json.dumps(outcome,allow_nan=False),owned['chart_id']))
                else:
                    waiting = True
            if waiting:
                return health('due_exit_waiting_for_complete_forward_path')
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='observations'").fetchone():
                return health('awaiting_friday_scan')
            newest = db.execute('SELECT finished,reviewed FROM scans ORDER BY finished DESC LIMIT 1').fetchone()
            if not newest or not 0 <= now-newest['finished'] <= 90 or not newest['finished'] <= newest['reviewed'] <= now:
                return health('awaiting_fresh_friday_scan')
            candidates = db.execute("""SELECT symbol,evidence FROM observations WHERE finished=? AND reason='review_candidate'
                ORDER BY change DESC,spread,symbol""",(newest['finished'],)).fetchall()
            for symbol,gate,evidence in db.execute('SELECT symbol,reason,evidence FROM observations WHERE finished=? AND symbol IN (?,?,?,?)',(newest['finished'],*sorted(COHORT))):
                decisions[symbol]=(gate,{'scanner_completed':newest['finished'],'snapshot':decoded(evidence,dict)})
            admitted = 0
            for candidate in candidates:
                if db.execute('SELECT count(*) FROM friday_trials WHERE closed IS NULL').fetchone()[0] >= MAX_OPEN:
                    for other in candidates:
                        sym=other['symbol']
                        if decisions[sym][0]=='review_candidate':
                            decisions[sym]=('open_position_cap',decisions[sym][1])
                    return health('open_position_cap')
                symbol = candidate['symbol']
                gate=classify(symbol,json.loads(candidate['evidence']),now)
                if symbol not in COHORT or gate != 'review_candidate':
                    decisions[symbol]=(gate,decisions[symbol][1])
                    continue
                row = stocks.execute("""SELECT * FROM (SELECT * FROM day_trader_experiments ORDER BY rowid DESC LIMIT 64) WHERE symbol=?
                    AND frozen_at BETWEEN ? AND ? ORDER BY frozen_at DESC,request_id LIMIT 1""",(symbol,now-15,now)).fetchone()
                if not row or row['outcome_json']:
                    decisions[symbol]=('chart_already_resolved' if row else 'missing_timely_chart',decisions[symbol][1])
                    continue
                chart = decoded(row['decision_json'],dict)
                proof=decisions[symbol][1]
                proof['chart']=chart
                decisions[symbol]=('independent_chart_gate_failed',proof)
                plan = entry_plan(chart,now)
                if plan and chart['observed_at'] == row['frozen_at']:
                    inserted = db.execute('INSERT OR IGNORE INTO friday_trials VALUES(?,?,?,?,?,?,NULL,NULL)',
                                (row['request_id'],symbol,now,'regular_session',json.dumps(plan),plan['expires_at'])).rowcount
                    decisions[symbol]=("simulated_position_opened" if inserted else "owned_symbol_or_duplicate_chart",proof)
                    if inserted:
                        alert = None
                        if db.execute("SELECT 1 FROM sqlite_master WHERE name='alerts'").fetchone():
                            alert = db.execute('''SELECT event_id,received FROM alerts WHERE symbol=? AND reviewed BETWEEN ? AND ? AND reason='review_candidate' ORDER BY reviewed DESC,event_id LIMIT 1''',
                                               (symbol,now-15,now)).fetchone()
                        db.execute('INSERT INTO friday_trial_evidence VALUES(?,?,?,?,?,?)',
                                   (row['request_id'],newest['finished'],candidate['evidence'],row['decision_json'],
                                    alert['event_id'] if alert else None,alert['received'] if alert else None))
                        admitted += 1
            return health('simulated_position_opened' if admitted else 'no_new_timely_matching_chart')


def status(path=None):
    result = {'version':TRIAL_VERSION,'mode':'local_simulation','broker_enabled':False,
              'broker_opt_in_requested':os.getenv('FRIDAY_ALPACA_PAPER_ENABLED','').lower() == 'true',
              'positions':[], 'history':[], 'health':'awaiting_first_cycle'}
    target = Path(path or ledger_path())
    if not target.exists():
        return result
    with closing(source_reader(target)) as db:
        db.execute('BEGIN')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='friday_trial_health'").fetchone():
            return result
        health = db.execute('SELECT checked,reason FROM friday_trial_health WHERE id=1').fetchone()
        if health:
            result.update(health=health['reason'],checked=health['checked'])
        for key,where in [('positions','closed IS NULL'),('history','closed IS NOT NULL')]:
            rows = db.execute(f'SELECT t.*,e.alert_id FROM friday_trials t LEFT JOIN friday_trial_evidence e USING(chart_id) WHERE {where} ORDER BY admitted DESC LIMIT 20')
            for r in rows:
                plan = json.loads(r['plan'])
                result[key].append({'symbol':r['symbol'],'shares':plan['shares'],'phase':r['phase'],
                    'source_group':'scanner_and_timely_alert' if r['alert_id'] else 'scanner_only',
                    'admitted':r['admitted'],'reconciled':r['closed'],'outcome':json.loads(r['outcome']) if r['outcome'] else None})
    return result
