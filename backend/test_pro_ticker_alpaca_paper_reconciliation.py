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
