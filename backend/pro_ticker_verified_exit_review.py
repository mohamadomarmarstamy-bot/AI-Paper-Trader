"""Read-only review of a persisted strategy-owned PAPER exit order."""
from __future__ import annotations
from typing import Any
from pro_ticker_fill_verification import assess_paper_order_fill


def review_verified_paper_exit_link(
    link: dict[str, Any] | None,
    reservation: dict[str, Any] | None,
    broker_order: dict[str, Any] | None,
) -> dict[str, Any]:
    base = {
        "paper": True, "preview_only": True, "reconciled": False,
        "order_submitted": False, "database_updated": False,
        "budget_released": False, "verified": False,
        "filled_shares": 0,
    }
    if not isinstance(link, dict) or not isinstance(reservation, dict):
        return {**base, "reason": "exit_link_or_reservation_missing"}
    if not isinstance(broker_order, dict):
        return {**base, "reason": "paper_exit_order_unavailable"}
    if (reservation.get("id") != link.get("reservation_id")
            or reservation.get("symbol") != link.get("symbol")
            or reservation.get("status") not in {"submitted", "filled"}
            or not reservation.get("broker_order_id")):
        return {**base, "reason": "strategy_reservation_ownership_mismatch"}
    if broker_order.get("client_order_id") != link.get("client_order_id"):
        return {**base, "reason": "paper_exit_client_order_id_mismatch"}
    verification = assess_paper_order_fill(
        expected_order_id=link.get("broker_order_id"),
        expected_symbol=link.get("symbol"),
        expected_side="sell",
        expected_shares=link.get("expected_shares"),
        broker_order=broker_order,
    )
    if not verification["verified"]:
        return {**base, "reason": verification["reason"]}
    return {
        **base, "verified": True, "filled_shares": verification["filled_shares"],
        "remaining_order_shares": verification["remaining_shares"],
        "terminal": verification["terminal"], "broker_status": verification["broker_status"],
        "reason": "verified_exit_snapshot_no_accounting_posted",
    }
