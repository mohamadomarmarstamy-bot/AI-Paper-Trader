"""Tests for read-only PAPER reconciliation orchestration."""
import unittest
from unittest.mock import Mock, patch

from pro_ticker_alpaca_paper_reconciliation import audit_alpaca_paper_reservation


class AlpacaPaperReconciliationTests(unittest.TestCase):
    def test_invalid_credentials_fail_closed_without_network(self):
        http_get = Mock()
        result = audit_alpaca_paper_reservation(
            "test-reservation", paper_key_id="", paper_secret_key="secret",
            http_get=http_get,
        )
        self.assertEqual(result["reason"], "invalid_paper_broker_configuration")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        http_get.assert_not_called()

    def test_paper_http_error_fails_closed(self):
        response = Mock()
        response.raise_for_status.side_effect = RuntimeError("broker unavailable")
        http_get = Mock(return_value=response)
        with patch(
            "pro_ticker_paper_broker_fetch_audit.get_pro_ticker_reservation",
            return_value={"broker_order_id": "abc"},
        ), patch(
            "pro_ticker_paper_broker_fetch_audit.get_connection",
        ) as connection:
            connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
            result = audit_alpaca_paper_reservation(
                "reservation", paper_key_id="paper-key",
                paper_secret_key="paper-secret", http_get=http_get,
            )
        self.assertEqual(result["reason"], "paper_order_fetch_failed")
        self.assertFalse(result["verified"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])

    def test_broker_order_id_mismatch_fails_closed(self):
        response = Mock()
        response.json.return_value = {"id": "unexpected"}
        http_get = Mock(return_value=response)
        with patch(
            "pro_ticker_paper_broker_fetch_audit.get_pro_ticker_reservation",
            return_value={"broker_order_id": "abc"},
        ), patch(
            "pro_ticker_paper_broker_fetch_audit.get_connection",
        ) as connection:
            connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
            result = audit_alpaca_paper_reservation(
                "reservation", paper_key_id="paper-key",
                paper_secret_key="paper-secret", http_get=http_get,
            )
        self.assertEqual(result["reason"], "paper_order_fetch_failed")
        self.assertFalse(result["broker_origin_authenticated"])
        self.assertFalse(result["release_authorized"])

    def test_partial_fill_from_paper_endpoint_fails_closed(self):
        response = Mock()
        response.json.return_value = {
            "id": "abc", "client_order_id": "pt-reservation",
            "symbol": "AAPL", "side": "buy", "qty": "5",
            "filled_qty": "2", "status": "partially_filled",
        }
        http_get = Mock(return_value=response)
        with patch(
            "pro_ticker_paper_broker_fetch_audit.get_pro_ticker_reservation",
            return_value={"broker_order_id": "abc"},
        ), patch(
            "pro_ticker_paper_broker_fetch_audit.get_connection",
        ) as connection, patch(
            "pro_ticker_broker_snapshot_audit.get_pro_ticker_reservation",
            return_value={"broker_order_id": "abc", "status": "filled",
                          "symbol": "AAPL", "expected_shares": 5},
        ):
            connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
            result = audit_alpaca_paper_reservation(
                "reservation", paper_key_id="paper-key",
                paper_secret_key="paper-secret", http_get=http_get,
            )
        self.assertEqual(result["reason"], "entry_broker_snapshot_not_fully_filled")
        self.assertFalse(result["verified"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])

    def test_non_json_broker_response_fails_closed(self):
        response = Mock()
        response.status_code = 200
        response.json.side_effect = ValueError("invalid JSON")
        http_get = Mock(return_value=response)
        with patch(
            "pro_ticker_paper_broker_fetch_audit.get_pro_ticker_reservation",
            return_value={"broker_order_id": "abc"},
        ), patch(
            "pro_ticker_paper_broker_fetch_audit.get_connection",
        ) as connection:
            connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
            result = audit_alpaca_paper_reservation(
                "reservation", paper_key_id="paper-key",
                paper_secret_key="paper-secret", http_get=http_get,
            )
        self.assertEqual(result["reason"], "paper_order_fetch_failed")
        self.assertFalse(result["verified"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])

    def test_exit_fetch_failure_fails_closed(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"id": "entry"}
        http_get = Mock(return_value=response)
        with patch(
            "pro_ticker_paper_broker_fetch_audit.get_pro_ticker_reservation",
            return_value={"broker_order_id": "entry"},
        ), patch(
            "pro_ticker_paper_broker_fetch_audit.get_connection",
        ) as connection:
            connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = [
                {"broker_order_id": "exit"},
            ]
            result = audit_alpaca_paper_reservation(
                "reservation", paper_key_id="paper-key",
                paper_secret_key="paper-secret", http_get=http_get,
            )
        self.assertEqual(result["reason"], "paper_order_fetch_failed")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        self.assertEqual(http_get.call_count, 2)

    def test_valid_config_passes_reader_to_audit_without_release(self):
        http_get = Mock()
        with patch(
            "pro_ticker_alpaca_paper_reconciliation.audit_fetched_paper_reservation",
            return_value={"verified": True, "broker_origin_authenticated": True,
                          "release_authorized": True, "budget_released": True,
                          "database_updated": True, "reason": "test"},
        ) as audit:
            result = audit_alpaca_paper_reservation(
                "reservation", paper_key_id="paper-key",
                paper_secret_key="paper-secret", http_get=http_get,
            )
        audit.assert_called_once()
        self.assertEqual(audit.call_args.args[0], "reservation")
        self.assertTrue(callable(audit.call_args.kwargs["fetch_paper_order"]))
        self.assertFalse(result["broker_origin_authenticated"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        self.assertFalse(result["database_updated"])
        http_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
