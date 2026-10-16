"""PAPER-only strategy allocation and order admission rules.

These pure functions do not place orders. Call them immediately before
routing a Pro Ticker intent to the existing PAPER broker adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

PRO_TICKER_VIRTUAL_BUDGET = 50_000.00
ORIGINAL_SCANNER_VIRTUAL_BUDGET = 45_024.13


@dataclass(frozen=True)
class PaperAdmission:
    allowed: bool
    reason: str
    max_notional: float = 0.0


def check_pro_ticker_paper_entry(
    *,
    symbol: str,
    proposed_notional: float,
    pro_ticker_committed: float,
    broker_buying_power: float,
    open_position_symbols: set[str],
    open_order_symbols: set[str],
    market_open: bool,
    broker_paper: bool,
    data_age_seconds: float,
    max_data_age_seconds: float = 90.0,
) -> PaperAdmission:
    """Fail closed if budget, broker mode, freshness or collision checks fail."""
    if broker_paper is not True:
        return PaperAdmission(False, "paper_account_required")
    if market_open is not True:
        return PaperAdmission(False, "regular_market_closed")
    values = (
        proposed_notional, pro_ticker_committed, broker_buying_power,
        data_age_seconds, max_data_age_seconds,
    )
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) for v in values):
        return PaperAdmission(False, "invalid_numeric_input")
    if proposed_notional <= 0 or pro_ticker_committed < 0 or broker_buying_power < 0:
        return PaperAdmission(False, "invalid_amount")
    if data_age_seconds < 0 or max_data_age_seconds <= 0 or data_age_seconds > max_data_age_seconds:
        return PaperAdmission(False, "stale_market_data")
    normalized = symbol.strip().upper() if isinstance(symbol, str) else ""
    if not normalized or not normalized.replace(".", "").isalnum():
        return PaperAdmission(False, "invalid_symbol")
    if normalized in {str(s).upper() for s in open_position_symbols}:
        return PaperAdmission(False, "existing_position")
    if normalized in {str(s).upper() for s in open_order_symbols}:
        return PaperAdmission(False, "existing_open_order")
    available = min(
        max(0.0, PRO_TICKER_VIRTUAL_BUDGET - pro_ticker_committed),
        broker_buying_power,
    )
    if proposed_notional > available:
        return PaperAdmission(False, "insufficient_virtual_or_broker_budget", round(available, 2))
    return PaperAdmission(True, "eligible_for_paper_order", round(available, 2))
