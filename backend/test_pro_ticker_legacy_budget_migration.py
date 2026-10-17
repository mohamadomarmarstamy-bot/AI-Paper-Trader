"""Legacy-schema preservation and concurrent first-request migration tests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import database
import pro_ticker_budget_ledger as budget
from pro_ticker_test_db_helper import tracked_test_connections

AAPL = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
MSFT = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'


class LegacyBudgetMigrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'legacy.db'
        self.patch = patch.object(database, 'DATABASE_PATH', self.path)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        tracker = tracked_test_connections()
        tracker.__enter__()
        self.addCleanup(tracker.__exit__, None, None, None)
        with closing(database.get_connection()) as conn, conn:
            conn.execute('CREATE TABLE pro_ticker_paper_reservations ('
                         'id TEXT PRIMARY KEY, symbol TEXT NOT NULL, notional_cents INTEGER NOT NULL, '
                         'status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, '
                         'broker_order_id TEXT, note TEXT)')
            conn.executemany('INSERT INTO pro_ticker_paper_reservations VALUES (?, ?, ?, ?, ?, ?, ?, ?)', [
                (AAPL, 'AAPL', 10000, 'filled', 'old', 'old', 'entry-aapl', 'preserve this note'),
                (MSFT, 'MSFT', 5000, 'reserved', 'old', 'old', None, 'unsubmitted'),
            ])

    def test_migration_preserves_budget_order_links_and_notes(self):
        self.assertEqual(budget.get_pro_ticker_budget()['committed'], 150)
        row = budget.get_pro_ticker_reservation(AAPL)
        self.assertEqual(row['status'], 'filled')
        self.assertEqual(row['broker_order_id'], 'entry-aapl')
        self.assertEqual(row['note'], 'preserve this note')
        self.assertEqual(row['created_at'], 'old')
        self.assertIsNone(row['expected_shares'])

    def test_old_filled_reservation_never_infers_expected_shares(self):
        budget.get_pro_ticker_budget()
        with self.assertRaisesRegex(ValueError, 'unlinked reserved'):
            budget.set_paper_reservation_expected_shares(AAPL, 5)
        budget.set_paper_reservation_expected_shares(MSFT, 3)
        self.assertEqual(budget.get_pro_ticker_reservation(MSFT)['expected_shares'], 3)
        self.assertEqual(budget.get_pro_ticker_budget()['committed'], 150)

    def test_repeated_initialization_does_not_repeat_alter(self):
        budget.get_pro_ticker_budget()
        conn = database.get_connection()
        statements = []
        conn.set_trace_callback(statements.append)
        with patch.object(budget, 'get_connection', return_value=conn):
            self.assertEqual(budget.get_pro_ticker_budget()['committed'], 150)
        self.assertFalse(any(statement.startswith('ALTER') for statement in statements))

    def test_concurrent_legacy_migration_rechecks_under_write_lock(self):
        barrier = threading.Barrier(4)
        connections = []
        class RacingConnection(sqlite3.Connection):
            observed = False
            def execute(self, sql, parameters=()):
                cursor = super().execute(sql, parameters)
                if sql.startswith('PRAGMA table_info') and not self.observed:
                    self.observed = True
                    rows = cursor.fetchall()
                    barrier.wait(timeout=5)
                    return rows
                return cursor
        def connect():
            conn = sqlite3.connect(self.path, timeout=10, factory=RacingConnection, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            connections.append(conn)
            return conn
        try:
            with patch.object(budget, 'get_connection', side_effect=connect), ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: budget.get_pro_ticker_budget(), range(4)))
            self.assertEqual([result['committed'] for result in results], [150] * 4)
        finally:
            for conn in connections:
                conn.close()
        with closing(database.get_connection()) as conn:
            names = [row['name'] for row in conn.execute('PRAGMA table_info(pro_ticker_paper_reservations)')]
            self.assertEqual(names.count('expected_shares'), 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM pro_ticker_paper_reservations').fetchone()[0], 2)

    def test_ambiguous_legacy_allocations_fail_without_deleting_rows(self):
        with closing(database.get_connection()) as conn, conn:
            conn.execute('INSERT INTO pro_ticker_paper_reservations VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                         ('cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'AAPL', 100, 'filled', 'old', 'old', 'other-entry', 'ambiguous'))
        with self.assertRaises(sqlite3.IntegrityError):
            budget.get_pro_ticker_budget()
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM pro_ticker_paper_reservations').fetchone()[0], 3)
            self.assertEqual(conn.execute('SELECT SUM(notional_cents) FROM pro_ticker_paper_reservations').fetchone()[0], 15100)


if __name__ == '__main__':
    unittest.main()
