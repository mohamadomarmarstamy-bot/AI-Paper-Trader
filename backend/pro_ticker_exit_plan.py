"""Conservative exit planning for the simulated Pro Ticker PAPER strategy.

Pure calculations only. A broker order must never be placed from this module.
"""
from __future__ import annotations

import math
from typing import Any


def plan_paper_exit(
    *,
    symbol: str,
    entry_price: float,
    current_price: float,
    held_shares: int,
    owned_shares: int,
    stop_loss_percent: float = 2.0,
    take_profit_percent: float = 4.0,
    quote_age_seconds: float,
    market_open: bool,
    open_sell_order: bool,
    strategy_ownership_verified: bool,
    maximum_quote_age_seconds: float = 30.0,
) -> dict[str, Any]:
    """Evaluate a simulated stop/target exit, fail closed on uncertain ownership."""
    reasons: list[str] = []
    values = (
        entry_price, current_price, stop_loss_percent,
        take_profit_percent, quote_age_seconds, maximum_quote_age_seconds,
    )
    if any(
        isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)
        for v in values
    ):
        reasons.append("invalid_numeric_input")
    elif (
        entry_price <= 0 or current_price <= 0 or
        not 0 < stop_loss_percent < 100 or
        take_profit_percent <= 0 or
        quote_age_seconds < 0 or maximum_quote_age_seconds <= 0
    ):
        reasons.append("invalid_numeric_input")
    if (
        isinstance(held_shares, bool) or not isinstance(held_shares, int) or
        isinstance(owned_shares, bool) or not isinstance(owned_shares, int) or
        held_shares <= 0 or owned_shares <= 0 or owned_shares > held_shares
    ):
        reasons.append("invalid_position_quantity")
    if not isinstance(symbol, str) or not symbol.strip():
        reasons.append("invalid_symbol")
    if strategy_ownership_verified is not True:
        reasons.append("strategy_ownership_unverified")
    if market_open is not True:
        reasons.append("market_closed")
    if open_sell_order:
        reasons.append("existing_sell_order")
    if not reasons and quote_age_seconds > maximum_quote_age_seconds:
        reasons.append("stale_quote")
    change_percent = None
    trigger = None
    if not reasons:
        change_percent = (current_price / entry_price - 1) * 100
        if change_percent <= -stop_loss_percent:
            trigger = "stop_loss"
        elif change_percent >= take_profit_percent:
            trigger = "take_profit"
    return {
        "paper": True, "preview_only": True, "order_submitted": False,
        "symbol": str(symbol or "").strip().upper(),
        "sell_shares": owned_shares if trigger else 0,
        "trigger": trigger,
        "change_percent": round(change_percent, 4) if change_percent is not None else None,
        "eligible_for_further_review": not reasons and trigger is not None,
        "block_reasons": reasons,
    }
