import json
import math
import os
import sqlite3
from pathlib import Path
from typing import Any


LOCAL_DATABASE_PATH = (
    Path(__file__).resolve().parent
    / "trader.db"
)

PERSISTENT_DATABASE_PATH = Path(
    "/data/trader.db"
)

DATABASE_PATH = Path(
    os.getenv(
        "DATABASE_PATH",
        str(
            PERSISTENT_DATABASE_PATH
            if Path("/data").exists()
            else LOCAL_DATABASE_PATH
        ),
    )
)

DEFAULT_STARTING_CASH = 100_000.0
SQLITE_TIMEOUT_SECONDS = 30


def get_connection() -> sqlite3.Connection:
    """
    Create and configure a SQLite database connection.

    A new connection is opened for each database operation.
    """
    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=SQLITE_TIMEOUT_SECONDS,
    )

    connection.row_factory = sqlite3.Row

    # Wait briefly instead of immediately failing when the database is busy.
    connection.execute("PRAGMA busy_timeout = 30000")

    # Enforce any foreign-key constraints added now or in the future.
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def _normalize_symbol(symbol: str) -> str:
    """Normalize and validate a stock symbol."""
    if not isinstance(symbol, str):
        raise ValueError("Symbol must be a string.")

    normalized = symbol.strip().upper()

    if not normalized:
        raise ValueError("Symbol cannot be empty.")

    return normalized


def _normalize_action(action: str) -> str:
    """Normalize and validate a trade action."""
    if not isinstance(action, str):
        raise ValueError("Action must be a string.")

    normalized = action.strip().upper()

    if normalized not in {"BUY", "SELL"}:
        raise ValueError("Action must be either BUY or SELL.")

    return normalized


def _validate_positive_integer(
    value: int,
    field_name: str,
) -> int:
    """Validate a positive whole-number value."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer.")

    if value <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")

    return value


def _validate_non_negative_integer(
    value: int,
    field_name: str,
) -> int:
    """Validate a non-negative whole-number value."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer.")

    if value < 0:
        raise ValueError(f"{field_name} cannot be negative.")

    return value


def _validate_finite_number(
    value: float,
    field_name: str,
    *,
    allow_zero: bool = True,
) -> float:
    """Validate that a value is a finite number."""
    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{field_name} must be a valid number."
        ) from error

    if not math.isfinite(numeric_value):
        raise ValueError(f"{field_name} must be finite.")

    if allow_zero:
        if numeric_value < 0:
            raise ValueError(f"{field_name} cannot be negative.")
    elif numeric_value <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")

    return numeric_value


def _validate_timestamp(timestamp: str) -> str:
    """Validate a stored timestamp string."""
    if not isinstance(timestamp, str):
        raise ValueError("Timestamp must be a string.")

    normalized = timestamp.strip()

    if not normalized:
        raise ValueError("Timestamp cannot be empty.")

    return normalized


def _normalize_optional_text(
    value: Any,
    field_name: str,
) -> str | None:
    """Normalize optional text values before storage."""
    if value is None:
        return None

    normalized = str(value).strip()

    if not normalized:
        return None

    return normalized


def _normalize_trade_book_status(status: str) -> str:
    """Normalize and validate a trade-book lifecycle status."""
    if not isinstance(status, str):
        raise ValueError("Trade-book status must be a string.")

    normalized = status.strip().upper()

    if normalized not in {"OPEN", "CLOSED"}:
        raise ValueError(
            "Trade-book status must be OPEN or CLOSED."
        )

    return normalized


def _serialize_json_object(value: dict[str, Any] | None) -> str:
    """Serialize structured metadata for SQLite storage."""
    payload = value if isinstance(value, dict) else {}

    return json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def _deserialize_json_object(value: Any) -> dict[str, Any]:
    """Safely deserialize structured metadata from SQLite."""
    if value is None:
        return {}

    try:
        payload = json.loads(str(value))
    except (TypeError, ValueError):
        return {}

    return payload if isinstance(payload, dict) else {}


