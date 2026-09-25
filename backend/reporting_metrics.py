"""Read-only reporting arithmetic. Never used to size or submit orders."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo


def number(value):
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def rounded(value, places=2):
    if value is None:
        return None
    return float(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def parsed_timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def eastern_day(value):
    timestamp = parsed_timestamp(value)
    return timestamp.astimezone(ZoneInfo("America/New_York")).date().isoformat() if timestamp else None


def timestamp_sort_key(value):
    timestamp = parsed_timestamp(value)
    return timestamp.timestamp() if timestamp else float("-inf")


def account_equity_metrics(account):
    """Broker equity change since prior close, not closed-trade profit.

    Cash flows/fees may affect this change. Missing equity/baseline is unknown,
    never replaced with cash or current equity to fabricate a zero return.
    """
    equity = number(account.get("equity"))
    previous = number(account.get("last_equity"))
    change = equity - previous if equity is not None and previous is not None and previous > 0 else None
    percent = change / previous * 100 if change is not None else None
    return {
        "equity": rounded(equity),
        "portfolio_value": rounded(equity), "total_value": rounded(equity),
        "previous_close_equity": rounded(previous),
        "starting_balance": rounded(previous), "starting_cash": rounded(previous),
        "daily_equity_change": rounded(change),
        "daily_equity_change_percent": rounded(percent, 4),
        "profit_loss": rounded(change), "total_profit_loss": rounded(change),
        "profit_loss_percent": rounded(percent, 4),
        "total_return_percent": rounded(percent, 4),
        "daily_pl_available": change is not None,
        "daily_pl_basis": "current_equity_minus_previous_market_close_equity",
        "daily_pl_note": "Account equity change since prior market close; includes open-position movement and may include cash flows and fees. Not journal realized P/L.",
    }


def summarize_trades(trades, *, ledger_summary=None):
    """Summarize every supplied canonical trade, independent of UI row limits.

    Rates and gross gains/losses use only fully closed, complete trades.
    Total realized P/L includes every matched exit, including partial exits.
    Closed-trade and partial-trade subtotals are exposed separately.
    """
    ledger = ledger_summary or {}
    closed = [t for t in trades if t.get("status") in {"CLOSED", "CLOSED_INCOMPLETE"}]
    eligible = [t for t in closed if t.get("status") == "CLOSED" and t.get("pnl_complete") is True and number(t.get("realized_profit_loss")) is not None]
    values = [number(t["realized_profit_loss"]) for t in eligible]
    known = [number(t.get("realized_profit_loss")) for t in closed]
    known = [v for v in known if v is not None]
    all_known = [number(t.get("realized_profit_loss")) for t in trades]
    all_known = [v for v in all_known if v is not None]
    gains = sum((v for v in values if v > 0), Decimal(0))
    losses = -sum((v for v in values if v < 0), Decimal(0))
    wins = sum(v > 0 for v in values)
    loss_count = sum(v < 0 for v in values)
    returns = [number(t.get("realized_return_percent")) for t in eligible]
    returns = [v for v in returns if v is not None]
    incomplete = len(closed) - len(eligible)
    unmatched = int(ledger.get("unmatched_sell_fills") or 0)
    truncated = bool(ledger.get("ledger_limit_reached"))
    missing_basis = any(t.get("pnl_complete") is not True for t in trades)
    complete = not missing_basis and incomplete == 0 and unmatched == 0 and not truncated
    return {
        "completed_trades": len(closed),
        "complete_trades": len(eligible), "incomplete_trades": incomplete,
        "wins": wins, "losses": loss_count, "breakeven": sum(v == 0 for v in values),
        "win_rate_percent": rounded(Decimal(wins) / len(values) * 100) if values else None,
        "money_win_rate_percent": rounded(gains / (gains + losses) * 100) if gains + losses > 0 else None,
        "gross_profit": rounded(gains), "gross_loss": rounded(losses),
        "profit_factor": rounded(gains / losses, 4) if losses else None,
        "rate_sample_trades": len(eligible),
        "rate_basis": "fully_closed_trades_with_complete_broker_matches",
        "total_realized_profit_loss": rounded(sum(all_known, Decimal(0))) if all_known or complete else None,
        "closed_trade_profit_loss": rounded(sum(known, Decimal(0))) if known or not closed else None,
        "partial_trade_realized_profit_loss": rounded(sum((number(t.get("realized_profit_loss")) or Decimal(0) for t in trades if t.get("status") not in {"CLOSED", "CLOSED_INCOMPLETE"}), Decimal(0))),
        "complete_trade_profit_loss": rounded(sum(values, Decimal(0))),
        "known_matched_realized_profit_loss": rounded(sum(all_known, Decimal(0))),
        "realized_profit_loss_complete": complete,
        "average_return_percent": rounded(sum(returns, Decimal(0)) / len(returns), 4) if returns else None,
        "unmatched_sell_fills": unmatched, "ledger_limit_reached": truncated,
        "ledger_last_fill_at": ledger.get("ledger_last_fill_at"),
        "summary_scope": "all_matched_bot_exit_fills_in_loaded_broker_ledger",
        "fees_included": False,
    }


def daily_realized_summaries(trades):
    """Attribute matched realized P/L to each exit's Eastern date, including partial exits."""
    days = {}
    for trade in trades:
        entry = number(trade.get("entry_price"))
        if entry is None or entry <= 0:
            continue
        for allocation in trade.get("exit_allocations") or []:
            day = eastern_day(allocation.get("timestamp"))
            price = number(allocation.get("price"))
            shares = number(allocation.get("shares"))
            if not day or price is None or price <= 0 or shares is None or shares <= 0:
                continue
            row = days.setdefault(day, {"pnl": Decimal(0), "allocations": 0, "orders": set()})
            row["pnl"] += (price - entry) * shares
            row["allocations"] += 1
            row["orders"].add(allocation.get("exit_order_id"))
    return {day: {
        "known_realized_profit_loss": rounded(row["pnl"]),
        "matched_exit_allocations": row["allocations"],
        "matched_exit_orders": len(row["orders"]),
        "basis": "matched_exit_fills_on_eastern_date_before_fees",
    } for day, row in sorted(days.items(), reverse=True)}


def annotate_order_history(history, canonical):
    """Do not infer a sell's full cost basis from only the latest 500 orders."""
    exits = {}
    for trade in canonical:
        entry = number(trade.get("entry_price"))
        if entry is None:
            continue
        for allocation in trade.get("exit_allocations") or []:
            shares = number(allocation.get("shares"))
            price = number(allocation.get("price"))
            if shares is None or shares <= 0 or price is None:
                continue
            row = exits.setdefault(allocation.get("exit_order_id"), [Decimal(0), Decimal(0), Decimal(0)])
            row[0] += shares
            row[1] += entry * shares
            row[2] += (price - entry) * shares
    for order in history:
        order["profit_loss_dollars"] = None
        order["profit_loss_percent"] = None
        order["pnl_complete"] = False
        if str(order.get("side", "")).upper() != "SELL":
            continue
        match = exits.get(order.get("order_id") or order.get("id"))
        shares = number(order.get("shares"))
        if match and shares is not None and abs(match[0] - shares) < Decimal("0.00000001") and match[1] > 0:
            order["profit_loss_dollars"] = rounded(match[2])
            order["profit_loss_percent"] = rounded(match[2] / match[1] * 100, 4)
            order["pnl_complete"] = True
    return history
