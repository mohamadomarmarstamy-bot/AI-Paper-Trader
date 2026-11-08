import unittest
from unittest.mock import patch
from email.message import Message
from types import SimpleNamespace

import requests
import solana_http as http


class PoolTests(unittest.TestCase):
    def response(self,request):
        value=requests.Response();value.status_code=200;value._content=b'{}'
        value.request=request;value.url=request.url
        headers=Message();headers.add_header('Set-Cookie','private_session=synthetic; Path=/')
        value.raw=SimpleNamespace(_original_response=SimpleNamespace(msg=headers))
        return value

    def test_pool_survives_requests_without_leaking_cookies_or_auth_headers(self):
        observed=[]
        def send(request,**kwargs):
            observed.append(request)
            return self.response(request)
        with patch.object(http._adapter,'send',side_effect=send),patch.object(http._adapter,'close') as close:
            first=http.post('https://rpc.test/read',json={'method':'getBalance'},headers={'x-api-key':'synthetic'},timeout=8,allow_redirects=False)
            second=http.post('https://rpc.test/next',json={'method':'getBalance'},timeout=8,allow_redirects=False)
            self.assertEqual(first.json(),{})
            self.assertEqual(second.json(),{})
            self.assertNotIn('Cookie',observed[1].headers)
            self.assertNotIn('x-api-key',observed[1].headers)
            close.assert_not_called()

    def test_transport_failure_is_single_attempt_and_does_not_close_shared_pool(self):
        with patch.object(http._adapter,'send',side_effect=requests.Timeout('synthetic')) as send,patch.object(http._adapter,'close') as close:
            with self.assertRaises(requests.Timeout):
                http.post('https://rpc.test/send',json={'method':'sendTransaction'},timeout=8,allow_redirects=False)
            self.assertEqual(send.call_count,1)
            self.assertEqual(http._adapter.max_retries.total,0)
            self.assertFalse(http._adapter.max_retries.read)
            close.assert_not_called()

    def test_non_https_requests_are_rejected(self):
        with self.assertRaises(ValueError):http.get('http://rpc.test')
