"""Snapshots must include WAL commits and never overwrite existing evidence."""
from contextlib import closing
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backup_paper_database import backup_database
from validate_paper_database_copy import validate_database_backup


class DatabaseBackupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / 'source.db'
        self.destination = self.root / 'snapshot.db'

    def test_snapshot_includes_wal_commit_excludes_uncommitted_and_validates(self):
        with closing(sqlite3.connect(self.source)) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('CREATE TABLE owner_evidence (value TEXT, raw BLOB)')
            conn.execute('INSERT INTO owner_evidence VALUES (?, ?)', ('private', b'\x00\xff'))
            conn.commit()
            conn.execute("INSERT INTO owner_evidence VALUES ('uncommitted', NULL)")
            self.assertTrue(Path(str(self.source) + '-wal').exists())
            self.assertTrue(backup_database(self.source, self.destination)['created'])
            with closing(sqlite3.connect(self.destination)) as snapshot:
                self.assertEqual(snapshot.execute('SELECT * FROM owner_evidence').fetchall(), [('private', b'\x00\xff')])
            self.assertTrue(validate_database_backup(self.destination)['valid'])
        self.assertEqual(os.stat(self.destination).st_mode & 0o777, 0o600)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['snapshot.db', 'source.db'])

    def test_missing_source_and_same_path_never_create_or_replace(self):
        with self.assertRaises(ValueError):
            backup_database(self.source, self.destination)
        self.assertFalse(self.source.exists())
        self.source.write_bytes(b'evidence')
        with self.assertRaises(ValueError):
            backup_database(self.source, self.source)
        self.assertEqual(self.source.read_bytes(), b'evidence')

    def test_existing_output_and_sidecars_are_preserved(self):
        self.source.touch()
        for suffix in ('', '-wal', '-shm', '-journal'):
            with self.subTest(suffix=suffix):
                evidence = Path(str(self.destination) + suffix)
                evidence.write_bytes(b'preserve')
                with self.assertRaises(ValueError):
                    backup_database(self.source, self.destination)
                self.assertEqual(evidence.read_bytes(), b'preserve')
                evidence.unlink()

    def test_failed_and_timed_out_snapshots_leave_no_output(self):
        self.source.write_bytes(b'corrupt')
        with self.assertRaises(sqlite3.DatabaseError):
            backup_database(self.source, self.destination)
        self.source.unlink()
        with sqlite3.connect(self.source) as conn:
            conn.execute('CREATE TABLE evidence (value TEXT)')
        with patch('backup_paper_database.time.monotonic', side_effect=[0, 31]):
            with self.assertRaises(TimeoutError):
                backup_database(self.source, self.destination)
        self.assertEqual([path.name for path in self.root.iterdir()], ['source.db'])

    def test_racing_destination_is_never_overwritten(self):
        with sqlite3.connect(self.source) as conn:
            conn.execute('CREATE TABLE evidence (value TEXT)')
        def racing_link(temporary, destination):
            destination.write_bytes(b'another-backup')
            raise FileExistsError()
        with patch('backup_paper_database.os.link', side_effect=racing_link):
            with self.assertRaises(FileExistsError):
                backup_database(self.source, self.destination)
        self.assertEqual(self.destination.read_bytes(), b'another-backup')
        self.assertFalse(list(self.root.glob('.paper-backup-*')))


if __name__ == '__main__':
    unittest.main()
