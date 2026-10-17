"""Read-only reconciliation of PAPER reservations with verified broker fills.

The snapshot never mutates the ledger and never treats an absent order as closed.
"""
from __future__ import annotations

from typing import Any

from pro_ticker_fill_verification import assess_paper_order_fill
from pro_ticker_order_ownership import verify_paper_order_link
from pro_ticker_reconciliation_review import assess_reservation_state


def review_paper_reservation(
    reservation: dict[str, Any],
    broker_order: dict[str, Any] | None,
    *,
    position_exists: bool | None,
) -> dict[str, Any]:
    """Require linked PAPER entry ownership before interpreting a broker fill."""
    result = {
        "reservation_id": reservation.get("id"),
        "symbol": reservation.get("symbol"),
        "current_status": reservation.get("status"),
        "suggested_status": reservation.get("status"),
        "safe_to_release": False,
        "fill_verified": False,
        "filled_shares": None,
        "remaining_shares": None,
        "reason": "unverified",
    }
    ownership = verify_paper_order_link(reservation, broker_order)
    if not ownership["verified"]:
        return {**result, "reason": ownership["reason"]}
    # A broker quantity is not proof that Pro Ticker owns all account shares.
    try:
        quantity = float(broker_order["qty"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return {**result, "reason": "invalid_order_quantity"}
    if not quantity.is_integer() or quantity < 1 or quantity > 1_000_000:
        return {**result, "reason": "invalid_order_quantity"}
    verification = assess_paper_order_fill(
        expected_order_id=str(reservation["broker_order_id"]),
        expected_symbol=str(reservation["symbol"]),
        expected_side="buy",
        expected_shares=int(quantity),
        broker_order=broker_order,
    )
    if not verification["verified"]:
        return {**result, "reason": verification["reason"]}
    review = assess_reservation_state(
        reservation, broker_order, position_exists=position_exists,
    )
    return {
        **result, **review,
        "fill_verified": True,
        "filled_shares": verification["filled_shares"],
        "remaining_shares": verification["remaining_shares"],
        "broker_order_terminal": verification["terminal"],
        # No allocation release is authorized without linked strategy exit fills.
        "safe_to_release": False,
    }
