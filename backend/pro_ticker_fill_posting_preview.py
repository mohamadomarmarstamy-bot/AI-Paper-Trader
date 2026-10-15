"""Read-only broker-to-ledger posting preview for Pro Ticker PAPER entries.

No database mutation, broker order, or allocation release occurs here.
"""
from __future__ import annotations

from typing import Any

from pro_ticker_fill_verification import assess_paper_order_fill
from pro_ticker_order_ownership import verify_paper_order_link


def preview_entry_fill_delta(
    reservation: dict[str, Any],
    broker_order: dict[str, Any] | None,
    recorded_order_shares: int,
) -> dict[str, Any]:
    """Identify new PAPER entry shares without trusting symbol alone."""
    base = {
        "paper": True, "preview_only": True, "order_submitted": False,
        "database_updated": False, "ready_for_posting": False,
        "delta_shares": 0, "reason": "unverified",
    }
    if isinstance(recorded_order_shares, bool) or not isinstance(recorded_order_shares, int) or recorded_order_shares < 0:
        return {**base, "reason": "invalid_accounted_quantity"}
    if not isinstance(reservation, dict):
        return {**base, "reason": "invalid_reservation"}
    ownership = verify_paper_order_link(reservation, broker_order)
    if not ownership["verified"]:
        return {**base, "reason": ownership["reason"]}
    try:
        raw_qty = float(broker_order["qty"])
    except (TypeError, ValueError, KeyError, OverflowError):
        return {**base, "reason": "invalid_broker_quantity"}
    if not raw_qty.is_integer() or raw_qty < 1 or raw_qty > 1_000_000:
        return {**base, "reason": "invalid_broker_quantity"}
    verification = assess_paper_order_fill(
        expected_order_id=str(reservation["broker_order_id"]),
        expected_symbol=str(reservation["symbol"]),
        expected_side="buy",
        expected_shares=int(raw_qty),
        broker_order=broker_order,
    )
    if not verification["verified"]:
        return {**base, "reason": verification["reason"]}
    filled = verification["filled_shares"]
    if recorded_order_shares > filled:
        return {**base, "reason": "ledger_ahead_of_broker_snapshot"}
    delta = filled - recorded_order_shares
    return {
        **base,
        "ready_for_posting": delta > 0,
        "delta_shares": delta,
        "broker_filled_shares": filled,
        "accounted_shares": recorded_order_shares,
        "reason": "verified_unposted_entry_shares" if delta else "entry_fills_already_accounted",
    }
