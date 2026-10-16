"""Durable attribution of Pro Ticker simulated PAPER exit orders.

The caller must independently fetch and verify the PAPER broker order.
No broker submissions, share postings, or budget releases occur here.
"""
from __future__ import annotations
import sqlite3
import math
import uuid
from datetime import datetime, timezone
from typing import Any
from database import get_connection
from pro_ticker_budget_ledger import get_pro_ticker_reservation

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pro_ticker_paper_exit_links (
    reservation_id TEXT NOT NULL,
    broker_order_id TEXT NOT NULL UNIQUE,
    client_order_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    expected_shares INTEGER NOT NULL CHECK(expected_shares > 0),
    linked_at TEXT NOT NULL,
    PRIMARY KEY(reservation_id, broker_order_id)
);
"""


def pro_ticker_exit_client_order_id(reservation_id: str, exit_id: str) -> str:
    """Deterministic identifier for one planned PAPER exit attempt."""
    try:
        if str(uuid.UUID(reservation_id)) != reservation_id:
            raise ValueError("Noncanonical reservation UUID")
        if str(uuid.UUID(exit_id)) != exit_id:
            raise ValueError("Noncanonical exit UUID")
    except (TypeError, AttributeError, ValueError) as error:
        raise ValueError("Canonical reservation and exit UUIDs required.") from error
    return "ptx-" + exit_id


def link_verified_paper_exit(
    *, reservation_id: str, symbol: str, expected_shares: int,
    broker_order: dict[str, Any], exit_id: str,
) -> dict[str, Any]:
    """Bind a preidentified PAPER sell order to a strategy reservation.

    Caller must obtain broker_order directly from the PAPER broker; a client
    request body is not evidence of ownership.
    """
    client_id = pro_ticker_exit_client_order_id(reservation_id, exit_id)
    if not isinstance(broker_order, dict):
        raise ValueError("Verified PAPER broker snapshot required.")
    if isinstance(expected_shares, bool) or not isinstance(expected_shares, int) or expected_shares <= 0:
        raise ValueError("Positive whole-share exit quantity required.")
    if (not isinstance(symbol, str) or not symbol or symbol != symbol.upper()
            or symbol != symbol.strip()):
        raise ValueError("Canonical symbol required.")
    order_id = broker_order.get("id")
    if not isinstance(order_id, str) or not order_id:
        raise ValueError("PAPER broker order ID required.")
    try:
        if str(uuid.UUID(order_id)) != order_id:
            raise ValueError("Noncanonical PAPER broker order UUID.")
    except (ValueError, AttributeError, TypeError) as error:
        raise ValueError("Canonical PAPER broker order UUID required.") from error
    if broker_order.get("client_order_id") != client_id:
        raise ValueError("PAPER exit client order ID mismatch.")
    if str(broker_order.get("symbol") or "") != symbol or str(broker_order.get("side") or "").lower() != "sell":
        raise ValueError("PAPER exit symbol or side mismatch.")
    try:
        quantity = float(broker_order["qty"])
    except (KeyError, ValueError, TypeError, OverflowError) as error:
        raise ValueError("Invalid PAPER exit quantity.") from error
    if not math.isfinite(quantity) or not quantity.is_integer() or quantity != expected_shares:
        raise ValueError("PAPER exit quantity mismatch.")
    if str(broker_order.get("status") or "").lower() not in {
        "new", "accepted", "pending_new", "partially_filled", "filled",
        "pending_cancel", "canceled", "expired", "rejected",
    }:
        raise ValueError("Unknown PAPER exit order status.")
    reservation = get_pro_ticker_reservation(reservation_id)
    if (reservation is None or reservation.get("symbol") != symbol
            or reservation.get("status") not in {"submitted", "filled"}
            or not reservation.get("broker_order_id")):
        raise ValueError("A linked, active PAPER strategy reservation is required.")
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute(
            "SELECT reservation_id, client_order_id, symbol, expected_shares "
            "FROM pro_ticker_paper_exit_links WHERE broker_order_id = ?", (order_id,),
        ).fetchone()
        if previous is not None:
            if tuple(previous) != (reservation_id, client_id, symbol, expected_shares):
                raise ValueError("Conflicting PAPER exit attribution.")
            return {"paper": True, "linked": True, "duplicate": True,
                    "broker_order_id": order_id, "order_submitted": False}
        conn.execute(
            "INSERT INTO pro_ticker_paper_exit_links "
            "(reservation_id, broker_order_id, client_order_id, symbol, expected_shares, linked_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (reservation_id, order_id, client_id, symbol, expected_shares, now),
        )
    return {"paper": True, "linked": True, "duplicate": False,
            "broker_order_id": order_id, "order_submitted": False}


def get_pro_ticker_exit_link(broker_order_id: str) -> dict[str, Any] | None:
    """Retrieve one persisted PAPER exit link; never infer ownership by symbol."""
    if not isinstance(broker_order_id, str):
        raise ValueError("PAPER broker order ID required.")
    try:
        canonical_id = str(uuid.UUID(broker_order_id))
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError("Valid PAPER broker order UUID required.") from error
    if canonical_id != broker_order_id:
        raise ValueError("Canonical PAPER broker order UUID required.")
    with get_connection() as conn:
        conn.executescript(_SCHEMA)
        row = conn.execute(
            "SELECT reservation_id, broker_order_id, client_order_id, symbol, "
            "expected_shares, linked_at FROM pro_ticker_paper_exit_links "
            "WHERE broker_order_id = ?", (broker_order_id,),
        ).fetchone()
    return dict(row) if row is not None else None
