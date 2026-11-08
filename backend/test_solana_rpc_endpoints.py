import os
import unittest
from unittest.mock import Mock, patch

import solana_real_execution as execution
import solana_rpc_endpoints as endpoints


class EndpointTests(unittest.TestCase):
    def test_execution_prefers_override_then_existing_provider_then_public(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(endpoints.execution_url(), endpoints.PUBLIC_HTTP)
            self.assertEqual(endpoints.execution_source(), 'public_fallback')
            os.environ['SOLANA_COPY_RPC_URL']='https://provider.test/rpc?api-key=synthetic'
            self.assertEqual(endpoints.execution_url(), os.environ['SOLANA_COPY_RPC_URL'])
            self.assertEqual(endpoints.execution_source(), 'configured_wallet_provider')
            self.assertEqual(endpoints.notifications_url(), 'wss://provider.test/rpc?api-key=synthetic')
            os.environ['SOLANA_REAL_RPC_URL']='https://execution.test/private'
            self.assertEqual(endpoints.execution_url(), 'https://execution.test/private')
            self.assertEqual(endpoints.execution_source(), 'dedicated_execution')
            # Execution override never changes the source subscriptions.
            self.assertTrue(endpoints.notifications_url().startswith('wss://provider.test/'))

    def test_invalid_config_never_silently_uses_another_endpoint(self):
        for bad in ('http://insecure.test','https://user:password@host.test','https://host.test/#secret',
                    'https://host.test:bad','https://host.test/ contains-space'):
            with self.subTest(bad=bad),patch.dict(os.environ, {'SOLANA_REAL_RPC_URL':bad}, clear=True):
                with self.assertRaisesRegex(ValueError,'configuration'):
                    endpoints.execution_url()
        with patch.dict(os.environ, {'SOLANA_COPY_WS_URL':'ws://insecure.test'},clear=True):
            with self.assertRaises(ValueError):endpoints.notifications_url()

    def test_explicit_websocket_url_and_port_are_preserved(self):
        with patch.dict(os.environ, {'SOLANA_COPY_RPC_URL':'https://host.test:8443/path?key=value'},clear=True):
            self.assertEqual(endpoints.notifications_url(),'wss://host.test:8443/path?key=value')
            os.environ['SOLANA_COPY_WS_URL']='wss://stream.test:9443/events?key=other'
            self.assertEqual(endpoints.notifications_url(),os.environ['SOLANA_COPY_WS_URL'])

    def test_send_uses_one_configured_endpoint_and_never_replays_on_failure(self):
        response=Mock(status_code=200,content=b'{}')
        response.json.return_value={'error':{'code':-32601}}
        with patch.dict(os.environ, {'SOLANA_COPY_RPC_URL':'https://provider.test/private'},clear=True),patch.object(execution.requests,'post',return_value=response) as post:
            with self.assertRaises(execution.Unavailable):
                execution.Provider().rpc('sendTransaction',['synthetic-not-signed-bytes'])
            self.assertEqual(post.call_count,1)
            self.assertEqual(post.call_args.args[0],'https://provider.test/private')
            self.assertFalse(post.call_args.kwargs['allow_redirects'])

    def test_read_only_mainnet_check_runs_on_existing_provider(self):
        response=Mock(status_code=200,content=b'{}')
        response.json.return_value={'result':execution.MAINNET_GENESIS}
        with patch.dict(os.environ, {'SOLANA_COPY_RPC_URL':'https://provider.test/private'},clear=True),patch.object(execution.requests,'post',return_value=response) as post:
            execution.Provider().network()
            self.assertEqual(post.call_args.kwargs['json']['method'],'getGenesisHash')
            self.assertEqual(post.call_args.args[0],'https://provider.test/private')
            status=execution.connection_status()
            self.assertTrue(status['mainnet_verified_recently'])
            self.assertNotIn('identity',status)
            os.environ['SOLANA_COPY_RPC_URL']='https://other.test/new-key'
            self.assertFalse(execution.connection_status()['mainnet_verified_recently'])
