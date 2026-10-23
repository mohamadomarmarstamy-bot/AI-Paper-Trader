"""Snapshot isolation and terminal partial-entry PAPER reconciliation tests."""
from contextlib import closing
import unittest
from unittest.mock import patch

import database
import pro_ticker_broker_snapshot_audit as audit
import test_pro_ticker_broker_snapshot_audit as fixtures


class AtomicBrokerAuditTests(unittest.TestCase):
    setUp = fixtures.BrokerSnapshotAuditTests.setUp
    audit = fixtures.BrokerSnapshotAuditTests.audit

    def test_terminal_partial_entry_and_complete_exit_match(self):
        for status in ('canceled', 'expired'):
            with self.subTest(status=status):
                # Five of eight ordered shares filled; only those five need exits.
                with closing(database.get_connection()) as conn, conn:
                    conn.execute('UPDATE pro_ticker_paper_reservations SET expected_shares = 8')
                result = self.audit(entry_order={**self.entry_order, 'qty': '8', 'status': status})
                self.assertTrue(result['verified'], result)
                self.assertEqual(result['entry_shares'], 5)
                self.assertFalse(result['release_authorized'])
                self.assertFalse(result['budget_released'])

    def test_nonterminal_partial_entry_cannot_establish_closure(self):
        with closing(database.get_connection()) as conn, conn:
            conn.execute('UPDATE pro_ticker_paper_reservations SET expected_shares = 8')
        result = self.audit(entry_order={**self.entry_order, 'qty': '8', 'status': 'partially_filled'})
        self.assertFalse(result['verified'])

    def test_rejected_entry_with_positive_fills_cannot_establish_closure(self):
        result = self.audit(entry_order={**self.entry_order, 'status': 'rejected'})
        self.assertFalse(result['verified'])

    def test_zero_fill_terminal_entry_cannot_establish_closure(self):
        result = self.audit(entry_order={**self.entry_order, 'filled_qty': '0', 'status': 'canceled'})
        self.assertFalse(result['verified'])

    def test_entire_audit_uses_one_read_connection_and_closes_it(self):
        connection = database.get_connection()
        statements = []
        connection.set_trace_callback(statements.append)
        with patch.object(audit, 'get_connection', return_value=connection) as connect:
            result = self.audit()
        self.assertTrue(result['verified'], result)
        connect.assert_called_once()
        self.assertEqual(statements[0], 'BEGIN')
        self.assertFalse(any(s.startswith(('CREATE', 'INSERT', 'UPDATE', 'DELETE')) for s in statements))
        with self.assertRaisesRegex(Exception, 'closed database'):
            connection.execute('SELECT 1')

    def test_concurrent_fill_change_cannot_mix_snapshots(self):
        # WAL lets a writer commit while the review holds its read snapshot.
        with closing(database.get_connection()) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
        original = audit._accounted
        changed = False
        def change_after_entry_read(conn, order_id):
            nonlocal changed
            shares = original(conn, order_id)
            if not changed:
                changed = True
                with closing(database.get_connection()) as writer, writer:
                    writer.execute("UPDATE pro_ticker_strategy_fill_events SET shares = 4 WHERE side = 'sell'")
            return shares
        with patch.object(audit, '_accounted', side_effect=change_after_entry_read):
            first = self.audit()
        self.assertTrue(first['verified'], first)
        second = self.audit()
        self.assertFalse(second['verified'], second)
        self.assertEqual(second['reason'], 'exit_accounting_mismatch')
        self.assertFalse(first['release_authorized'])

    def test_fresh_database_fails_closed_without_creating_schema(self):
        with closing(database.get_connection()) as conn, conn:
            conn.execute('DROP TABLE pro_ticker_paper_exit_links')
        result = self.audit()
        self.assertEqual(result['reason'], 'paper_accounting_schema_incomplete')
        self.assertFalse(result['verified'])
        with closing(database.get_connection()) as conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name = 'pro_ticker_paper_exit_links'").fetchone())

    def test_fetch_adapter_missing_exit_ledger_never_calls_broker(self):
        from pro_ticker_paper_broker_fetch_audit import audit_fetched_paper_reservation
        from unittest.mock import Mock
        with closing(database.get_connection()) as conn, conn:
            conn.execute('DROP TABLE pro_ticker_paper_exit_links')
        reader = Mock()
        result = audit_fetched_paper_reservation(self.reservation_id, fetch_paper_order=reader)
        reader.assert_not_called()
        self.assertEqual(result['reason'], 'paper_exit_ledger_missing')
        self.assertFalse(result['budget_released'])

    def test_fetch_adapter_invalid_uuid_never_calls_broker(self):
        from pro_ticker_paper_broker_fetch_audit import audit_fetched_paper_reservation
        from unittest.mock import Mock
        reader = Mock()
        result = audit_fetched_paper_reservation('invalid', fetch_paper_order=reader)
        reader.assert_not_called()
        self.assertEqual(result['reason'], 'invalid_reservation_id')

    def test_invalid_uuid_returns_diagnostic(self):
        result = self.audit(reservation_id='not-a-uuid')
        self.assertEqual(result['reason'], 'invalid_reservation_id')
        self.assertFalse(result['budget_released'])


if __name__ == '__main__':
    unittest.main()
