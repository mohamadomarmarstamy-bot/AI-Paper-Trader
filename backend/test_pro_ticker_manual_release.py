"""End-to-end operator-approved PAPER release against disposable SQLite."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import unittest
from unittest.mock import Mock, patch

import database
import pro_ticker_manual_release as release
import test_pro_ticker_broker_snapshot_audit as fixtures
from pro_ticker_budget_ledger import get_pro_ticker_budget, get_pro_ticker_reservation


class ManualPaperReleaseTests(unittest.TestCase):
    setUp = fixtures.BrokerSnapshotAuditTests.setUp

    def reader(self, order_id):
        return {fixtures.ENTRY_ID: self.entry_order, fixtures.EXIT_ID: self.exit_order}[order_id].copy()

    def prepare(self):
        return release.prepare_manual_paper_release(self.reservation_id, fetch_paper_order=self.reader)

    def approve(self, review):
        return release.approve_manual_paper_release(
            review['review_id'], evidence_digest=review['evidence_digest'],
            approved_by='isolated-test-operator', independent_paper_provenance_confirmed=True)

    def apply(self, review, reader=None):
        return release.release_manually_approved_paper_budget(review['review_id'], fetch_paper_order=reader or self.reader)

    def assert_committed(self):
        self.assertEqual(get_pro_ticker_budget()['committed'], 100)
        self.assertEqual(get_pro_ticker_reservation(self.reservation_id)['status'], 'filled')

    def test_review_does_not_authorize_or_release(self):
        review = self.prepare()
        self.assertFalse(review['release_authorized'])
        self.assertFalse(review['broker_origin_authenticated'])
        self.assertFalse(review['budget_released'])
        with self.assertRaisesRegex(ValueError, 'Explicit operator'):
            self.apply(review)
        self.assert_committed()

    def test_operator_attestation_required(self):
        review = self.prepare()
        for value in (False, None, 1, 'true'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'provenance'):
                release.approve_manual_paper_release(review['review_id'], evidence_digest=review['evidence_digest'],
                    approved_by='operator', independent_paper_provenance_confirmed=value)
        self.assert_committed()

    def test_wrong_digest_and_blank_identity_cannot_approve(self):
        review = self.prepare()
        for digest, actor in [('wrong', 'operator'), (review['evidence_digest'], ''),
                              (review['evidence_digest'], ' operator ')]:
            with self.subTest(actor=actor), self.assertRaises(ValueError):
                release.approve_manual_paper_release(review['review_id'], evidence_digest=digest,
                    approved_by=actor, independent_paper_provenance_confirmed=True)
        self.assert_committed()

    def test_success_and_retry_are_exactly_once(self):
        review = self.prepare()
        self.approve(review)
        result = self.apply(review)
        self.assertTrue(result['budget_released'])
        self.assertEqual(result['released_cents'], 10000)
        self.assertFalse(result['automatic_release_enabled'])
        self.assertEqual(get_pro_ticker_budget()['committed'], 0)
        reader = Mock(side_effect=AssertionError('Retry must not call broker'))
        retry = self.apply(review, reader)
        self.assertTrue(retry['already_released'])
        self.assertFalse(retry['budget_released'])
        reader.assert_not_called()
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM pro_ticker_budget_release_events').fetchone()[0], 1)
            self.assertIsNotNone(conn.execute('SELECT consumed_at FROM pro_ticker_manual_release_reviews').fetchone()[0])

    def test_expired_review_cannot_approve(self):
        review = self.prepare()
        with patch.object(release.time, 'time', return_value=review['expires_at']):
            with self.assertRaisesRegex(ValueError, 'expired'):
                self.approve(review)
        self.assert_committed()

    def test_expired_approval_cannot_release(self):
        review = self.prepare()
        self.approve(review)
        with patch.object(release.time, 'time', return_value=review['expires_at']):
            with self.assertRaisesRegex(ValueError, 'expired'):
                self.apply(review)
        self.assert_committed()

    def test_open_position_cannot_prepare(self):
        with closing(database.get_connection()) as conn, conn:
            conn.execute('UPDATE pro_ticker_strategy_positions SET shares = 1')
        with self.assertRaisesRegex(ValueError, 'open or missing'):
            self.prepare()
        self.assert_committed()

    def test_nonterminal_order_cannot_prepare(self):
        self.exit_order['status'] = 'pending_cancel'
        with self.assertRaisesRegex(ValueError, 'not_terminal'):
            self.prepare()
        self.assert_committed()

    def test_mismatched_broker_identity_cannot_prepare(self):
        for key, value in [('id', fixtures.ENTRY_ID), ('symbol', 'MSFT'), ('client_order_id', 'foreign')]:
            with self.subTest(key=key), patch.dict(self.exit_order, {key: value}):
                with self.assertRaises(ValueError):
                    self.prepare()
        self.assert_committed()

    def test_fresh_broker_fill_change_invalidates_approval(self):
        review = self.prepare()
        self.approve(review)
        self.exit_order.update(status='canceled', filled_qty='4')
        with self.assertRaises(ValueError):
            self.apply(review)
        self.assert_committed()

    def test_ledger_change_invalidates_approval_even_with_same_fill_totals(self):
        review = self.prepare()
        self.approve(review)
        with closing(database.get_connection()) as conn, conn:
            conn.execute("UPDATE pro_ticker_strategy_fill_events SET recorded_at = 'changed'")
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.apply(review)
        self.assert_committed()

    def test_change_during_network_fetch_is_rejected(self):
        review = self.prepare()
        self.approve(review)
        def reader(order_id):
            with closing(database.get_connection()) as conn, conn:
                conn.execute("UPDATE pro_ticker_paper_reservations SET note = 'changed during fetch'")
            return self.reader(order_id)
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.apply(review, reader)
        self.assert_committed()

    def test_network_failure_keeps_approval_retryable(self):
        review = self.prepare()
        self.approve(review)
        with self.assertRaisesRegex(RuntimeError, 'network unavailable'):
            self.apply(review, Mock(side_effect=RuntimeError('network unavailable')))
        self.assert_committed()
        self.assertTrue(self.apply(review)['budget_released'])

    def test_database_failure_rolls_back_audit_and_release(self):
        review = self.prepare()
        self.approve(review)
        with closing(database.get_connection()) as conn:
            conn.executescript("CREATE TRIGGER fail_release BEFORE UPDATE OF status ON pro_ticker_paper_reservations "
                               "BEGIN SELECT RAISE(ABORT, 'simulated interruption'); END;")
        with self.assertRaisesRegex(Exception, 'simulated interruption'):
            self.apply(review)
        self.assert_committed()
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM pro_ticker_budget_release_events').fetchone()[0], 0)
            self.assertIsNone(conn.execute('SELECT consumed_at FROM pro_ticker_manual_release_reviews').fetchone()[0])
            conn.execute('DROP TRIGGER fail_release')
        self.assertTrue(self.apply(review)['budget_released'])

    def test_second_approved_review_cannot_release_same_reservation(self):
        first, second = self.prepare(), self.prepare()
        self.approve(first)
        self.approve(second)
        self.apply(first)
        with self.assertRaises(ValueError):
            self.apply(second)
        self.assertEqual(get_pro_ticker_budget()['committed'], 0)

    def test_concurrent_same_review_releases_once(self):
        review = self.prepare()
        self.approve(review)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.apply(review), range(2)))
        self.assertEqual(sum(result['budget_released'] for result in results), 1)
        self.assertEqual(sum(result['already_released'] for result in results), 1)

    def test_partial_terminal_entry_can_release_only_its_filled_shares(self):
        with closing(database.get_connection()) as conn, conn:
            conn.execute('UPDATE pro_ticker_paper_reservations SET expected_shares = 8')
        self.entry_order.update(qty='8', status='canceled')
        review = self.prepare()
        self.approve(review)
        self.assertTrue(self.apply(review)['budget_released'])

    def test_evidence_is_durably_stored_without_transport_secrets(self):
        import hashlib
        import json
        self.entry_order['secret_key'] = 'must-not-persist'
        review = self.prepare()
        with closing(database.get_connection()) as conn:
            evidence = conn.execute('SELECT evidence_json FROM pro_ticker_manual_release_reviews').fetchone()[0]
        self.assertEqual(hashlib.sha256(evidence.encode()).hexdigest(), review['evidence_digest'])
        self.assertNotIn('must-not-persist', evidence)
        self.assertEqual(json.loads(evidence)['entry']['id'], fixtures.ENTRY_ID)
        self.assertEqual(review['reserved_cents'], 10000)

    def test_inconsistent_symbol_balance_blocks_review(self):
        with closing(database.get_connection()) as conn, conn:
            conn.execute("INSERT INTO pro_ticker_strategy_fill_events VALUES ('unrelated', 'foreign-order', 'AAPL', 'buy', 1, 'now')")
        with self.assertRaisesRegex(ValueError, 'symbol fill balance'):
            self.prepare()
        self.assert_committed()

    def test_expiry_during_fetch_cannot_commit_release(self):
        review = self.prepare()
        self.approve(review)
        clock = patch.object(release.time, 'time', return_value=review['expires_at'])
        def reader(order_id):
            if order_id == fixtures.ENTRY_ID:
                clock.start()
            return self.reader(order_id)
        try:
            with self.assertRaisesRegex(ValueError, 'expired'):
                self.apply(review, reader)
        finally:
            clock.stop()
        self.assert_committed()

    def test_prepare_detects_accounting_change_during_fetch(self):
        def reader(order_id):
            with closing(database.get_connection()) as conn, conn:
                conn.execute("UPDATE pro_ticker_paper_reservations SET note = 'changed'")
            return self.reader(order_id)
        with self.assertRaisesRegex(ValueError, 'changed during broker fetch'):
            release.prepare_manual_paper_release(self.reservation_id, fetch_paper_order=reader)
        self.assert_committed()

    def test_service_is_not_imported_by_execution_or_http_modules(self):
        import ast
        from pathlib import Path
        # Only the guarded operator router may import the mutation service.
        for path in Path(__file__).parent.glob('*.py'):
            if path.name.startswith('test') or path.name in {'pro_ticker_manual_release.py', 'pro_ticker_release_routes.py'}:
                continue
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
            imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            self.assertNotIn('pro_ticker_manual_release', imports, str(path))
            names = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
            self.assertNotIn('pro_ticker_manual_release', names, str(path))

    def test_partial_entry_and_split_exit_lifecycle_through_paper_reader(self):
        import uuid
        from pro_ticker_budget_ledger import (
            reserve_pro_ticker_budget, set_paper_reservation_expected_shares,
            link_verified_paper_entry_order, record_verified_paper_order_state,
        )
        from pro_ticker_exit_links import link_verified_paper_exit
        from pro_ticker_verified_fill_bridge import account_linked_paper_entry_snapshot
        from pro_ticker_verified_exit_fill_bridge import account_linked_paper_exit_snapshot
        from pro_ticker_alpaca_paper_reader import make_alpaca_paper_order_reader
        reservation = reserve_pro_ticker_budget('MSFT', 200)['reservation_id']
        set_paper_reservation_expected_shares(reservation, 8)
        entry_id = str(uuid.uuid4())
        entry = dict(id=entry_id, client_order_id='pt-' + reservation, symbol='MSFT',
                     side='buy', qty='8', filled_qty='3', status='partially_filled')
        link_verified_paper_entry_order(reservation, entry)
        self.assertEqual(account_linked_paper_entry_snapshot(reservation, entry)['delta_shares'], 3)
        self.assertEqual(account_linked_paper_entry_snapshot(reservation, entry)['delta_shares'], 0)
        entry['status'] = 'canceled'
        record_verified_paper_order_state(reservation, entry_id, 'canceled', filled_quantity=3)
        orders = {entry_id: entry}
        for quantity, filled, status in [(3, 1, 'canceled'), (2, 2, 'filled')]:
            exit_id, broker_id = str(uuid.uuid4()), str(uuid.uuid4())
            order = dict(id=broker_id, client_order_id='ptx-' + exit_id, symbol='MSFT',
                         side='sell', qty=str(quantity), filled_qty=str(filled), status=status)
            link_verified_paper_exit(reservation_id=reservation, symbol='MSFT', expected_shares=quantity,
                                    broker_order=order, exit_id=exit_id)
            self.assertEqual(account_linked_paper_exit_snapshot(broker_id, order)['delta_shares'], filled)
            self.assertEqual(account_linked_paper_exit_snapshot(broker_id, order)['delta_shares'], 0)
            orders[broker_id] = order
        calls = []
        def http_get(url, *, headers, timeout, allow_redirects):
            self.assertTrue(url.startswith('https://paper-api.alpaca.markets/v2/orders/'))
            self.assertFalse(allow_redirects)
            calls.append(url)
            return Mock(status_code=200, json=Mock(return_value=orders[url.rsplit('/', 1)[1]].copy()))
        reader = make_alpaca_paper_order_reader(key_id='synthetic-paper-key', secret_key='synthetic-paper-secret', http_get=http_get)
        review = release.prepare_manual_paper_release(reservation, fetch_paper_order=reader)
        self.approve(review)
        result = release.release_manually_approved_paper_budget(review['review_id'], fetch_paper_order=reader)
        self.assertTrue(result['budget_released'])
        self.assertEqual(result['released_cents'], 20000)
        self.assertEqual(len(calls), 6)  # Every linked order re-fetched before release.
        self.assertEqual(get_pro_ticker_budget()['committed'], 100)  # Other reservation unaffected.
        self.assertEqual(get_pro_ticker_reservation(reservation)['status'], 'released')

    def test_approval_cannot_be_reassigned(self):
        review = self.prepare()
        self.approve(review)
        with self.assertRaisesRegex(ValueError, 'another operator'):
            release.approve_manual_paper_release(review['review_id'], evidence_digest=review['evidence_digest'],
                approved_by='different-operator', independent_paper_provenance_confirmed=True)
        self.assert_committed()


if __name__ == '__main__':
    unittest.main()
