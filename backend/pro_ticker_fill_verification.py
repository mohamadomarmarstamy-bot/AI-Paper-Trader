"""Pure simulated PAPER broker order lifecycle checks.

These checks do not submit, cancel, or modify broker orders.
"""
from __future__ import annotations

from math import isfinite
from typing import Any


def assess_paper_order_fill(
    *,
    expected_order_id: str,
    expected_symbol: str,
    expected_side: str,
    expected_shares: int,
    broker_order: dict[str, Any] | None,
) -> dict[str, Any]:
    """Verify identity, side, quantity and status of a PAPER broker order."""
    base = {
        "paper": True, "order_submitted": False,
        "verified": False, "filled_shares": 0,
        "remaining_shares": None, "terminal": False,
    }
    if (
        not isinstance(expected_order_id, str) or not expected_order_id.strip()
        or not isinstance(expected_symbol, str) or not expected_symbol.strip()
        or expected_side not in {"buy", "sell"}
        or isinstance(expected_shares, bool)
        or not isinstance(expected_shares, int) or expected_shares <= 0
    ):
        return {**base, "reason": "invalid_expected_order"}
    if not isinstance(broker_order, dict):
        return {**base, "reason": "broker_order_unavailable"}
    if str(broker_order.get("id") or "") != expected_order_id:
        return {**base, "reason": "order_id_mismatch"}
    if str(broker_order.get("symbol") or "").upper() != expected_symbol.upper():
        return {**base, "reason": "symbol_mismatch"}
    if str(broker_order.get("side") or "").lower() != expected_side:
        return {**base, "reason": "side_mismatch"}
    try:
        order_qty = float(broker_order["qty"])
        filled_qty = float(broker_order["filled_qty"])
    except (KeyError, TypeError, ValueError):
        return {**base, "reason": "invalid_broker_quantities"}
    if (
        not isfinite(order_qty) or not isfinite(filled_qty)
        or order_qty != expected_shares
        or filled_qty < 0 or filled_qty > order_qty
        or not filled_qty.is_integer()
    ):
        return {**base, "reason": "quantity_mismatch"}
    status = str(broker_order.get("status") or "").lower()
    terminal = status in {"filled", "canceled", "expired", "rejected"}
    recognized = terminal or status in {
        "new", "accepted", "pending_new", "pending_cancel",
        "partially_filled", "accepted_for_bidding", "stopped",
    }
    if not recognized:
        return {**base, "reason": "unknown_broker_status"}
    if status == "filled" and filled_qty != order_qty:
        return {**base, "reason": "inconsistent_filled_status"}
    if status in {"new", "accepted", "pending_new"} and filled_qty > 0:
        return {**base, "reason": "inconsistent_pending_status"}
    return {
        **base, "verified": True, "reason": "verified_paper_order",
        "filled_shares": int(filled_qty),
        "remaining_shares": int(order_qty - filled_qty),
        "terminal": terminal, "broker_status": status,
    }
