"""Read-only PAPER broker snapshot cross-check against linked reservation fills.

The caller must supply independently fetched PAPER broker snapshots. This
module cannot authenticate their provenance, submit orders, or release budget.
"""
from __future__ import annotations

from typing import Any

from contextlib import closing
import sqlite3
import uuid

from database import get_connection
from pro_ticker_broker_audit_inputs import validate_paper_snapshot_inputs
from pro_ticker_fill_verification import assess_paper_order_fill


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
    try:
        if str(uuid.UUID(reservation_id)) != reservation_id:
            raise ValueError("Noncanonical reservation UUID")
    except (ValueError, TypeError, AttributeError):
        return {**base, "reason": "invalid_reservation_id"}
    with closing(get_connection()) as conn:
        # No schema initialization or HTTP work inside this read transaction.
        # Every ownership link and cumulative fill is from the same snapshot.
        conn.execute("BEGIN")
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        if not {"pro_ticker_paper_reservations", "pro_ticker_strategy_fill_events",
                "pro_ticker_paper_exit_links"}.issubset(tables):
            return {**base, "reason": "paper_accounting_schema_incomplete"}
        return _audit_snapshot(conn, reservation_id, entry_order, exit_orders, base)


def _audit_snapshot(conn, reservation_id, entry_order, exit_orders, base):
    row = conn.execute(
        "SELECT * FROM pro_ticker_paper_reservations WHERE id = ?", (reservation_id,),
    ).fetchone()
    reservation = dict(row) if row else None
    if not reservation or reservation.get("status") != "filled":
        return {**base, "reason": "reservation_missing_or_not_filled"}
    entry_id = reservation.get("broker_order_id")
    expected_shares = reservation.get("expected_shares")
    entry = assess_paper_order_fill(
        expected_order_id=entry_id, expected_symbol=reservation["symbol"],
        expected_side="buy", expected_shares=expected_shares,
        broker_order=entry_order,
    )
    if (not entry["verified"] or not entry["terminal"] or entry["filled_shares"] <= 0
            or entry["broker_status"] not in {"filled", "canceled", "expired"}):
        return {**base, "reason": "entry_broker_snapshot_not_fully_filled"}
    if entry_order.get("client_order_id") != "pt-" + reservation_id:
        return {**base, "reason": "entry_client_order_id_mismatch"}
    entry_rows = conn.execute(
        "SELECT symbol, side FROM pro_ticker_strategy_fill_events "
        "WHERE broker_order_id = ? GROUP BY symbol, side",
        (entry_id,),
    ).fetchall()
    if len(entry_rows) != 1 or entry_rows[0]["symbol"] != reservation["symbol"] or entry_rows[0]["side"] != "buy":
        return {**base, "reason": "entry_fill_attribution_mismatch"}
    if _accounted(conn, entry_id) != entry["filled_shares"]:
        return {**base, "reason": "entry_accounting_mismatch"}
    links = [dict(row) for row in conn.execute(
        "SELECT * FROM pro_ticker_paper_exit_links WHERE reservation_id = ?",
        (reservation_id,),
    )]
    if any(link["broker_order_id"] == entry_id for link in links):
        return {**base, "reason": "entry_exit_order_id_overlap"}
    if any(link["symbol"] != reservation["symbol"] for link in links):
        return {**base, "reason": "exit_link_symbol_mismatch"}
    persisted_exit_ids = {link["broker_order_id"] for link in links}
    by_id = {link["broker_order_id"]: link for link in links}
    if set(exit_orders) != persisted_exit_ids:
        return {**base, "reason": "exit_snapshots_incomplete_or_extra"}
    total_exits = 0
    for order_id, snapshot in exit_orders.items():
        link = by_id.get(order_id)
        if not link or link.get("reservation_id") != reservation_id:
            return {**base, "reason": "exit_ownership_link_missing"}
        from pro_ticker_verified_exit_review import review_verified_paper_exit_link
        reviewed = review_verified_paper_exit_link(link, reservation, snapshot)
        if not reviewed["verified"]:
            return {**base, "reason": reviewed["reason"]}
        if not reviewed["terminal"]:
            return {**base, "reason": "exit_broker_order_not_terminal"}
        # Total shares alone cannot detect a corrupted fill record posted
        # under the wrong symbol or side. Verify the event attribution too.
        fill_groups = conn.execute(
            "SELECT symbol, side, SUM(shares) AS shares "
            "FROM pro_ticker_strategy_fill_events WHERE broker_order_id = ? "
            "GROUP BY symbol, side",
            (order_id,),
        ).fetchall()
        if fill_groups and (
            len(fill_groups) != 1
            or fill_groups[0]["symbol"] != reservation["symbol"]
            or fill_groups[0]["side"] != "sell"
        ):
            return {**base, "reason": "exit_fill_attribution_mismatch"}
        if _accounted(conn, order_id) != reviewed["filled_shares"]:
            return {**base, "reason": "exit_accounting_mismatch"}
        total_exits += reviewed["filled_shares"]
    if total_exits > entry["filled_shares"]:
        return {**base, "reason": "broker_exit_shares_exceed_entry"}
    if total_exits != entry["filled_shares"]:
        return {**base, "reason": "reservation_shares_still_open"}
    # This flag attests only to consistency of caller-provided snapshots,
    # not that this module independently fetched them from Alpaca PAPER.
    return {
        **base, "verified": True,
        "reason": "broker_snapshots_match_persisted_accounting_review_only",
        "entry_shares": entry["filled_shares"],
        "exit_shares": total_exits,
    }


def _accounted(conn: sqlite3.Connection, order_id: str) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(shares), 0) FROM pro_ticker_strategy_fill_events "
        "WHERE broker_order_id = ?", (order_id,),
    ).fetchone()[0]
