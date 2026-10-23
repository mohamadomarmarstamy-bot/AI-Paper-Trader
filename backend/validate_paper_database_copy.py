"""Validate an exported standalone SQLite backup using a disposable copy only.

Usage: python backend/validate_paper_database_copy.py /secure/export.sqlite3
Never point this at a running production database or mount production storage.
No source rows, account data or secrets are printed.
"""
from contextlib import closing
from pathlib import Path
from collections import Counter
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _row_fingerprints(conn, table, columns):
    """Preserve duplicate rows and typed values without retaining private data."""
    fingerprints = Counter()
    query = 'SELECT ' + ','.join(map(_quote, columns)) + ' FROM ' + _quote(table)
    for row in conn.execute(query):
        digest = hashlib.sha256()
        for value in row:
            if isinstance(value, bytes):
                encoded = b'blob:' + value
            else:
                encoded = (type(value).__name__ + ':' + repr(value)).encode('utf-8')
            digest.update(str(len(encoded)).encode('ascii') + b':' + encoded)
        fingerprints[digest.digest()] += 1
    return fingerprints


def _validate_working_copy():
    import database
    from pro_ticker_budget_ledger import get_pro_ticker_budget
    with closing(database.get_connection()) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Copy integrity check failed.')
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        reservations = None
        if 'pro_ticker_paper_reservations' in tables:
            reservations = [dict(row) for row in conn.execute('SELECT * FROM pro_ticker_paper_reservations ORDER BY id')]
        snapshots = {}
        for table in tables:
            # SQLite may advance internal sequence counters for newly seeded rows.
            if table.startswith('sqlite_'):
                continue
            columns = [row[1] for row in conn.execute('PRAGMA table_info(' + _quote(table) + ')')]
            snapshots[table] = (columns, _row_fingerprints(conn, table, columns))
    database.initialize_database()
    budget = get_pro_ticker_budget()
    with closing(database.get_connection()) as conn:
        if reservations is not None:
            current = [dict(row) for row in conn.execute('SELECT * FROM pro_ticker_paper_reservations ORDER BY id')]
            # Legacy expected_shares may be added, but existing fields must survive.
            if len(current) != len(reservations) or any(
                any(new.get(key) != value for key, value in old.items())
                for old, new in zip(reservations, current)
            ):
                raise ValueError('Reservation data changed during copy migration.')
            expected_cents = sum(row['notional_cents'] for row in reservations
                                 if row['status'] in {'reserved', 'submitted', 'filled'})
            if round(budget['committed'] * 100) != expected_cents:
                raise ValueError('Copy reservation budget changed.')
        for table, (columns, before) in snapshots.items():
            after = _row_fingerprints(conn, table, columns)
            if before - after:
                raise ValueError('Existing data changed during copy migration.')
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Migrated copy integrity check failed.')
        if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise ValueError('Migrated copy foreign-key check failed.')
    return dict(valid=True, integrity='ok', existing_rows_preserved=True,
                existing_values_preserved=True, foreign_keys='ok',
                reservation_accounting_preserved=True, source_modified=False,
                validation_scope='disposable_copy_schema_and_accounting')


def validate_database_backup(source):
    source = Path(source).resolve()
    if not source.is_file():
        raise ValueError('An exported SQLite backup file is required.')
    # immutable mode skips journal recovery. A rollback journal can therefore
    # hide an interrupted transaction just as a WAL can hide committed rows.
    if any(Path(str(source) + suffix).exists() for suffix in ('-wal', '-journal', '-shm')):
        raise ValueError('Use a standalone, consistent SQLite backup without journal sidecars.')
    with tempfile.TemporaryDirectory(prefix='paper-db-validation-') as directory:
        target = Path(directory) / 'validation.sqlite3'
        try:
            # immutable=1 is appropriate only for the exported standalone backup.
            # It also prevents journal/shared-memory creation beside the source.
            with closing(sqlite3.connect(source.as_uri() + '?mode=ro&immutable=1', uri=True)) as original, closing(sqlite3.connect(target)) as copied:
                original.backup(copied)
            env = {'DATABASE_PATH': str(target), 'AUTO_TRADER_START_ENABLED': 'false',
                   'APP_ACCESS_PASSWORD': '', 'APP_SESSION_SECRET': '',
                   'PYTHONPATH': str(Path(__file__).resolve().parent)}
            completed = subprocess.run(
                [sys.executable, '-c', 'import json; from validate_paper_database_copy import _validate_working_copy; print(json.dumps(_validate_working_copy()))'],
                cwd=str(Path(__file__).resolve().parent), env=env,
                capture_output=True, text=True, timeout=120,
            )
            if completed.returncode != 0:
                return dict(valid=False, source_modified=False, reason='copy_schema_or_accounting_validation_failed')
            return json.loads(completed.stdout)
        except (sqlite3.Error, subprocess.TimeoutExpired, ValueError):
            return dict(valid=False, source_modified=False, reason='exported_backup_validation_failed')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Provide one exported SQLite backup path.')
    try:
        result = validate_database_backup(sys.argv[1])
    except ValueError:
        result = dict(valid=False, source_modified=False, reason='standalone_exported_backup_required')
    print(json.dumps(result))
    raise SystemExit(0 if result['valid'] else 1)
