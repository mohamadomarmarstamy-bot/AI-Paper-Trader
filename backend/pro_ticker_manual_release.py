"""Explicit operator-approved virtual PAPER release service.

Only the opt-in authenticated operator router calls this module in the app.
No scheduler, scanner or broker submission calls this module.
A trusted operator must independently verify PAPER provenance and approve the
exact review digest. A generic reader's output is never automatic authority.
The service re-fetches orders and rechecks all evidence before committing.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import time
import uuid

from database import get_connection
from pro_ticker_broker_audit_inputs import validate_paper_snapshot_inputs
from pro_ticker_broker_snapshot_audit import _audit_snapshot

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pro_ticker_manual_release_reviews (
    id TEXT PRIMARY KEY,
    reservation_id TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    approved_by TEXT,
    approved_at REAL,
    consumed_at REAL
);
CREATE TABLE IF NOT EXISTS pro_ticker_budget_release_events (
    reservation_id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL UNIQUE,
    evidence_digest TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    released_cents INTEGER NOT NULL CHECK(released_cents > 0),
    released_at TEXT NOT NULL
);
"""
_BASE = dict(paper=True, verified=False, preview_only=True,
             release_authorized=False, budget_released=False, database_updated=False,
             broker_origin_authenticated=False)


def _uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('Canonical UUID required.')
    return value


def _ledger(conn, reservation_id):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not {'pro_ticker_paper_reservations', 'pro_ticker_paper_exit_links',
            'pro_ticker_strategy_fill_events', 'pro_ticker_strategy_positions'}.issubset(tables):
        raise ValueError('PAPER accounting schema incomplete.')
    reservation = conn.execute(
        'SELECT * FROM pro_ticker_paper_reservations WHERE id = ?', (reservation_id,),
    ).fetchone()
    if reservation is None:
        raise ValueError('PAPER reservation missing.')
    symbol = reservation['symbol']
    links = conn.execute(
        'SELECT * FROM pro_ticker_paper_exit_links WHERE reservation_id = ? ORDER BY broker_order_id',
        (reservation_id,),
    ).fetchall()
    # Include every symbol fill and position, even records not attributed to
    # this reservation: changing shared accounting invalidates the approval.
    fills = conn.execute(
        'SELECT * FROM pro_ticker_strategy_fill_events WHERE symbol = ? OR broker_order_id = ? '
        'OR broker_order_id IN (SELECT broker_order_id FROM pro_ticker_paper_exit_links '
        'WHERE reservation_id = ?) ORDER BY event_id',
        (symbol, reservation['broker_order_id'], reservation_id),
    ).fetchall()
    position = conn.execute(
        'SELECT * FROM pro_ticker_strategy_positions WHERE symbol = ?', (symbol,),
    ).fetchone()
    if position is None or position['shares'] != 0:
        raise ValueError('PAPER strategy position is open or missing.')
    symbol_balance = sum(row['shares'] if row['side'] == 'buy' else -row['shares']
                         for row in fills if row['symbol'] == symbol)
    if symbol_balance != position['shares']:
        raise ValueError('PAPER symbol fill balance conflicts with position ledger.')
    duplicate_entry = conn.execute(
        'SELECT COUNT(*) FROM pro_ticker_paper_reservations WHERE broker_order_id = ?',
        (reservation['broker_order_id'],),
    ).fetchone()[0]
    if duplicate_entry != 1:
        raise ValueError('PAPER entry ownership is ambiguous.')
    return dict(reservation=dict(reservation), links=[dict(row) for row in links],
                fills=[dict(row) for row in fills], position=dict(position))


def _fetch(ledger, reader):
    if not callable(reader):
        raise ValueError('Trusted PAPER order reader required.')
    entry = reader(ledger['reservation']['broker_order_id'])
    exits = {link['broker_order_id']: reader(link['broker_order_id']) for link in ledger['links']}
    error = validate_paper_snapshot_inputs(entry, exits)
    if error:
        raise ValueError('PAPER release snapshot invalid: ' + error)
    return entry, exits


def _evidence_json(ledger, entry, exits):
    # Only verified immutable/terminal order identity and execution fields bind
    # approval. Cosmetic broker timestamps or prices cannot alter share proof.
    fields = ('id', 'client_order_id', 'symbol', 'side', 'qty', 'filled_qty', 'status')
    def order(value):
        return {field: value.get(field) for field in fields}
    data = dict(ledger=ledger, entry=order(entry),
                exits={key: order(value) for key, value in exits.items()})
    return json.dumps(data, sort_keys=True, allow_nan=False)


def _digest(ledger, entry, exits):
    return hashlib.sha256(_evidence_json(ledger, entry, exits).encode()).hexdigest()


def _verify(conn, reservation_id, entry, exits):
    result = _audit_snapshot(conn, reservation_id, entry, exits, _BASE)
    if result.get('verified') is not True:
        raise ValueError('PAPER closure evidence blocked: ' + result['reason'])


