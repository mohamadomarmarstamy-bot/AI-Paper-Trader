"""Conservative PAPER-only strategy share accounting.

Pure state transitions. Broker fills must be verified independently before
calling this function. No brokerage actions or database mutations occur here.
"""
from __future__ import annotations

from typing import Any


def apply_verified_paper_fill(
    *,
    symbol: str,
    current_shares: int,
    filled_shares: int,
    side: str,
    ownership_verified: bool,
    fill_verified: bool,
    already_accounted: bool,
) -> dict[str, Any]:
    """Compute an idempotency-gated strategy position update, without writing."""
    base = {
        "paper": True, "preview_only": True, "database_updated": False,
        "symbol": str(symbol or "").strip().upper(),
        "accepted": False, "next_shares": None, "reason": "blocked",
    }
    if (
        not isinstance(symbol, str) or not symbol.strip()
        or isinstance(current_shares, bool) or not isinstance(current_shares, int)
        or current_shares < 0
        or isinstance(filled_shares, bool) or not isinstance(filled_shares, int)
        or filled_shares <= 0 or side not in {"buy", "sell"}
    ):
        return {**base, "reason": "invalid_fill_or_position"}
    if ownership_verified is not True or fill_verified is not True:
        return {**base, "reason": "unverified_broker_fill_or_ownership"}
    if already_accounted is not False:
        return {**base, "reason": "fill_already_accounted_or_unknown"}
    if side == "sell" and filled_shares > current_shares:
        return {**base, "reason": "sell_exceeds_strategy_shares"}
    next_shares = current_shares + filled_shares if side == "buy" else current_shares - filled_shares
    return {**base, "accepted": True, "next_shares": next_shares, "reason": "verified_fill_ready_for_atomic_posting"}
