import math
import sqlite3
from pathlib import Path
from typing import Any


DATABASE_PATH = Path(__file__).resolve().parent / "trader.db"

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