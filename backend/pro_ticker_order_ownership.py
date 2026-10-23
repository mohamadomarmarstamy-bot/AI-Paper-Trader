"""Conservative PAPER order ownership checks for Pro Ticker Lab.

A symbol match is never enough to attribute an order to a strategy.
"""
from __future__ import annotations

from typing import Any


def verify_paper_order_link(
    reservation: dict[str, Any],
    order: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate persisted broker ID, symbol and buy-side before reconciliation."""
    if not isinstance(order, dict):
        return {"verified": False, "reason": "order_missing"}
    linked_id = str(reservation.get("broker_order_id") or "").strip()
    if not linked_id:
        return {"verified": False, "reason": "reservation_unlinked"}
    if linked_id != str(order.get("id") or "").strip():
        return {"verified": False, "reason": "order_id_mismatch"}
    symbol = str(reservation.get("symbol") or "").strip().upper()
    if not symbol or symbol != str(order.get("symbol") or "").strip().upper():
        return {"verified": False, "reason": "symbol_mismatch"}
    if str(order.get("side") or "").strip().lower() != "buy":
        return {"verified": False, "reason": "not_entry_buy"}
    return {"verified": True, "reason": "linked_paper_entry_order"}
