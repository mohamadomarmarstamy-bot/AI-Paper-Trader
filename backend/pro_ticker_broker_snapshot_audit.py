"""Read-only PAPER broker snapshot cross-check against linked reservation fills.

The caller must supply independently fetched PAPER broker snapshots. This
module cannot authenticate their provenance, submit orders, or release budget.
"""
from __future__ import annotations

from typing import Any

from database import get_connection
from pro_ticker_budget_ledger import get_pro_ticker_reservation
from pro_ticker_broker_audit_inputs import validate_paper_snapshot_inputs
from pro_ticker_exit_links import get_pro_ticker_exit_link
from pro_ticker_fill_verification import assess_paper_order_fill
from pro_ticker_linked_share_review import review_linked_reservation_paper_shares
from pro_ticker_position_ledger import get_accounted_paper_order_shares


def audit_linked_paper_broker_snapshots(
    reservation_id: str,
    entry_order: dict[str, Any] | None,
    exit_orders: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compare cumulative PAPER order fills with persisted linked accounting."""
    base = {
        "paper": True, "preview_only": True, "verified": False,
        "release_authorized": False, "budget_released": False,
        "database_updated": False, "broker_origin_authenticated": False,
    }
    if not isinstance(reservation_id, str) or not reservation_id.strip() or reservation_id != reservation_id.strip():
        return {**base, "reason": "invalid_reservation_id"}
    input_failure = validate_paper_snapshot_inputs(entry_order, exit_orders)
    if input_failure:
        return {**base, "reason": input_failure}
    reservation = get_pro_ticker_reservation(reservation_id)
    if not reservation or reservation.get("status") != "filled":
        return {**base, "reason": "reservation_missing_or_not_filled"}
    entry_id = reservation.get("broker_order_id")
    expected_shares = reservation.get("expected_shares")
    entry = assess_paper_order_fill(
        expected_order_id=entry_id, expected_symbol=reservation["symbol"],
        expected_side="buy", expected_shares=expected_shares,
        broker_order=entry_order,
    )
    if not entry["verified"] or not entry["terminal"] or entry["broker_status"] != "filled":
        return {**base, "reason": "entry_broker_snapshot_not_fully_filled"}
    if entry_order.get("client_order_id") != "pt-" + reservation_id:
        return {**base, "reason": "entry_client_order_id_mismatch"}
    if get_accounted_paper_order_shares(entry_id) != entry["filled_shares"]:
        return {**base, "reason": "entry_accounting_mismatch"}
    linked = review_linked_reservation_paper_shares(reservation_id)
    if linked.get("remaining_attributed_shares") is not None and linked["remaining_attributed_shares"] < 0:
        return {**base, "reason": "negative_reservation_share_balance"}
    if "entry_shares" not in linked:
        return {**base, "reason": linked.get("reason", "linked_share_review_failed")}
    if linked["entry_shares"] != entry["filled_shares"]:
        return {**base, "reason": "linked_entry_shares_mismatch"}
    with get_connection() as conn:
        persisted_exit_ids = {
            row["broker_order_id"] for row in conn.execute(
                "SELECT broker_order_id FROM pro_ticker_paper_exit_links "
                "WHERE reservation_id = ?", (reservation_id,),
            ).fetchall()
        }
    if set(exit_orders) != persisted_exit_ids:
        return {**base, "reason": "exit_snapshots_incomplete_or_extra"}
    total_exits = 0
    for order_id, snapshot in exit_orders.items():
        link = get_pro_ticker_exit_link(order_id)
        if not link or link.get("reservation_id") != reservation_id:
            return {**base, "reason": "exit_ownership_link_missing"}
        from pro_ticker_verified_exit_review import review_verified_paper_exit_link
        reviewed = review_verified_paper_exit_link(link, reservation, snapshot)
        if not reviewed["verified"]:
            return {**base, "reason": reviewed["reason"]}
        if not reviewed["terminal"]:
            return {**base, "reason": "exit_broker_order_not_terminal"}
        if get_accounted_paper_order_shares(order_id) != reviewed["filled_shares"]:
            return {**base, "reason": "exit_accounting_mismatch"}
        total_exits += reviewed["filled_shares"]
    if total_exits > entry["filled_shares"]:
        return {**base, "reason": "broker_exit_shares_exceed_entry"}
    if total_exits != linked["exit_shares"]:
        return {**base, "reason": "exit_snapshots_incomplete_or_mismatched"}
    if linked["remaining_attributed_shares"] != 0:
        return {**base, "reason": "reservation_shares_still_open"}
    # This flag attests only to consistency of caller-provided snapshots,
    # not that this module independently fetched them from Alpaca PAPER.
    return {
        **base, "verified": True,
        "reason": "broker_snapshots_match_persisted_accounting_review_only",
        "entry_shares": entry["filled_shares"],
        "exit_shares": total_exits,
    }