def initialize_database() -> None:
    """Create all required database tables and indexes."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    with get_connection() as connection:
        # WAL mode improves reliability when multiple API requests access
        # SQLite around the same time.
        connection.execute("PRAGMA journal_mode = WAL")

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                shares INTEGER NOT NULL,
                price REAL NOT NULL,
                action TEXT NOT NULL
                    CHECK(action IN ('BUY', 'SELL')),
                timestamp TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                shares INTEGER NOT NULL,
                average_cost REAL NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                value REAL NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_book (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK(status IN ('OPEN', 'CLOSED')),
                shares REAL NOT NULL,
                entry_price REAL NOT NULL,
                entry_timestamp TEXT NOT NULL,
                exit_price REAL,
                exit_timestamp TEXT,
                realized_profit_loss REAL,
                realized_return_percent REAL,
                entry_order_id TEXT,
                exit_order_id TEXT,
                entry_reason TEXT,
                exit_reason TEXT,
                strategy TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_book_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_book_id INTEGER,
                symbol TEXT NOT NULL,
                event TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(trade_book_id)
                    REFERENCES trade_book(id)
                    ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_book_id INTEGER UNIQUE,
                symbol TEXT NOT NULL,
                entry_score REAL,
                entry_confidence REAL,
                entry_signal TEXT,
                scanner_rank REAL,
                spread_percent REAL,
                stop_loss_percent REAL,
                take_profit_percent REAL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                shares REAL NOT NULL,
                realized_profit_loss REAL NOT NULL,
                realized_return_percent REAL NOT NULL,
                exit_reason TEXT,
                holding_seconds REAL,
                won INTEGER NOT NULL
                    CHECK(won IN (0, 1)),
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(trade_book_id)
                    REFERENCES trade_book(id)
                    ON DELETE SET NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recommendation_type TEXT NOT NULL,
                message TEXT NOT NULL,
                confidence TEXT NOT NULL,
                sample_size INTEGER NOT NULL,
                supporting_data_json TEXT NOT NULL DEFAULT '{}',
                active INTEGER NOT NULL DEFAULT 1
                    CHECK(active IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS account (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                cash REAL NOT NULL
            )
            """
        )

        connection.execute(
            """
            INSERT OR IGNORE INTO account (id, cash)
            VALUES (1, ?)
            """,
            (DEFAULT_STARTING_CASH,),
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trades_symbol
            ON trades(symbol)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trades_timestamp
            ON trades(timestamp)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_portfolio_history_timestamp
            ON portfolio_history(timestamp)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trade_book_symbol
            ON trade_book(symbol)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trade_book_status
            ON trade_book(status)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trade_book_entry_timestamp
            ON trade_book(entry_timestamp)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trade_book_exit_timestamp
            ON trade_book(exit_timestamp)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trade_book_entry_order_id
            ON trade_book(entry_order_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trade_book_exit_order_id
            ON trade_book(exit_order_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trade_book_events_trade_id
            ON trade_book_events(trade_book_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trade_book_events_symbol
            ON trade_book_events(symbol)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trade_book_events_timestamp
            ON trade_book_events(timestamp)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learning_outcomes_symbol
            ON learning_outcomes(symbol)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learning_outcomes_created_at
            ON learning_outcomes(created_at)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learning_recommendations_active
            ON learning_recommendations(active)
            """
        )


def save_trade(
    symbol: str,
    shares: int,
    price: float,
    action: str,
    timestamp: str,
) -> int:
    """Save a completed paper trade and return its database ID."""
    normalized_symbol = _normalize_symbol(symbol)
    normalized_shares = _validate_positive_integer(
        shares,
        "Shares",
    )
    normalized_price = _validate_finite_number(
        price,
        "Price",
        allow_zero=False,
    )
    normalized_action = _normalize_action(action)
    normalized_timestamp = _validate_timestamp(timestamp)

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO trades (
                symbol,
                shares,
                price,
                action,
                timestamp
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                normalized_symbol,
                normalized_shares,
                normalized_price,
                normalized_action,
                normalized_timestamp,
            ),
        )

        trade_id = cursor.lastrowid

    if trade_id is None:
        raise RuntimeError("The trade was saved without an ID.")

    return int(trade_id)


def load_trades() -> list[dict[str, Any]]:
    """Load all completed trades in the order they were saved."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                symbol,
                shares,
                price,
                action,
                timestamp
            FROM trades
            ORDER BY id ASC
            """
        ).fetchall()

    return [
        {
            "id": int(row["id"]),
            "symbol": str(row["symbol"]),
            "shares": int(row["shares"]),
            "price": float(row["price"]),
            "action": str(row["action"]),
            "timestamp": str(row["timestamp"]),
        }
        for row in rows
    ]


def save_position(
    symbol: str,
    shares: int,
    average_cost: float,
) -> None:
    """
    Create or update an open position.

    Positions with zero shares are removed rather than stored.
    """
    normalized_symbol = _normalize_symbol(symbol)
    normalized_shares = _validate_non_negative_integer(
        shares,
        "Shares",
    )

    if normalized_shares == 0:
        delete_position(normalized_symbol)
        return

    normalized_average_cost = _validate_finite_number(
        average_cost,
        "Average cost",
        allow_zero=False,
    )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO positions (
                symbol,
                shares,
                average_cost
            )
            VALUES (?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                shares = excluded.shares,
                average_cost = excluded.average_cost
            """,
            (
                normalized_symbol,
                normalized_shares,
                normalized_average_cost,
            ),
        )


def delete_position(symbol: str) -> None:
    """Remove an open position from the database."""
    normalized_symbol = _normalize_symbol(symbol)

    with get_connection() as connection:
        connection.execute(
            """
            DELETE FROM positions
            WHERE symbol = ?
            """,
            (normalized_symbol,),
        )


def load_positions() -> dict[str, dict[str, float | int]]:
    """Load all open positions, keyed by stock symbol."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                symbol,
                shares,
                average_cost
            FROM positions
            ORDER BY symbol ASC
            """
        ).fetchall()

    positions: dict[str, dict[str, float | int]] = {}

    for row in rows:
        symbol = str(row["symbol"])

        positions[symbol] = {
            "shares": int(row["shares"]),
            "average_cost": float(row["average_cost"]),
        }

    return positions


def save_portfolio_snapshot(
    timestamp: str,
    value: float,
) -> int:
    """Save a portfolio-value snapshot and return its database ID."""
    normalized_timestamp = _validate_timestamp(timestamp)
    normalized_value = _validate_finite_number(
        value,
        "Portfolio value",
    )

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO portfolio_history (
                timestamp,
                value
            )
            VALUES (?, ?)
            """,
            (
                normalized_timestamp,
                normalized_value,
            ),
        )

        snapshot_id = cursor.lastrowid

    if snapshot_id is None:
        raise RuntimeError(
            "The portfolio snapshot was saved without an ID."
        )

    return int(snapshot_id)


def load_portfolio_history() -> list[dict[str, Any]]:
    """Load saved portfolio-value history."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                timestamp AS time,
                value
            FROM portfolio_history
            ORDER BY id ASC
            """
        ).fetchall()

    return [
        {
            "time": str(row["time"]),
            "value": float(row["value"]),
        }
        for row in rows
    ]


def save_cash(cash: float) -> None:
    """
    Save the account cash balance.

    Uses an upsert so the account row is restored if it is ever missing.
    """
    normalized_cash = _validate_finite_number(
        cash,
        "Cash",
    )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO account (id, cash)
            VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET
                cash = excluded.cash
            """,
            (normalized_cash,),
        )


def load_cash(
    default_cash: float = DEFAULT_STARTING_CASH,
) -> float:
    """Load the account cash balance."""
    normalized_default = _validate_finite_number(
        default_cash,
        "Default cash",
    )

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT cash
            FROM account
            WHERE id = 1
            """
        ).fetchone()

        if row is None:
            connection.execute(
                """
                INSERT INTO account (id, cash)
                VALUES (1, ?)
                """,
                (normalized_default,),
            )

            return normalized_default

    return float(row["cash"])

# =========================================================
# Trade book
# =========================================================

def create_trade_book_entry(
    *,
    symbol: str,
    shares: float,
    entry_price: float,
    entry_timestamp: str,
    created_at: str,
    entry_order_id: str | None = None,
    entry_reason: str | None = None,
    strategy: str | None = None,
) -> int:
    """Create an OPEN trade-book record and return its ID."""
    normalized_symbol = _normalize_symbol(symbol)
    normalized_shares = _validate_finite_number(
        shares,
        "Shares",
        allow_zero=False,
    )
    normalized_entry_price = _validate_finite_number(
        entry_price,
        "Entry price",
        allow_zero=False,
    )
    normalized_entry_timestamp = _validate_timestamp(
        entry_timestamp
    )
    normalized_created_at = _validate_timestamp(created_at)

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO trade_book (
                symbol,
                status,
                shares,
                entry_price,
                entry_timestamp,
                entry_order_id,
                entry_reason,
                strategy,
                created_at,
                updated_at
            )
            VALUES (?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_symbol,
                normalized_shares,
                normalized_entry_price,
                normalized_entry_timestamp,
                _normalize_optional_text(
                    entry_order_id,
                    "Entry order ID",
                ),
                _normalize_optional_text(
                    entry_reason,
                    "Entry reason",
                ),
                _normalize_optional_text(
                    strategy,
                    "Strategy",
                ),
                normalized_created_at,
                normalized_created_at,
            ),
        )
        trade_book_id = cursor.lastrowid

    if trade_book_id is None:
        raise RuntimeError(
            "The trade-book entry was saved without an ID."
        )

    return int(trade_book_id)


def load_trade_book_entry(
    trade_book_id: int,
) -> dict[str, Any] | None:
    """Load one trade-book record by ID."""
    normalized_id = _validate_positive_integer(
        trade_book_id,
        "Trade-book ID",
    )

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM trade_book
            WHERE id = ?
            """,
            (normalized_id,),
        ).fetchone()

    return dict(row) if row is not None else None


