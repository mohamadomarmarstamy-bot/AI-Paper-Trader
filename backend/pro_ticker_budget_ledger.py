"""Durable virtual PAPER budget reservations; no broker orders are placed here.

A reservation is not a fill. Submitted/filled reservations continue consuming
budget until an independently verified close/reconciliation releases them.
"""
from __future__ import annotations

import math
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from database import get_connection
from pro_ticker_paper_policy import PRO_TICKER_VIRTUAL_BUDGET

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pro_ticker_paper_reservations (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    notional_cents INTEGER NOT NULL CHECK (notional_cents > 0),
    status TEXT NOT NULL CHECK (status IN ('reserved', 'submitted', 'filled', 'released', 'failed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    broker_order_id TEXT,
    note TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS pro_ticker_one_active_reservation_per_symbol
ON pro_ticker_paper_reservations(symbol)
WHERE status IN ('reserved', 'submitted', 'filled');
"""


def _init(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA)


def _cents(amount: float) -> int:
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise ValueError("Notional must be a positive finite number.")
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("Notional must be a positive finite number.")
    cents = round(amount * 100)
    if cents <= 0 or abs(cents / 100 - amount) > 0.000001:
        raise ValueError("Notional must be a positive amount in cents.")
    return cents


def get_pro_ticker_budget() -> dict[str, Any]:
    with get_connection() as conn:
        _init(conn)
        committed = conn.execute(
            "SELECT COALESCE(SUM(notional_cents), 0) FROM pro_ticker_paper_reservations "
            "WHERE status IN ('reserved', 'submitted', 'filled')"
        ).fetchone()[0]
        return {
            "paper": True, "budget": PRO_TICKER_VIRTUAL_BUDGET,
            "committed": committed / 100,
            "available": max(0, round(PRO_TICKER_VIRTUAL_BUDGET - committed / 100, 2)),
            "broker_reconciled": False,
            "order_execution_enabled": False,
        }


def reserve_pro_ticker_budget(symbol: str, amount: float) -> dict[str, Any]:
    """Atomically reserve PAPER notional; duplicate symbols and overspend fail."""
    from pro_ticker_manual_intake import parse_manual_symbols
    symbols = parse_manual_symbols(symbol)
    if len(symbols) != 1:
        raise ValueError("Exactly one valid symbol is required.")
    cents = _cents(amount)
    reservation_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        # DDL outside transaction so the reservation is serialized by BEGIN IMMEDIATE.
        _init(conn)
        conn.execute("BEGIN IMMEDIATE")
        committed = conn.execute(
            "SELECT COALESCE(SUM(notional_cents), 0) FROM pro_ticker_paper_reservations "
            "WHERE status IN ('reserved', 'submitted', 'filled')"
        ).fetchone()[0]
        if committed + cents > round(PRO_TICKER_VIRTUAL_BUDGET * 100):
            raise ValueError("Pro Ticker PAPER virtual budget exceeded.")
        try:
            conn.execute(
                "INSERT INTO pro_ticker_paper_reservations "
                "(id, symbol, notional_cents, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'reserved', ?, ?)",
                (reservation_id, symbols[0], cents, now, now),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("An active reservation already exists for this symbol.") from error
    return {
        "paper": True, "reservation_id": reservation_id,
        "symbol": symbols[0], "reserved": cents / 100,
        "status": "reserved", "order_submitted": False,
    }


def list_pro_ticker_reservations(limit: int = 100) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("Invalid limit.")
    with get_connection() as conn:
        _init(conn)
        rows = conn.execute(
            "SELECT id, symbol, notional_cents, status, created_at, updated_at, "
            "broker_order_id, note FROM pro_ticker_paper_reservations "
            "ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"paper": True, "reservations": [
        {**dict(row), "notional": row["notional_cents"] / 100}
        for row in rows
    ]}
