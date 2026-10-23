"""Manual Pro Ticker research intake; intentionally never submits broker orders.

Manual submissions are observations, not verified original Pro Ticker alerts.
Capture timestamps now; obtain market indicators separately at observation time.
"""
from __future__ import annotations

import re
import json
from datetime import datetime, timezone
from typing import Any, Callable

from database import get_connection

_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_MAX_BATCH = 100


def parse_manual_symbols(raw: str) -> list[str]:
    """Normalize a comma/space/newline-separated batch; reject malformed input."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Enter at least one stock symbol.")
    tokens = re.split(r"[,;\s]+", raw.strip())
    if len(tokens) > _MAX_BATCH:
        raise ValueError("A batch cannot contain more than 100 symbols.")
    symbols: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        symbol = token.upper()
        if not _SYMBOL.fullmatch(symbol):
            raise ValueError(f"Invalid stock symbol: {token!r}")
        if symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def save_manual_observations(
    raw: str,
    quote_fetcher: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist manually discovered tickers with their actual observation time.

    This is research-only. No order placement, simulated fills, or claim that
    an original alert was issued at the observation timestamp.
    """
    symbols = parse_manual_symbols(raw)
    observed_at = datetime.now(timezone.utc).isoformat()
    snapshots: dict[str, dict[str, Any]] = {}
    if quote_fetcher is not None:
        for symbol in symbols:
            try:
                quote = quote_fetcher(symbol)
                if not isinstance(quote, dict):
                    raise ValueError("Invalid market-data response")
                snapshots[symbol] = {
                    "source": "alpaca_latest_trade",
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "market_timestamp": quote.get("t"),
                    "price": quote.get("p"),
                    "size": quote.get("s"),
                    "conditions": quote.get("c"),
                    "status": "captured" if quote.get("p") is not None and quote.get("t") else "unavailable",
                }
            except Exception:
                snapshots[symbol] = {
                    "source": "alpaca_latest_trade",
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "status": "unavailable",
                }
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pro_ticker_manual_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual_pro_ticker',
                observed_at TEXT NOT NULL,
                indicator_snapshot_json TEXT,
                order_status TEXT NOT NULL DEFAULT 'research_only',
                CHECK (source = 'manual_pro_ticker')
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO pro_ticker_manual_observations
                (symbol, observed_at, indicator_snapshot_json)
            VALUES (?, ?, ?)
            """,
            [(symbol, observed_at, json.dumps(snapshots[symbol]) if symbol in snapshots else None) for symbol in symbols],
        )
    return {
        "paper": True,
        "research_only": True,
        "orders_submitted": 0,
        "observed_at": observed_at,
        "symbols": symbols,
        "count": len(symbols),
        "market_snapshots_captured": sum(1 for v in snapshots.values() if v["status"] == "captured"),
    }


def list_manual_observations(limit: int = 100) -> dict[str, Any]:
    """Return the latest saved manual research observations, newest first."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("Limit must be between 1 and 500.")
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pro_ticker_manual_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual_pro_ticker',
                observed_at TEXT NOT NULL,
                indicator_snapshot_json TEXT,
                order_status TEXT NOT NULL DEFAULT 'research_only',
                CHECK (source = 'manual_pro_ticker')
            )
            """
        )
        rows = connection.execute(
            """
            SELECT id, symbol, source, observed_at, order_status, indicator_snapshot_json
            FROM pro_ticker_manual_observations
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return {
        "paper": True,
        "research_only": True,
        "count": len(rows),
        "observations": [
            {**dict(row), "market_snapshot": json.loads(row["indicator_snapshot_json"]) if row["indicator_snapshot_json"] else None}
            for row in rows
        ],
    }
