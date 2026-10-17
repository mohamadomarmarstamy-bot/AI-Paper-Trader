"""Real FastAPI HTTP boundaries and SQLite manual release lifecycle."""
import asyncio
from contextlib import closing
import hashlib
import hmac
import time
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI
import database
import test_pro_ticker_broker_snapshot_audit as fixtures
from test_pro_ticker_production_auth_http import production_auth
from pro_ticker_release_routes import build_paper_release_router
from pro_ticker_release_reporting import list_paper_budget_releases
from pro_ticker_budget_ledger import get_pro_ticker_budget

PREFIX = '/auto-trader/pro-ticker-paper'


class ReleaseHTTPTests(unittest.TestCase):
    setUpFixture = fixtures.BrokerSnapshotAuditTests.setUp

    def setUp(self):
        self.setUpFixture()
        self.enabled = True
        self.auth = production_auth()
        self.orders = {fixtures.ENTRY_ID: self.entry_order, fixtures.EXIT_ID: self.exit_order}
        self.reader = Mock(side_effect=lambda order_id: self.orders[order_id].copy())
        self.make_reader = Mock(return_value=self.reader)
        self.app = FastAPI()
        self.app.include_router(build_paper_release_router(
            require_session=self.auth['require_app_session'], operator_enabled=lambda: self.enabled,
            make_paper_reader=self.make_reader,
        ))

    def request(self, method, path, *, authenticated=True, headers=None, **kwargs):
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://testserver') as client:
                if authenticated:
                    stamp = str(int(time.time()))
                    sig = hmac.new(b'offline-http-test-secret', stamp.encode(), hashlib.sha256).hexdigest()
                    client.cookies.set('ai_paper_trader_session', stamp + '.' + sig)
                return await client.request(method, PREFIX + path, headers=headers or {}, **kwargs)
        return asyncio.run(send())

    def write(self, path, data, **kwargs):
        return self.request('POST', path, json=data, headers={
            'origin': 'http://testserver', 'x-pro-ticker-operator': 'manual-release', **kwargs.pop('headers', {})}, **kwargs)

    def review(self):
        response = self.write('/release-review', {'reservation_id': self.reservation_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def confirmation(self, review):
        return dict(review_id=review['review_id'], evidence_digest=review['evidence_digest'], paper_provenance_confirmed=True)

    def test_all_endpoints_reject_anonymous_before_broker_or_ledger_work(self):
        for method, path in [('GET', '/release-status'), ('GET', '/release-events'),
                             ('POST', '/release-review'), ('POST', '/release-confirm')]:
            with self.subTest(path=path):
                response = self.request(method, path, authenticated=False, content=b'bad json')
                self.assertEqual(response.status_code, 401)
        self.make_reader.assert_not_called()

    def test_controls_default_disabled_but_history_remains_readable(self):
        self.enabled = False
        self.assertFalse(self.request('GET', '/release-status').json()['manual_release_enabled'])
        self.assertEqual(self.write('/release-review', {'reservation_id': self.reservation_id}).status_code, 403)
        self.assertEqual(self.request('GET', '/release-events').status_code, 200)
        self.make_reader.assert_not_called()

    def test_cross_origin_and_missing_origin_cannot_write(self):
        for origin in ('https://attacker.example', 'null', 'http://testserver.attacker.example', ''):
            with self.subTest(origin=origin):
                self.assertEqual(self.write('/release-review', {'reservation_id': self.reservation_id}, headers={'origin': origin}).status_code, 403)
        self.make_reader.assert_not_called()

    def test_missing_intent_and_non_json_cannot_write(self):
        self.assertEqual(self.write('/release-review', {'reservation_id': self.reservation_id}, headers={'x-pro-ticker-operator': ''}).status_code, 403)
        response = self.request('POST', '/release-review', content=b'{}', headers={
            'origin': 'http://testserver', 'x-pro-ticker-operator': 'manual-release', 'content-type': 'text/plain'})
        self.assertEqual(response.status_code, 415)
        self.make_reader.assert_not_called()

    def test_request_cannot_select_operator_or_submit_broker_evidence(self):
        for extra in ({'approved_by': 'admin'}, {'broker_order': self.entry_order}, {'fetch_paper_order': 'remote'}):
            with self.subTest(extra=extra):
                self.assertEqual(self.write('/release-review', {'reservation_id': self.reservation_id, **extra}).status_code, 422)
        self.make_reader.assert_not_called()

    def test_bad_json_shape_uuid_and_oversized_requests_fail_without_broker(self):
        for data in ([], None, {}, {'reservation_id': 'wrong'}, {'reservation_id': True}):
            with self.subTest(data=data):
                response = self.write('/release-review', data)
                self.assertIn(response.status_code, (400, 415, 422))
        response = self.request('POST', '/release-review', content=b'{' + b' ' * 3000, headers={
            'origin': 'http://testserver', 'x-pro-ticker-operator': 'manual-release', 'content-type': 'application/json'})
        self.assertEqual(response.status_code, 413)
        self.make_reader.assert_not_called()

    def test_review_confirm_retry_and_history_are_consistent(self):
        review = self.review()
        self.assertFalse(review['budget_released'])
        self.assertEqual(get_pro_ticker_budget()['committed'], 100)
        for expected_release in (True, False):
            if not expected_release:
                self.make_reader.side_effect = RuntimeError('credentials temporarily unavailable')
            response = self.write('/release-confirm', self.confirmation(review))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['budget_released'], expected_release)
        history = self.request('GET', '/release-events').json()
        self.assertEqual(history['total_count'], 1)
        self.assertEqual(history['total_released_cents'], 10000)
        self.assertEqual(history['events'][0]['symbol'], 'AAPL')
        self.assertEqual(history['events'][0]['approved_by'], 'authenticated-app-owner')
        self.assertFalse(history['counts_as_realized_profit'])
        self.assertFalse(history['automatic_release_enabled'])
        self.assertEqual(get_pro_ticker_budget()['committed'], 0)

    def test_confirmation_requires_true_attestation_and_exact_digest(self):
        review = self.review()
        for update in ({'paper_provenance_confirmed': False}, {'paper_provenance_confirmed': 1},
                       {'evidence_digest': 'bad'}, {'review_id': []}, {'approved_by': 'forged'}):
            with self.subTest(update=update):
                response = self.write('/release-confirm', {**self.confirmation(review), **update})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(get_pro_ticker_budget()['committed'], 100)

    def test_transport_exception_does_not_expose_secret_text(self):
        self.reader.side_effect = RuntimeError('secret-key=must-not-leak')
        response = self.write('/release-review', {'reservation_id': self.reservation_id})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('must-not-leak', response.text)
        self.assertEqual(get_pro_ticker_budget()['committed'], 100)

    def test_network_failure_after_approval_can_retry_same_confirmation(self):
        review = self.review()
        self.reader.side_effect = RuntimeError('temporary network failure')
        response = self.write('/release-confirm', self.confirmation(review))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(get_pro_ticker_budget()['committed'], 100)
        self.reader.side_effect = lambda order_id: self.orders[order_id].copy()
        self.assertEqual(self.write('/release-confirm', self.confirmation(review)).status_code, 200)

    def test_changed_evidence_returns_conflict_and_keeps_budget(self):
        review = self.review()
        self.exit_order.update(status='canceled', filled_qty='4')
        self.assertEqual(self.write('/release-confirm', self.confirmation(review)).status_code, 409)
        self.assertEqual(get_pro_ticker_budget()['committed'], 100)

    def test_reporting_is_read_only_and_has_validated_pagination(self):
        connection = database.get_connection()
        statements = []
        connection.set_trace_callback(statements.append)
        with patch('pro_ticker_release_reporting.get_connection', return_value=connection):
            report = list_paper_budget_releases()
        self.assertEqual(report['events'], [])
        self.assertFalse(any(statement.startswith(('CREATE', 'UPDATE', 'INSERT')) for statement in statements))
        for query in ('?limit=0', '?limit=101', '?offset=-1'):
            self.assertEqual(self.request('GET', '/release-events' + query).status_code, 422)
        for limit, offset in [(True, 0), (10, True), (101, 0), (10, -1)]:
            with self.assertRaises(ValueError):
                list_paper_budget_releases(limit=limit, offset=offset)


if __name__ == '__main__':
    unittest.main()
