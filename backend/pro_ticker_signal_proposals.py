"""Durable research-linked PAPER drafts, never reservations or execution authority."""
from contextlib import closing
from datetime import datetime, timezone
import json
import time
import uuid
import database

_SCHEMA = '''CREATE TABLE IF NOT EXISTS pro_ticker_signal_proposals (
 request_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL UNIQUE,
 intent_json TEXT NOT NULL, proposal_json TEXT NOT NULL);'''


class ProposalConflict(ValueError):
    pass


def _uuid(value):
    try:
        if str(uuid.UUID(value)) == value:
            return value
    except (TypeError, ValueError, AttributeError):
        pass
    raise ValueError('Canonical UUID required.')


def proposals_for_signals(conn, ids):
    if not ids or not conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_signal_proposals'").fetchone():
        return {}
    rows = conn.execute('SELECT signal_id,proposal_json FROM pro_ticker_signal_proposals WHERE signal_id IN (' + ','.join('?' for _ in ids) + ')', ids)
    return {r['signal_id']: json.loads(r['proposal_json']) for r in rows}


def save_signal_proposal(payload, *, quote_fetcher, clock=None):
    """Explicit operator sizing, immutable intent and fresh server reference.

    A draft does NOT lock buying power. All broker and ledger admission must be
    performed again before any future order. No request can enable execution.
    """
    if not isinstance(payload, dict) or set(payload) != {'request_id', 'signal_id', 'shares', 'stop_loss_percent', 'take_profit_percent'}:
        raise ValueError('Provide exactly the draft fields.')
    request_id = _uuid(payload['request_id']); signal_id = _uuid(payload['signal_id'])
    # Canonical JSON prevents bool/number equivalence from altering an intent.
    intent = json.dumps(payload, sort_keys=True, allow_nan=False, separators=(',', ':'))
    with closing(database.get_connection()) as conn:
        conn.executescript(_SCHEMA)
        def existing():
            row = conn.execute('SELECT * FROM pro_ticker_signal_proposals WHERE request_id=? OR signal_id=?', (request_id, signal_id)).fetchone()
            if row:
                if row['intent_json'] != intent:
                    raise ProposalConflict('A draft already exists for this review or request ID.')
                return {'paper': True, 'duplicate': True, 'proposal': json.loads(row['proposal_json'])}
        prior = existing()
        if prior:
            return prior
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_manual_signal_reviews'").fetchone():
            raise ValueError('Saved grade required.')
        review = conn.execute('SELECT * FROM pro_ticker_manual_signal_reviews WHERE request_id=?', (signal_id,)).fetchone()
        if not review or review['status'] != 'graded':
            raise ValueError('Completed grade required.')
        grade = json.loads(review['grade_json'])
        if grade.get('status') != 'graded' or grade.get('signal') != 'BUY':
            raise ValueError('A BUY research grade is required for a long draft.')
        # Validate operator numbers BEFORE touching the provider.
        from pro_ticker_workspace import hypothetical_proposal
        sizing = {key: payload[key] for key in ('shares', 'stop_loss_percent', 'take_profit_percent')}
        hypothetical_proposal({'symbol': review['symbol'], 'reference_price': '1', **sizing})
        from pro_ticker_manual_signals import quote_snapshot
        raw = quote_fetcher(review['symbol'])
        if isinstance(raw, dict) and raw.get('symbol') not in (None, review['symbol']):
            raise ValueError('Quote belongs to another symbol.')
        snapshot = quote_snapshot(raw, now=(clock or time.time)())
        if snapshot['fresh'] is not True:
            raise ValueError('Fresh server quote required.')
        estimate = hypothetical_proposal({'symbol': review['symbol'], 'reference_price': snapshot['price'], **sizing})
        if not estimate['risk_limits_satisfied'] or not estimate['within_virtual_budget']:
            raise ValueError('Draft exceeds current risk or virtual budget limits.')
        proposal = {**estimate, 'request_id': request_id, 'signal_id': signal_id,
                    'created_at': datetime.fromtimestamp((clock or time.time)(), timezone.utc).isoformat(),
                    'reference_price_source': 'server_quote_at_draft_creation', 'market_snapshot': snapshot,
                    'entry_grade': grade, 'status': 'research_draft', 'execution_authorized': False,
                    'admission_blockers': ['broker_account_not_verified', 'broker_positions_and_orders_not_verified',
                                           'market_session_not_verified', 'protection_not_verified', 'funds_not_reserved'],
                    'reason': 'Saved research draft only. No funds reserved or order submitted. Quote, settings, buying power, ownership, open orders and protection require fresh admission before any future execution.'}
        # No network work inside the write lock. Unique review and request IDs
        # serialize racing retries; crash rollback cannot leave a partial draft.
        conn.execute('BEGIN IMMEDIATE')
        prior = existing()
        if prior:
            conn.rollback()
            return prior
        if not quote_snapshot(raw, now=(clock or time.time)())['fresh']:
            raise ValueError('Quote expired while waiting to save.')
        conn.execute('INSERT INTO pro_ticker_signal_proposals VALUES (?,?,?,?)',
                     (request_id, signal_id, intent, json.dumps(proposal, allow_nan=False)))
        conn.commit()
        return {'paper': True, 'duplicate': False, 'proposal': proposal}
