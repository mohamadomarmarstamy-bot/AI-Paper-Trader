"""Versioned Pro Ticker proposal/research settings; never enable execution."""
from contextlib import closing
from datetime import datetime, timezone
import json
import database
from pro_ticker_paper_policy import PRO_TICKER_VIRTUAL_BUDGET

BUDGET_CENTS = round(PRO_TICKER_VIRTUAL_BUDGET * 100)
DEFAULTS = {'max_allocation_cents': BUDGET_CENTS, 'max_stop_risk_cents': BUDGET_CENTS,
            'discovery_article_limit': 5}
_SCHEMA = '''CREATE TABLE IF NOT EXISTS pro_ticker_workspace_settings (
 id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL CHECK(revision>0),
 settings_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pro_ticker_workspace_settings_events (
 revision INTEGER PRIMARY KEY, previous_json TEXT NOT NULL, settings_json TEXT NOT NULL,
 changed_at TEXT NOT NULL, changed_by TEXT NOT NULL);'''


class SettingsConflict(ValueError):
    pass


def _validate(values):
    if not isinstance(values, dict) or set(values) != set(DEFAULTS):
        raise ValueError('Unexpected Pro Ticker setting fields.')
    for key, value in values.items():
        maximum = 5 if key == 'discovery_article_limit' else BUDGET_CENTS
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError('Setting value outside supported range.')
    return dict(values)


def read_settings(connection=None):
    if connection is None:
        with closing(database.get_connection()) as conn:
            return read_settings(conn)
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_workspace_settings'").fetchone()
    row = connection.execute('SELECT * FROM pro_ticker_workspace_settings WHERE id=1').fetchone() if exists else None
    values = _validate(json.loads(row['settings_json'])) if row else dict(DEFAULTS)
    return {'paper': True, 'scope': 'pro_ticker_proposals_and_manual_discovery',
            'order_execution_enabled': False, 'automatic_release_enabled': False,
            'revision': row['revision'] if row else 0, 'settings': values,
            'updated_at': row['updated_at'] if row else None}


def save_settings(payload):
    if not isinstance(payload, dict) or set(payload) != {'expected_revision', 'settings'}:
        raise ValueError('Provide revision and settings only.')
    revision = payload['expected_revision']
    if isinstance(revision, bool) or not isinstance(revision, int) or not 0 <= revision < 2147483647:
        raise ValueError('Invalid settings revision.')
    values = _validate(payload['settings'])
    now = datetime.now(timezone.utc).isoformat()
    with closing(database.get_connection()) as conn:
        conn.executescript(_SCHEMA)
        conn.execute('BEGIN IMMEDIATE')
        try:
            current = read_settings(conn)
            if current['revision'] != revision:
                raise SettingsConflict('Settings changed. Reload before saving again.')
            conn.execute('INSERT INTO pro_ticker_workspace_settings_events VALUES (?,?,?,?,?)',
                (revision+1,json.dumps(current['settings'],sort_keys=True),json.dumps(values,sort_keys=True),now,'authenticated-app-owner'))
            conn.execute('INSERT INTO pro_ticker_workspace_settings VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,settings_json=excluded.settings_json,updated_at=excluded.updated_at',
                (revision+1,json.dumps(values,sort_keys=True),now))
            result = read_settings(conn)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
