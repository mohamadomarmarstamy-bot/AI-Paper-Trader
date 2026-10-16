"""Pure PAPER execution planning: no broker calls, database writes, or live orders."""
from __future__ import annotations

import math
from typing import Any


def plan_paper_entry(
    *,
    symbol: str,
    reference_price: float,
    requested_shares: int,
    available_budget: float,
    broker_buying_power: float,
    market_open: bool,
    quote_age_seconds: float,
    existing_position: bool,
    existing_open_order: bool,
    maximum_quote_age_seconds: float = 30.0,
) -> dict[str, Any]:
    """Fail-closed integer-share plan; caller must revalidate before execution."""
    blocked: list[str] = []
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol or not normalized_symbol.replace(".", "").isalnum():
        blocked.append("invalid_symbol")
    if not isinstance(requested_shares, int) or isinstance(requested_shares, bool) or requested_shares < 1:
        blocked.append("invalid_quantity")
    values = (reference_price, available_budget, broker_buying_power, quote_age_seconds)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        blocked.append("invalid_market_or_budget_data")
    elif reference_price <= 0 or available_budget < 0 or broker_buying_power < 0 or quote_age_seconds < 0:
        blocked.append("invalid_market_or_budget_data")
    if market_open is not True:
        blocked.append("market_closed")
    if existing_position is not False:
        blocked.append("position_already_exists")
    if existing_open_order is not False:
        blocked.append("open_order_already_exists")
    if (
        isinstance(maximum_quote_age_seconds, bool)
        or not isinstance(maximum_quote_age_seconds, (int, float))
        or not math.isfinite(maximum_quote_age_seconds)
        or maximum_quote_age_seconds <= 0
    ):
        blocked.append("invalid_maximum_quote_age")
    elif isinstance(quote_age_seconds, (int, float)) and not isinstance(quote_age_seconds, bool) and math.isfinite(quote_age_seconds):
        if quote_age_seconds > maximum_quote_age_seconds:
            blocked.append("stale_quote")
    estimated_notional = None
    if not blocked:
        estimated_notional = round(reference_price * requested_shares, 2)
        if estimated_notional > available_budget:
            blocked.append("virtual_budget_exceeded")
        if estimated_notional > broker_buying_power:
            blocked.append("broker_buying_power_exceeded")
    return {
        "paper": True,
        "preview_only": True,
        "order_submitted": False,
        "allowed_for_further_review": not blocked,
        "symbol": normalized_symbol,
        "shares": requested_shares,
        "estimated_notional": estimated_notional,
        "block_reasons": blocked,
    }
