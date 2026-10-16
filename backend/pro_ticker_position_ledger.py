"""Durable, idempotent simulated Pro Ticker PAPER position accounting.

This module never calls a broker. Callers MUST independently verify a PAPER
broker order and strategy ownership before posting any fill. A fill event
represents a NEW increment, not the cumulative broker filled_qty.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from database import get_connection
from pro_ticker_position_accounting import apply_verified_paper_fill

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pro_ticker_strategy_positions (
    symbol TEXT PRIMARY KEY,
    shares INTEGER NOT NULL CHECK(shares >= 0),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pro_ticker_strategy_fill_events (
    event_id TEXT PRIMARY KEY,
    broker_order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('buy','sell')),
    shares INTEGER NOT NULL CHECK(shares > 0),
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pro_ticker_strategy_fill_events_order_idx
ON pro_ticker_strategy_fill_events(broker_order_id);
"""


def _initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def post_verified_paper_fill(
    *,
    event_id: str,
    broker_order_id: str,
    symbol: str,
    side: str,
    filled_shares: int,
    ownership_verified: bool,
    fill_verified: bool,
) -> dict[str, Any]:
    """Atomically post one independently verified, uniquely identified fill.

    Event IDs MUST identify individual broker execution increments; using the
    cumulative filled quantity repeatedly would double-count shares.
    """
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("A stable broker execution event ID is required.")
    if not isinstance(broker_order_id, str) or not broker_order_id.strip():
        raise ValueError("A verified PAPER broker order ID is required.")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("A valid symbol is required.")
    normalized_symbol = symbol.strip().upper()
    if side not in {"buy", "sell"} or isinstance(filled_shares, bool) or not isinstance(filled_shares, int) or filled_shares <= 0:
        raise ValueError("Valid whole-share PAPER fill increment and side required.")
    if ownership_verified is not True or fill_verified is not True:
        raise ValueError("PAPER fill and strategy ownership must be verified.")
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        _initialize(conn)
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute(
            "SELECT broker_order_id, symbol, side, shares "
            "FROM pro_ticker_strategy_fill_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if previous is not None:
            if (previous["broker_order_id"], previous["symbol"], previous["side"], previous["shares"]) != (
                broker_order_id, normalized_symbol, side, filled_shares,
            ):
                raise ValueError("Conflicting reuse of PAPER fill event ID.")
            shares_row = conn.execute(
                "SELECT shares FROM pro_ticker_strategy_positions WHERE symbol = ?",
                (normalized_symbol,),
            ).fetchone()
            return {
                "paper": True, "posted": False, "duplicate": True,
                "symbol": normalized_symbol,
                "current_shares": int(shares_row["shares"]) if shares_row else 0,
            }
        conflicting = conn.execute(
            "SELECT 1 FROM pro_ticker_strategy_fill_events "
            "WHERE broker_order_id = ? AND (symbol != ? OR side != ?) LIMIT 1",
            (broker_order_id, normalized_symbol, side),
        ).fetchone()
        if conflicting is not None:
            raise ValueError("Broker order already attributed to another symbol or side.")
        row = conn.execute(
            "SELECT shares FROM pro_ticker_strategy_positions WHERE symbol = ?",
            (normalized_symbol,),
        ).fetchone()
        current = int(row["shares"]) if row else 0
        decision = apply_verified_paper_fill(
            symbol=normalized_symbol, current_shares=current,
            filled_shares=filled_shares, side=side,
            ownership_verified=ownership_verified, fill_verified=fill_verified,
            already_accounted=False,
        )
        if not decision["accepted"]:
            raise ValueError("PAPER fill accounting blocked: " + decision["reason"])
        conn.execute(
            "INSERT INTO pro_ticker_strategy_fill_events "
            "(event_id, broker_order_id, symbol, side, shares, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, broker_order_id, normalized_symbol, side, filled_shares, now),
        )
        conn.execute(
            "INSERT INTO pro_ticker_strategy_positions(symbol, shares, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
            "shares = excluded.shares, updated_at = excluded.updated_at",
            (normalized_symbol, decision["next_shares"], now),
        )
    return {
        "paper": True, "posted": True, "duplicate": False,
        "symbol": normalized_symbol, "current_shares": decision["next_shares"],
    }


def list_pro_ticker_strategy_positions() -> dict[str, Any]:
    """Return only shares attributed by verified, posted strategy events."""
    with get_connection() as conn:
        _initialize(conn)
        rows = conn.execute(
            "SELECT symbol, shares, updated_at FROM pro_ticker_strategy_positions "
            "WHERE shares > 0 ORDER BY symbol"
        ).fetchall()
    return {
        "paper": True, "broker_reconciled": False,
        "positions": [dict(row) for row in rows],
    }


def post_verified_cumulative_paper_fill(
    *,
    broker_order_id: str,
    symbol: str,
    side: str,
    cumulative_filled_shares: int,
    ownership_verified: bool,
    fill_verified: bool,
) -> dict[str, Any]:
    """Atomically account only NEW shares in a verified broker order snapshot.

    Repeated or stale cumulative snapshots cannot double-count shares. Caller
    MUST independently verify PAPER order identity, quantity and ownership.
    This does not place orders or authorize any budget release.
    """
    if not isinstance(broker_order_id, str) or not broker_order_id.strip():
        raise ValueError("Verified PAPER broker order ID required.")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Valid symbol required.")
    if side not in {"buy", "sell"}:
        raise ValueError("Invalid PAPER order side.")
    if (
        isinstance(cumulative_filled_shares, bool)
        or not isinstance(cumulative_filled_shares, int)
        or cumulative_filled_shares < 0
    ):
        raise ValueError("Invalid cumulative fill quantity.")
    if ownership_verified is not True or fill_verified is not True:
        raise ValueError("PAPER order ownership and fill must be verified.")
    normalized_symbol = symbol.strip().upper()
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        _initialize(conn)
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT symbol, side, shares FROM pro_ticker_strategy_fill_events "
            "WHERE broker_order_id = ?",
            (broker_order_id,),
        ).fetchall()
        if any(row["symbol"] != normalized_symbol or row["side"] != side for row in existing):
            raise ValueError("Broker order already attributed to another symbol or side.")
        accounted = sum(int(row["shares"]) for row in existing)
        if cumulative_filled_shares < accounted:
            return {
                "paper": True, "posted": False, "stale_snapshot": True,
                "delta_shares": 0, "accounted_shares": accounted,
            }
        delta = cumulative_filled_shares - accounted
        if delta == 0:
            return {
                "paper": True, "posted": False, "stale_snapshot": False,
                "delta_shares": 0, "accounted_shares": accounted,
            }
        position = conn.execute(
            "SELECT shares FROM pro_ticker_strategy_positions WHERE symbol = ?",
            (normalized_symbol,),
        ).fetchone()
        current = int(position["shares"]) if position else 0
        decision = apply_verified_paper_fill(
            symbol=normalized_symbol, current_shares=current,
            filled_shares=delta, side=side,
            ownership_verified=True, fill_verified=True, already_accounted=False,
        )
        if not decision["accepted"]:
            raise ValueError("PAPER fill accounting blocked: " + decision["reason"])
        # The event ID is deterministic per broker order and cumulative fill
        # level; a replay of the same snapshot is idempotent.
        event_id = f"cumulative:{broker_order_id}:{cumulative_filled_shares}"
        conn.execute(
            "INSERT INTO pro_ticker_strategy_fill_events "
            "(event_id, broker_order_id, symbol, side, shares, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, broker_order_id, normalized_symbol, side, delta, now),
        )
        conn.execute(
            "INSERT INTO pro_ticker_strategy_positions(symbol, shares, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
            "shares = excluded.shares, updated_at = excluded.updated_at",
            (normalized_symbol, decision["next_shares"], now),
        )
    return {
        "paper": True, "posted": True, "stale_snapshot": False,
        "delta_shares": delta, "accounted_shares": cumulative_filled_shares,
        "current_shares": decision["next_shares"],
    }


def get_accounted_paper_order_shares(broker_order_id: str) -> int:
    """Read total recorded incremental fills for one linked PAPER broker order."""
    if not isinstance(broker_order_id, str) or not broker_order_id.strip():
        raise ValueError("A PAPER broker order ID is required.")
    with get_connection() as conn:
        _initialize(conn)
        row = conn.execute(
            "SELECT COALESCE(SUM(shares), 0) FROM pro_ticker_strategy_fill_events "
            "WHERE broker_order_id = ?",
            (broker_order_id,),
        ).fetchone()
    return int(row[0])