def load_open_trade_book_entry(
    symbol: str,
) -> dict[str, Any] | None:
    """Load the newest OPEN trade-book record for a symbol."""
    normalized_symbol = _normalize_symbol(symbol)

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM trade_book
            WHERE symbol = ?
              AND status = 'OPEN'
            ORDER BY id DESC
            LIMIT 1
            """,
            (normalized_symbol,),
        ).fetchone()

    return dict(row) if row is not None else None


def load_trade_book(
    *,
    status: str | None = None,
    symbol: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Load trade-book records newest first."""
    safe_limit = max(1, min(int(limit), 5000))
    clauses: list[str] = []
    params: list[Any] = []

    if status is not None:
        clauses.append("status = ?")
        params.append(_normalize_trade_book_status(status))

    if symbol is not None:
        clauses.append("symbol = ?")
        params.append(_normalize_symbol(symbol))

    where_sql = (
        " WHERE " + " AND ".join(clauses)
        if clauses
        else ""
    )
    params.append(safe_limit)

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM trade_book
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

    return [dict(row) for row in rows]


def update_trade_book_entry(
    trade_book_id: int,
    *,
    shares: float | None = None,
    entry_price: float | None = None,
    entry_timestamp: str | None = None,
    entry_order_id: str | None = None,
    entry_reason: str | None = None,
    strategy: str | None = None,
    updated_at: str,
) -> None:
    """Update editable fields on an OPEN trade-book record."""
    normalized_id = _validate_positive_integer(
        trade_book_id,
        "Trade-book ID",
    )
    normalized_updated_at = _validate_timestamp(updated_at)

    updates: list[str] = ["updated_at = ?"]
    params: list[Any] = [normalized_updated_at]

    if shares is not None:
        updates.append("shares = ?")
        params.append(
            _validate_finite_number(
                shares,
                "Shares",
                allow_zero=False,
            )
        )

    if entry_price is not None:
        updates.append("entry_price = ?")
        params.append(
            _validate_finite_number(
                entry_price,
                "Entry price",
                allow_zero=False,
            )
        )

    if entry_timestamp is not None:
        updates.append("entry_timestamp = ?")
        params.append(_validate_timestamp(entry_timestamp))

    if entry_order_id is not None:
        updates.append("entry_order_id = ?")
        params.append(_normalize_optional_text(entry_order_id, "Entry order ID"))

    if entry_reason is not None:
        updates.append("entry_reason = ?")
        params.append(_normalize_optional_text(entry_reason, "Entry reason"))

    if strategy is not None:
        updates.append("strategy = ?")
        params.append(_normalize_optional_text(strategy, "Strategy"))

    params.append(normalized_id)

    with get_connection() as connection:
        cursor = connection.execute(
            f"""
            UPDATE trade_book
            SET {", ".join(updates)}
            WHERE id = ?
              AND status = 'OPEN'
            """,
            tuple(params),
        )
        if cursor.rowcount == 0:
            raise ValueError("Open trade-book entry was not found.")


