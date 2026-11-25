"""Ten versioned paper strategies authored by a local worker.

Expressions are a small interpreted language, never Python execution. Network,
files, credentials, accounting and validation cannot be accessed by expressions.
"""
import ast
import hashlib
import hmac
import json
import math
import os
import time
from functools import lru_cache


SLOTS = {f'research-{n:02}': (f'Research {n}', 'instant', 'research') for n in range(1, 11)}
MIN_COINS = 100
CAPITAL = 2000_000000
MAX_OPEN = 4
SCHEMA = '''
CREATE TABLE IF NOT EXISTS local_strategies(
 id TEXT PRIMARY KEY, slot TEXT NOT NULL, created REAL NOT NULL, rule TEXT NOT NULL,
 sources TEXT NOT NULL, status TEXT NOT NULL, report TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS local_active_slot ON local_strategies(slot) WHERE status IN ('testing','proven');
CREATE TABLE IF NOT EXISTS local_worker_state(name TEXT PRIMARY KEY,value TEXT NOT NULL);
'''
ENTRY_FIELDS = {'liquidity', 'age', 'volume_m5_usd', 'buys_m5', 'sells_m5', 'volume_ratio', 'rsi'}
EXIT_FIELDS = ENTRY_FIELDS | {'gain', 'peak', 'held_seconds'}
OPS = {ast.Add: lambda a,b:a+b, ast.Sub: lambda a,b:a-b, ast.Mult: lambda a,b:a*b,
       ast.Div: lambda a,b:a/b}
COMPARISONS = {ast.Gt: lambda a,b:a>b, ast.GtE: lambda a,b:a>=b, ast.Lt: lambda a,b:a<b,
               ast.LtE: lambda a,b:a<=b, ast.Eq: lambda a,b:a==b, ast.NotEq: lambda a,b:a!=b}


UNITS = {'liquidity':'usd', 'volume_m5_usd':'usd', 'age':'minutes',
         'buys_m5':'count', 'sells_m5':'count', 'volume_ratio':'ratio',
         'rsi':'rsi', 'gain':'percent', 'peak':'percent', 'held_seconds':'seconds'}


def check_units(node):
    """Conservative grammar: normalize ratios only between matching quantities."""
    if isinstance(node, ast.Name):
        return UNITS[node.id]
    if isinstance(node, ast.Constant):
        return 'bool' if type(node.value) is bool else 'literal'
    if isinstance(node, ast.UnaryOp):
        unit = check_units(node.operand)
        if isinstance(node.op, ast.Not):
            if unit != 'bool':
                raise ValueError('not requires a comparison')
            return 'bool'
        if unit == 'bool':
            raise ValueError('Boolean arithmetic is unsupported')
        return unit
    if isinstance(node, ast.BoolOp):
        if any(check_units(value) != 'bool' for value in node.values):
            raise ValueError('and/or require comparisons')
        return 'bool'
    if isinstance(node, ast.Compare):
        units = [check_units(value) for value in [node.left, *node.comparators]]
        for left, right in zip(units, units[1:]):
            if 'bool' in (left,right) or (left != right and 'literal' not in (left,right)):
                raise ValueError('Comparison mixes incompatible units')
        return 'bool'
    if isinstance(node, ast.BinOp):
        left, right = check_units(node.left), check_units(node.right)
        if 'bool' in (left,right):
            raise ValueError('Boolean arithmetic is unsupported')
        if isinstance(node.op, ast.Div):
            if right == 'literal':
                return left
            if left == right:
                return 'ratio'
            raise ValueError('Divide matching quantities only; compare volume_ratio directly')
        if isinstance(node.op, ast.Mult):
            if left == 'literal':
                return right
            if right == 'literal':
                return left
            raise ValueError('Multiply a quantity by a numeric constant only')
        if left == right:
            return left
        if left == 'literal':
            return right
        if right == 'literal':
            return left
        raise ValueError('Arithmetic mixes incompatible units')
    raise ValueError('Unsupported expression')


