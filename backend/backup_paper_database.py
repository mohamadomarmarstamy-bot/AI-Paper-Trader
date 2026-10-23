"""Create a consistent standalone SQLite snapshot, including committed WAL data.

Usage: python backend/backup_paper_database.py SOURCE NEW_BACKUP
Run on the host with access to the intended database. Never copy a live .db file
alone, and never restore this snapshot over newer order/fill evidence.
"""
from contextlib import closing
from pathlib import Path
import json
import os
import sqlite3
import sys
import tempfile
import time


def backup_database(source, destination, *, timeout=30):
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if not source.is_file() or source == destination:
        raise ValueError('An existing source and a distinct new backup path are required.')
    if destination.exists() or any(Path(str(destination) + suffix).exists()
                                   for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('Backup path must be unused, without journal sidecars.')
    if not 0 < timeout <= 120:
        raise ValueError('Backup timeout must be between zero and 120 seconds.')
    # Private temporary output on the same filesystem allows atomic publication.
    fd, temporary = tempfile.mkstemp(prefix='.paper-backup-', dir=destination.parent)
    os.close(fd)
    deadline = time.monotonic() + timeout

    def progress(status, remaining, total):
        if time.monotonic() >= deadline:
            raise TimeoutError('SQLite snapshot timed out.')

    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True,
                                     timeout=timeout)) as original, closing(sqlite3.connect(temporary)) as copied:
            original.backup(copied, pages=256, progress=progress, sleep=0.05)
            if copied.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Snapshot integrity check failed.')
        with open(temporary, 'rb') as snapshot:
            os.fsync(snapshot.fileno())
        # link fails if another process created the destination in the meantime.
        os.link(temporary, destination)
        return dict(created=True, integrity='ok', consistent_sqlite_snapshot=True)
    finally:
        for suffix in ('', '-wal', '-shm', '-journal'):
            Path(temporary + suffix).unlink(missing_ok=True)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('Provide source database and new backup paths.')
    try:
        result = backup_database(*sys.argv[1:])
    except (OSError, sqlite3.Error, ValueError, TimeoutError):
        result = dict(created=False, reason='consistent_snapshot_failed')
    print(json.dumps(result))
    raise SystemExit(0 if result['created'] else 1)
