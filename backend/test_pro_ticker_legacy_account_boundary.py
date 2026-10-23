"""Exercise legacy main route bodies without starting the original trader."""
import ast
import asyncio
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from fastapi import Request,HTTPException,Query
from pro_ticker_paper_gateway import AlpacaPaperEvidenceGateway,PaperGatewayUnavailable

ORDER='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
ACCOUNT='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
ROUTES={'pro_ticker_paper_entry_fill_reconcile','pro_ticker_paper_fill_posting_preview',
        'pro_ticker_paper_exit_fill_preview','pro_ticker_paper_combined_audit',
        'pro_ticker_paper_position_audit','pro_ticker_paper_reconciliation_preview',
        'pro_ticker_paper_preview'}


def routes():
    tree=ast.parse(Path(__file__).with_name('main.py').read_text())
    names=ROUTES|{'make_pro_ticker_evidence_gateway','require_pro_ticker_same_origin'}
    nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    assert {n.name for n in nodes}==names
    for node in nodes:node.decorator_list=[]
    shared={name:Mock(side_effect=AssertionError('Original broker must not be used')) for name in
            ('alpaca_paper_request','alpaca_market_data_request','fetch_alpaca_paper_positions','fetch_alpaca_paper_orders','fetch_alpaca_market_clock')}
    gateway=Mock();gateway.read_account.return_value={'id':ACCOUNT};gateway.read_positions.return_value=[];gateway.read_orders.return_value=[]
    gateway.read_snapshot.return_value={'account':{'id':ACCOUNT,'buying_power':'100'},'positions':[],
        'orders':[],'clock':{'is_open':True},'quote':{'p':10,'t':datetime.now(timezone.utc).isoformat()}}
    context=SimpleNamespace(expected_account_id=ACCOUNT,validate=Mock(),gateway=Mock(return_value=gateway))
    ns={'Request':Request,'HTTPException':HTTPException,'Query':Query,'Any':object,
        'require_app_session':Mock(),'dedicated_paper_context':Mock(return_value=context),**shared}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'main.py','exec'),ns)
    return ns,gateway,shared


class LegacyAccountBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.ns,self.gateway,self.shared=routes()
        self.request=SimpleNamespace(headers={'origin':'https://staging.test'},base_url='https://staging.test/')

    def test_previews_use_verified_dedicated_account_without_original_helpers(self):
        with patch('pro_ticker_budget_ledger.list_pro_ticker_reservations',return_value={'reservations':[]}), \
             patch('pro_ticker_position_ledger.list_pro_ticker_strategy_positions',return_value={'positions':[]}):
            for name in ('pro_ticker_paper_fill_posting_preview','pro_ticker_paper_combined_audit',
                         'pro_ticker_paper_position_audit','pro_ticker_paper_reconciliation_preview'):
                with self.subTest(route=name):
                    before=self.gateway.read_account.call_count
                    kwargs={'limit':10} if name.endswith(('posting_preview','reconciliation_preview')) else {}
                    self.ns[name](self.request,**kwargs)
                    self.assertGreater(self.gateway.read_account.call_count,before)
        with patch('pro_ticker_budget_ledger.get_pro_ticker_budget',return_value={'committed':0}):
            self.request.json=Mock(return_value=None)
            async def payload():return {'symbol':'AAPL','proposed_notional':10}
            self.request.json=payload
            result=asyncio.run(self.ns['pro_ticker_paper_preview'](self.request))
            self.assertFalse(result['allowed']);self.gateway.read_snapshot.assert_called_once_with('AAPL')
        for helper in self.shared.values():helper.assert_not_called()

    def test_entry_reconcile_requires_origin_and_dedicated_identity_before_posting(self):
        with patch('pro_ticker_budget_ledger.get_pro_ticker_reservation',return_value={'broker_order_id':ORDER}), \
             patch('pro_ticker_verified_fill_bridge.account_linked_paper_entry_snapshot',return_value={'posted':True}) as post:
            self.gateway.read_order.return_value={'id':ORDER}
            result=self.ns['pro_ticker_paper_entry_fill_reconcile'](self.request,reservation_id=ORDER)
            self.assertTrue(result['posted']);post.assert_called_once()
            post.reset_mock();self.gateway.read_order.reset_mock()
            self.gateway.read_account.return_value={'id':'other'}
            result=self.ns['pro_ticker_paper_entry_fill_reconcile'](self.request,reservation_id=ORDER)
            self.assertFalse(result['reconciled']);post.assert_not_called();self.gateway.read_order.assert_not_called()
            self.request.headers={'origin':'https://other.invalid'}
            with self.assertRaises(HTTPException) as error:self.ns['pro_ticker_paper_entry_fill_reconcile'](self.request,reservation_id=ORDER)
            self.assertEqual(error.exception.status_code,403)
        for helper in self.shared.values():helper.assert_not_called()

    def test_missing_dedicated_configuration_never_falls_back(self):
        self.ns['dedicated_paper_context'].return_value=None
        with self.assertRaises(ValueError):self.ns['make_pro_ticker_evidence_gateway']()
        self.gateway.read_account.assert_not_called()
        for helper in self.shared.values():helper.assert_not_called()

    def test_exit_preview_uses_account_verified_exact_order(self):
        with patch('pro_ticker_exit_links.get_pro_ticker_exit_link',return_value={'reservation_id':ORDER}), \
             patch('pro_ticker_budget_ledger.get_pro_ticker_reservation',return_value={}), \
             patch('pro_ticker_verified_exit_review.review_verified_paper_exit_link',return_value={'verified':True}):
            self.assertTrue(self.ns['pro_ticker_paper_exit_fill_preview'](self.request,order_id=ORDER)['verified'])
        self.gateway.read_account.assert_called_once();self.gateway.read_order.assert_called_once_with(ORDER)
        for helper in self.shared.values():helper.assert_not_called()

    def test_bounded_history_read_rejects_full_or_malformed_pages(self):
        get=Mock();gateway=AlpacaPaperEvidenceGateway(account_id=ACCOUNT,key_id='synthetic',secret_key='synthetic',http_get=get)
        for payload in ([{}]*500,{},None):
            get.return_value=Mock(status_code=200,json=lambda:payload)
            with self.assertRaises(PaperGatewayUnavailable):gateway.read_orders()
        get.return_value=Mock(status_code=200,json=lambda:[])
        self.assertEqual(gateway.read_orders(),[])
        self.assertEqual(get.call_args.args[0],'https://paper-api.alpaca.markets/v2/orders?status=all&limit=500&nested=true')
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