def check_exit_logic(root):
    """Check profit/trailing conflicts through nested AND/OR expressions.

    Normalize to bounded conjunction cases. A profit-cap case shadows a
    trailing case when its other requirements are shared by the trailing case.
    This remains a conservative check, not a proof of strategy correctness.
    """
    def cases(node):
        if not isinstance(node, ast.BoolOp):
            return [[node]]
        groups = [cases(child) for child in node.values]
        if isinstance(node.op, ast.Or):
            result = [case for group in groups for case in group]
        else:
            result = [[]]
            for group in groups:
                if len(result) * len(group) > 64:
                    raise ValueError('Exit logic expands beyond 64 cases')
                result = [left + right for left in result for right in group]
        if len(result) > 64:
            raise ValueError('Exit logic expands beyond 64 cases')
        return result

    groups = cases(root)
    def threshold(node, field):
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                and node.left.id == field and len(node.ops) == 1
                and isinstance(node.ops[0], (ast.GtE, ast.Gt))
                and isinstance(node.comparators[0], ast.Constant)):
            return node.comparators[0].value
        return None

    targets = [(threshold(node, 'gain'), {ast.dump(other) for other in group if other is not node})
               for group in groups for node in group if threshold(node, 'gain') is not None]
    for group in groups:
        arms = [threshold(node, 'peak') for node in group if threshold(node, 'peak') is not None]
        trailing = any(
            isinstance(node, ast.Compare) and len(node.ops) == 1
            and isinstance(node.left, ast.Name) and node.left.id == 'gain'
            and isinstance(node.ops[0], (ast.LtE, ast.Lt))
            and isinstance(node.comparators[0], ast.BinOp)
            and isinstance(node.comparators[0].op, ast.Sub)
            and isinstance(node.comparators[0].left, ast.Name)
            and node.comparators[0].left.id == 'peak' for node in group)
        signatures = {ast.dump(node) for node in group}
        if trailing and any(target <= arm and requirements <= signatures
                            for target, requirements in targets for arm in arms):
            raise ValueError('Profit target must exceed the trailing arm level, or remove the target')


@lru_cache(maxsize=128)
def parse(expression, exit_rule=False):
    if not isinstance(expression, str) or not 1 <= len(expression) <= 500:
        raise ValueError('Expression must be 1–500 characters')
    try:
        root = ast.parse(expression, mode='eval').body
    except (SyntaxError, RecursionError):
        raise ValueError('Invalid strategy expression') from None
    nodes = list(ast.walk(root))
    if len(nodes) > 100:
        raise ValueError('Expression too complex')
    allowed = (ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare, ast.Name, ast.Constant,
               ast.Load, ast.And, ast.Or, ast.Not, ast.USub, *OPS, *COMPARISONS)
    for node in nodes:
        if not isinstance(node, allowed):
            raise ValueError('Unsupported expression operation')
        if isinstance(node, ast.Name) and node.id not in (EXIT_FIELDS if exit_rule else ENTRY_FIELDS):
            raise ValueError('Unknown strategy variable')
        if isinstance(node, ast.Constant) and (type(node.value) not in (int, float, bool)
                or not math.isfinite(node.value) or abs(node.value) > 100_000_000):
            raise ValueError('Invalid constant')
    if not isinstance(root, (ast.BoolOp, ast.Compare)):
        raise ValueError('Strategy must return a comparison')
    check_units(root)
    if exit_rule:
        check_exit_logic(root)
    return root


def run(expression, values, exit_rule=False):
    def visit(node):
        if isinstance(node, ast.Name):
            value = values.get(node.id)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('Missing observation')
            return value
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BoolOp):
            # Short-circuit permits guarding divisions and optional observations.
            if isinstance(node.op, ast.And):
                return all(visit(value) for value in node.values)
            return any(visit(value) for value in node.values)
        if isinstance(node, ast.UnaryOp):
            return not visit(node.operand) if isinstance(node.op, ast.Not) else -visit(node.operand)
        if isinstance(node, ast.BinOp):
            value = OPS[type(node.op)](visit(node.left), visit(node.right))
            if not math.isfinite(value) or abs(value) > 1e18:
                raise ValueError('Arithmetic out of range')
            return value
        if isinstance(node, ast.Compare):
            left = visit(node.left)
            for op, right_node in zip(node.ops, node.comparators):
                right = visit(right_node)
                if not COMPARISONS[type(op)](left, right):
                    return False
                left = right
            return True
        raise ValueError('Invalid expression')
    try:
        return bool(visit(parse(expression, exit_rule)))
    except (ValueError, ZeroDivisionError, OverflowError, TypeError):
        return False


def validate(rule):
    if not isinstance(rule, dict) or set(rule) != {'name','hypothesis','entry','exit'}:
        raise ValueError('Expected name, hypothesis, entry, exit')
    for key, maximum in (('name',80),('hypothesis',1500)):
        if not isinstance(rule[key], str) or not 1 <= len(rule[key]) <= maximum:
            raise ValueError('Invalid description')
    if not isinstance(rule['entry'],str) or not isinstance(rule['exit'],str):
        raise ValueError('Expressions must be strings')
    parse(rule['entry'])
    parse(rule['exit'], True)
    return rule


