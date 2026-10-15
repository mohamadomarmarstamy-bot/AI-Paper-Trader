"""Verified PAPER entry fill accounting bridge.

The caller must supply a fresh order fetched from the Alpaca PAPER account.
This module does not access live trading endpoints or place broker orders.
"""
from __future__ import annotations

import math
from typing import Any

from pro_ticker_budget_ledger import list_pro_ticker_reservations
from pro_ticker_fill_verification import assess_paper_order_fill
from pro_ticker_order_linking import pro_ticker_client_order_id
from pro_ticker_position_ledger import post_verified_cumulative_paper_fill


def account_linked_paper_entry_snapshot(
    reservation_id: str,
    broker_order: dict[str, Any],
) -> dict[str, Any]:
    """Verify linked ownership and cumulative fills before an idempotent post.

    Fail closed for missing or ambiguous reservation lookup and order data.
    Never release reserved funds or submit a broker order.
    """
    if not isinstance(reservation_id, str) or not reservation_id.strip():
        raise ValueError("Reservation ID required.")
    if not isinstance(broker_order, dict):
        raise ValueError("Fresh PAPER broker order snapshot required.")
    reservations = list_pro_ticker_reservations(limit=500)["reservations"]
    matches = [row for row in reservations if row["id"] == reservation_id]
    if len(matches) != 1:
        raise ValueError("Unique reservation not found in ledger snapshot.")
    reservation = matches[0]
    if reservation["status"] not in {"submitted", "filled"}:
        raise ValueError("Reservation not linked to a submitted PAPER order.")
    linked_id = str(reservation.get("broker_order_id") or "").strip()
    if not linked_id or str(broker_order.get("id") or "") != linked_id:
        raise ValueError("PAPER broker order does not match persisted link.")
    if str(broker_order.get("client_order_id") or "") != pro_ticker_client_order_id(reservation_id):
        raise ValueError("PAPER client order ID does not match reservation.")
    symbol = str(reservation["symbol"]).strip().upper()
    if symbol != str(broker_order.get("symbol") or "").strip().upper():
        raise ValueError("PAPER order symbol mismatch.")
    if str(broker_order.get("side") or "").strip().lower() != "buy":
        raise ValueError("Only verified PAPER entry buys may be posted.")
    try:
        quantity = float(broker_order["qty"])
    except (KeyError, ValueError, TypeError, OverflowError) as error:
        raise ValueError("Invalid PAPER order quantity.") from error
    if not math.isfinite(quantity) or quantity <= 0 or not quantity.is_integer():
        raise ValueError("Whole-share PAPER order quantity required.")
    verification = assess_paper_order_fill(
        expected_order_id=linked_id,
        expected_symbol=symbol,
        expected_side="buy",
        expected_shares=int(quantity),
        broker_order=broker_order,
    )
    if not verification["verified"]:
        raise ValueError("PAPER fill verification failed: " + verification["reason"])
    return post_verified_cumulative_paper_fill(
        broker_order_id=linked_id,
        symbol=symbol,
        side="buy",
        cumulative_filled_shares=verification["filled_shares"],
        ownership_verified=True,
        fill_verified=True,
    )
