"""Validate an exported standalone SQLite backup using a disposable copy only.

Usage: python backend/validate_paper_database_copy.py /secure/export.sqlite3
Never point this at a running production database or mount production storage.
No source rows, account data or secrets are printed.
"""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import tempfile


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
        counts = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            counts[table] = conn.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0]
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
        for table, before in counts.items():
            quoted = '"' + table.replace('"', '""') + '"'
            if conn.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0] < before:
                raise ValueError('Existing rows were lost during copy migration.')
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Migrated copy integrity check failed.')
    return dict(valid=True, integrity='ok', existing_rows_preserved=True,
                reservation_accounting_preserved=True, source_modified=False,
                validation_scope='disposable_copy_schema_and_accounting')


def validate_database_backup(source):
    source = Path(source).resolve()
    if not source.is_file():
        raise ValueError('An exported SQLite backup file is required.')
    if Path(str(source) + '-wal').exists():
        raise ValueError('Use a standalone, consistent SQLite backup without a live WAL.')
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
