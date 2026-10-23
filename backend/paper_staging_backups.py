"""Owner-invoked staging snapshot download. Never mounted by production."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading

from fastapi import HTTPException
from fastapi.responses import FileResponse
from backup_paper_database import backup_database

_download_slot = threading.BoundedSemaphore(1)


class _SnapshotResponse(FileResponse):
    def __init__(self, *args, cleanup, **kwargs):
        super().__init__(*args, **kwargs)
        self._cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._cleanup()


def staging_backup_response(source):
    """Stream a private consistent snapshot, cleaning it up after delivery."""
    if not _download_slot.acquire(blocking=False):
        raise HTTPException(409, 'A staging backup download is already active.')
    directory = None
    try:
        directory = tempfile.TemporaryDirectory(prefix='paper-staging-export-')
        target = Path(directory.name) / 'snapshot.sqlite3'
        backup_database(source, target)

        def cleanup():
            try:
                directory.cleanup()
            finally:
                _download_slot.release()

        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        return _SnapshotResponse(target, media_type='application/vnd.sqlite3',
            filename=f'paper-staging-backup-{stamp}.sqlite3',
            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'},
            cleanup=cleanup)
    except Exception:
        try:
            if directory is not None:
                directory.cleanup()
        finally:
            _download_slot.release()
        raise HTTPException(503, 'Staging snapshot unavailable. No data was restored.') from None
