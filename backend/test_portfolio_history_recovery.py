import ast
from pathlib import Path
import unittest
from unittest.mock import Mock
from fastapi import HTTPException
from fastapi.responses import JSONResponse


def adapter(name,**overrides):
    source=ast.parse(Path(__file__).with_name('main.py').read_text())
    node=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name==name)
    ns={'HTTPException':HTTPException,'JSONResponse':JSONResponse,'require_app_session':Mock(),'clean_error_message':str,
        'safe_float':lambda x:float(x) if x is not None else None,**overrides}
    # Decorators use the app router; this isolates the real route body.
    node.decorator_list=[]
    exec(compile(ast.Module(body=ast.parse('from __future__ import annotations').body+[node],type_ignores=[]),'main.py','exec'),ns)
    return ns[name],ns

class PortfolioRecoveryTests(unittest.TestCase):
    def test_failure_returns_retryable_503_instead_of_empty_history(self):
        cache=Mock();cache.get.side_effect=RuntimeError('temporary');cache.get_last_successful.return_value=None
        fn,ns=adapter('portfolio_history',_portfolio_history_cache=cache,fetch_alpaca_portfolio_history=Mock())
        with self.assertRaises(HTTPException) as raised:fn(object())
        self.assertEqual(raised.exception.status_code,503);self.assertEqual(raised.exception.headers['Retry-After'],'15')
        ns['require_app_session'].assert_called_once()
    def test_last_successful_broker_history_is_labeled_stale(self):
        cache=Mock();cache.get.side_effect=RuntimeError('temporary');cache.get_last_successful.return_value=[{'time':100,'value':100000}]
        fn,_=adapter('portfolio_history',_portfolio_history_cache=cache,fetch_alpaca_portfolio_history=Mock())
        result=fn(object());self.assertEqual(result.status_code,200);self.assertEqual(result.headers['X-Portfolio-History-Status'],'stale')
        self.assertIn(b'100000',result.body)
    def test_malformed_provider_data_is_not_cached_as_empty_success(self):
        fn,_=adapter('fetch_alpaca_portfolio_history',alpaca_paper_request=Mock(return_value={'equity':None}))
        with self.assertRaises(ValueError):fn()

if __name__=='__main__':unittest.main()
