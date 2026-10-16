"""Read-only broker-to-ledger posting preview for Pro Ticker PAPER entries.

No database mutation, broker order, or allocation release occurs here.
"""
from __future__ import annotations

from typing import Any
import math

from pro_ticker_order_linking import pro_ticker_client_order_id

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
    expected_shares = reservation.get("expected_shares")
    if isinstance(expected_shares, bool) or not isinstance(expected_shares, int) or expected_shares <= 0:
        return {**base, "reason": "reservation_expected_shares_missing"}
    if not math.isfinite(raw_qty) or raw_qty != expected_shares:
        return {**base, "reason": "reservation_quantity_mismatch"}
    try:
        client_id = pro_ticker_client_order_id(reservation["id"])
    except (KeyError, ValueError, TypeError):
        return {**base, "reason": "invalid_reservation_id"}
    if str(broker_order.get("client_order_id") or "") != client_id:
        return {**base, "reason": "client_order_id_mismatch"}
    if reservation.get("status") not in {"submitted", "filled"}:
        return {**base, "reason": "reservation_not_submitted"}
    verification = assess_paper_order_fill(
        expected_order_id=str(reservation["broker_order_id"]),
        expected_symbol=str(reservation["symbol"]),
        expected_side="buy",
        expected_shares=expected_shares,
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
