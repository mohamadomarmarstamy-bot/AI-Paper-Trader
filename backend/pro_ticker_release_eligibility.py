"""Conservative read-only PAPER virtual budget release eligibility review.

This function never mutates a reservation or places an order. Caller must
independently establish the broker origin of all supplied verified evidence.
"""
from __future__ import annotations

from typing import Any


def review_paper_release_eligibility(
    reservation: dict[str, Any],
    entry_order_id: str,
    entry_filled_shares: int,
    exit_fills: list[dict[str, Any]],
    remaining_strategy_shares: int,
    *,
    broker_evidence_verified: bool,
) -> dict[str, Any]:
    """Evaluate evidence; never authorize or execute budget release."""
    base = {
        "paper": True, "preview_only": True, "database_updated": False,
        "budget_released": False, "release_authorized": False,
        "eligible_for_independent_review": False,
    }

    def blocked(reason: str) -> dict[str, Any]:
        return {**base, "reason": reason}

    if broker_evidence_verified is not True:
        return blocked("broker_evidence_unverified")
    if not isinstance(reservation, dict):
        return blocked("invalid_reservation")
    if reservation.get("status") != "filled":
        return blocked("reservation_not_filled")
    linked = reservation.get("broker_order_id")
    if not isinstance(linked, str) or not linked.strip() or linked != entry_order_id:
        return blocked("entry_order_link_mismatch")
    symbol = reservation.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip() or symbol != symbol.strip().upper():
        return blocked("invalid_symbol")
    if (isinstance(entry_filled_shares, bool) or not isinstance(entry_filled_shares, int)
            or entry_filled_shares <= 0):
        return blocked("invalid_entry_fill_count")
    if (isinstance(remaining_strategy_shares, bool)
            or not isinstance(remaining_strategy_shares, int)
            or remaining_strategy_shares < 0):
        return blocked("invalid_remaining_shares")
    if not isinstance(exit_fills, list) or not exit_fills:
        return blocked("verified_exit_fills_missing")
    total = 0
    order_ids: set[str] = set()
    for fill in exit_fills:
        if not isinstance(fill, dict):
            return blocked("invalid_exit_evidence")
        order_id = fill.get("broker_order_id")
        shares = fill.get("accounted_shares")
        if (not isinstance(order_id, str) or not order_id.strip()
                or order_id in order_ids or order_id == linked
                or fill.get("symbol") != symbol or fill.get("side") != "sell"
                or fill.get("ownership_verified") is not True
                or fill.get("fill_verified") is not True
                or isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0):
            return blocked("invalid_exit_evidence")
        order_ids.add(order_id)
        total += shares
    if total != entry_filled_shares:
        return blocked("entry_exit_share_mismatch")
    if remaining_strategy_shares != 0:
        return blocked("strategy_position_still_open")
    return {
        **base, "eligible_for_independent_review": True,
        "verified_entry_shares": entry_filled_shares,
        "accounted_exit_shares": total,
        "reason": "complete_exit_evidence_requires_separate_release_authorization",
    }
