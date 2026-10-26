"""Strict strategy attribution for future Alpaca PAPER order submissions.

An order must carry the reservation-specific client_order_id generated BEFORE
submission. A symbol match or broker order ID alone is never sufficient.
No broker API calls or orders are made in this module.
"""
from __future__ import annotations

import re
from typing import Any

_RESERVATION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def pro_ticker_client_order_id(reservation_id: str) -> str:
    if not isinstance(reservation_id, str) or not _RESERVATION_ID.fullmatch(reservation_id):
        raise ValueError("Expected a lowercase UUID reservation ID.")
    return "pt-" + reservation_id


def verify_paper_entry_for_link(
    reservation: dict[str, Any],
    broker_order: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate a broker-returned PAPER entry order before durable attribution."""
    base = {"verified": False, "paper": True, "order_submitted": False}
    if not isinstance(reservation, dict) or not isinstance(broker_order, dict):
        return {**base, "reason": "missing_reservation_or_order"}
    try:
        client_id = pro_ticker_client_order_id(reservation.get("id"))
    except ValueError:
        return {**base, "reason": "invalid_reservation_id"}
    if reservation.get("status") != "reserved":
        return {**base, "reason": "reservation_not_reserved"}
    if reservation.get("broker_order_id"):
        return {**base, "reason": "reservation_already_linked"}
    if str(broker_order.get("client_order_id") or "") != client_id:
        return {**base, "reason": "client_order_id_mismatch"}
    order_id = broker_order.get("id")
    if not isinstance(order_id, str) or not order_id.strip():
        return {**base, "reason": "broker_order_id_missing"}
    symbol = str(reservation.get("symbol") or "").strip().upper()
    if not symbol or symbol != str(broker_order.get("symbol") or "").strip().upper():
        return {**base, "reason": "symbol_mismatch"}
    if str(broker_order.get("side") or "").lower() != "buy":
        return {**base, "reason": "not_entry_buy"}
    if str(broker_order.get("status") or "").lower() not in {
        "new", "accepted", "pending_new", "partially_filled", "filled",
        "pending_cancel", "canceled", "expired", "rejected",
    }:
        return {**base, "reason": "unknown_broker_status"}
    return {
        **base, "verified": True, "reason": "reservation_client_order_id_matched",
        "broker_order_id": order_id.strip(), "client_order_id": client_id,
    }