def close_trade_book_entry(
    trade_book_id: int,
    *,
    exit_price: float,
    exit_timestamp: str,
    updated_at: str,
    exit_order_id: str | None = None,
    exit_reason: str | None = None,
) -> dict[str, Any]:
    """Close an OPEN trade-book record and calculate realized P/L."""
    normalized_id = _validate_positive_integer(trade_book_id, "Trade-book ID")
    normalized_exit_price = _validate_finite_number(
        exit_price,
        "Exit price",
        allow_zero=False,
    )
    normalized_exit_timestamp = _validate_timestamp(exit_timestamp)
    normalized_updated_at = _validate_timestamp(updated_at)

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM trade_book
            WHERE id = ?
              AND status = 'OPEN'
            """,
            (normalized_id,),
        ).fetchone()

        if row is None:
            raise ValueError("Open trade-book entry was not found.")

        shares = float(row["shares"])
        entry_price = float(row["entry_price"])
        realized_profit_loss = (normalized_exit_price - entry_price) * shares
        cost_basis = entry_price * shares
        realized_return_percent = (
            (realized_profit_loss / cost_basis) * 100
            if cost_basis > 0
            else 0.0
        )

        connection.execute(
            """
            UPDATE trade_book
            SET
                status = 'CLOSED',
                exit_price = ?,
                exit_timestamp = ?,
                realized_profit_loss = ?,
                realized_return_percent = ?,
                exit_order_id = ?,
                exit_reason = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                normalized_exit_price,
                normalized_exit_timestamp,
                round(realized_profit_loss, 4),
                round(realized_return_percent, 6),
                _normalize_optional_text(exit_order_id, "Exit order ID"),
                _normalize_optional_text(exit_reason, "Exit reason"),
                normalized_updated_at,
                normalized_id,
            ),
        )

    result = load_trade_book_entry(normalized_id)
    if result is None:
        raise RuntimeError("Closed trade-book entry could not be reloaded.")
    return result


