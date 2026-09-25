"""Offline accounting regressions; no application startup or broker/database writes."""
import ast
from datetime import datetime, timezone
from pathlib import Path
import time
import unittest

from reporting_metrics import (
    account_equity_metrics, annotate_order_history, daily_realized_summaries,
    number, summarize_trades, timestamp_sort_key,
)


def closed(pnl, **changes):
    return dict({
        "status": "CLOSED", "pnl_complete": True, "realized_profit_loss": pnl,
        "realized_return_percent": pnl, "entry_order_id": str(pnl),
        "entry_price": 100, "entry_shares": 1,
        "exit_timestamp": "2026-09-25T15:00:00Z",
    }, **changes)


def fill(order_id, side, qty, price, timestamp, *, auto=True, legs=None):
    return {
        "order_id": order_id, "symbol": "TEST", "side": side,
        "shares": qty, "price": price, "filled_at": timestamp,
        "raw_order": {"client_order_id": "auto-entry-" + order_id if auto else order_id, "legs": legs or []},
    }


def actual_functions(**overrides):
    source = ast.parse((Path(__file__).parent / "main.py").read_text(encoding="utf-8"))
    selected = {"auto_trader_history", "auto_trader_canonical_broker_trades", "build_alpaca_live_account_snapshot", "build_alpaca_dashboard_account"}
    nodes = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in selected]
    for node in nodes:
        node.decorator_list = []
    ns = {
        "Query": lambda default=None, **kwargs: default,
        "safe_float": lambda v: float(number(v)) if number(v) is not None else None,
        "clean_symbol": lambda value: str(value or "").strip().upper(),
        "load_broker_fills": lambda **kwargs: [], "load_trade_book": lambda **kwargs: [],
        "load_all_trade_book_order_links": lambda: [],
        "load_learning_outcomes": lambda **kwargs: [], "load_trade_book_events": lambda **kwargs: [],
        "calculate_learning_summary": lambda: {},
        "fetch_alpaca_paper_positions": lambda: [],
        "calculate_holding_seconds": lambda *args: None,
        "require_app_session": lambda request: None,
        "timestamp_sort_key": timestamp_sort_key, "time": time,
        "summarize_trades": summarize_trades, "daily_realized_summaries": daily_realized_summaries,
        "account_equity_metrics": account_equity_metrics, "annotate_order_history": annotate_order_history,
    }
    ns.update(overrides)
    future = ast.parse("from __future__ import annotations").body
    exec(compile(ast.Module(body=future + nodes, type_ignores=[]), "main.py", "exec"), ns)
    return ns


class ReportingArithmeticTests(unittest.TestCase):
    def test_money_weighting_is_not_trade_count(self):
        summary = summarize_trades([closed(100), closed(-10), closed(-10), closed(-10)])
        self.assertEqual(summary["win_rate_percent"], 25)
        self.assertEqual(summary["money_win_rate_percent"], 76.92)
        self.assertEqual(summary["gross_profit"], 100)
        self.assertEqual(summary["gross_loss"], 30)
        self.assertEqual(summary["total_realized_profit_loss"], 70)

    def test_incomplete_trade_is_not_a_win_loss_or_breakeven(self):
        summary = summarize_trades([closed(10), closed(-100, status="CLOSED_INCOMPLETE", pnl_complete=False), closed(None, status="CLOSED_INCOMPLETE", pnl_complete=False)])
        self.assertEqual(summary["money_win_rate_percent"], 100)
        self.assertEqual(summary["win_rate_percent"], 100)
        self.assertEqual(summary["total_realized_profit_loss"], -90)
        self.assertEqual(summary["incomplete_trades"], 2)
        self.assertEqual(summary["breakeven"], 0)
        self.assertFalse(summary["realized_profit_loss_complete"])

    def test_zero_and_no_data_rates_are_not_fabricated(self):
        for trades in ([], [closed(0)], [closed(None, status="CLOSED_INCOMPLETE", pnl_complete=False)]):
            self.assertIsNone(summarize_trades(trades)["money_win_rate_percent"])
        self.assertIsNone(summarize_trades([closed(None, status="CLOSED_INCOMPLETE", pnl_complete=False)])["total_realized_profit_loss"])
        self.assertEqual(summarize_trades([closed(-10)])["money_win_rate_percent"], 0)
        self.assertEqual(summarize_trades([closed(0)])["breakeven"], 1)

    def test_nonfinite_and_boolean_are_unknown(self):
        for value in (True, float("nan"), float("inf"), "", "bad"):
            self.assertIsNone(number(value))
            self.assertEqual(summarize_trades([closed(value)])["complete_trades"], 0)

    def test_cash_equity_missing_is_not_zero_profit(self):
        for account in ({"cash": 100}, {"equity": 100}, {"equity": 100, "last_equity": 0}):
            self.assertIsNone(account_equity_metrics(account)["daily_equity_change"])
        summary = account_equity_metrics({"equity": "96189.06", "last_equity": "96433.63"})
        self.assertEqual(summary["daily_equity_change"], -244.57)

    def test_coverage_flags_survive(self):
        for ledger in ({"ledger_limit_reached": True}, {"unmatched_sell_fills": 2}):
            self.assertFalse(summarize_trades([closed(10)], ledger_summary=ledger)["realized_profit_loss_complete"])

    def test_partial_exits_use_each_eastern_day_and_decimal_shares(self):
        trade = closed(0, status="PARTIAL", entry_price="10.01", exit_allocations=[
            {"timestamp": "2026-09-26T00:30:00Z", "shares": "0.1", "price": "10.11", "exit_order_id": "a"},
            {"timestamp": "2026-09-28T15:00:00Z", "shares": "0.2", "price": "9.96", "exit_order_id": "b"},
        ])
        days = daily_realized_summaries([trade])
        self.assertEqual(days["2026-09-25"]["known_realized_profit_loss"], 0.01)
        self.assertEqual(days["2026-09-28"]["known_realized_profit_loss"], -0.01)
        self.assertNotIn("2026-09-26", days)

    def test_invalid_dates_are_not_assigned_to_entry_day(self):
        trade = closed(10, exit_allocations=[{"timestamp": None, "price": 110, "shares": 1}])
        self.assertEqual(daily_realized_summaries([trade]), {})

    def test_partial_cost_basis_never_shown_as_full_sell_profit(self):
        canonical = [closed(10, exit_allocations=[{"exit_order_id": "sell", "price": 110, "shares": 1}])]
        incomplete = annotate_order_history([{"id": "sell", "side": "SELL", "shares": 2}], canonical)[0]
        self.assertIsNone(incomplete["profit_loss_dollars"])
        complete = annotate_order_history([{"id": "sell", "side": "SELL", "shares": 1}], canonical)[0]
        self.assertEqual(complete["profit_loss_dollars"], 10)


