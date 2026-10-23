"""Fetch independently from a caller-supplied PAPER-only order reader.

This read-only adapter never submits orders, changes balances, or treats
caller-supplied order dictionaries as authenticated broker provenance.
"""
from __future__ import annotations

from typing import Any, Callable
from contextlib import closing

from database import get_connection
from pro_ticker_budget_ledger import get_pro_ticker_reservation
from pro_ticker_broker_snapshot_audit import audit_linked_paper_broker_snapshots

PaperOrderReader = Callable[[str], dict[str, Any] | None]


def audit_fetched_paper_reservation(
    reservation_id: str, *, fetch_paper_order: PaperOrderReader,
) -> dict[str, Any]:
    base = {
        "paper": True, "preview_only": True, "verified": False,
        "broker_origin_authenticated": False, "release_authorized": False,
        "budget_released": False, "database_updated": False,
    }
    if not callable(fetch_paper_order):
        return {**base, "reason": "paper_order_reader_required"}
    if not isinstance(reservation_id, str) or not reservation_id.strip() or reservation_id != reservation_id.strip():
        return {**base, "reason": "invalid_reservation_id"}
    try:
        reservation = get_pro_ticker_reservation(reservation_id)
    except ValueError:
        return {**base, "reason": "invalid_reservation_id"}
    if not reservation or not reservation.get("broker_order_id"):
        return {**base, "reason": "reservation_or_entry_order_missing"}
    with closing(get_connection()) as conn:
        # Empty ledgers are expected before the first exit. Do not create
        # tables or claim closure just to satisfy a diagnostic read.
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'pro_ticker_paper_exit_links'"
        ).fetchone()
        if exists is None:
            return {**base, "reason": "paper_exit_ledger_missing"}
        exit_ids = [
            row["broker_order_id"] for row in conn.execute(
                "SELECT broker_order_id FROM pro_ticker_paper_exit_links "
                "WHERE reservation_id = ? ORDER BY broker_order_id",
                (reservation_id,),
            ).fetchall()
        ]
    try:
        entry_order = fetch_paper_order(reservation["broker_order_id"])
        exits = {order_id: fetch_paper_order(order_id) for order_id in exit_ids}
    except Exception:
        return {**base, "reason": "paper_order_fetch_failed"}
    if not isinstance(entry_order, dict) or any(
        not isinstance(order, dict) for order in exits.values()
    ):
        return {**base, "reason": "paper_order_fetch_incomplete"}
    result = audit_linked_paper_broker_snapshots(
        reservation_id, entry_order, exits,
    )
    # A generic callback cannot prove it used an authenticated Alpaca PAPER
    # connection. Never promote its results to release authorization.
    return {**result, "broker_origin_authenticated": False,
            "release_authorized": False, "budget_released": False}
