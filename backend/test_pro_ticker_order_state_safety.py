"""Interrupted fill posting must not accidentally free virtual PAPER funds."""
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import database
from pro_ticker_test_db_helper import tracked_test_connections
import pro_ticker_budget_ledger as budget
from pro_ticker_verified_fill_bridge import account_linked_paper_entry_snapshot


class PaperOrderStateSafetyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        tracker = tracked_test_connections()
        tracker.__enter__()
        self.addCleanup(tracker.__exit__, None, None, None)
        db_patch = patch.object(database, 'DATABASE_PATH', Path(temp.name) / 'test.db')
        db_patch.start()
        self.addCleanup(db_patch.stop)
        self.id = budget.reserve_pro_ticker_budget('AAPL', 1000)['reservation_id']
        budget.set_paper_reservation_expected_shares(self.id, 5)
        self.order = dict(id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
                          client_order_id='pt-' + self.id, symbol='AAPL',
                          side='buy', qty='5', filled_qty='2', status='partially_filled')
        budget.link_verified_paper_entry_order(self.id, self.order)

    def record(self, status, quantity):
        return budget.record_verified_paper_order_state(self.id, self.order['id'], status, filled_quantity=quantity)

    def test_interrupted_fill_then_stale_zero_cancel_cannot_release(self):
        account_linked_paper_entry_snapshot(self.id, self.order)
        self.assertEqual(budget.get_pro_ticker_reservation(self.id)['status'], 'submitted')
        for status in ('canceled', 'expired', 'rejected'):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, 'already accounted'):
                self.record(status, 0)
        self.assertEqual(budget.get_pro_ticker_budget()['committed'], 1000)
        self.assertEqual(self.record('canceled', 2)['status'], 'filled')
        self.assertEqual(budget.get_pro_ticker_budget()['committed'], 1000)

    def test_stale_positive_snapshot_cannot_reduce_recorded_fills(self):
        account_linked_paper_entry_snapshot(self.id, self.order)
        with self.assertRaisesRegex(ValueError, 'already accounted'):
            self.record('canceled', 1)
        self.assertEqual(budget.get_pro_ticker_budget()['committed'], 1000)

    def test_invalid_status_and_quantity_pairs_never_change_budget(self):
        for status, quantity in [('unknown', 2), ('rejected', 2), ('new', 2),
                                 ('filled', 0), ('filled', 2), ('filled', 6),
                                 ('canceled', 6), ('canceled', 1.5)]:
            with self.subTest(status=status, quantity=quantity), self.assertRaises(ValueError):
                self.record(status, quantity)
            self.assertEqual(budget.get_pro_ticker_reservation(self.id)['status'], 'submitted')
            self.assertEqual(budget.get_pro_ticker_budget()['committed'], 1000)

    def test_zero_fill_terminal_order_remains_supported(self):
        self.assertEqual(self.record('canceled', 0)['status'], 'failed')
        self.assertEqual(budget.get_pro_ticker_budget()['committed'], 0)

    def test_full_fill_and_retry_preserve_reservation(self):
        for _ in range(2):
            self.assertEqual(self.record('filled', 5)['status'], 'filled')
        self.assertEqual(budget.get_pro_ticker_budget()['committed'], 1000)


if __name__ == '__main__':
    unittest.main()