class ActualAccountingFlowTests(unittest.TestCase):
    def test_journal_summary_does_not_change_with_row_limit(self):
        ns = actual_functions()
        rows = [closed(100), closed(-20), closed(None, status="CLOSED_INCOMPLETE", pnl_complete=False)]
        ns["auto_trader_canonical_broker_trades"] = lambda **kwargs: {"trades": rows, "summary": {}}
        one = ns["auto_trader_history"](limit=1)
        all_rows = ns["auto_trader_history"](limit=5000)
        self.assertEqual(one["summary"], all_rows["summary"])
        self.assertEqual(one["count"], 1)
        self.assertEqual(one["total_count"], 3)
        self.assertTrue(one["has_more"])
        self.assertEqual(one["summary"]["money_win_rate_percent"], 83.33)

    def test_missing_exit_is_unknown_not_zero_breakeven(self):
        ns = actual_functions(load_broker_fills=lambda **kwargs: [fill("buy", "BUY", 1, 10, "2026-09-25T10:00:00Z")])
        payload = ns["auto_trader_history"](limit=100)
        self.assertEqual(payload["trades"][0]["status"], "CLOSED_INCOMPLETE")
        self.assertIsNone(payload["trades"][0]["realized_profit_loss"])
        self.assertIsNone(payload["summary"]["win_rate_percent"])

    def test_offsets_are_sorted_chronologically(self):
        fills = [fill("buy", "BUY", 1, 10, "2026-09-25T14:00:00Z"), fill("sell", "SELL", 1, 12, "2026-09-25T10:01:00-04:00")]
        ns = actual_functions(load_broker_fills=lambda **kwargs: fills)
        trade = ns["auto_trader_canonical_broker_trades"]()["trades"][0]
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(trade["realized_profit_loss"], 2)

    def test_partial_exit_is_in_daily_pnl_before_trade_closes(self):
        fills = [fill("buy", "BUY", 2, 10, "2026-09-25T14:00:00Z"), fill("sell", "SELL", 1, 12, "2026-09-25T15:00:00Z")]
        ns = actual_functions(load_broker_fills=lambda **kwargs: fills, fetch_alpaca_paper_positions=lambda: [{"symbol": "TEST", "qty": 1}])
        payload = ns["auto_trader_history"](limit=100)
        self.assertEqual(payload["trades"], [])
        self.assertEqual(payload["daily_realized"]["2026-09-25"]["known_realized_profit_loss"], 2)

    def test_broker_link_still_takes_precedence_over_fifo(self):
        fills = [
            fill("old", "BUY", 1, 10, "2026-09-25T14:00:00Z"),
            fill("new", "BUY", 1, 20, "2026-09-25T14:01:00Z", legs=[{"id": "sell", "side": "sell"}]),
            fill("sell", "SELL", 1, 22, "2026-09-25T14:02:00Z"),
        ]
        ns = actual_functions(load_broker_fills=lambda **kwargs: fills, fetch_alpaca_paper_positions=lambda: [{"symbol": "TEST", "qty": 1}])
        trades = ns["auto_trader_canonical_broker_trades"]()["trades"]
        self.assertEqual(next(t for t in trades if t["entry_order_id"] == "new")["realized_profit_loss"], 2)

    def test_dashboard_uses_same_journal_summary(self):
        ns = actual_functions(
            fetch_alpaca_paper_account=lambda: {"cash": 100, "equity": 120, "last_equity": 110},
            normalize_alpaca_position=lambda p: p,
            fetch_alpaca_paper_trade_history=lambda **kwargs: [],
        )
        rows = [closed(100), closed(-20)]
        ns["auto_trader_canonical_broker_trades"] = lambda **kwargs: {"trades": rows, "summary": {}}
        account = ns["build_alpaca_dashboard_account"]()
        journal = ns["auto_trader_history"](limit=1)
        self.assertEqual(account["realized_profit_loss"], journal["summary"]["total_realized_profit_loss"])
        self.assertEqual(account["money_win_rate_percent"], journal["summary"]["money_win_rate_percent"])
        self.assertEqual(account["daily_equity_change"], 10)


if __name__ == "__main__":
    unittest.main()