def prepare_manual_paper_release(reservation_id, *, fetch_paper_order):
    """Persist a five-minute review; never approve or release any budget.

    Caller binds a PAPER-only reader. The operator must separately verify its
    provenance. No claim of broker authentication is made by this function.
    """
    _uuid(reservation_id)
    with closing(get_connection()) as conn:
        conn.executescript(_SCHEMA)
        conn.execute('BEGIN')
        initial = _ledger(conn, reservation_id)
        conn.rollback()
    entry, exits = _fetch(initial, fetch_paper_order)
    review_id = str(uuid.uuid4())
    now = time.time()
    with closing(get_connection()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        current = _ledger(conn, reservation_id)
        if current != initial:
            raise ValueError('PAPER accounting changed during broker fetch; retry review.')
        _verify(conn, reservation_id, entry, exits)
        digest = _digest(current, entry, exits)
        conn.execute(
            'INSERT INTO pro_ticker_manual_release_reviews '
            '(id, reservation_id, evidence_digest, evidence_json, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)',
            (review_id, reservation_id, digest, _evidence_json(current, entry, exits), now, now + 300),
        )
    return {**_BASE, 'database_updated': True, 'review_id': review_id, 'reservation_id': reservation_id,
            'evidence_digest': digest, 'expires_at': now + 300,
            'symbol': current['reservation']['symbol'],
            'reserved_cents': current['reservation']['notional_cents'],
            'filled_entry_shares': int(float(entry['filled_qty'])),
            'filled_exit_shares': sum(int(float(order['filled_qty'])) for order in exits.values()),
            'entry_order_id': current['reservation']['broker_order_id'],
            'exit_order_ids': sorted(exits),
            'reason': 'requires_independent_operator_paper_provenance_approval'}


def approve_manual_paper_release(review_id, *, evidence_digest, approved_by,
                                 independent_paper_provenance_confirmed=False):
    """Trusted operator boundary; NEVER accept operator identity from request input.

    Approval must come from an authenticated operator who has independently
    checked the PAPER account, linked orders and the displayed evidence digest.
    The operator router authenticates same-origin requests before accepting an
    explicit human attestation. There are no automatic callers.
    """
    _uuid(review_id)
    if independent_paper_provenance_confirmed is not True:
        raise ValueError('Independent operator verification of PAPER provenance required.')
    if not isinstance(approved_by, str) or not approved_by.strip() or approved_by != approved_by.strip() or len(approved_by) > 200:
        raise ValueError('Authenticated operator identity required.')
    with closing(get_connection()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        review = conn.execute('SELECT * FROM pro_ticker_manual_release_reviews WHERE id = ?', (review_id,)).fetchone()
        if review is None or review['evidence_digest'] != evidence_digest:
            raise ValueError('Exact reviewed PAPER evidence digest required.')
        if review['consumed_at'] is not None:
            if review['approved_by'] == approved_by:
                return dict(paper=True, review_id=review_id, manual_approval_recorded=True, budget_released=False)
            raise ValueError('PAPER review already consumed by another operator.')
        if time.time() >= review['expires_at']:
            raise ValueError('PAPER review expired or consumed.')
        if review['approved_by'] not in (None, approved_by):
            raise ValueError('PAPER review already approved by another operator.')
        conn.execute('UPDATE pro_ticker_manual_release_reviews SET approved_by = ?, approved_at = COALESCE(approved_at, ?) WHERE id = ?',
                     (approved_by, time.time(), review_id))
    return dict(paper=True, review_id=review_id, manual_approval_recorded=True, budget_released=False)


def release_manually_approved_paper_budget(review_id, *, fetch_paper_order):
    """Re-fetch terminal fills, then consume approval and release atomically.

    An exact retry returns the existing audit event. A different review cannot
    release the same reservation twice. No network occurs while holding a lock.
    """
    _uuid(review_id)
    with closing(get_connection()) as conn:
        review = conn.execute('SELECT * FROM pro_ticker_manual_release_reviews WHERE id = ?', (review_id,)).fetchone()
        if review is None or not review['approved_by']:
            raise ValueError('Explicit operator PAPER approval required.')
        existing = conn.execute('SELECT * FROM pro_ticker_budget_release_events WHERE review_id = ?', (review_id,)).fetchone()
        if existing is not None:
            return dict(paper=True, budget_released=False, already_released=True, event=dict(existing))
        if time.time() >= review['expires_at']:
            raise ValueError('PAPER review expired.')
        conn.execute('BEGIN')
        initial = _ledger(conn, review['reservation_id'])
        conn.rollback()
    entry, exits = _fetch(initial, fetch_paper_order)
    with closing(get_connection()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute('SELECT * FROM pro_ticker_budget_release_events WHERE review_id = ?', (review_id,)).fetchone()
        if existing is not None:
            return dict(paper=True, budget_released=False, already_released=True, event=dict(existing))
        live = conn.execute('SELECT * FROM pro_ticker_manual_release_reviews WHERE id = ?', (review_id,)).fetchone()
        if live is None or live['approved_by'] != review['approved_by'] or live['consumed_at'] is not None or time.time() >= live['expires_at']:
            raise ValueError('PAPER approval unavailable or expired.')
        current = _ledger(conn, live['reservation_id'])
        _verify(conn, live['reservation_id'], entry, exits)
        digest = _digest(current, entry, exits)
        if current != initial or digest != live['evidence_digest']:
            raise ValueError('PAPER evidence changed; new operator review required.')
        now = datetime.now(timezone.utc).isoformat()
        cents = current['reservation']['notional_cents']
        conn.execute('INSERT INTO pro_ticker_budget_release_events VALUES (?, ?, ?, ?, ?, ?)',
                     (live['reservation_id'], review_id, digest, live['approved_by'], cents, now))
        changed = conn.execute("UPDATE pro_ticker_paper_reservations SET status = 'released', updated_at = ? WHERE id = ? AND status = 'filled'",
                               (now, live['reservation_id'])).rowcount
        if changed != 1:
            raise ValueError('PAPER reservation is no longer releasable.')
        conn.execute('UPDATE pro_ticker_manual_release_reviews SET consumed_at = ? WHERE id = ?', (time.time(), review_id))
    return dict(paper=True, budget_released=True, already_released=False,
                reservation_id=live['reservation_id'], released_cents=cents,
                automatic_release_enabled=False)
