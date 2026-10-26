"""Read-only virtual budget release history, separate from trading P/L."""
from contextlib import closing
from database import get_connection


def list_paper_budget_releases(*, limit=100, offset=0):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise ValueError('Invalid release history pagination.')
    with closing(get_connection()) as conn:
        conn.execute('BEGIN')
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        base = dict(paper=True, event_type='virtual_budget_release', counts_as_realized_profit=False,
                    limit=limit, offset=offset, automatic_release_enabled=False)
        if 'pro_ticker_budget_release_events' not in tables:
            return {**base, 'events': [], 'total_count': 0, 'total_released_cents': 0, 'has_more': False}
        summary = conn.execute('SELECT COUNT(*), COALESCE(SUM(released_cents), 0) FROM pro_ticker_budget_release_events').fetchone()
        rows = conn.execute(
            'SELECT e.*, r.symbol FROM pro_ticker_budget_release_events e '
            'LEFT JOIN pro_ticker_paper_reservations r ON r.id = e.reservation_id '
            'ORDER BY e.released_at DESC, e.reservation_id DESC LIMIT ? OFFSET ?', (limit, offset),
        ).fetchall()
        return {**base, 'events': [dict(row) for row in rows], 'total_count': summary[0],
                'total_released_cents': summary[1], 'has_more': offset + len(rows) < summary[0]}
