"""Read-only PAPER exit-fill attribution for Pro Ticker positions.

No position is considered closed merely because a broker position is absent.
"""
from __future__ import annotations

from typing import Any

from pro_ticker_fill_verification import assess_paper_order_fill


def review_linked_paper_exit(
    *,
    symbol: str,
    entry_filled_shares: int,
    expected_exit_order_id: str,
    expected_exit_shares: int,
    broker_exit_order: dict[str, Any] | None,
) -> dict[str, Any]:
    """Review one explicitly linked PAPER sell order; never release funds."""
    base = {
        "paper": True,
        "preview_only": True,
        "database_updated": False,
        "budget_released": False,
        "fully_closed_verified": False,
        "exit_filled_shares": 0,
        "reason": "unverified",
    }
    if (
        isinstance(entry_filled_shares, bool)
        or not isinstance(entry_filled_shares, int)
        or entry_filled_shares < 1
        or isinstance(expected_exit_shares, bool)
        or not isinstance(expected_exit_shares, int)
        or expected_exit_shares < 1
        or expected_exit_shares > entry_filled_shares
    ):
        return {**base, "reason": "invalid_strategy_share_quantity"}
    verification = assess_paper_order_fill(
        expected_order_id=expected_exit_order_id,
        expected_symbol=symbol,
        expected_side="sell",
        expected_shares=expected_exit_shares,
        broker_order=broker_exit_order,
    )
    if not verification["verified"]:
        return {**base, "reason": verification["reason"]}
    # A single exit order can only prove full closure if all entry shares
    # were covered. Multiple exit fills require an aggregated fill ledger.
    fully_closed = (
        verification["terminal"]
        and verification["filled_shares"] == entry_filled_shares
    )
    return {
        **base,
        "exit_filled_shares": verification["filled_shares"],
        "remaining_strategy_shares": entry_filled_shares - verification["filled_shares"],
        "fully_closed_verified": fully_closed,
        "reason": "linked_exit_fully_filled" if fully_closed else "partial_or_pending_linked_exit",
    }
