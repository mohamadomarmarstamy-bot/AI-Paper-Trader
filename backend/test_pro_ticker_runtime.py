"""Dedicated configuration never reads or dispatches with original trader keys."""
import unittest
from unittest.mock import Mock,patch
from pro_ticker_runtime import dedicated_paper_context,dedicated_release_reader
from pro_ticker_broker_admission import execution_status


class DedicatedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.env={'PRO_TICKER_PAPER_ACCOUNT_ID':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            'PRO_TICKER_ORIGINAL_ACCOUNT_ID':'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
            'PRO_TICKER_DEDICATED_ACCOUNT_CONFIRMED':'true','PRO_TICKER_PAPER_KEY_ID':'dedicated-synthetic',
            'PRO_TICKER_PAPER_SECRET_KEY':'dedicated-secret-synthetic'}

    def test_default_and_original_only_are_off_without_transport(self):
        for env in ({},{'ALPACA_API_KEY':'original','ALPACA_SECRET_KEY':'original'}):
            self.assertIsNone(dedicated_paper_context(environ=env))

    def test_missing_mismatched_or_reused_identity_is_off(self):
        changes=[{'PRO_TICKER_PAPER_ACCOUNT_ID':'invalid'},{'PRO_TICKER_ORIGINAL_ACCOUNT_ID':''},
                 {'PRO_TICKER_PAPER_ACCOUNT_ID':self.env['PRO_TICKER_ORIGINAL_ACCOUNT_ID']},
                 {'PRO_TICKER_PAPER_KEY_ID':''},{'PRO_TICKER_PAPER_SECRET_KEY':' has whitespace '},
                 {'PRO_TICKER_DEDICATED_ACCOUNT_CONFIRMED':'false'}, {'ALPACA_API_KEY':'dedicated-synthetic'},
                 {'APCA_API_KEY_ID':'dedicated-synthetic'}]
        for change in changes:
            with self.subTest(change=change):self.assertIsNone(dedicated_paper_context(environ={**self.env,**change}))

    def test_reads_only_by_default_and_owner_submission_requires_exact_opt_in(self):
        get=Mock();post=Mock()
        context=dedicated_paper_context(environ=self.env,http_get=get,http_post=post)
        self.assertTrue(execution_status(context)['admission_review_enabled']);self.assertFalse(execution_status(context)['owner_submission_enabled'])
        with self.assertRaises(ValueError):context.submission_gateway()
        get.assert_not_called();post.assert_not_called()
        enabled=dedicated_paper_context(environ={**self.env,'PRO_TICKER_OWNER_PAPER_SUBMISSION_ENABLED':'true'},http_get=get,http_post=post)
        self.assertTrue(execution_status(enabled)['owner_submission_enabled'])
        self.assertEqual(enabled.submission_gateway().base_url,'https://paper-api.alpaca.markets')
        self.assertFalse(execution_status(enabled)['automatic_execution_enabled'])

    def test_release_reader_requires_dedicated_config_without_fallback(self):
        with patch.dict('os.environ',{'ALPACA_API_KEY':'original','ALPACA_SECRET_KEY':'original'},clear=True),self.assertRaises(ValueError):dedicated_release_reader()
        gateway=Mock();context=Mock();context.gateway.return_value=gateway
        with patch('pro_ticker_runtime.dedicated_paper_context',return_value=context):
            reader=dedicated_release_reader();self.assertEqual(reader,gateway.read_order);gateway.read_account.assert_called_once()


    def test_management_is_a_separate_default_off_gate(self):
        context=dedicated_paper_context(environ=self.env)
        self.assertFalse(execution_status(context)['owner_management_enabled'])
        enabled=dedicated_paper_context(environ={**self.env,'PRO_TICKER_OWNER_PAPER_MANAGEMENT_ENABLED':'true'})
        self.assertTrue(execution_status(enabled)['owner_management_enabled'])
        self.assertFalse(execution_status(enabled)['owner_submission_enabled'])
