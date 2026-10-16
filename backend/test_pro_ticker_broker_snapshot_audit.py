"""Integration tests for read-only PAPER broker snapshot cross-checks."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import database
from pro_ticker_test_db_helper import tracked_test_connections
from pro_ticker_budget_ledger import (
    reserve_pro_ticker_budget, set_paper_reservation_expected_shares,
    link_verified_paper_entry_order, record_verified_paper_order_state,
)
from pro_ticker_exit_links import link_verified_paper_exit
from pro_ticker_position_ledger import post_verified_cumulative_paper_fill
from pro_ticker_broker_snapshot_audit import audit_linked_paper_broker_snapshots
from pro_ticker_paper_broker_fetch_audit import audit_fetched_paper_reservation
from pro_ticker_alpaca_paper_reconciliation import audit_alpaca_paper_reservation


ENTRY_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
EXIT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
EXIT_CLIENT_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


class BrokerSnapshotAuditTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_patch = patch.object(database, "DATABASE_PATH", Path(temp.name) / "test.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        tracker = tracked_test_connections()
        tracker.__enter__()
        self.addCleanup(tracker.__exit__, None, None, None)
        self.reservation_id = reserve_pro_ticker_budget("AAPL", 100)["reservation_id"]
        set_paper_reservation_expected_shares(self.reservation_id, 5)
        self.entry_order = {
            "id": ENTRY_ID, "client_order_id": "pt-" + self.reservation_id,
            "symbol": "AAPL", "side": "buy", "qty": "5",
            "filled_qty": "5", "status": "filled",
        }
        link_verified_paper_entry_order(self.reservation_id, self.entry_order)
        record_verified_paper_order_state(
            self.reservation_id, ENTRY_ID, "filled", filled_quantity=5,
        )
        post_verified_cumulative_paper_fill(
            broker_order_id=ENTRY_ID, symbol="AAPL", side="buy",
            cumulative_filled_shares=5, ownership_verified=True, fill_verified=True,
        )
        self.exit_order = {
            "id": EXIT_ID, "client_order_id": "ptx-" + EXIT_CLIENT_ID,
            "symbol": "AAPL", "side": "sell", "qty": "5",
            "filled_qty": "5", "status": "filled",
        }
        with patch("pro_ticker_exit_links.get_pro_ticker_reservation", return_value={
            "id": self.reservation_id, "symbol": "AAPL", "status": "filled",
            "broker_order_id": ENTRY_ID,
        }):
            link_verified_paper_exit(
                reservation_id=self.reservation_id, symbol="AAPL",
                expected_shares=5, exit_id=EXIT_CLIENT_ID,
                broker_order=self.exit_order,
            )
        post_verified_cumulative_paper_fill(
            broker_order_id=EXIT_ID, symbol="AAPL", side="sell",
            cumulative_filled_shares=5, ownership_verified=True, fill_verified=True,
        )

    def audit(self, **updates):
        values = dict(
            reservation_id=self.reservation_id,
            entry_order=self.entry_order,
            exit_orders={EXIT_ID: self.exit_order},
        )
        values.update(updates)
        return audit_linked_paper_broker_snapshots(**values)

    def test_entry_fill_wrong_symbol_fails_closed_even_if_share_count_matches(self):
        from database import get_connection
        with get_connection() as conn:
            conn.execute(
                "UPDATE pro_ticker_strategy_fill_events SET symbol = ? "
                "WHERE broker_order_id = ?",
                ("MSFT", ENTRY_ID),
            )
        result = self.audit()
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "entry_fill_attribution_mismatch")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])

    def test_entry_fill_wrong_side_fails_closed_even_if_share_count_matches(self):
        from database import get_connection
        with get_connection() as conn:
            conn.execute(
                "UPDATE pro_ticker_strategy_fill_events SET side = ? "
                "WHERE broker_order_id = ?",
                ("sell", ENTRY_ID),
            )
        result = self.audit()
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "entry_fill_attribution_mismatch")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])

    def test_full_alpaca_paper_get_to_sqlite_audit_read_only(self):
        orders = {ENTRY_ID: self.entry_order, EXIT_ID: self.exit_order}
        requested = []
        def http_get(url, *, headers, timeout, allow_redirects):
            self.assertTrue(url.startswith("https://paper-api.alpaca.markets/v2/orders/"))
            self.assertFalse(allow_redirects)
            self.assertEqual(timeout, 10.0)
            self.assertEqual(headers["APCA-API-KEY-ID"], "test-paper-key")
            order_id = url.rsplit("/", 1)[-1]
            requested.append(order_id)
            response = Mock()
            response.status_code = 200
            response.json.return_value = orders[order_id]
            return response
        result = audit_alpaca_paper_reservation(
            self.reservation_id, paper_key_id="test-paper-key",
            paper_secret_key="test-paper-secret", http_get=http_get,
        )
        self.assertEqual(set(requested), {ENTRY_ID, EXIT_ID})
        self.assertEqual(len(requested), 2)
        self.assertTrue(result["verified"])
        self.assertFalse(result["broker_origin_authenticated"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        self.assertFalse(result["database_updated"])

    def test_full_alpaca_paper_get_recovers_after_transient_fetch_failure(self):
        orders = {ENTRY_ID: self.entry_order, EXIT_ID: self.exit_order}
        attempts = []
        def http_get(url, **kwargs):
            order_id = url.rsplit("/", 1)[-1]
            attempts.append(order_id)
            if len(attempts) == 1:
                raise TimeoutError("simulated transient PAPER broker timeout")
            response = Mock()
            response.status_code = 200
            response.json.return_value = orders[order_id]
            return response
        params = dict(
            paper_key_id="test-paper-key",
            paper_secret_key="test-paper-secret", http_get=http_get,
        )
        first = audit_alpaca_paper_reservation(self.reservation_id, **params)
        self.assertFalse(first["verified"])
        self.assertEqual(first["reason"], "paper_order_fetch_failed")
        self.assertFalse(first["database_updated"])
        self.assertFalse(first["budget_released"])
        second = audit_alpaca_paper_reservation(self.reservation_id, **params)
        self.assertTrue(second["verified"], second)
        self.assertEqual(attempts, [ENTRY_ID, ENTRY_ID, EXIT_ID])
        self.assertFalse(second["broker_origin_authenticated"])
        self.assertFalse(second["release_authorized"])
        self.assertFalse(second["budget_released"])
        self.assertFalse(second["database_updated"])

    def test_failed_exit_fetch_then_retry_keeps_accounting_unchanged(self):
        from database import get_connection
        from pro_ticker_budget_ledger import get_pro_ticker_budget
        def fill_rows():
            with get_connection() as conn:
                return [tuple(row) for row in conn.execute(
                    "SELECT broker_order_id, symbol, side, shares "
                    "FROM pro_ticker_strategy_fill_events "
                    "ORDER BY broker_order_id, rowid"
                ).fetchall()]
        before_budget = get_pro_ticker_budget()
        before_rows = fill_rows()
        requests = []
        def http_get(url, **kwargs):
            order_id = url.rsplit("/", 1)[-1]
            requests.append(order_id)
            if order_id == EXIT_ID and requests.count(EXIT_ID) == 1:
                raise TimeoutError("simulated PAPER exit fetch timeout")
            response = Mock()
            response.status_code = 200
            response.json.return_value = (
                self.entry_order if order_id == ENTRY_ID else self.exit_order
            )
            return response
        params = dict(paper_key_id="test-paper-key",
                      paper_secret_key="test-paper-secret", http_get=http_get)
        first = audit_alpaca_paper_reservation(self.reservation_id, **params)
        self.assertFalse(first["verified"])
        self.assertEqual(first["reason"], "paper_order_fetch_failed")
        self.assertEqual(get_pro_ticker_budget(), before_budget)
        self.assertEqual(fill_rows(), before_rows)
        second = audit_alpaca_paper_reservation(self.reservation_id, **params)
        self.assertTrue(second["verified"], second)
        self.assertEqual(requests, [ENTRY_ID, EXIT_ID, ENTRY_ID, EXIT_ID])
        self.assertEqual(get_pro_ticker_budget(), before_budget)
        self.assertEqual(fill_rows(), before_rows)
        self.assertFalse(second["broker_origin_authenticated"])
        self.assertFalse(second["release_authorized"])
        self.assertFalse(second["budget_released"])

    def test_reopened_sqlite_connections_preserve_paper_audit_evidence(self):
        from database import get_connection
        from pro_ticker_budget_ledger import get_pro_ticker_budget
        from pro_ticker_position_ledger import get_accounted_paper_order_shares
        before = get_pro_ticker_budget()
        for _ in range(3):
            # Each accessor opens a fresh connection to the same durable
            # temporary SQLite file, emulating recovery after a restart.
            with get_connection() as conn:
                persisted = conn.execute(
                    "SELECT status, broker_order_id, expected_shares "
                    "FROM pro_ticker_paper_reservations WHERE id = ?",
                    (self.reservation_id,),
                ).fetchone()
                self.assertEqual(tuple(persisted), ("filled", ENTRY_ID, 5))
            self.assertEqual(get_accounted_paper_order_shares(ENTRY_ID), 5)
            self.assertEqual(get_accounted_paper_order_shares(EXIT_ID), 5)
            result = self.audit()
            self.assertTrue(result["verified"], result)
            self.assertFalse(result["broker_origin_authenticated"])
            self.assertFalse(result["budget_released"])
            self.assertFalse(result["database_updated"])
        self.assertEqual(get_pro_ticker_budget(), before)

    def test_full_alpaca_paper_get_rejects_wrong_exit_snapshot(self):
        orders = {
            ENTRY_ID: self.entry_order,
            EXIT_ID: {**self.exit_order, "filled_qty": "4", "status": "canceled"},
        }
        def http_get(url, **kwargs):
            response = Mock()
            response.status_code = 200
            response.json.return_value = orders[url.rsplit("/", 1)[-1]]
            return response
        result = audit_alpaca_paper_reservation(
            self.reservation_id, paper_key_id="test-paper-key",
            paper_secret_key="test-paper-secret", http_get=http_get,
        )
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "exit_accounting_mismatch")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])

    def test_full_alpaca_paper_get_refuses_redirect_without_budget_release(self):
        requested = []
        def http_get(url, **kwargs):
            requested.append((url, kwargs))
            response = Mock()
            response.status_code = 302
            response.headers = {"Location": "https://example.invalid/"}
            return response
        result = audit_alpaca_paper_reservation(
            self.reservation_id, paper_key_id="test-paper-key",
            paper_secret_key="test-paper-secret", http_get=http_get,
        )
        self.assertEqual(len(requested), 1)
        self.assertFalse(requested[0][1]["allow_redirects"])
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "paper_order_fetch_failed")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        self.assertFalse(result["database_updated"])

    def test_full_alpaca_paper_get_rejects_mismatched_entry_identity(self):
        requested = []
        def http_get(url, **kwargs):
            order_id = url.rsplit("/", 1)[-1]
            requested.append(order_id)
            response = Mock()
            response.status_code = 200
            response.json.return_value = {
                **self.entry_order, "id": "not-the-linked-order",
            }
            return response
        result = audit_alpaca_paper_reservation(
            self.reservation_id, paper_key_id="test-paper-key",
            paper_secret_key="test-paper-secret", http_get=http_get,
        )
        self.assertEqual(requested, [ENTRY_ID])
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "paper_order_fetch_failed")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        self.assertFalse(result["database_updated"])

    def test_full_alpaca_paper_get_rejects_mismatched_exit_identity(self):
        requested = []
        def http_get(url, **kwargs):
            order_id = url.rsplit("/", 1)[-1]
            requested.append(order_id)
            response = Mock()
            response.status_code = 200
            order = self.entry_order if order_id == ENTRY_ID else self.exit_order
            response.json.return_value = (
                order if order_id == ENTRY_ID else {
                    **order, "id": "not-the-linked-exit",
                }
            )
            return response
        result = audit_alpaca_paper_reservation(
            self.reservation_id, paper_key_id="test-paper-key",
            paper_secret_key="test-paper-secret", http_get=http_get,
        )
        self.assertEqual(requested, [ENTRY_ID, EXIT_ID])
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "paper_order_fetch_failed")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        self.assertFalse(result["database_updated"])

    def test_full_alpaca_paper_get_rejects_missing_exit_response(self):
        requested = []
        def http_get(url, **kwargs):
            order_id = url.rsplit("/", 1)[-1]
            requested.append(order_id)
            response = Mock()
            response.status_code = 200
            response.json.return_value = (
                self.entry_order if order_id == ENTRY_ID else None
            )
            return response
        result = audit_alpaca_paper_reservation(
            self.reservation_id, paper_key_id="test-paper-key",
            paper_secret_key="test-paper-secret", http_get=http_get,
        )
        self.assertEqual(requested, [ENTRY_ID, EXIT_ID])
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "paper_order_fetch_failed")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        self.assertFalse(result["database_updated"])

    def test_full_alpaca_paper_get_rejects_non_dict_entry_response(self):
        requested = []
        def http_get(url, **kwargs):
            order_id = url.rsplit("/", 1)[-1]
            requested.append(order_id)
            response = Mock()
            response.status_code = 200
            response.json.return_value = ["unexpected", "list"]
            return response
        result = audit_alpaca_paper_reservation(
            self.reservation_id, paper_key_id="test-paper-key",
            paper_secret_key="test-paper-secret", http_get=http_get,
        )
        self.assertEqual(requested, [ENTRY_ID])
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "paper_order_fetch_failed")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        self.assertFalse(result["database_updated"])

    def test_full_alpaca_paper_get_rejects_non_dict_exit_response(self):
        requested = []
        def http_get(url, **kwargs):
            order_id = url.rsplit("/", 1)[-1]
            requested.append(order_id)
            response = Mock()
            response.status_code = 200
            response.json.return_value = (
                self.entry_order if order_id == ENTRY_ID else ["invalid", "exit"]
            )
            return response
        result = audit_alpaca_paper_reservation(
            self.reservation_id, paper_key_id="test-paper-key",
            paper_secret_key="test-paper-secret", http_get=http_get,
        )
        self.assertEqual(requested, [ENTRY_ID, EXIT_ID])
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "paper_order_fetch_failed")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        self.assertFalse(result["database_updated"])

    def test_fetched_broker_audit_uses_exact_linked_order_ids(self):
        requested = []
        orders = {ENTRY_ID: self.entry_order, EXIT_ID: self.exit_order}
        def read_order(order_id):
            requested.append(order_id)
            return orders[order_id]
        result = audit_fetched_paper_reservation(
            self.reservation_id, fetch_paper_order=read_order,
        )
        self.assertEqual(set(requested), {ENTRY_ID, EXIT_ID})
        self.assertTrue(result["verified"])
        self.assertFalse(result["broker_origin_authenticated"])
        self.assertFalse(result["release_authorized"])

    def test_fetched_broker_audit_fails_closed_on_missing_order(self):
        result = audit_fetched_paper_reservation(
            self.reservation_id, fetch_paper_order=lambda order_id: None,
        )
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "paper_order_fetch_incomplete")
        self.assertFalse(result["budget_released"])

    def test_same_symbol_second_active_reservation_is_rejected(self):
        # The virtual budget deliberately prohibits simultaneous active
        # reservations for the same symbol; no cross-attribution is possible.
        with self.assertRaisesRegex(ValueError, "active reservation already exists"):
            reserve_pro_ticker_budget("AAPL", 100)
        first = self.audit()
        self.assertTrue(first["verified"])
        self.assertFalse(first["release_authorized"])
        self.assertFalse(first["budget_released"])

    def test_exit_fill_wrong_symbol_fails_closed_even_when_share_total_matches(self):
        # A corrupted persisted event must not be accepted just because
        # its share count matches the PAPER broker snapshot.
        from database import get_connection
        with get_connection() as conn:
            conn.execute(
                "UPDATE pro_ticker_strategy_fill_events SET symbol = ? "
                "WHERE broker_order_id = ?",
                ("MSFT", EXIT_ID),
            )
        result = self.audit()
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "exit_fill_attribution_mismatch")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])

    def test_exit_fill_wrong_side_fails_closed_even_when_total_matches(self):
        from database import get_connection
        with get_connection() as conn:
            conn.execute(
                "UPDATE pro_ticker_strategy_fill_events SET side = ? "
                "WHERE broker_order_id = ?",
                ("buy", EXIT_ID),
            )
        result = self.audit()
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "exit_fill_attribution_mismatch")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        self.assertFalse(result["database_updated"])

    def test_entry_fill_quantity_corruption_fails_closed(self):
        from database import get_connection
        with get_connection() as conn:
            conn.execute(
                "UPDATE pro_ticker_strategy_fill_events SET shares = ? "
                "WHERE broker_order_id = ?",
                (4, ENTRY_ID),
            )
        result = self.audit()
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "entry_accounting_mismatch")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        self.assertFalse(result["database_updated"])

    def test_two_linked_exit_orders_do_not_double_count(self):
        # A second linked exit was canceled without a fill. It must not
        # inflate the five shares already sold by the first exit.
        second_exit_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        second_client_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        second_exit = {
            "id": second_exit_id, "client_order_id": "ptx-" + second_client_id,
            "symbol": "AAPL", "side": "sell", "qty": "2",
            "filled_qty": "0", "status": "canceled",
        }
        with patch("pro_ticker_exit_links.get_pro_ticker_reservation", return_value={
            "id": self.reservation_id, "symbol": "AAPL", "status": "filled",
            "broker_order_id": ENTRY_ID,
        }):
            link_verified_paper_exit(
                reservation_id=self.reservation_id, symbol="AAPL",
                expected_shares=2, exit_id=second_client_id,
                broker_order=second_exit,
            )
        result = self.audit(exit_orders={
            EXIT_ID: self.exit_order, second_exit_id: second_exit,
        })
        self.assertTrue(result["verified"], result)
        self.assertEqual(result["exit_shares"], 5)
        self.assertFalse(result["release_authorized"])

    def test_zero_fill_canceled_exit_does_not_close_remaining_shares(self):
        # A canceled order with no fill is not evidence of a closed position.
        # Keep the existing five-share fill out of this audit by using a
        # separate reservation with no posted exit shares.
        reservation_id = reserve_pro_ticker_budget("MSFT", 100)["reservation_id"]
        entry_id = "11111111-1111-4111-8111-111111111112"
        exit_id = "22222222-2222-4222-8222-222222222223"
        client_id = "33333333-3333-4333-8333-333333333334"
        set_paper_reservation_expected_shares(reservation_id, 5)
        entry = {
            "id": entry_id, "client_order_id": "pt-" + reservation_id,
            "symbol": "MSFT", "side": "buy", "qty": "5",
            "filled_qty": "5", "status": "filled",
        }
        link_verified_paper_entry_order(reservation_id, entry)
        record_verified_paper_order_state(
            reservation_id, entry_id, "filled", filled_quantity=5,
        )
        post_verified_cumulative_paper_fill(
            broker_order_id=entry_id, symbol="MSFT", side="buy",
            cumulative_filled_shares=5, ownership_verified=True,
            fill_verified=True,
        )
        canceled = {
            "id": exit_id, "client_order_id": "ptx-" + client_id,
            "symbol": "MSFT", "side": "sell", "qty": "5",
            "filled_qty": "0", "status": "canceled",
        }
        with patch("pro_ticker_exit_links.get_pro_ticker_reservation", return_value={
            "id": reservation_id, "symbol": "MSFT", "status": "filled",
            "broker_order_id": entry_id,
        }):
            link_verified_paper_exit(
                reservation_id=reservation_id, symbol="MSFT",
                expected_shares=5, exit_id=client_id,
                broker_order=canceled,
            )
        result = self.audit(
            reservation_id=reservation_id, entry_order=entry,
            exit_orders={exit_id: canceled},
        )
        self.assertFalse(result["verified"], result)
        self.assertEqual(result["reason"], "reservation_shares_still_open")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        self.assertFalse(result["database_updated"])

    def test_partially_filled_terminal_exit_and_second_exit_reconcile(self):
        # Start with a three-share first exit and a two-share second exit.
        # This test uses a separate ledger for each test run, with the
        # existing five-share exit replaced by a three-share fill.
        second_exit_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        second_client_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        second_exit = {
            "id": second_exit_id, "client_order_id": "ptx-" + second_client_id,
            "symbol": "AAPL", "side": "sell", "qty": "2",
            "filled_qty": "2", "status": "filled",
        }
        with patch("pro_ticker_exit_links.get_pro_ticker_reservation", return_value={
            "id": self.reservation_id, "symbol": "AAPL", "status": "filled",
            "broker_order_id": ENTRY_ID,
        }):
            link_verified_paper_exit(
                reservation_id=self.reservation_id, symbol="AAPL",
                expected_shares=2, exit_id=second_client_id,
                broker_order=second_exit,
            )
        # The first exit already accounts for five shares; a second
        # two-share fill must be rejected rather than over-counted.
        result = self.audit(exit_orders={
            EXIT_ID: self.exit_order, second_exit_id: second_exit,
        })
        self.assertFalse(result["verified"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])

    def test_two_exit_orders_with_partial_terminal_fill_reconcile(self):
        # Set up a fresh, independent reservation to avoid rewriting
        # existing fill history. Five entry shares close as 3 + 2.
        reservation_id = reserve_pro_ticker_budget("MSFT", 100)["reservation_id"]
        entry_id = "11111111-1111-4111-8111-111111111111"
        set_paper_reservation_expected_shares(reservation_id, 5)
        entry = {
            "id": entry_id, "client_order_id": "pt-" + reservation_id,
            "symbol": "MSFT", "side": "buy", "qty": "5",
            "filled_qty": "5", "status": "filled",
        }
        link_verified_paper_entry_order(reservation_id, entry)
        record_verified_paper_order_state(
            reservation_id, entry_id, "filled", filled_quantity=5,
        )
        post_verified_cumulative_paper_fill(
            broker_order_id=entry_id, symbol="MSFT", side="buy",
            cumulative_filled_shares=5, ownership_verified=True,
            fill_verified=True,
        )
        exit_orders = {}
        for index, (shares, ordered, status) in enumerate([
            (3, 5, "canceled"), (2, 2, "filled"),
        ]):
            exit_id = ("22222222-2222-4222-8222-22222222222" + str(index))
            client_id = ("33333333-3333-4333-8333-33333333333" + str(index))
            snapshot = {
                "id": exit_id, "client_order_id": "ptx-" + client_id,
                "symbol": "MSFT", "side": "sell", "qty": str(ordered),
                "filled_qty": str(shares), "status": status,
            }
            with patch("pro_ticker_exit_links.get_pro_ticker_reservation", return_value={
                "id": reservation_id, "symbol": "MSFT", "status": "filled",
                "broker_order_id": entry_id,
            }):
                link_verified_paper_exit(
                    reservation_id=reservation_id, symbol="MSFT",
                    expected_shares=ordered, exit_id=client_id,
                    broker_order=snapshot,
                )
            post_verified_cumulative_paper_fill(
                broker_order_id=exit_id, symbol="MSFT", side="sell",
                cumulative_filled_shares=shares,
                ownership_verified=True, fill_verified=True,
            )
            exit_orders[exit_id] = snapshot
        result = self.audit(
            reservation_id=reservation_id, entry_order=entry,
            exit_orders=exit_orders,
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["entry_shares"], 5)
        self.assertEqual(result["exit_shares"], 5)
        self.assertFalse(result["broker_origin_authenticated"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])

    def test_partial_terminal_exit_accounting_mismatch_fails_closed(self):
        # Broker reports only four of the five shares accounted in ledger.
        incorrect = {**self.exit_order, "filled_qty": "4", "status": "canceled"}
        result = self.audit(exit_orders={EXIT_ID: incorrect})
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "exit_accounting_mismatch")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])

    def test_duplicate_exit_order_keys_are_not_accepted_as_extra_fills(self):
        # Two snapshots with the same ID cannot represent two distinct exits.
        result = self.audit(exit_orders={
            EXIT_ID: self.exit_order,
            "dddddddd-dddd-4ddd-8ddd-dddddddddddd": {
                **self.exit_order,
                "id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            },
        })
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "exit_snapshots_incomplete_or_extra")
        self.assertFalse(result["budget_released"])

    def test_repeated_successful_audit_never_changes_virtual_budget_or_fill_rows(self):
        from database import get_connection
        from pro_ticker_budget_ledger import get_pro_ticker_budget
        before_budget = get_pro_ticker_budget()
        with get_connection() as conn:
            before_rows = conn.execute(
                "SELECT COUNT(*) FROM pro_ticker_strategy_fill_events"
            ).fetchone()[0]
        for _ in range(3):
            result = self.audit()
            self.assertTrue(result["verified"], result)
            self.assertFalse(result["release_authorized"])
            self.assertFalse(result["budget_released"])
            self.assertFalse(result["database_updated"])
        self.assertEqual(get_pro_ticker_budget(), before_budget)
        with get_connection() as conn:
            after_rows = conn.execute(
                "SELECT COUNT(*) FROM pro_ticker_strategy_fill_events"
            ).fetchone()[0]
        self.assertEqual(after_rows, before_rows)

    def test_consistent_audit_does_not_authenticate_evidence_for_release(self):
        from pro_ticker_release_eligibility import review_paper_release_eligibility
        from pro_ticker_budget_ledger import get_pro_ticker_reservation
        audit = self.audit()
        self.assertTrue(audit["verified"], audit)
        self.assertFalse(audit["broker_origin_authenticated"])
        reservation = get_pro_ticker_reservation(self.reservation_id)
        exit_fills = [{
            "broker_order_id": EXIT_ID, "symbol": "AAPL", "side": "sell",
            "accounted_shares": audit["exit_shares"],
            "ownership_verified": True, "fill_verified": True,
        }]
        release_review = review_paper_release_eligibility(
            reservation=reservation, entry_order_id=ENTRY_ID,
            entry_filled_shares=audit["entry_shares"],
            exit_fills=exit_fills, remaining_strategy_shares=0,
            broker_evidence_verified=audit["broker_origin_authenticated"],
        )
        self.assertEqual(release_review["reason"], "broker_evidence_unverified")
        self.assertFalse(release_review["eligible_for_independent_review"])
        self.assertFalse(release_review["release_authorized"])
        self.assertFalse(release_review["budget_released"])
        self.assertFalse(release_review["database_updated"])

    def test_complete_linked_broker_snapshots_match(self):
        result = self.audit()
        self.assertTrue(result["verified"])
        self.assertFalse(result["broker_origin_authenticated"])
        self.assertEqual(result["entry_shares"], 5)
        self.assertEqual(result["exit_shares"], 5)
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])

    def test_exit_snapshot_identity_mismatch_fails_closed(self):
        incorrect = {**self.exit_order, "id": ENTRY_ID}
        result = self.audit(exit_orders={EXIT_ID: incorrect})
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "exit_snapshot_order_id_mismatch")

    def test_exit_snapshot_non_dict_fails_closed(self):
        result = self.audit(exit_orders={EXIT_ID: None})
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "invalid_exit_broker_snapshot")

    def test_missing_entry_snapshot_fails_closed(self):
        result = self.audit(entry_order=None)
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "entry_broker_snapshot_missing")

    def test_missing_exit_snapshot_fails_closed(self):
        result = self.audit(exit_orders={})
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "exit_snapshots_incomplete_or_extra")

    def test_wrong_exit_client_order_fails_closed(self):
        bad = {**self.exit_order, "client_order_id": "wrong"}
        result = self.audit(exit_orders={EXIT_ID: bad})
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "paper_exit_client_order_id_mismatch")

    def test_entry_broker_snapshot_share_mismatch_fails_closed(self):
        incorrect = {**self.entry_order, "filled_qty": "3", "status": "partially_filled"}
        result = self.audit(entry_order=incorrect)
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "entry_broker_snapshot_not_fully_filled")
        self.assertFalse(result["budget_released"])

    def test_exit_broker_snapshot_share_mismatch_fails_closed(self):
        incorrect = {**self.exit_order, "filled_qty": "4", "status": "canceled"}
        result = self.audit(exit_orders={EXIT_ID: incorrect})
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "exit_accounting_mismatch")
        self.assertFalse(result["release_authorized"])

    def test_partial_exit_snapshot_fails_closed(self):
        partial = {**self.exit_order, "status": "partially_filled", "filled_qty": "2"}
        result = self.audit(exit_orders={EXIT_ID: partial})
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "exit_broker_order_not_terminal")


if __name__ == "__main__":
    unittest.main()