def record_trade_book_event(
    *,
    symbol: str,
    event: str,
    timestamp: str,
    trade_book_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> int:
    """Record an important lifecycle or diagnostic event."""
    normalized_symbol = _normalize_symbol(symbol)
    normalized_event = _normalize_optional_text(event, "Event")
    if normalized_event is None:
        raise ValueError("Event cannot be empty.")
    normalized_timestamp = _validate_timestamp(timestamp)

    normalized_trade_book_id = None
    if trade_book_id is not None:
        normalized_trade_book_id = _validate_positive_integer(
            trade_book_id,
            "Trade-book ID",
        )

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO trade_book_events (
                trade_book_id,
                symbol,
                event,
                timestamp,
                details_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                normalized_trade_book_id,
                normalized_symbol,
                normalized_event,
                normalized_timestamp,
                _serialize_json_object(details),
            ),
        )
        event_id = cursor.lastrowid

    if event_id is None:
        raise RuntimeError("The trade-book event was saved without an ID.")
    return int(event_id)


def load_trade_book_events(
    *,
    trade_book_id: int | None = None,
    symbol: str | None = None,
    event: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Load saved trade-book events newest first."""
    safe_limit = max(1, min(int(limit), 5000))
    clauses: list[str] = []
    params: list[Any] = []

    if trade_book_id is not None:
        clauses.append("trade_book_id = ?")
        params.append(_validate_positive_integer(trade_book_id, "Trade-book ID"))

    if symbol is not None:
        clauses.append("symbol = ?")
        params.append(_normalize_symbol(symbol))

    if event is not None:
        normalized_event = str(event).strip().lower()
        if not normalized_event:
            raise ValueError("Event cannot be empty.")

        clauses.append("event = ?")
        params.append(normalized_event)

    where_sql = (
        " WHERE " + " AND ".join(clauses)
        if clauses
        else ""
    )
    params.append(safe_limit)

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM trade_book_events
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["details"] = _deserialize_json_object(
            item.pop("details_json", "{}")
        )
        results.append(item)
    return results


# =========================================================
# Learning memory
# =========================================================

def save_learning_outcome(
    *,
    symbol: str,
    entry_price: float,
    exit_price: float,
    shares: float,
    realized_profit_loss: float,
    realized_return_percent: float,
    created_at: str,
    trade_book_id: int | None = None,
    entry_score: float | None = None,
    entry_confidence: float | None = None,
    entry_signal: str | None = None,
    scanner_rank: float | None = None,
    spread_percent: float | None = None,
    stop_loss_percent: float | None = None,
    take_profit_percent: float | None = None,
    exit_reason: str | None = None,
    holding_seconds: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    """Store evidence for the learning engine without changing strategy settings."""
    normalized_symbol = _normalize_symbol(symbol)
    normalized_entry_price = _validate_finite_number(entry_price, "Entry price", allow_zero=False)
    normalized_exit_price = _validate_finite_number(exit_price, "Exit price", allow_zero=False)
    normalized_shares = _validate_finite_number(shares, "Shares", allow_zero=False)
    normalized_pl = float(realized_profit_loss)
    normalized_return = float(realized_return_percent)
    if not math.isfinite(normalized_pl):
        raise ValueError("Realized profit/loss must be finite.")
    if not math.isfinite(normalized_return):
        raise ValueError("Realized return percent must be finite.")
    normalized_created_at = _validate_timestamp(created_at)

    normalized_trade_book_id = None
    if trade_book_id is not None:
        normalized_trade_book_id = _validate_positive_integer(trade_book_id, "Trade-book ID")

    def optional_number(value: float | None, field_name: str) -> float | None:
        if value is None:
            return None
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"{field_name} must be finite.")
        return numeric

    won = 1 if normalized_pl > 0 else 0
    values = (
        normalized_trade_book_id,
        normalized_symbol,
        optional_number(entry_score, "Entry score"),
        optional_number(entry_confidence, "Entry confidence"),
        _normalize_optional_text(entry_signal, "Entry signal"),
        optional_number(scanner_rank, "Scanner rank"),
        optional_number(spread_percent, "Spread percent"),
        optional_number(stop_loss_percent, "Stop-loss percent"),
        optional_number(take_profit_percent, "Take-profit percent"),
        normalized_entry_price,
        normalized_exit_price,
        normalized_shares,
        normalized_pl,
        normalized_return,
        _normalize_optional_text(exit_reason, "Exit reason"),
        optional_number(holding_seconds, "Holding seconds"),
        won,
        _serialize_json_object(metadata),
        normalized_created_at,
    )

    with get_connection() as connection:
        existing = None
        if normalized_trade_book_id is not None:
            existing = connection.execute(
                "SELECT id FROM learning_outcomes WHERE trade_book_id = ?",
                (normalized_trade_book_id,),
            ).fetchone()

        if existing is None:
            cursor = connection.execute(
                """
                INSERT INTO learning_outcomes (
                    trade_book_id, symbol, entry_score, entry_confidence,
                    entry_signal, scanner_rank, spread_percent,
                    stop_loss_percent, take_profit_percent, entry_price,
                    exit_price, shares, realized_profit_loss,
                    realized_return_percent, exit_reason, holding_seconds,
                    won, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            outcome_id = cursor.lastrowid
        else:
            outcome_id = int(existing["id"])
            connection.execute(
                """
                UPDATE learning_outcomes
                SET
                    symbol = ?, entry_score = ?, entry_confidence = ?,
                    entry_signal = ?, scanner_rank = ?, spread_percent = ?,
                    stop_loss_percent = ?, take_profit_percent = ?,
                    entry_price = ?, exit_price = ?, shares = ?,
                    realized_profit_loss = ?, realized_return_percent = ?,
                    exit_reason = ?, holding_seconds = ?, won = ?,
                    metadata_json = ?, created_at = ?
                WHERE id = ?
                """,
                (normalized_symbol, *values[2:], outcome_id),
            )

    if outcome_id is None:
        raise RuntimeError("The learning outcome was saved without an ID.")
    return int(outcome_id)


def load_learning_outcomes(
    *,
    symbol: str | None = None,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Load completed learning outcomes newest first."""
    safe_limit = max(1, min(int(limit), 10000))
    params: list[Any] = []
    where_sql = ""
    if symbol is not None:
        where_sql = " WHERE symbol = ?"
        params.append(_normalize_symbol(symbol))
    params.append(safe_limit)

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM learning_outcomes
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["won"] = bool(item.get("won"))
        item["metadata"] = _deserialize_json_object(
            item.pop("metadata_json", "{}")
        )
        results.append(item)
    return results


def calculate_learning_summary(
    *,
    minimum_required: int = 10,
) -> dict[str, Any]:
    """Summarize learning evidence without mutating strategy settings."""
    outcomes = load_learning_outcomes(limit=10000)
    completed = len(outcomes)
    minimum = max(1, int(minimum_required))

    if completed == 0:
        return {
            "completed_trades": 0,
            "minimum_required": minimum,
            "enough_data": False,
            "wins": 0,
            "losses": 0,
            "win_rate_percent": 0.0,
            "average_return_percent": 0.0,
            "average_profit_loss": 0.0,
        }

    wins = sum(1 for outcome in outcomes if bool(outcome.get("won")))
    losses = completed - wins
    average_return = sum(
        float(outcome.get("realized_return_percent", 0.0) or 0.0)
        for outcome in outcomes
    ) / completed
    average_profit_loss = sum(
        float(outcome.get("realized_profit_loss", 0.0) or 0.0)
        for outcome in outcomes
    ) / completed

    return {
        "completed_trades": completed,
        "minimum_required": minimum,
        "enough_data": completed >= minimum,
        "wins": wins,
        "losses": losses,
        "win_rate_percent": round((wins / completed) * 100, 4),
        "average_return_percent": round(average_return, 4),
        "average_profit_loss": round(average_profit_loss, 4),
    }


def save_learning_recommendation(
    *,
    recommendation_type: str,
    message: str,
    confidence: str,
    sample_size: int,
    created_at: str,
    supporting_data: dict[str, Any] | None = None,
    active: bool = True,
) -> int:
    """Store a read-only learning recommendation."""
    normalized_type = _normalize_optional_text(recommendation_type, "Recommendation type")
    normalized_message = _normalize_optional_text(message, "Recommendation message")
    normalized_confidence = _normalize_optional_text(confidence, "Recommendation confidence")
    if normalized_type is None:
        raise ValueError("Recommendation type cannot be empty.")
    if normalized_message is None:
        raise ValueError("Recommendation message cannot be empty.")
    if normalized_confidence is None:
        raise ValueError("Recommendation confidence cannot be empty.")

    normalized_sample_size = _validate_non_negative_integer(sample_size, "Sample size")
    normalized_created_at = _validate_timestamp(created_at)

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO learning_recommendations (
                recommendation_type, message, confidence, sample_size,
                supporting_data_json, active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_type,
                normalized_message,
                normalized_confidence,
                normalized_sample_size,
                _serialize_json_object(supporting_data),
                1 if active else 0,
                normalized_created_at,
                normalized_created_at,
            ),
        )
        recommendation_id = cursor.lastrowid

    if recommendation_id is None:
        raise RuntimeError("The learning recommendation was saved without an ID.")
    return int(recommendation_id)


def load_learning_recommendations(
    *,
    active_only: bool = False,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Load stored learning recommendations newest first."""
    safe_limit = max(1, min(int(limit), 5000))
    where_sql = " WHERE active = 1" if active_only else ""

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM learning_recommendations
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["active"] = bool(item.get("active"))
        item["supporting_data"] = _deserialize_json_object(
            item.pop("supporting_data_json", "{}")
        )
        results.append(item)
    return results


def set_learning_recommendation_active(
    recommendation_id: int,
    *,
    active: bool,
    updated_at: str,
) -> None:
    """Activate or archive a stored learning recommendation."""
    normalized_id = _validate_positive_integer(recommendation_id, "Recommendation ID")
    normalized_updated_at = _validate_timestamp(updated_at)

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE learning_recommendations
            SET active = ?, updated_at = ?
            WHERE id = ?
            """,
            (1 if active else 0, normalized_updated_at, normalized_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("Learning recommendation was not found.")
