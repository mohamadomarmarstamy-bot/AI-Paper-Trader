"""Shared HTTPS connection pools with isolated per-request sessions and no retries.

    Sessions aren't shared between threads: headers, cookies and authentication
    never persist across calls. Only the thread-safe urllib3 pool is reused.
"""
import requests
from requests.adapters import HTTPAdapter

_adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16, pool_block=True, max_retries=0)


class _Session(requests.Session):
    def __init__(self):
        super().__init__()
        self.mount('https://',_adapter)

    def close(self):
        # Session.close normally closes every mounted adapter. The shared pool
        # must survive this one request, including failed/uncertain submissions.
        self.adapters.pop('https://',None)
        super().close()


def request(method,url,**kwargs):
    if not isinstance(url,str) or not url.startswith('https://'):
        raise ValueError('HTTPS is required for Solana provider requests.')
    with _Session() as session:
        return session.request(method,url,**kwargs)


def get(url,**kwargs):
    return request('GET',url,**kwargs)


def post(url,**kwargs):
    return request('POST',url,**kwargs)