def observations(row, evidence):
    return {'liquidity': row.get('liquidity_usd'), 'age': row.get('age_minutes'),
            **{k: row.get(k) for k in ('volume_m5_usd','buys_m5','sells_m5')},
            **{k:(evidence.get('chart') or {}).get(k) for k in ('volume_ratio','rsi')}}


def authorized(header):
    secret = os.getenv('JARVIS_LOCAL_WORKER_TOKEN', '')
    return len(secret) >= 32 and hmac.compare_digest((header or '').encode(), ('Bearer ' + secret).encode())


def record_event(conn, at, ident, kind, detail):
    # Server-only import: the laptop also uses this module as a standalone validator.
    import jarvis_hourly_mail
    jarvis_hourly_mail.event(conn,at,ident,kind,detail)


def submit(api, payload, now=None):
    now = time.time() if now is None else now
    if not isinstance(payload, dict) or set(payload) != {'rule','sources'}:
        raise ValueError('Expected rule and sources')
    rule = validate(payload['rule'])
    sources = payload['sources']
    if not isinstance(sources, list) or not 1 <= len(sources) <= 10:
        raise ValueError('Expected research sources')
    from urllib.parse import urlsplit
    for source in sources:
        if (not isinstance(source, dict) or set(source) != {'url','title','snippet'}
                or any(not isinstance(v,str) for v in source.values())
                or len(source['url']) > 2000 or len(source['title']) > 200 or len(source['snippet']) > 2000
                or urlsplit(source['url']).scheme != 'https'):
            raise ValueError('Invalid research source')
    # Semantic identity prevents renaming the same expressions to reset a failed trial.
    identity = ast.dump(parse(rule['entry'])) + ast.dump(parse(rule['exit'], True))
    ident = hashlib.sha256(identity.encode()).hexdigest()[:24]
    with api.connection() as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        state, _ = api.read(conn)
        if not state['enabled']:
            raise ValueError('Paper engine is paused')
        if conn.execute('SELECT 1 FROM local_strategies WHERE id=?',(ident,)).fetchone():
            record_event(conn,now,ident,'duplicate',{'name':rule['name']})
            return {'id':ident,'status':'duplicate'}
        occupied = {r['slot'] for r in conn.execute("SELECT slot FROM local_strategies WHERE status IN ('testing','proven')")}
        slot = next((slot for slot in SLOTS if slot not in occupied and not state['bots'][slot]['positions']), None)
        if slot is None:
            return {'status':'full'}
        portfolio = state['bots'][slot]
        if not portfolio.get('local_funded'):
            portfolio.update(cash=CAPITAL, funding=CAPITAL, local_funded=True)
        if portfolio['cash'] < api.BUY_COST:
            return {'status':'capital_exhausted','slot':slot}
        conn.execute('INSERT INTO local_strategies(id,slot,created,rule,sources,status) VALUES (?,?,?,?,?,?)',
                     (ident,slot,now,json.dumps(rule),json.dumps(sources),'testing'))
        record_event(conn,now,ident,'added',{'name':rule['name'],'slot':slot})
        state.setdefault('local_rules',{})[slot] = {'id':ident,'created':now,**rule}
        conn.execute('UPDATE run SET state=?,revision=revision+1 WHERE id=1',(json.dumps(state),))
        conn.execute("INSERT OR REPLACE INTO local_worker_state VALUES ('last_submit',?)",(str(now),))
    return {'id':ident,'slot':slot,'status':'testing'}


def entry_allowed(state, slot, row, evidence, now):
    rule = state.get('local_rules',{}).get(slot)
    portfolio = state['bots'][slot]
    return (rule and row['first'] >= rule['created'] and 0 <= now-row['first'] <= 900
            and len(portfolio['positions']) < MAX_OPEN and portfolio['cash'] >= 50_250000
            and run(rule['entry'], observations(row,evidence)))


def exit_reason(position, gain, now, row, evidence):
    rule = position['policy'].get('local_rule')
    if not rule:
        return None
    # Fixed simulator guardrails cannot be edited by generated expressions.
    if gain <= -10:
        return 'Research safety stop (sampled quote)'
    if now-position['opened'] >= 3600:
        return 'Research maximum hold (sampled quote)'
    values = {**observations(row,evidence),'gain':gain,'peak':position['peak'],
              'held_seconds':now-position['opened']}
    if run(rule['exit'], values, True):
        return 'Research strategy exit'
    return None


