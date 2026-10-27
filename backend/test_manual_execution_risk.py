"""Execute real risk validator with an offline broker: no orders or credentials."""
import ast
import math
from pathlib import Path
import unittest
from unittest.mock import Mock


def validator(**overrides):
    source=ast.parse(Path(__file__).with_name('main.py').read_text(encoding='utf-8-sig'))
    node=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='validate_alpaca_paper_order_risk')
    ns=dict(math=math,clean_symbol=lambda s:s.upper(),safe_float=lambda v:float(v) if v is not None else None,
        clean_error_message=str, BrokerRateLimited=type('BrokerRateLimited',(Exception,),{}),
        alpaca_paper_request=Mock(return_value={'status':'active','tradable':True}),
        fetch_alpaca_paper_account=Mock(return_value={'equity':'100000','buying_power':'50000'}),
        fetch_alpaca_paper_positions=Mock(return_value=[{'symbol':'BRAI','qty':'100','market_value':'400'}]),
        fetch_alpaca_market_clock=Mock(return_value={'is_open':True}),
        fetch_alpaca_open_orders_for_symbol=Mock(return_value=[]),
        fetch_alpaca_risk_quote=Mock(return_value={'midpoint':4,'spread_percent':17.17}),
        get_alpaca_position_for_symbol=lambda s,p:next((x for x in p if x['symbol']==s),None),
        get_auto_trader_setting=lambda k:{'max_order_size_percent':2.5,'max_position_size_percent':5,'max_open_positions':50}[k],
        RISK_REQUIRE_REGULAR_MARKET_OPEN=True,RISK_MAX_SPREAD_PERCENT=2,RISK_BUYING_POWER_BUFFER_DOLLARS=25,
        RISK_BLOCK_SHORT_SELLING=True)
    ns.update(overrides)
    exec(compile(ast.Module(body=ast.parse('from __future__ import annotations').body+[node],type_ignores=[]),'main.py','exec'),ns)
    return ns['validate_alpaca_paper_order_risk'],ns


class ManualExecutionRiskTests(unittest.TestCase):
    def test_long_sell_ignores_spread_and_does_not_require_any_quote(self):
        fn,ns=validator(fetch_alpaca_risk_quote=Mock(side_effect=RuntimeError('No quote')))
        result=fn(symbol='BRAI',shares=100,side='sell')
        self.assertTrue(result['approved']);self.assertFalse(result['spread_check_applied'])
        ns['fetch_alpaca_risk_quote'].assert_not_called();ns['alpaca_paper_request'].assert_not_called()

    def test_sell_cannot_oversell_or_open_a_short(self):
        fn,ns=validator()
        self.assertFalse(fn(symbol='BRAI',shares=101,side='sell')['approved'])
        self.assertFalse(fn(symbol='UNKNOWN',shares=1,side='sell')['approved'])
        ns['fetch_alpaca_risk_quote'].assert_not_called()

    def test_conflicting_order_still_blocks_a_sell(self):
        fn,ns=validator(fetch_alpaca_open_orders_for_symbol=Mock(return_value=[{'id':'foreign','side':'buy'}]))
        self.assertFalse(fn(symbol='BRAI',shares=100,side='sell')['approved'])

    def test_wide_spread_does_not_veto_automatic_or_owner_selected_buys(self):
        fn,ns=validator()
        self.assertTrue(fn(symbol='BRAI',shares=100,side='buy')['approved'])
        self.assertTrue(fn(symbol='BRAI',shares=100,side='buy',owner_manual_entry=True)['approved'])
        self.assertTrue(all(c.args[0]=='GET' for c in ns['alpaca_paper_request'].call_args_list))

    def test_owner_selection_keeps_buying_power_and_asset_eligibility(self):
        fn,ns=validator(fetch_alpaca_paper_account=Mock(return_value={'equity':'100000','buying_power':'0'}))
        self.assertFalse(fn(symbol='BRAI',shares=100,side='buy',owner_manual_entry=True)['approved'])
        ns['alpaca_paper_request'].return_value={'status':'inactive','tradable':False}
        self.assertFalse(fn(symbol='BRAI',shares=100,side='buy',owner_manual_entry=True)['approved'])
