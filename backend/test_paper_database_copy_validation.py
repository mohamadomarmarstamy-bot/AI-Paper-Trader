"""Export validation must never mutate its source or infer lost reservations."""
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import database
from validate_paper_database_copy import validate_database_backup, _validate_working_copy


class DatabaseCopyValidationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'export.sqlite3'

    def seed(self, duplicate=False):
        with sqlite3.connect(self.source) as conn:
            conn.execute('CREATE TABLE pro_ticker_paper_reservations (id TEXT PRIMARY KEY, symbol TEXT, '
                         'notional_cents INTEGER, status TEXT, created_at TEXT, updated_at TEXT, broker_order_id TEXT, note TEXT)')
            conn.execute("INSERT INTO pro_ticker_paper_reservations VALUES ('a','AAPL',12345,'filled','old','old','entry','private-note')")
            if duplicate:
                conn.execute("INSERT INTO pro_ticker_paper_reservations VALUES ('b','AAPL',100,'filled','old','old','other','private-note')")

    def test_valid_legacy_export_migrates_copy_without_changing_source(self):
        self.seed()
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result = validate_database_backup(self.source)
        self.assertTrue(result['valid'], result)
        self.assertTrue(result['reservation_accounting_preserved'])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['export.sqlite3'])
        with sqlite3.connect(self.source) as conn:
            self.assertNotIn('expected_shares', [row[1] for row in conn.execute('PRAGMA table_info(pro_ticker_paper_reservations)')])

    def test_ambiguous_export_fails_without_source_changes_or_row_leaks(self):
        self.seed(duplicate=True)
        before = self.source.read_bytes()
        result = validate_database_backup(self.source)
        self.assertFalse(result['valid'])
        self.assertNotIn('private-note', str(result))
        self.assertEqual(self.source.read_bytes(), before)

    def test_corrupt_export_fails_without_overwriting_source(self):
        self.source.write_bytes(b'not-a-database')
        self.assertFalse(validate_database_backup(self.source)['valid'])
        self.assertEqual(self.source.read_bytes(), b'not-a-database')

    def test_missing_file_or_live_wal_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_database_backup(self.source)
        self.seed()
        Path(str(self.source) + '-wal').write_bytes(b'not-a-standalone-backup')
        with self.assertRaises(ValueError):
            validate_database_backup(self.source)

    def test_journal_sidecars_are_rejected_without_recovery_or_source_changes(self):
        self.seed()
        before = self.source.read_bytes()
        for suffix in ('-journal', '-shm'):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(self.source) + suffix)
                sidecar.write_bytes(b'private-interrupted-transaction')
                with self.assertRaisesRegex(ValueError, 'standalone'):
                    validate_database_backup(self.source)
                self.assertEqual(self.source.read_bytes(), before)
                self.assertEqual(sidecar.read_bytes(), b'private-interrupted-transaction')
                sidecar.unlink()

    def test_same_count_changed_order_evidence_is_rejected(self):
        self.seed()
        with sqlite3.connect(self.source) as conn:
            conn.execute('CREATE TABLE broker_evidence (value TEXT, raw BLOB)')
            conn.executemany('INSERT INTO broker_evidence VALUES (?, ?)',
                             [('owned', b'\x00\xff'), ('owned', b'\x00\xff')])
        initialize = database.initialize_database
        def corrupt_migration():
            initialize()
            with sqlite3.connect(self.source) as conn:
                conn.execute("UPDATE broker_evidence SET raw=X'00' WHERE rowid=1")
        with patch.object(database, 'DATABASE_PATH', self.source), patch.object(database, 'initialize_database', side_effect=corrupt_migration):
            with self.assertRaisesRegex(ValueError, 'Existing data changed'):
                _validate_working_copy()

    def test_migration_added_columns_do_not_change_existing_typed_evidence(self):
        self.seed()
        with sqlite3.connect(self.source) as conn:
            conn.execute('CREATE TABLE broker_evidence ("quoted column" TEXT, raw BLOB)')
            conn.executemany('INSERT INTO broker_evidence VALUES (?, ?)',
                             [('private', b'\x00\xff'), ('private', b'\x00\xff'), (None, b'')])
        result = validate_database_backup(self.source)
        self.assertTrue(result['valid'], result)
        self.assertTrue(result['existing_values_preserved'])
        self.assertNotIn('private', str(result))

    def test_foreign_key_violation_is_rejected_without_source_changes(self):
        self.seed()
        with sqlite3.connect(self.source) as conn:
            conn.execute('CREATE TABLE parent_evidence (id INTEGER PRIMARY KEY)')
            conn.execute('CREATE TABLE child_evidence (parent_id INTEGER REFERENCES parent_evidence(id))')
            conn.execute('INSERT INTO child_evidence VALUES (99)')
        before = self.source.read_bytes()
        self.assertFalse(validate_database_backup(self.source)['valid'])
        self.assertEqual(self.source.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
