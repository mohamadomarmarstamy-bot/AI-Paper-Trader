"""Matched Solana paper entry experiments with delayed, exact-size quotes."""
import json

DOLLAR = 1_000_000
ENTRY_DELAY = 8
ENTRY_WINDOW = 300
HOLD_SECONDS = 900
ROUTE_GRACE = 300
WINDOW = 25
MAX_OPEN = 6
START_INTERVAL = 120
MODES = {
    'instant': 'Instant',
    'pullback': 'Pullback',
    'breakout_retest': 'Chart retest',
    'chart_social': 'Chart retest + Bluesky',
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS experiment_coins(
    id INTEGER PRIMARY KEY, mint TEXT NOT NULL UNIQUE, opened REAL NOT NULL,
    done REAL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS experiment_done ON experiment_coins(done,id);
CREATE TABLE IF NOT EXISTS experiment_reviews(
    checkpoint INTEGER PRIMARY KEY, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiment_state(
    id INTEGER PRIMARY KEY CHECK (id=1), lease REAL DEFAULT 0,
    checkpoint REAL DEFAULT 0, policy TEXT NOT NULL DEFAULT '{}'
);
INSERT OR IGNORE INTO experiment_state(id) VALUES (1);
"""


def signal(mode, row, evidence, now):
    if mode == 'instant':
        return True
    if mode == 'pullback':
        return row.get('pullback') is not None and 0 <= now - row['pullback'] <= 90
    if (evidence.get('chart') or {}).get('pattern') != 'breakout_retest':
        return False
    return mode == 'breakout_retest' or evidence.get('bluesky', {}).get('mint_authors', 0) >= 2


def new_coin(row, now):
    return {'mint': row['mint'], 'pair': row['pair'], 'opened': now,
            'arms': {mode: {'status': 'waiting', 'misses': 0, 'entry_misses': 0} for mode in MODES}}


def advance(coin, row, evidence, now, budget, buy_cost):
    """Refresh signals and build quote jobs. All arms share the same exit."""
    jobs = {}
    age = now - coin['opened']
    for mode, arm in coin['arms'].items():
        status = arm['status']
        if status == 'waiting':
            if age >= ENTRY_WINDOW:
                arm.update(status='skipped', pnl=0, settled=now)
            elif row and row['pair'] == coin['pair'] and 0 <= now - row['last'] <= 90 and signal(mode, row, evidence, now):
                arm.update(status='entry', due=now + ENTRY_DELAY, signal_at=now)
        if arm['status'] == 'entry':
            if age >= ENTRY_WINDOW:
                arm.update(status='skipped', pnl=0, settled=now)
            elif now >= arm['due']:
                jobs.setdefault(('buy', budget), []).append(arm)
        elif status == 'open':
            if now >= arm['bought'] + HOLD_SECONDS + ROUTE_GRACE:
                arm.update(status='unpriced', pnl=-buy_cost, settled=now)
            elif now >= arm['due']:
                jobs.setdefault(('sell', arm['size']), []).append(arm)
    return jobs


def quotes(arms, side, value, now, buy_cost, sell_fee, exit_floor):
    for arm in arms:
        if side == 'buy':
            if value is None:
                arm['entry_misses'] += 1
                arm['due'] = now + 15
            else:
                arm.update(status='open', size=value, bought=now, due=now + ENTRY_DELAY, peak=0,
                           entry_wait=now - arm['signal_at'])
            continue
        if value is None:
            arm['misses'] += 1
            arm['due'] = now + 20
            continue
        wait = now - arm['due']
        proceeds = max(0, value - sell_fee)
        gain = (proceeds / buy_cost - 1) * 100
        arm['peak'] = max(gain, arm['peak'])
        arm.update(mark=proceeds, due=now + ENTRY_DELAY)
        floor = exit_floor(arm['peak'])
        if gain <= floor or now >= arm['bought'] + HOLD_SECONDS:
            arm.update(status='closed', pnl=proceeds - buy_cost, settled=now, exit_wait=wait,
                       gap=max(0, floor - gain) if gain <= floor else None)


def table(coins):
    return {mode: metrics([coin['arms'][mode] for coin in coins]) for mode in MODES}


def metrics(arms):
    outcomes = [arm for arm in arms if 'pnl' in arm]
    pnl = peak = drawdown = 0
    # Accumulate adverse outcomes, including the proceeds haircut for unpriced exits.
    for arm in sorted(outcomes, key=lambda outcome: outcome.get('settled', 0)):
        pnl += arm['pnl']
        peak = max(peak, pnl)
        drawdown = max(drawdown, peak - pnl)
    closed = [arm for arm in arms if arm['status'] == 'closed']
    return {'opportunities': len(arms), 'closed': sum(a['status'] == 'closed' for a in arms),
            'skipped': sum(a['status'] == 'skipped' for a in arms), 'unpriced': sum(a['status'] == 'unpriced' for a in arms),
            'open': sum(a['status'] == 'open' for a in arms), 'failed_exits': sum(a['misses'] for a in arms),
            'failed_entries': sum(a['entry_misses'] for a in arms), 'net': pnl / DOLLAR,
            'per_opportunity': pnl / (len(outcomes) * DOLLAR) if outcomes else None,
            'avg_entry_wait': sum(a.get('entry_wait', 0) for a in closed) / len(closed) if closed else None,
            'avg_exit_wait': sum(a.get('exit_wait', 0) for a in closed) / len(closed) if closed else None,
            'drawdown': drawdown / DOLLAR}


def score(coins, mode):
    arms = [coin['arms'][mode] for coin in coins]
    if sum(a['status'] == 'closed' for a in arms) < 8:
        return None
    pnl = [a['pnl'] for a in arms]
    return sum(pnl) - max(0, max(pnl))


def review(policy, coins):
    """Review fixed batches of fresh matched coins. Require two wins in succession."""
    policy = dict(policy)
    current = policy.get('mode', 'instant')
    benchmark = score(coins, 'instant')
    ranks = {mode: score(coins, mode) for mode in MODES}
    if benchmark is None:
        policy.update(candidate=None, streak=0, result='Too few priced benchmark exits to validate this batch.')
        return policy
    if current != 'instant' and (ranks[current] is None or ranks[current] < benchmark):
        policy.update(mode='instant', candidate=None, streak=0, result='Rolled back after a weak fresh batch.')
        return policy
    threshold = max(0, benchmark, ranks[current] or 0)
    winners = [mode for mode, value in ranks.items() if value is not None and value > threshold]
    candidate = max(winners, key=ranks.get) if winners else None
    streak = policy.get('streak', 0) + 1 if candidate and candidate == policy.get('candidate') else int(bool(candidate))
    policy.update(candidate=candidate, streak=streak)
    if streak >= 2:
        policy.update(mode=candidate, candidate=None, streak=0, result='Promoted after two fresh matched batches.')
    elif candidate:
        policy['result'] = 'Challenger led one fresh batch.'
    else:
        policy['result'] = 'No challenger beat the benchmark.'
    return policy


def read_board(conn):
    policy = json.loads(conn.execute('SELECT policy FROM experiment_state WHERE id=1').fetchone()[0])
    finished = conn.execute('SELECT data FROM experiment_coins WHERE done IS NOT NULL ORDER BY id DESC LIMIT 250').fetchall()
    active = conn.execute('SELECT data FROM experiment_coins WHERE done IS NULL ORDER BY id').fetchall()
    coins = [json.loads(r['data']) for r in reversed(finished)]
    reviews = conn.execute('SELECT data FROM experiment_reviews ORDER BY checkpoint DESC LIMIT 3').fetchall()
    live = [json.loads(r['data']) for r in active]
    return {'policy': policy, 'completed': len(coins), 'active': len(live),
            'reviews': [json.loads(r['data']) for r in reviews],
            'arms': {mode: {'name': MODES[mode], **result} for mode, result in table(coins).items()},
            'running': {mode: sum(coin['arms'][mode]['status'] == 'open' for coin in live) for mode in MODES}}


def update_policy(conn):
    row = conn.execute('SELECT checkpoint,policy FROM experiment_state WHERE id=1').fetchone()
    while True:
        batch = conn.execute('SELECT id,done,data FROM experiment_coins WHERE id>? ORDER BY id LIMIT ?',
                             (row['checkpoint'], WINDOW)).fetchall()
        if len(batch) < WINDOW or any(item['done'] is None for item in batch):
            return
        coins = [json.loads(item['data']) for item in batch]
        policy = review(json.loads(row['policy']), coins)
        packed = json.dumps(policy)
        checkpoint = batch[-1]['id']
        conn.execute('UPDATE experiment_state SET checkpoint=?,policy=? WHERE id=1', (checkpoint, packed))
        conn.execute('INSERT INTO experiment_reviews(checkpoint,data) VALUES (?,?)',
                     (checkpoint, json.dumps({'checkpoint': checkpoint, **policy, 'results': table(coins)})))
        row = {'checkpoint': checkpoint, 'policy': packed}
