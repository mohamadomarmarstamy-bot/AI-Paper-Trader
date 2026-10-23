"""Unit tests for GET-only Alpaca PAPER broker order reader."""
import unittest
from unittest.mock import Mock

from pro_ticker_alpaca_paper_reader import (
    PAPER_BASE_URL, make_alpaca_paper_order_reader,
)


class AlpacaPaperReaderTests(unittest.TestCase):
    def setUp(self):
        self.response = Mock()
        self.response.json.return_value = {"id": "abc", "status": "filled"}
        self.response.status_code = 200
        self.http_get = Mock(return_value=self.response)
        self.reader = make_alpaca_paper_order_reader(
            key_id="test-key", secret_key="test-secret",
            http_get=self.http_get,
        )

    def test_fetch_uses_paper_host_and_get_only(self):
        result = self.reader("abc")
        self.assertEqual(result["id"], "abc")
        args, kwargs = self.http_get.call_args
        self.assertEqual(args[0], PAPER_BASE_URL + "/v2/orders/abc")
        self.assertEqual(kwargs["timeout"], 10.0)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["headers"]["APCA-API-KEY-ID"], "test-key")
        self.response.raise_for_status.assert_called_once()

    def test_redirect_refused_without_following(self):
        self.response.status_code = 302
        with self.assertRaisesRegex(ValueError, "redirect refused"):
            self.reader("abc")
        self.assertFalse(self.http_get.call_args.kwargs["allow_redirects"])

    def test_unsuccessful_status_fails_closed_even_with_permissive_transport(self):
        for status in (100, 201, 204, 400, 401, 429, 500):
            with self.subTest(status=status):
                self.response.status_code = status
                self.response.raise_for_status.reset_mock()
                self.response.json.reset_mock()
                with self.assertRaisesRegex(ValueError, "unexpected HTTP status"):
                    self.reader("abc")
                self.response.json.assert_not_called()

    def test_malformed_http_status_never_parses_order(self):
        for status in (None, True, False, "200", 200.0, [], {}):
            with self.subTest(status=status):
                self.response.status_code = status
                self.response.json.reset_mock()
                with self.assertRaisesRegex(ValueError, "invalid HTTP status"):
                    self.reader("abc")
                self.response.json.assert_not_called()

    def test_mismatched_order_fails_closed(self):
        self.response.json.return_value = {"id": "different"}
        with self.assertRaises(ValueError):
            self.reader("abc")

    def test_non_dictionary_response_fails_closed(self):
        self.response.json.return_value = []
        with self.assertRaises(ValueError):
            self.reader("abc")

    def test_invalid_order_id_never_reaches_network(self):
        for invalid in ("", "../orders", "abc?x=1", " abc", "abc\n", "abc\t"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.reader(invalid)
        self.http_get.assert_not_called()

    def test_http_failure_propagates(self):
        self.response.raise_for_status.side_effect = RuntimeError("unavailable")
        with self.assertRaises(RuntimeError):
            self.reader("abc")

    def test_missing_secret_rejected_before_network(self):
        with self.assertRaises(ValueError):
            make_alpaca_paper_order_reader(
                key_id="test-key", secret_key=" ", http_get=self.http_get,
            )
        self.http_get.assert_not_called()

    def test_non_callable_transport_rejected(self):
        with self.assertRaises(ValueError):
            make_alpaca_paper_order_reader(
                key_id="test-key", secret_key="test-secret", http_get=None,
            )

    def test_invalid_timeout_rejected_before_network(self):
        for timeout in (0, -1, 31, True, "10", float("nan"), float("inf"), float("-inf")):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    make_alpaca_paper_order_reader(
                        key_id="test-key", secret_key="test-secret",
                        http_get=self.http_get, timeout_seconds=timeout,
                    )
        self.http_get.assert_not_called()

    def test_order_id_control_and_path_characters_rejected_before_network(self):
        for invalid in (
            "abc/def", "abc#fragment", "abc?query", "abc\r",
            "abc\x00", "abc\x7f", "abc\n", "abc\t",
            "abc ", " abc", "abc\u001f",
        ):
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaises(ValueError):
                    self.reader(invalid)
        self.http_get.assert_not_called()

    def test_json_decode_failure_propagates_without_retry(self):
        self.response.json.side_effect = ValueError("invalid JSON")
        with self.assertRaises(ValueError):
            self.reader("abc")
        self.http_get.assert_called_once()

    def test_transport_exception_propagates_without_retry(self):
        self.http_get.side_effect = TimeoutError("request timed out")
        with self.assertRaises(TimeoutError):
            self.reader("abc")
        self.http_get.assert_called_once()

    def test_http_authentication_failure_does_not_retry(self):
        self.response.status_code = 401
        self.response.raise_for_status.side_effect = PermissionError(
            "unauthorized PAPER credentials"
        )
        with self.assertRaises(PermissionError):
            self.reader("abc")
        self.http_get.assert_called_once()
        self.response.json.assert_not_called()

    def test_http_rate_limit_failure_does_not_retry(self):
        self.response.status_code = 429
        self.response.raise_for_status.side_effect = RuntimeError(
            "PAPER rate limit"
        )
        with self.assertRaises(RuntimeError):
            self.reader("abc")
        self.http_get.assert_called_once()
        self.response.json.assert_not_called()

    def test_missing_credentials_rejected(self):
        with self.assertRaises(ValueError):
            make_alpaca_paper_order_reader(
                key_id="", secret_key="test", http_get=self.http_get,
            )


if __name__ == "__main__":
    unittest.main()
