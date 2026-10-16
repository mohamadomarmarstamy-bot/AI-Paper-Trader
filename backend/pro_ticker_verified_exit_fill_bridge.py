"""Post cumulative PAPER exit fills only after persisted ownership verification.

The supplied broker order must be fetched independently from the PAPER broker.
This module never submits orders or releases virtual budget.
"""
from __future__ import annotations

from typing import Any

from pro_ticker_budget_ledger import get_pro_ticker_reservation
from pro_ticker_exit_links import get_pro_ticker_exit_link
from pro_ticker_verified_exit_review import review_verified_paper_exit_link
from pro_ticker_position_ledger import post_verified_cumulative_paper_fill


def account_linked_paper_exit_snapshot(
    broker_order_id: str, broker_order: dict[str, Any],
) -> dict[str, Any]:
    """Reconcile a persisted exit link with a verified cumulative broker fill."""
    if not isinstance(broker_order_id, str) or not broker_order_id.strip() or broker_order_id != broker_order_id.strip():
        raise ValueError("Canonical PAPER exit broker order ID required.")
    if not isinstance(broker_order, dict):
        raise ValueError("Fresh PAPER exit broker order snapshot required.")
    link = get_pro_ticker_exit_link(broker_order_id)
    if link is None:
        raise ValueError("PAPER exit ownership link missing.")
    reservation = get_pro_ticker_reservation(link["reservation_id"])
    review = review_verified_paper_exit_link(link, reservation, broker_order)
    if review.get("verified") is not True:
        raise ValueError("PAPER exit fill verification failed: " + review["reason"])
    return post_verified_cumulative_paper_fill(
        broker_order_id=broker_order_id, symbol=link["symbol"], side="sell",
        cumulative_filled_shares=review["filled_shares"],
        ownership_verified=True, fill_verified=True,
    )
