"""Offline regressions for accounting allocation and scanner measurement time."""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from reporting_metrics import number, summarize_trades
from test_reporting_metrics import actual_functions, closed, fill


class AccountingAccuracyTests(unittest.TestCase):
    def test_manual_buy_cost_basis_is_not_charged_to_bot(self):
        rows = [fill('manual', 'BUY', 1, 5, '2026-09-25T10:00:00Z', auto=False),
                fill('bot', 'BUY', 1, 10, '2026-09-25T10:01:00Z'),
                fill('manual-exit', 'SELL', 1, 6, '2026-09-25T10:02:00Z', auto=False)]
        ns = actual_functions(load_broker_fills=lambda **kw: rows,
                              fetch_alpaca_paper_positions=lambda: [{'symbol': 'TEST', 'qty': 1}])
        payload = ns['auto_trader_canonical_broker_trades']()
        self.assertEqual(len(payload['trades']), 1)
        self.assertEqual(payload['trades'][0]['status'], 'OPEN')
        self.assertIsNone(payload['trades'][0]['realized_profit_loss'])
        self.assertEqual(payload['summary']['unmatched_sell_fills'], 0)

    def test_manual_remaining_position_does_not_hide_missing_bot_exit(self):
        rows = [fill('bot', 'BUY', 1, 10, '2026-09-25T10:00:00Z'),
                fill('manual', 'BUY', 1, 5, '2026-09-25T10:01:00Z', auto=False)]
        ns = actual_functions(load_broker_fills=lambda **kw: rows,
                              fetch_alpaca_paper_positions=lambda: [{'symbol': 'TEST', 'qty': 1}])
        trade = ns['auto_trader_canonical_broker_trades']()['trades'][0]
        self.assertEqual(trade['status'], 'CLOSED_INCOMPLETE')
        self.assertFalse(trade['pnl_complete'])

    def test_open_lot_allocation_orders_timezone_offsets_correctly(self):
        rows = [fill('older', 'BUY', 1, 10, '2026-09-25T14:00:00Z'),
                fill('newer', 'BUY', 1, 20, '2026-09-25T10:01:00-04:00')]
        ns = actual_functions(load_broker_fills=lambda **kw: rows,
                              fetch_alpaca_paper_positions=lambda: [{'symbol': 'TEST', 'qty': 1}])
        trades = {t['entry_order_id']: t for t in ns['auto_trader_canonical_broker_trades']()['trades']}
        self.assertEqual(trades['newer']['status'], 'OPEN')
        self.assertEqual(trades['older']['status'], 'CLOSED_INCOMPLETE')

    def test_realized_total_includes_partial_exit_but_win_rate_does_not(self):
        summary = summarize_trades([closed(10), closed(-4, status='PARTIAL')])
        self.assertEqual(summary['total_realized_profit_loss'], 6)
        self.assertEqual(summary['closed_trade_profit_loss'], 10)
        self.assertEqual(summary['partial_trade_realized_profit_loss'], -4)
        self.assertEqual(summary['win_rate_percent'], 100)
        self.assertEqual(summary['rate_sample_trades'], 1)

    def test_incomplete_partial_position_marks_total_incomplete(self):
        summary = summarize_trades([closed(10, status='PARTIAL', pnl_complete=False)])
        self.assertFalse(summary['realized_profit_loss_complete'])

    def test_unmatched_only_ledger_does_not_claim_zero_profit(self):
        summary = summarize_trades([], ledger_summary={'unmatched_sell_fills': 1})
        self.assertIsNone(summary['total_realized_profit_loss'])


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 25, 15, 0, tzinfo=timezone.utc)


def research_functions(**overrides):
    source = ast.parse((Path(__file__).parent / 'main.py').read_text(encoding='utf-8'))
    names = {'fetch_forward_research_bar', 'evaluate_scanner_forward_outcomes'}
    nodes = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {'datetime': FixedDateTime, 'timedelta': timedelta, 'timezone': timezone,
          'clean_symbol': lambda s: str(s).strip().upper(),
          'safe_float': lambda n: float(number(n)) if number(n) is not None else None,
          'clean_error_message': str}
    ns.update(overrides)
    exec(compile(ast.Module(body=ast.parse('from __future__ import annotations').body + nodes,
                            type_ignores=[]), 'main.py', 'exec'), ns)
    return ns


class ScannerOutcomeAccuracyTests(unittest.TestCase):
    def test_close_timestamp_is_one_minute_after_bar_start(self):
        ns = research_functions(alpaca_market_data_request=lambda *a, **kw:
                                {'bars': [{'t': '2026-09-25T14:14:00Z', 'c': 10.1234}]})
        bar = ns['fetch_forward_research_bar']('TEST', '2026-09-25T14:15:00Z')
        self.assertEqual(bar['bar_closed_at'], '2026-09-25T14:15:00+00:00')
        self.assertEqual(bar['price'], 10.1234)

    def test_bar_outside_search_window_is_not_accepted(self):
        ns = research_functions(alpaca_market_data_request=lambda *a, **kw:
                                {'bars': [{'t': '2026-09-25T14:25:00Z', 'c': 10}]})
        self.assertIsNone(ns['fetch_forward_research_bar']('TEST', '2026-09-25T14:15:00Z'))

    def test_unfinished_bar_is_not_accepted(self):
        ns = research_functions(alpaca_market_data_request=lambda *a, **kw:
                                {'bars': [{'t': '2026-09-25T15:00:00Z', 'c': 10}]})
        self.assertIsNone(ns['fetch_forward_research_bar']('TEST', '2026-09-25T15:00:30Z'))

    def test_measurement_waits_for_window_and_saves_close_time(self):
        due = []
        saved = []
        def load(**kw):
            due.append(kw['due_at'])
            return [{'id': 1, 'symbol': 'TEST', 'observed_at': '2026-09-25T14:30:00Z', 'reference_price': 10}]
        ns = research_functions(load_due_scanner_forward_observations=load,
                                save_scanner_forward_outcome=lambda **kw: saved.append(kw),
                                alpaca_market_data_request=lambda *a, **kw:
                                {'bars': [{'t': '2026-09-25T14:44:00Z', 'c': 11}]})
        result = ns['evaluate_scanner_forward_outcomes']()
        self.assertEqual(result['saved'], 1)
        self.assertEqual(saved[0]['evaluated_at'], '2026-09-25T14:45:00+00:00')
        self.assertTrue(all(t == '2026-09-25T14:54:00+00:00' for t in due))


if __name__ == '__main__':
    unittest.main()