def review(conn, state, now):
    for slot, rule in list(state.get('local_rules',{}).items()):
        rows = conn.execute("SELECT pnl,mint FROM trades WHERE bot=? AND json_extract(data,'$.policy.local_rule.id')=? ORDER BY opened,id",
                            (slot,rule['id'])).fetchall()
        total_closed = len(rows)
        rows = rows[-MIN_COINS:]
        pnl = [r['pnl'] for r in rows]
        coins = len({r['mint'] for r in rows})
        middle = len(pnl)//2
        halves = (pnl[:middle],pnl[middle:])
        consistent = all(sum(p)-max([0,*p]) > 0 for p in halves)
        report = {'coins':coins,'closed':total_closed,'net':sum(pnl)/1e6,
                  'net_without_best':(sum(pnl)-max([0,*pnl]))/1e6,
                  'open':len(state['bots'][slot]['positions']), 'halves_positive':consistent}
        status = 'testing'
        if coins >= MIN_COINS:
            if sum(pnl) <= 0:
                status = 'failed'
            elif report['net_without_best'] > 0 and consistent:
                status = 'proven'
            else:
                report['note'] = 'Profitable but not robust across both halves without their biggest winners; keep testing.'
            if status == 'failed':
                del state['local_rules'][slot]
        elif now-rule['created'] >= 24*3600 and not rows and not state['bots'][slot]['positions']:
            # Inactive is explicitly not a profitability failure.
            status = 'inactive'
            del state['local_rules'][slot]
        previous=conn.execute('SELECT status FROM local_strategies WHERE id=?',(rule['id'],)).fetchone()
        if previous and previous['status'] != status:
            record_event(conn,now,rule['id'],status,{'name':rule['name']})
        conn.execute('UPDATE local_strategies SET report=?,status=? WHERE id=?',
                     (json.dumps(report),status,rule['id']))
        if status == 'proven' and rule['id'] not in state.setdefault('local_proven',[]):
            state['local_proven'].append(rule['id'])
        if (status == 'proven' and (not state.get('strategy') or state.get('entry_guard'))
                and (state.get('strategy') or {}).get('version') != 'local-'+rule['id']):
            record_event(conn,now,rule['id'],'promoted',{'name':rule['name']})
            state['strategy'] = {'source':slot, 'entry':'instant', 'exit':'research',
                'version':'local-'+rule['id'], 'activated_at':now, 'min_liquidity':0, 'min_age':0,
                'entry_filter':'baseline', 'local_rule':dict(rule),
                'validation_pnl':report['net_without_best']*1e6, 'validation_count':coins}
            state.pop('entry_guard',None)


def context(api, now=None):
    now = time.time() if now is None else now
    with api.connection() as conn:
        state, _ = api.read(conn)
        rows = [{**dict(row),'rule':json.loads(row['rule']),'report':json.loads(row['report']),'sources':json.loads(row['sources'])}
                for row in conn.execute("SELECT id,slot,created,rule,status,report,sources FROM local_strategies ORDER BY (status IN ('testing','proven')) DESC,created DESC LIMIT 30")]
        occupied = {r['slot'] for r in conn.execute("SELECT slot FROM local_strategies WHERE status IN ('testing','proven')")}
        free = [slot for slot in SLOTS if slot not in occupied and not state['bots'][slot]['positions']
                and (not state['bots'][slot].get('local_funded') or state['bots'][slot]['cash'] >= api.BUY_COST)]
        return {'enabled':state['enabled'],'available_slots':free,'strategies':rows,
                'entry_status':api.local_entry_status(conn,state,now),
                'minimum_coins':MIN_COINS,'entry_fields':sorted(ENTRY_FIELDS),'exit_fields':sorted(EXIT_FIELDS),
                'outcome_warnings':(state.get('outcome_review') or {}).get('warnings',[]),
                'results':[dict(r) for r in conn.execute("SELECT bot,count(*) AS closed,sum(pnl)/1000000.0 AS net FROM trades GROUP BY bot")],
                'last_seen':next((float(r['value']) for r in conn.execute("SELECT value FROM local_worker_state WHERE name='last_seen'")),None)}


def guard(conn, state):
    strategy = state.get('strategy') or {}
    if not strategy.get('local_rule') or state.get('entry_guard'):
        return
    rows = conn.execute("SELECT pnl FROM trades WHERE bot='jarvis' AND json_extract(data,'$.policy.version')=? ORDER BY id DESC LIMIT 100",
                        (strategy['version'],)).fetchall()
    if len(rows) >= MIN_COINS and sum(r['pnl'] for r in rows) <= 0:
        state['entry_guard'] = {'version':strategy['version'], 'reason':'Local strategy paused after 100 Jarvis closes with nonpositive net P/L; exits continue.'}


def heartbeat(api):
    with api.connection() as conn, conn:
        conn.execute("INSERT OR REPLACE INTO local_worker_state VALUES ('last_seen',?)",(str(time.time()),))
