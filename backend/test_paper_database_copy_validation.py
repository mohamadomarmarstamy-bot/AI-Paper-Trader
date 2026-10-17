"""Export validation must never mutate its source or infer lost reservations."""
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from validate_paper_database_copy import validate_database_backup


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


if __name__ == '__main__':
    unittest.main()
