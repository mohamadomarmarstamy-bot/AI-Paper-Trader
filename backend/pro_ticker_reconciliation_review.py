"""Read-only reconciliation of simulated PAPER reservation status.

This module never places orders or changes reservations.
"""
from __future__ import annotations

from typing import Any


def assess_reservation_state(
    reservation: dict[str, Any],
    broker_order: dict[str, Any] | None,
    *,
    position_exists: bool | None = None,
) -> dict[str, Any]:
    """Return a conservative suggested status without mutating anything."""
    symbol = str(reservation.get("symbol") or "").upper()
    current = str(reservation.get("status") or "")
    if current in {"released", "failed"}:
        return {"suggested_status": current, "safe_to_release": False, "reason": "terminal_record"}
    if not isinstance(broker_order, dict):
        return {"suggested_status": current, "safe_to_release": False, "reason": "broker_order_missing"}
    if str(broker_order.get("symbol") or "").upper() != symbol:
        return {"suggested_status": current, "safe_to_release": False, "reason": "symbol_mismatch"}
    expected_order_id = str(reservation.get("broker_order_id") or "").strip()
    if not expected_order_id:
        return {"suggested_status": current, "safe_to_release": False, "reason": "reservation_order_unlinked"}
    if expected_order_id != str(broker_order.get("id") or ""):
        return {"suggested_status": current, "safe_to_release": False, "reason": "order_id_mismatch"}
    if not broker_order.get("id"):
        return {"suggested_status": current, "safe_to_release": False, "reason": "missing_order_id"}
    if str(broker_order.get("side") or "").strip().lower() != "buy":
        return {"suggested_status": current, "safe_to_release": False, "reason": "entry_order_side_unverified"}
    try:
        filled = float(broker_order.get("filled_qty", 0))
    except (ValueError, TypeError):
        return {"suggested_status": current, "safe_to_release": False, "reason": "invalid_filled_quantity"}
    from math import isfinite
    if not isfinite(filled) or filled < 0:
        return {"suggested_status": current, "safe_to_release": False, "reason": "invalid_filled_quantity"}
    status = str(broker_order.get("status") or "").lower()
    if filled > 0:
        # Position absence alone is not proof of a matched strategy exit.
        # Another strategy, an external manual close, or stale position data
        # could explain the absence. Require linked exit fills separately.
        if status == "filled" and position_exists is False:
            return {"suggested_status": "filled", "safe_to_release": False, "reason": "matched_exit_fill_required"}
        return {"suggested_status": "filled", "safe_to_release": False, "reason": "shares_filled_or_close_unverified"}
    if status in {"canceled", "expired", "rejected"} and position_exists is False:
        return {"suggested_status": "failed", "safe_to_release": True, "reason": "no_fill_and_no_position"}
    if status in {"new", "accepted", "pending_new", "pending_cancel", "partially_filled"}:
        return {"suggested_status": "submitted", "safe_to_release": False, "reason": "order_in_progress"}
    return {"suggested_status": current, "safe_to_release": False, "reason": "unverified_state"}
