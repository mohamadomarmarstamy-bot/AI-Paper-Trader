"""Read-only reservation-specific PAPER share review using persisted order links.

This diagnostic never asserts broker reconciliation or authorizes budget release.
"""
from __future__ import annotations

from typing import Any

from database import get_connection
from pro_ticker_budget_ledger import get_pro_ticker_reservation
from pro_ticker_position_ledger import review_reservation_attributed_shares


def review_linked_reservation_paper_shares(reservation_id: str) -> dict[str, Any]:
    base = {
        "paper": True, "preview_only": True, "broker_reconciled": False,
        "release_authorized": False, "budget_released": False,
        "database_updated": False,
    }
    reservation = get_pro_ticker_reservation(reservation_id)
    if reservation is None:
        return {**base, "reason": "reservation_missing"}
    entry_id = reservation.get("broker_order_id")
    if not isinstance(entry_id, str) or not entry_id:
        return {**base, "reason": "entry_order_unlinked"}
    with get_connection() as conn:
        links = conn.execute(
            "SELECT broker_order_id, symbol FROM pro_ticker_paper_exit_links "
            "WHERE reservation_id = ? ORDER BY broker_order_id",
            (reservation_id,),
        ).fetchall()
    if len({link["broker_order_id"] for link in links}) != len(links):
        return {**base, "reason": "duplicate_exit_order_links"}
    if any(link["broker_order_id"] == entry_id for link in links):
        return {**base, "reason": "entry_exit_order_id_overlap"}
    if any(link["symbol"] != reservation["symbol"] for link in links):
        return {**base, "reason": "exit_link_symbol_mismatch"}
    result = review_reservation_attributed_shares(
        reservation_id=reservation_id, entry_order_id=entry_id,
        exit_order_ids=[link["broker_order_id"] for link in links],
    )
    if result.get("symbol") is not None and result["symbol"] != reservation["symbol"]:
        return {**base, "reason": "entry_fill_symbol_mismatch"}
    if result.get("entry_shares") is not None and result.get("exit_shares") is not None:
        if result["exit_shares"] > result["entry_shares"]:
            return {
                **result, **base, "reason": "attributed_exit_exceeds_entry",
                "verified": False,
            }
    return {**result, **base}
