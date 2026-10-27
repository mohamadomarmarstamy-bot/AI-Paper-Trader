"""Execute production adapter bodies with a synthetic broker; no network/orders."""
import ast
import os
from pathlib import Path
import re
import unittest
from unittest.mock import Mock, patch
import uuid

SOURCE=ast.parse(Path(__file__).with_name('main.py').read_text(encoding='utf-8-sig'))


def adapter(name, **overrides):
    ns={'os':os,'re':re,'uuid':uuid,'AUTO_TRADER_STRATEGY_VERSION':'original',
        'clean_symbol':lambda s:s.upper(),'safe_float':lambda s:float(s) if s is not None else None,
        'clean_error_message':str,
        'validate_alpaca_paper_order_risk':Mock(return_value={'approved':True}),
        'get_auto_trader_setting':lambda key:5 if key=='stop_loss_percent' else 10,
        'automatic_entries_paused':lambda:True,
        'alpaca_paper_request':Mock(return_value={'id':str(uuid.uuid4())}),
        'wait_for_alpaca_paper_order':lambda order:order,
        'normalize_alpaca_paper_order':lambda *a,**k:{'success':True,'trade':{'status':'filled'}},
        'fetch_alpaca_paper_positions':Mock(return_value=[{'symbol':'TEST','qty':'43'}]),
        'fetch_alpaca_open_orders_for_symbol':Mock(return_value=[{'id':'stop'}]),
        'cancel_alpaca_open_orders_for_symbol':Mock(return_value=['stop']),
        'submit_alpaca_paper_market_order':Mock(return_value={'success':True,'trade':{'status':'filled'}}),
        'load_trade_book_entry_by_order_id':Mock(side_effect=AssertionError('Legacy journal must not post manual fills'))}
    ns.update(overrides)
    node=next(n for n in SOURCE.body if isinstance(n,ast.FunctionDef) and n.name==name)
    module=ast.Module(body=ast.parse('from __future__ import annotations').body+[node],type_ignores=[])
    exec(compile(module,'main.py','exec'),ns)
    return ns[name],ns


class ManualScannerAdapterTests(unittest.TestCase):
    def test_manual_bracket_uses_claim_identity_and_keeps_native_protective_legs(self):
        fn,ns=adapter('submit_alpaca_auto_bracket_buy')
        with patch.dict(os.environ,{'PRO_TICKER_MANUAL_SCANNER_ENABLED':'true'}),patch('pro_ticker_manual_scanner.assert_dispatch',return_value='ptm-'+'a'*32) as claim:
            result=fn(symbol='test',shares=43,reference_price=45.5,scanner_result={},manual_request_id='claim')
        self.assertTrue(result['success'])
        self.assertTrue(ns['validate_alpaca_paper_order_risk'].call_args.kwargs['owner_manual_entry'])
        self.assertEqual(claim.call_count,2)
        body=ns['alpaca_paper_request'].call_args.kwargs['json_body']
        self.assertEqual(body['client_order_id'],'ptm-'+'a'*32)
        self.assertEqual(body['qty'],'43')
        self.assertEqual(body['order_class'],'bracket')
        self.assertEqual(body['stop_loss'],{'stop_price':'43.23'})
        self.assertEqual(body['take_profit'],{'limit_price':'50.05'})
        self.assertEqual(ns['alpaca_paper_request'].call_count,1)

    def test_manual_penny_bracket_keeps_four_decimal_prices(self):
        fn,ns=adapter('submit_alpaca_auto_bracket_buy')
        with patch.dict(os.environ,{'PRO_TICKER_MANUAL_SCANNER_ENABLED':'true'}),patch('pro_ticker_manual_scanner.assert_dispatch',return_value='ptm-'+'a'*32):
            fn(symbol='test',shares=4000,reference_price=.5001,scanner_result={},manual_request_id='claim')
        body=ns['alpaca_paper_request'].call_args.kwargs['json_body']
        self.assertEqual(body['stop_loss']['stop_price'],'0.4751')
        self.assertEqual(body['take_profit']['limit_price'],'0.5501')

    def test_old_automatic_buy_remains_paused(self):
        fn,ns=adapter('submit_alpaca_auto_bracket_buy')
        result=fn(symbol='test',shares=43,reference_price=45.5,scanner_result={})
        self.assertTrue(result['entries_paused'])
        self.assertFalse(ns['validate_alpaca_paper_order_risk'].call_args.kwargs['owner_manual_entry'])
        ns['alpaca_paper_request'].assert_not_called()

    def test_disabled_manual_mode_cannot_bypass_entry_pause(self):
        fn,ns=adapter('submit_alpaca_auto_bracket_buy')
        with patch.dict(os.environ,{'PRO_TICKER_MANUAL_SCANNER_ENABLED':'false'}),self.assertRaises(ValueError):
            fn(symbol='test',shares=43,reference_price=45.5,scanner_result={},manual_request_id='claim')
        ns['alpaca_paper_request'].assert_not_called()

    def test_scoped_close_does_not_upsize_mixed_holdings_or_cancel_foreign_orders(self):
        fn,ns=adapter('close_alpaca_paper_position_manually')
        with patch.dict(os.environ,{'PRO_TICKER_MANUAL_SCANNER_ENABLED':'true'}):
            result=fn(symbol='TEST',shares=42,expected_total_shares=42,client_order_id='ptm-exit-'+'a'*32,owned_order_ids={'stop'},expected_entry_order_id='entry')
        self.assertFalse(result['success'])
        ns['cancel_alpaca_open_orders_for_symbol'].assert_not_called()
        ns['submit_alpaca_paper_market_order'].assert_not_called()

    def test_scoped_close_forwards_stable_identity_and_has_single_journal_writer(self):
        fn,ns=adapter('close_alpaca_paper_position_manually')
        client='ptm-exit-'+'a'*32
        with patch.dict(os.environ,{'PRO_TICKER_MANUAL_SCANNER_ENABLED':'true'}):
            result=fn(symbol='TEST',shares=43,expected_total_shares=43,client_order_id=client,owned_order_ids={'stop'},expected_entry_order_id='entry')
        self.assertTrue(result['success'])
        ns['submit_alpaca_paper_market_order'].assert_called_once_with(symbol='TEST',shares=43,side='sell',client_order_id=client)
        ns['load_trade_book_entry_by_order_id'].assert_not_called()

    def test_unrelated_orders_are_rejected_before_any_cancellation(self):
        fn,ns=adapter('cancel_alpaca_open_orders_for_symbol',fetch_alpaca_open_orders_for_symbol=Mock(return_value=[{'id':'foreign'}]))
        with self.assertRaises(ValueError):fn('TEST',allowed_order_ids={'owned'})
        ns['alpaca_paper_request'].assert_not_called()
