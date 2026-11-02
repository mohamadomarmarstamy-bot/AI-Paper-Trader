"""One reviewed PAPER-only void, retaining original rows and an audit trail.

A bad first trim changes all subsequent profit protection for the lot. Void the
whole simulated round trip rather than invent a historical replacement fill.
No live ledger, signer, wallet transaction, setting or source cursor is touched.
"""
from copy import deepcopy
import json
import math
import time

REVIEW = 'fear-dust-price-2026-11-02-v1'
LOT = 'c17f2b98156f450885ee39fa9b29684c81766b3dac92f0a791222c7b24d03be4'
MINT = 'CHtXwJ3qa2ozS9vQkxhR3MmNwkoUqkh3icGXgZPepump'
WALLET = 'J9TYAsWWidbrcZybmLSfrLzryANf4CgJBLdvwdGuC8MB'
EXPECTED = {
    '3bdc277b-7cf3-4208-95fc-d0c48b87ad14': (6567.181286833308, True),
    'cf16ce13-7699-4653-b8d9-5819ef10b8e3': (-2.283993235226056, False),
}
BASIS = 'FEAR dust-swap price invalidated the simulated trim and dependent exit. Entire paper lot voided; no replacement fill assumed.'


def matches(rows):
    return (len(rows) == 2 and all(
        r.get('lot_id') == LOT and r.get('mint') == MINT and r.get('wallet') == WALLET
        and math.isclose(r['quantity'], 615427.0325093176, rel_tol=1e-12)
        and r['cost_usd'] == 25.05
        and 1793656730 <= r['closed_at'] <= 1793656761 for r in rows)
        and sorted(r['partial_exit'] for r in rows) == [False, True])


def void(rows, at):
    delta = sum(r['pnl_usd'] for r in rows)
    for r in rows:
        r.update(voided=True, price_review_id=REVIEW, voided_at=at, void_reason=BASIS)
    return delta


def repair(conn, now=None):
    """Atomic, idempotent, exact-receipt repair, deferred while a cycle holds its lease."""
    now = time.time() if now is None else now
    # The caller owns a fresh connection; no nested transaction or network I/O.
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_copy_closes'").fetchone()
    if not exists: return False
    conn.execute('BEGIN IMMEDIATE')
    try:
        saved = conn.execute('SELECT state_json,revision,lease_until FROM solana_copy_state WHERE id=1').fetchone()
        if not saved:
            conn.rollback(); return False
        state = json.loads(saved[0])
        if any(r['id'] == REVIEW for r in state.get('paper_price_reviews', [])) or saved[2] > now:
            conn.rollback(); return False
        records = conn.execute("SELECT id,details_json FROM solana_copy_closes WHERE id IN (?,?) ORDER BY closed_at", tuple(EXPECTED)).fetchall()
        rows = [json.loads(r[1]) for r in records]
        if not matches(rows) or {r['id'] for r in rows} != set(EXPECTED) or any(key != r['id'] for (key, _), r in zip(records, rows)) or LOT in state['positions'] or any(r.get('voided') for r in rows):
            conn.rollback(); return False
        if any(r['id'] not in EXPECTED or r['partial_exit'] != EXPECTED[r['id']][1]
               or not math.isclose(r['pnl_usd'], EXPECTED[r['id']][0], abs_tol=1e-8) for r in rows):
            conn.rollback(); return False
        original = deepcopy(rows)
        delta = void(rows, now)
        state['cash_usd'] -= delta
        # Remove affected/post-event historical points rather than leaving a
        # false equity spike or fabricating corrected minute-by-minute marks.
        state['history'] = [r for r in state.get('history', []) if r['at'] < min(x['opened_at'] for x in rows)]
        state.setdefault('paper_price_reviews', []).append({
            'id': REVIEW, 'lot_id': LOT, 'mint': MINT, 'symbol': 'FEAR', 'at': now,
            'removed_pnl_usd': delta, 'basis': BASIS, 'original_exits': original,
            'history_gap_until': now,
        })
        for r in rows:
            conn.execute('UPDATE solana_copy_closes SET details_json=? WHERE id=?', (json.dumps(r, allow_nan=False), r['id']))
        # The matched experiments received the identical entries/quotes but have
        # independent close IDs. Validate the complete lot before adjusting them.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_trailing_comparison'").fetchone():
            comparison = conn.execute('SELECT state_json FROM solana_trailing_comparison WHERE id=1').fetchone()
            if comparison:
                experiment = json.loads(comparison[0])
                for arm in experiment['arms'].values():
                    affected = [r for r in arm['closed'] if r.get('lot_id') == LOT]
                    if not affected: continue
                    if not matches(affected) or LOT in arm['positions'] or any(r.get('voided') for r in affected):
                        raise ValueError('FEAR comparison review does not match; repair rolled back')
                    if not any(math.isclose(r['pnl_usd'], 6567.181286833308, abs_tol=1e-8) and r['partial_exit'] for r in affected):
                        raise ValueError('FEAR comparison trim does not match')
                    arm['cash_usd'] -= void(affected, now)
                    # Original peaks/drawdowns are contaminated; do not report
                    # them as valid, nor invent a historical reconstruction.
                    arm['drawdown_invalidated'] = REVIEW
                conn.execute('UPDATE solana_trailing_comparison SET state_json=? WHERE id=1', (json.dumps(experiment, allow_nan=False),))
        conn.execute('UPDATE solana_copy_state SET state_json=?,revision=revision+1 WHERE id=1', (json.dumps(state, allow_nan=False),))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
