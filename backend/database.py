import json
from datetime import datetime, timedelta, timezone
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


def _normalize_optional_finite_number(
    value: Any,
    field_name: str,
) -> float | None:
    """Normalize an optional finite number, including signed values."""
    if value is None:
        return None

    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{field_name} must be a valid number."
        ) from error

    if not math.isfinite(numeric_value):
        raise ValueError(
            f"{field_name} must be finite."
        )

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


def get_scheduler_state(
    key: str,
    default: Any = None,
) -> Any:
    """
    Load one persistent scheduler value.
    """

    normalized_key = str(key).strip()

    if not normalized_key:
        return default

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT value_json
            FROM scheduler_state
            WHERE key = ?
            LIMIT 1
            """,
            (normalized_key,),
        ).fetchone()

    if row is None:
        return default

    try:
        value_json = row["value_json"]
    except Exception:
        try:
            value_json = row[0]
        except Exception:
            return default

    try:
        return json.loads(value_json)
    except Exception:
        return default


def set_scheduler_state(
    key: str,
    value: Any,
) -> None:
    """
    Persist one scheduler value as JSON.
    """

    normalized_key = str(key).strip()

    if not normalized_key:
        raise ValueError(
            "Scheduler state key cannot be empty."
        )

    value_json = json.dumps(
        value,
        separators=(",", ":"),
        default=str,
    )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO scheduler_state (
                key,
                value_json,
                updated_at
            )
            VALUES (
                ?,
                ?,
                strftime(
                    '%Y-%m-%dT%H:%M:%fZ',
                    'now'
                )
            )
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (
                normalized_key,
                value_json,
            ),
        )


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
            CREATE TABLE IF NOT EXISTS trade_excursions (
                trade_book_id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                entry_price REAL NOT NULL,
                max_price REAL NOT NULL,
                min_price REAL NOT NULL,
                mfe_percent REAL NOT NULL,
                mae_percent REAL NOT NULL,
                observation_count INTEGER NOT NULL DEFAULT 1,
                first_observed_at TEXT NOT NULL,
                last_observed_at TEXT NOT NULL,
                FOREIGN KEY(trade_book_id)
                    REFERENCES trade_book(id)
                    ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scanner_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                reference_price REAL NOT NULL,
                signal TEXT,
                score REAL,
                confidence REAL,
                scanner_rank REAL,
                rsi REAL,
                macd REAL,
                macd_signal REAL,
                macd_histogram REAL,
                volume_ratio REAL,
                average_volume REAL,
                one_day_change REAL,
                five_day_change REAL,
                twenty_day_change REAL,
                atr REAL,
                atr_percent REAL,
                spread_percent REAL,
                trend TEXT,
                trend_strength TEXT,
                risk TEXT,
                ma20 REAL,
                ma50 REAL,
                ma200 REAL,
                momentum_candidate INTEGER,
                momentum_move_percent REAL,
                market_regime TEXT,
                market_regime_score REAL,
                news_sentiment TEXT,
                news_score REAL,
                selected_for_entry INTEGER NOT NULL DEFAULT 0
                    CHECK(selected_for_entry IN (0, 1)),
                strategy_version TEXT,
                features_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_scanner_observations_symbol_observed_at
            ON scanner_observations (
                symbol,
                observed_at
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS broker_fills (
                order_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL
                    CHECK(side IN ('BUY', 'SELL')),
                shares REAL NOT NULL,
                price REAL NOT NULL,
                filled_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'alpaca_paper',
                raw_order_json TEXT NOT NULL DEFAULT '{}',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_broker_fills_symbol
            ON broker_fills(symbol)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_broker_fills_filled_at
            ON broker_fills(filled_at)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_broker_fills_side
            ON broker_fills(side)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduler_state (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pro_ticker_discovery_state (
                name TEXT PRIMARY KEY,
                page_cursor INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pro_ticker_research (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                article_url TEXT NOT NULL UNIQUE,
                article_title TEXT,
                published_date TEXT,
                alert_date TEXT,
                alert_time TEXT,
                direction TEXT,
                long_level REAL,
                short_level REAL,
                reported_high REAL,
                reported_low REAL,
                reported_move_percent REAL,
                setup_type TEXT,
                relative_volume TEXT,
                vwap_context TEXT,
                vwma_context TEXT,
                rsi_context TEXT,
                volume_context TEXT,
                consolidation_context TEXT,
                higher_lows INTEGER,
                breakout_context TEXT,
                continuation_context TEXT,
                exhaustion_context TEXT,
                article_summary TEXT,
                raw_features_json TEXT,
                our_scanner_seen INTEGER,
                our_scanner_score REAL,
                our_scanner_confidence REAL,
                our_scanner_rank INTEGER,
                our_bot_action TEXT,
                our_skip_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_pro_ticker_research_symbol
            ON pro_ticker_research(symbol)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_pro_ticker_research_published_date
            ON pro_ticker_research(published_date)
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
            CREATE INDEX IF NOT EXISTS idx_trade_excursions_symbol
            ON trade_excursions(symbol)
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


def record_scanner_observation(
    *,
    symbol: str,
    observed_at: str,
    reference_price: float,
    signal: str | None = None,
    score: Any = None,
    confidence: Any = None,
    scanner_rank: Any = None,
    rsi: Any = None,
    macd: Any = None,
    macd_signal: Any = None,
    macd_histogram: Any = None,
    volume_ratio: Any = None,
    average_volume: Any = None,
    one_day_change: Any = None,
    five_day_change: Any = None,
    twenty_day_change: Any = None,
    atr: Any = None,
    atr_percent: Any = None,
    spread_percent: Any = None,
    trend: str | None = None,
    trend_strength: str | None = None,
    risk: str | None = None,
    ma20: Any = None,
    ma50: Any = None,
    ma200: Any = None,
    momentum_candidate: bool | None = None,
    momentum_move_percent: Any = None,
    market_regime: str | None = None,
    market_regime_score: Any = None,
    news_sentiment: str | None = None,
    news_score: Any = None,
    selected_for_entry: bool = False,
    strategy_version: str | None = None,
    features: dict[str, Any] | None = None,
) -> int:
    """Record an entry-time scanner snapshot for research."""
    normalized_symbol = _normalize_symbol(symbol)
    normalized_observed_at = _validate_timestamp(observed_at)
    normalized_reference_price = _validate_finite_number(
        reference_price,
        "Scanner observation reference price",
        allow_zero=False,
    )

    numeric_values = {
        "score": score,
        "confidence": confidence,
        "scanner_rank": scanner_rank,
        "rsi": rsi,
        "macd": macd,
        "macd_signal": macd_signal,
        "macd_histogram": macd_histogram,
        "volume_ratio": volume_ratio,
        "average_volume": average_volume,
        "one_day_change": one_day_change,
        "five_day_change": five_day_change,
        "twenty_day_change": twenty_day_change,
        "atr": atr,
        "atr_percent": atr_percent,
        "spread_percent": spread_percent,
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "momentum_move_percent": momentum_move_percent,
        "market_regime_score": market_regime_score,
        "news_score": news_score,
    }

    normalized_numbers = {
        key: _normalize_optional_finite_number(
            value,
            f"Scanner observation {key}",
        )
        for key, value in numeric_values.items()
    }

    normalized_momentum_candidate = (
        None
        if momentum_candidate is None
        else int(bool(momentum_candidate))
    )

    observed_datetime = datetime.fromisoformat(
        normalized_observed_at.replace("Z", "+00:00")
    )

    if observed_datetime.tzinfo is None:
        observed_datetime = observed_datetime.replace(
            tzinfo=timezone.utc
        )

    observation_cutoff = (
        observed_datetime
        - timedelta(minutes=15)
    ).isoformat()

    created_at = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        existing_observation = connection.execute(
            """
            SELECT id
            FROM scanner_observations
            WHERE symbol = ?
              AND observed_at >= ?
              AND observed_at <= ?
            ORDER BY observed_at DESC
            LIMIT 1
            """,
            (
                normalized_symbol,
                observation_cutoff,
                normalized_observed_at,
            ),
        ).fetchone()

        if existing_observation is not None:
            return int(existing_observation["id"])

        cursor = connection.execute(
            """
            INSERT INTO scanner_observations (
                symbol,
                observed_at,
                reference_price,
                signal,
                score,
                confidence,
                scanner_rank,
                rsi,
                macd,
                macd_signal,
                macd_histogram,
                volume_ratio,
                average_volume,
                one_day_change,
                five_day_change,
                twenty_day_change,
                atr,
                atr_percent,
                spread_percent,
                trend,
                trend_strength,
                risk,
                ma20,
                ma50,
                ma200,
                momentum_candidate,
                momentum_move_percent,
                market_regime,
                market_regime_score,
                news_sentiment,
                news_score,
                selected_for_entry,
                strategy_version,
                features_json,
                created_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?
            )
            """,
            (
                normalized_symbol,
                normalized_observed_at,
                normalized_reference_price,
                _normalize_optional_text(signal, "Signal"),
                normalized_numbers["score"],
                normalized_numbers["confidence"],
                normalized_numbers["scanner_rank"],
                normalized_numbers["rsi"],
                normalized_numbers["macd"],
                normalized_numbers["macd_signal"],
                normalized_numbers["macd_histogram"],
                normalized_numbers["volume_ratio"],
                normalized_numbers["average_volume"],
                normalized_numbers["one_day_change"],
                normalized_numbers["five_day_change"],
                normalized_numbers["twenty_day_change"],
                normalized_numbers["atr"],
                normalized_numbers["atr_percent"],
                normalized_numbers["spread_percent"],
                _normalize_optional_text(trend, "Trend"),
                _normalize_optional_text(
                    trend_strength,
                    "Trend strength",
                ),
                _normalize_optional_text(risk, "Risk"),
                normalized_numbers["ma20"],
                normalized_numbers["ma50"],
                normalized_numbers["ma200"],
                normalized_momentum_candidate,
                normalized_numbers["momentum_move_percent"],
                _normalize_optional_text(
                    market_regime,
                    "Market regime",
                ),
                normalized_numbers["market_regime_score"],
                _normalize_optional_text(
                    news_sentiment,
                    "News sentiment",
                ),
                normalized_numbers["news_score"],
                int(bool(selected_for_entry)),
                _normalize_optional_text(
                    strategy_version,
                    "Strategy version",
                ),
                _serialize_json_object(features),
                created_at,
            ),
        )
        observation_id = cursor.lastrowid

    if observation_id is None:
        raise RuntimeError(
            "Scanner observation was saved without an ID."
        )

    return int(observation_id)


def mark_scanner_observation_selected(
    observation_id: int,
) -> None:
    """Mark a scanner observation as resulting in a paper entry."""
    normalized_observation_id = int(observation_id)

    if normalized_observation_id <= 0:
        raise ValueError(
            "Scanner observation ID must be positive."
        )

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE scanner_observations
            SET selected_for_entry = 1
            WHERE id = ?
            """,
            (normalized_observation_id,),
        )


def upsert_trade_excursion(
    *,
    trade_book_id: int,
    symbol: str,
    entry_price: float,
    current_price: float,
    observed_at: str,
) -> dict[str, Any]:
    """Create or update passive MFE/MAE tracking for one trade."""
    normalized_trade_book_id = _validate_positive_integer(
        trade_book_id,
        "Trade-book ID",
    )
    normalized_symbol = _normalize_symbol(symbol)
    normalized_entry_price = _validate_finite_number(
        entry_price,
        "Entry price",
        allow_zero=False,
    )
    normalized_current_price = _validate_finite_number(
        current_price,
        "Current price",
        allow_zero=False,
    )
    normalized_observed_at = _validate_timestamp(
        observed_at
    )

    return_percent = (
        (
            normalized_current_price
            - normalized_entry_price
        )
        / normalized_entry_price
    ) * 100

    with get_connection() as connection:
        existing = connection.execute(
            """
            SELECT *
            FROM trade_excursions
            WHERE trade_book_id = ?
            """,
            (normalized_trade_book_id,),
        ).fetchone()

        if existing is None:
            max_price = max(
                normalized_entry_price,
                normalized_current_price,
            )
            min_price = min(
                normalized_entry_price,
                normalized_current_price,
            )
            mfe_percent = max(
                0.0,
                return_percent,
            )
            mae_percent = min(
                0.0,
                return_percent,
            )
            observation_count = 1
            first_observed_at = normalized_observed_at

            connection.execute(
                """
                INSERT INTO trade_excursions (
                    trade_book_id,
                    symbol,
                    entry_price,
                    max_price,
                    min_price,
                    mfe_percent,
                    mae_percent,
                    observation_count,
                    first_observed_at,
                    last_observed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_trade_book_id,
                    normalized_symbol,
                    normalized_entry_price,
                    max_price,
                    min_price,
                    mfe_percent,
                    mae_percent,
                    observation_count,
                    first_observed_at,
                    normalized_observed_at,
                ),
            )

        else:
            max_price = max(
                float(existing["max_price"]),
                normalized_current_price,
            )
            min_price = min(
                float(existing["min_price"]),
                normalized_current_price,
            )

            mfe_percent = (
                (
                    max_price
                    - normalized_entry_price
                )
                / normalized_entry_price
            ) * 100

            mae_percent = (
                (
                    min_price
                    - normalized_entry_price
                )
                / normalized_entry_price
            ) * 100

            observation_count = (
                int(existing["observation_count"])
                + 1
            )

            first_observed_at = str(
                existing["first_observed_at"]
            )

            connection.execute(
                """
                UPDATE trade_excursions
                SET symbol = ?,
                    entry_price = ?,
                    max_price = ?,
                    min_price = ?,
                    mfe_percent = ?,
                    mae_percent = ?,
                    observation_count = ?,
                    last_observed_at = ?
                WHERE trade_book_id = ?
                """,
                (
                    normalized_symbol,
                    normalized_entry_price,
                    max_price,
                    min_price,
                    mfe_percent,
                    mae_percent,
                    observation_count,
                    normalized_observed_at,
                    normalized_trade_book_id,
                ),
            )

    return {
        "trade_book_id": normalized_trade_book_id,
        "symbol": normalized_symbol,
        "entry_price": normalized_entry_price,
        "max_price": max_price,
        "min_price": min_price,
        "mfe_percent": mfe_percent,
        "mae_percent": mae_percent,
        "observation_count": observation_count,
        "first_observed_at": first_observed_at,
        "last_observed_at": normalized_observed_at,
    }

def load_trade_excursions(
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Load recent passive MFE/MAE tracking rows."""
    normalized_limit = max(
        1,
        min(
            int(limit),
            1000,
        ),
    )

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM trade_excursions
            ORDER BY last_observed_at DESC
            LIMIT ?
            """,
            (normalized_limit,),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def load_pro_ticker_discovery_cursor(
    name: str = "historical_backfill",
) -> int:
    normalized_name = str(name).strip()

    if not normalized_name:
        normalized_name = "historical_backfill"

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT page_cursor
            FROM pro_ticker_discovery_state
            WHERE name = ?
            """,
            (normalized_name,),
        ).fetchone()

    if row is None:
        return 1

    try:
        return max(
            1,
            int(row["page_cursor"]),
        )
    except Exception:
        return 1


def save_pro_ticker_discovery_cursor(
    page_cursor: int,
    name: str = "historical_backfill",
) -> int:
    normalized_name = str(name).strip()

    if not normalized_name:
        normalized_name = "historical_backfill"

    normalized_cursor = max(
        1,
        int(page_cursor),
    )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO pro_ticker_discovery_state (
                name,
                page_cursor,
                updated_at
            )
            VALUES (?, ?, ?)
            ON CONFLICT(name)
            DO UPDATE SET
                page_cursor = excluded.page_cursor,
                updated_at = excluded.updated_at
            """,
            (
                normalized_name,
                normalized_cursor,
                now,
            ),
        )

        connection.commit()

    return normalized_cursor


# =========================================================
# Broker fill ledger
# =========================================================

def upsert_broker_fill(
    *,
    order_id: str,
    symbol: str,
    side: str,
    shares: float,
    price: float,
    filled_at: str,
    raw_order: dict[str, Any] | None = None,
    source: str = "alpaca_paper",
) -> dict[str, Any]:
    """Insert or refresh one broker execution by Alpaca order ID."""

    normalized_order_id = str(order_id).strip()

    if not normalized_order_id:
        raise ValueError(
            "Broker order ID cannot be empty."
        )

    normalized_symbol = _normalize_symbol(
        symbol
    )

    normalized_side = str(
        side
    ).strip().upper()

    if normalized_side not in {
        "BUY",
        "SELL",
    }:
        raise ValueError(
            "Broker fill side must be BUY or SELL."
        )

    normalized_shares = (
        _validate_finite_number(
            shares,
            "Broker fill shares",
            allow_zero=False,
        )
    )

    normalized_price = (
        _validate_finite_number(
            price,
            "Broker fill price",
            allow_zero=False,
        )
    )

    normalized_filled_at = (
        _validate_timestamp(
            filled_at
        )
    )

    normalized_source = str(
        source or "alpaca_paper"
    ).strip()

    if not normalized_source:
        normalized_source = (
            "alpaca_paper"
        )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    raw_order_json = (
        _serialize_json_object(
            raw_order
        )
    )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO broker_fills (
                order_id,
                symbol,
                side,
                shares,
                price,
                filled_at,
                source,
                raw_order_json,
                first_seen_at,
                last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(order_id)
            DO UPDATE SET
                symbol = excluded.symbol,
                side = excluded.side,
                shares = excluded.shares,
                price = excluded.price,
                filled_at = excluded.filled_at,
                source = CASE
                    WHEN broker_fills.source
                        = 'alpaca_paper_immediate'
                    THEN broker_fills.source
                    ELSE excluded.source
                END,
                raw_order_json = excluded.raw_order_json,
                last_seen_at = excluded.last_seen_at
            """,
            (
                normalized_order_id,
                normalized_symbol,
                normalized_side,
                normalized_shares,
                normalized_price,
                normalized_filled_at,
                normalized_source,
                raw_order_json,
                now,
                now,
            ),
        )

        row = connection.execute(
            """
            SELECT *
            FROM broker_fills
            WHERE order_id = ?
            """,
            (
                normalized_order_id,
            ),
        ).fetchone()

    if row is None:
        raise RuntimeError(
            "Broker fill could not be reloaded."
        )

    result = dict(row)

    result["raw_order"] = (
        _deserialize_json_object(
            result.pop(
                "raw_order_json",
                "{}",
            )
        )
    )

    return result


def get_broker_fill(
    order_id: str,
) -> dict[str, Any] | None:
    """Load one broker fill by order ID."""

    normalized_order_id = str(
        order_id
    ).strip()

    if not normalized_order_id:
        return None

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM broker_fills
            WHERE order_id = ?
            """,
            (
                normalized_order_id,
            ),
        ).fetchone()

    if row is None:
        return None

    result = dict(row)

    result["raw_order"] = (
        _deserialize_json_object(
            result.pop(
                "raw_order_json",
                "{}",
            )
        )
    )

    return result


def load_broker_fills(
    *,
    symbol: str | None = None,
    side: str | None = None,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Load broker fills newest first."""

    safe_limit = max(
        1,
        min(
            int(limit),
            10000,
        ),
    )

    clauses: list[str] = []
    params: list[Any] = []

    if symbol is not None:
        clauses.append(
            "symbol = ?"
        )

        params.append(
            _normalize_symbol(
                symbol
            )
        )

    if side is not None:
        normalized_side = str(
            side
        ).strip().upper()

        if normalized_side not in {
            "BUY",
            "SELL",
        }:
            raise ValueError(
                "Broker fill side must be BUY or SELL."
            )

        clauses.append(
            "side = ?"
        )

        params.append(
            normalized_side
        )

    if start_timestamp is not None:
        clauses.append(
            "filled_at >= ?"
        )

        params.append(
            _validate_timestamp(
                start_timestamp
            )
        )

    if end_timestamp is not None:
        clauses.append(
            "filled_at <= ?"
        )

        params.append(
            _validate_timestamp(
                end_timestamp
            )
        )

    where_sql = (
        " WHERE "
        + " AND ".join(
            clauses
        )
        if clauses
        else ""
    )

    params.append(
        safe_limit
    )

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM broker_fills
            {where_sql}
            ORDER BY filled_at DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

    results: list[
        dict[str, Any]
    ] = []

    for row in rows:
        item = dict(row)

        item["raw_order"] = (
            _deserialize_json_object(
                item.pop(
                    "raw_order_json",
                    "{}",
                )
            )
        )

        results.append(
            item
        )

    return results

def upsert_pro_ticker_research(
    *,
    article_url: str,
    symbol: str | None = None,
    article_title: str | None = None,
    published_date: str | None = None,
    alert_date: str | None = None,
    alert_time: str | None = None,
    direction: str | None = None,
    long_level: float | None = None,
    short_level: float | None = None,
    reported_high: float | None = None,
    reported_low: float | None = None,
    reported_move_percent: float | None = None,
    setup_type: str | None = None,
    relative_volume: str | None = None,
    vwap_context: str | None = None,
    vwma_context: str | None = None,
    rsi_context: str | None = None,
    volume_context: str | None = None,
    consolidation_context: str | None = None,
    higher_lows: bool | None = None,
    breakout_context: str | None = None,
    continuation_context: str | None = None,
    exhaustion_context: str | None = None,
    article_summary: str | None = None,
    raw_features: dict[str, Any] | None = None,
    our_scanner_seen: bool | None = None,
    our_scanner_score: float | None = None,
    our_scanner_confidence: float | None = None,
    our_scanner_rank: int | None = None,
    our_bot_action: str | None = None,
    our_skip_reason: str | None = None,
) -> dict[str, Any]:
    """Insert or update one external Pro Ticker research record."""

    normalized_url = str(article_url).strip()

    if not normalized_url:
        raise ValueError("Article URL cannot be empty.")

    normalized_symbol = (
        _normalize_symbol(symbol)
        if symbol is not None and str(symbol).strip()
        else None
    )

    def optional_text(value: Any) -> str | None:
        if value is None:
            return None

        normalized = str(value).strip()
        return normalized or None

    def optional_number(value: Any) -> float | None:
        if value is None:
            return None

        return _validate_finite_number(
            value,
            "Research numeric value",
            allow_zero=True,
        )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    raw_features_json = _serialize_json_object(
        raw_features
    )

    values = (
        normalized_symbol,
        normalized_url,
        optional_text(article_title),
        optional_text(published_date),
        optional_text(alert_date),
        optional_text(alert_time),
        optional_text(direction),
        optional_number(long_level),
        optional_number(short_level),
        optional_number(reported_high),
        optional_number(reported_low),
        optional_number(reported_move_percent),
        optional_text(setup_type),
        optional_text(relative_volume),
        optional_text(vwap_context),
        optional_text(vwma_context),
        optional_text(rsi_context),
        optional_text(volume_context),
        optional_text(consolidation_context),
        (
            int(bool(higher_lows))
            if higher_lows is not None
            else None
        ),
        optional_text(breakout_context),
        optional_text(continuation_context),
        optional_text(exhaustion_context),
        optional_text(article_summary),
        raw_features_json,
        (
            int(bool(our_scanner_seen))
            if our_scanner_seen is not None
            else None
        ),
        optional_number(our_scanner_score),
        optional_number(our_scanner_confidence),
        (
            int(our_scanner_rank)
            if our_scanner_rank is not None
            else None
        ),
        optional_text(our_bot_action),
        optional_text(our_skip_reason),
        now,
        now,
    )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO pro_ticker_research (
                symbol,
                article_url,
                article_title,
                published_date,
                alert_date,
                alert_time,
                direction,
                long_level,
                short_level,
                reported_high,
                reported_low,
                reported_move_percent,
                setup_type,
                relative_volume,
                vwap_context,
                vwma_context,
                rsi_context,
                volume_context,
                consolidation_context,
                higher_lows,
                breakout_context,
                continuation_context,
                exhaustion_context,
                article_summary,
                raw_features_json,
                our_scanner_seen,
                our_scanner_score,
                our_scanner_confidence,
                our_scanner_rank,
                our_bot_action,
                our_skip_reason,
                created_at,
                updated_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(article_url) DO UPDATE SET
                symbol = excluded.symbol,
                article_title = excluded.article_title,
                published_date = excluded.published_date,
                alert_date = excluded.alert_date,
                alert_time = excluded.alert_time,
                direction = excluded.direction,
                long_level = excluded.long_level,
                short_level = excluded.short_level,
                reported_high = excluded.reported_high,
                reported_low = excluded.reported_low,
                reported_move_percent = excluded.reported_move_percent,
                setup_type = excluded.setup_type,
                relative_volume = excluded.relative_volume,
                vwap_context = excluded.vwap_context,
                vwma_context = excluded.vwma_context,
                rsi_context = excluded.rsi_context,
                volume_context = excluded.volume_context,
                consolidation_context = excluded.consolidation_context,
                higher_lows = excluded.higher_lows,
                breakout_context = excluded.breakout_context,
                continuation_context = excluded.continuation_context,
                exhaustion_context = excluded.exhaustion_context,
                article_summary = excluded.article_summary,
                raw_features_json = excluded.raw_features_json,
                our_scanner_seen = COALESCE(
                    excluded.our_scanner_seen,
                    pro_ticker_research.our_scanner_seen
                ),
                our_scanner_score = COALESCE(
                    excluded.our_scanner_score,
                    pro_ticker_research.our_scanner_score
                ),
                our_scanner_confidence = COALESCE(
                    excluded.our_scanner_confidence,
                    pro_ticker_research.our_scanner_confidence
                ),
                our_scanner_rank = COALESCE(
                    excluded.our_scanner_rank,
                    pro_ticker_research.our_scanner_rank
                ),
                our_bot_action = COALESCE(
                    excluded.our_bot_action,
                    pro_ticker_research.our_bot_action
                ),
                our_skip_reason = COALESCE(
                    excluded.our_skip_reason,
                    pro_ticker_research.our_skip_reason
                ),
                updated_at = excluded.updated_at
            """,
            values,
        )

        row = connection.execute(
            """
            SELECT *
            FROM pro_ticker_research
            WHERE article_url = ?
            """,
            (normalized_url,),
        ).fetchone()

    if row is None:
        raise RuntimeError(
            "Pro Ticker research record could not be loaded after save."
        )

    result = dict(row)
    result["raw_features"] = _deserialize_json_object(
        result.pop(
            "raw_features_json",
            "{}",
        )
    )

    return result


def update_pro_ticker_decision_observation(
    *,
    article_url: str,
    our_scanner_seen: bool | None = None,
    our_scanner_score: float | None = None,
    our_scanner_confidence: float | None = None,
    our_scanner_rank: int | None = None,
    our_bot_action: str | None = None,
    our_skip_reason: str | None = None,
) -> dict[str, Any] | None:
    """
    Update only our PAPER-trader observation fields
    for an existing Pro Ticker research record.

    This intentionally does not modify article research,
    parsed alert data, or reported outcome fields.
    """
    normalized_url = str(
        article_url
    ).strip()

    if not normalized_url:
        raise ValueError(
            "Article URL cannot be empty."
        )

    assignments: list[str] = []
    params: list[Any] = []

    if our_scanner_seen is not None:
        assignments.append(
            "our_scanner_seen = ?"
        )
        params.append(
            int(bool(our_scanner_seen))
        )

    if our_scanner_score is not None:
        assignments.append(
            "our_scanner_score = ?"
        )
        params.append(
            _validate_finite_number(
                our_scanner_score,
                "Pro Ticker scanner score",
                allow_zero=True,
            )
        )

    if our_scanner_confidence is not None:
        assignments.append(
            "our_scanner_confidence = ?"
        )
        params.append(
            _validate_finite_number(
                our_scanner_confidence,
                "Pro Ticker scanner confidence",
                allow_zero=True,
            )
        )

    if our_scanner_rank is not None:
        assignments.append(
            "our_scanner_rank = ?"
        )
        params.append(
            _validate_positive_integer(
                our_scanner_rank,
                "Pro Ticker scanner rank",
            )
        )

    if our_bot_action is not None:
        normalized_action = str(
            our_bot_action
        ).strip()

        if normalized_action:
            assignments.append(
                "our_bot_action = ?"
            )
            params.append(
                normalized_action
            )

    if our_skip_reason is not None:
        normalized_reason = str(
            our_skip_reason
        ).strip()

        if normalized_reason:
            assignments.append(
                "our_skip_reason = ?"
            )
            params.append(
                normalized_reason
            )

    if not assignments:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM pro_ticker_research
                WHERE article_url = ?
                """,
                (normalized_url,),
            ).fetchone()

        if row is None:
            return None

        result = dict(row)
        result["raw_features"] = (
            _deserialize_json_object(
                result.pop(
                    "raw_features_json",
                    "{}",
                )
            )
        )
        return result

    assignments.append(
        "updated_at = ?"
    )
    params.append(
        datetime.now(
            timezone.utc
        ).isoformat()
    )
    params.append(
        normalized_url
    )

    with get_connection() as connection:
        connection.execute(
            f"""
            UPDATE pro_ticker_research
            SET {", ".join(assignments)}
            WHERE article_url = ?
            """,
            tuple(params),
        )

        row = connection.execute(
            """
            SELECT *
            FROM pro_ticker_research
            WHERE article_url = ?
            """,
            (normalized_url,),
        ).fetchone()

    if row is None:
        return None

    result = dict(row)
    result["raw_features"] = (
        _deserialize_json_object(
            result.pop(
                "raw_features_json",
                "{}",
            )
        )
    )

    return result


def load_pro_ticker_research(
    *,
    symbol: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Load saved Pro Ticker research records newest first."""

    safe_limit = max(
        1,
        min(
            int(limit),
            5000,
        ),
    )

    params: list[Any] = []

    if symbol is not None:
        normalized_symbol = _normalize_symbol(
            symbol
        )

        where_sql = " WHERE symbol = ?"
        params.append(normalized_symbol)
    else:
        where_sql = ""

    params.append(safe_limit)

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM pro_ticker_research
            {where_sql}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

    results: list[dict[str, Any]] = []

    for row in rows:
        item = dict(row)

        item["raw_features"] = (
            _deserialize_json_object(
                item.pop(
                    "raw_features_json",
                    "{}",
                )
            )
        )

        results.append(item)

    return results


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
    """
    Load completed learning outcomes newest first,
    including passive MFE/MAE excursion evidence when available.
    """
    safe_limit = max(
        1,
        min(
            int(limit),
            10000,
        ),
    )

    params: list[Any] = []
    where_sql = ""

    if symbol is not None:
        where_sql = " WHERE lo.symbol = ?"
        params.append(
            _normalize_symbol(symbol)
        )

    params.append(
        safe_limit
    )

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                lo.*,
                te.max_price AS excursion_max_price,
                te.min_price AS excursion_min_price,
                te.mfe_percent,
                te.mae_percent,
                te.observation_count AS excursion_observation_count,
                te.first_observed_at AS excursion_first_observed_at,
                te.last_observed_at AS excursion_last_observed_at
            FROM learning_outcomes AS lo
            LEFT JOIN trade_excursions AS te
                ON te.trade_book_id = lo.trade_book_id
            {where_sql}
            ORDER BY lo.id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

    results: list[dict[str, Any]] = []

    for row in rows:
        item = dict(row)

        item["won"] = bool(
            item.get("won")
        )

        item["metadata"] = (
            _deserialize_json_object(
                item.pop(
                    "metadata_json",
                    "{}",
                )
            )
        )

        results.append(
            item
        )

    return results


def calculate_learning_summary(
    *,
    minimum_required: int = 10,
) -> dict[str, Any]:
    """
    Summarize completed learning evidence, including
    passive MFE/MAE excursion behavior.

    This function does not change strategy settings.
    """
    outcomes = load_learning_outcomes(
        limit=10000
    )

    completed = len(outcomes)
    minimum = max(
        1,
        int(minimum_required),
    )

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
            "excursion_trade_count": 0,
            "average_mfe_percent": None,
            "average_mae_percent": None,
            "winner_average_mfe_percent": None,
            "winner_average_mae_percent": None,
            "loser_average_mfe_percent": None,
            "loser_average_mae_percent": None,
            "gave_back_profit_count": 0,
            "gave_back_profit_percent": 0.0,
            "never_profitable_count": 0,
            "never_profitable_percent": 0.0,
        }

    wins = sum(
        1
        for outcome in outcomes
        if bool(outcome.get("won"))
    )

    losses = completed - wins

    average_return = (
        sum(
            float(
                outcome.get(
                    "realized_return_percent",
                    0.0,
                )
                or 0.0
            )
            for outcome in outcomes
        )
        / completed
    )

    average_profit_loss = (
        sum(
            float(
                outcome.get(
                    "realized_profit_loss",
                    0.0,
                )
                or 0.0
            )
            for outcome in outcomes
        )
        / completed
    )

    excursion_outcomes = [
        outcome
        for outcome in outcomes
        if outcome.get("mfe_percent") is not None
        and outcome.get("mae_percent") is not None
    ]

    excursion_trade_count = len(
        excursion_outcomes
    )

    winner_excursions = [
        outcome
        for outcome in excursion_outcomes
        if bool(outcome.get("won"))
    ]

    loser_excursions = [
        outcome
        for outcome in excursion_outcomes
        if not bool(outcome.get("won"))
    ]

    def average_field(
        rows: list[dict[str, Any]],
        field: str,
    ) -> float | None:
        if not rows:
            return None

        return sum(
            float(row.get(field) or 0.0)
            for row in rows
        ) / len(rows)

    average_mfe = average_field(
        excursion_outcomes,
        "mfe_percent",
    )

    average_mae = average_field(
        excursion_outcomes,
        "mae_percent",
    )

    winner_average_mfe = average_field(
        winner_excursions,
        "mfe_percent",
    )

    winner_average_mae = average_field(
        winner_excursions,
        "mae_percent",
    )

    loser_average_mfe = average_field(
        loser_excursions,
        "mfe_percent",
    )

    loser_average_mae = average_field(
        loser_excursions,
        "mae_percent",
    )

    gave_back_profit = [
        outcome
        for outcome in excursion_outcomes
        if float(
            outcome.get(
                "mfe_percent",
                0.0,
            )
            or 0.0
        ) >= 0.5
        and float(
            outcome.get(
                "realized_return_percent",
                0.0,
            )
            or 0.0
        ) <= 0.0
    ]

    never_profitable = [
        outcome
        for outcome in excursion_outcomes
        if float(
            outcome.get(
                "mfe_percent",
                0.0,
            )
            or 0.0
        ) <= 0.0
    ]

    gave_back_profit_count = len(
        gave_back_profit
    )

    never_profitable_count = len(
        never_profitable
    )

    return {
        "completed_trades": completed,
        "minimum_required": minimum,
        "enough_data": completed >= minimum,
        "wins": wins,
        "losses": losses,
        "win_rate_percent": round(
            (wins / completed) * 100,
            4,
        ),
        "average_return_percent": round(
            average_return,
            4,
        ),
        "average_profit_loss": round(
            average_profit_loss,
            4,
        ),
        "excursion_trade_count": (
            excursion_trade_count
        ),
        "average_mfe_percent": (
            round(average_mfe, 4)
            if average_mfe is not None
            else None
        ),
        "average_mae_percent": (
            round(average_mae, 4)
            if average_mae is not None
            else None
        ),
        "winner_average_mfe_percent": (
            round(winner_average_mfe, 4)
            if winner_average_mfe is not None
            else None
        ),
        "winner_average_mae_percent": (
            round(winner_average_mae, 4)
            if winner_average_mae is not None
            else None
        ),
        "loser_average_mfe_percent": (
            round(loser_average_mfe, 4)
            if loser_average_mfe is not None
            else None
        ),
        "loser_average_mae_percent": (
            round(loser_average_mae, 4)
            if loser_average_mae is not None
            else None
        ),
        "gave_back_profit_count": (
            gave_back_profit_count
        ),
        "gave_back_profit_percent": (
            round(
                (
                    gave_back_profit_count
                    / excursion_trade_count
                )
                * 100,
                4,
            )
            if excursion_trade_count
            else 0.0
        ),
        "never_profitable_count": (
            never_profitable_count
        ),
        "never_profitable_percent": (
            round(
                (
                    never_profitable_count
                    / excursion_trade_count
                )
                * 100,
                4,
            )
            if excursion_trade_count
            else 0.0
        ),
    }



def calculate_feature_performance(
    *,
    minimum_group_size: int = 5,
    limit: int = 10000,
) -> dict[str, Any]:
    """
    Compare entry-time features with completed paper-trade
    outcomes.

    Entry features come only from the saved entry event, so
    post-entry information is not used as an entry feature.

    This analysis is read-only and does not change strategy
    settings.
    """
    safe_minimum = max(
        1,
        int(minimum_group_size),
    )

    safe_limit = max(
        1,
        min(
            int(limit),
            10000,
        ),
    )

    outcomes = load_learning_outcomes(
        limit=safe_limit,
    )

    entry_events = load_trade_book_events(
        event="entry",
        limit=min(
            safe_limit,
            5000,
        ),
    )

    entry_by_trade_id: dict[
        int,
        dict[str, Any],
    ] = {}

    for event in entry_events:
        trade_book_id = event.get(
            "trade_book_id"
        )

        details = event.get(
            "details"
        )

        if (
            isinstance(
                trade_book_id,
                int,
            )
            and isinstance(
                details,
                dict,
            )
            and trade_book_id
            not in entry_by_trade_id
        ):
            entry_by_trade_id[
                trade_book_id
            ] = details

    joined: list[
        tuple[
            dict[str, Any],
            dict[str, Any],
        ]
    ] = []

    for outcome in outcomes:
        trade_book_id = outcome.get(
            "trade_book_id"
        )

        if not isinstance(
            trade_book_id,
            int,
        ):
            continue

        entry = entry_by_trade_id.get(
            trade_book_id
        )

        if not isinstance(
            entry,
            dict,
        ):
            continue

        joined.append(
            (
                outcome,
                entry,
            )
        )

    def finite_number(
        value: Any,
    ) -> float | None:
        try:
            number = float(value)
        except (
            TypeError,
            ValueError,
        ):
            return None

        if not math.isfinite(number):
            return None

        return number

    def summarize(
        rows: list[
            tuple[
                dict[str, Any],
                dict[str, Any],
            ]
        ],
    ) -> dict[str, Any]:
        count = len(rows)

        wins = sum(
            1
            for outcome, _entry in rows
            if bool(
                outcome.get("won")
            )
        )

        returns = [
            value
            for outcome, _entry in rows
            for value in [
                finite_number(
                    outcome.get(
                        "realized_return_percent"
                    )
                )
            ]
            if value is not None
        ]

        profit_losses = [
            value
            for outcome, _entry in rows
            for value in [
                finite_number(
                    outcome.get(
                        "realized_profit_loss"
                    )
                )
            ]
            if value is not None
        ]

        mfe_values = [
            value
            for outcome, _entry in rows
            for value in [
                finite_number(
                    outcome.get(
                        "mfe_percent"
                    )
                )
            ]
            if value is not None
        ]

        mae_values = [
            value
            for outcome, _entry in rows
            for value in [
                finite_number(
                    outcome.get(
                        "mae_percent"
                    )
                )
            ]
            if value is not None
        ]

        excursion_rows = [
            (
                outcome,
                mfe,
                mae,
                realized_return,
            )
            for outcome, _entry in rows
            for mfe in [
                finite_number(
                    outcome.get(
                        "mfe_percent"
                    )
                )
            ]
            for mae in [
                finite_number(
                    outcome.get(
                        "mae_percent"
                    )
                )
            ]
            for realized_return in [
                finite_number(
                    outcome.get(
                        "realized_return_percent"
                    )
                )
            ]
            if mfe is not None
            and mae is not None
            and realized_return is not None
        ]

        gave_back_profit_rows = [
            item
            for item in excursion_rows
            if item[1] >= 0.5
            and item[3] <= 0.0
        ]

        never_profitable_rows = [
            item
            for item in excursion_rows
            if item[1] <= 0.0
        ]

        profit_capture_values = [
            (
                realized_return
                / mfe
            )
            * 100.0
            for (
                _outcome,
                mfe,
                _mae,
                realized_return,
            ) in excursion_rows
            if mfe >= 0.5
        ]

        giveback_values = [
            max(0.0, mfe - realized_return)
            for (
                _outcome,
                mfe,
                _mae,
                realized_return,
            ) in excursion_rows
            if mfe > 0.0
        ]

        def average(
            values: list[float],
        ) -> float | None:
            if not values:
                return None

            return round(
                sum(values)
                / len(values),
                4,
            )

        return {
            "sample_size": count,
            "enough_data": (
                count >= safe_minimum
            ),
            "wins": wins,
            "losses": (
                count - wins
            ),
            "win_rate_percent": (
                round(
                    (
                        wins
                        / count
                        * 100.0
                    ),
                    4,
                )
                if count
                else 0.0
            ),
            "average_return_percent": (
                average(returns)
            ),
            "average_profit_loss": (
                average(
                    profit_losses
                )
            ),
            "average_mfe_percent": (
                average(mfe_values)
            ),
            "average_mae_percent": (
                average(mae_values)
            ),
            "excursion_sample_size": len(
                excursion_rows
            ),
            "gave_back_profit_count": len(
                gave_back_profit_rows
            ),
            "gave_back_profit_percent": (
                round(
                    (
                        len(
                            gave_back_profit_rows
                        )
                        / len(excursion_rows)
                    )
                    * 100.0,
                    4,
                )
                if excursion_rows
                else 0.0
            ),
            "never_profitable_count": len(
                never_profitable_rows
            ),
            "never_profitable_percent": (
                round(
                    (
                        len(
                            never_profitable_rows
                        )
                        / len(excursion_rows)
                    )
                    * 100.0,
                    4,
                )
                if excursion_rows
                else 0.0
            ),
            "average_profit_capture_percent": (
                average(
                    profit_capture_values
                )
            ),
            "average_giveback_percent": (
                average(
                    giveback_values
                )
            ),
        }

    groups: dict[
        str,
        dict[
            str,
            list[
                tuple[
                    dict[str, Any],
                    dict[str, Any],
                ]
            ],
        ],
    ] = {}

    def add_group(
        feature: str,
        bucket: Any,
        row: tuple[
            dict[str, Any],
            dict[str, Any],
        ],
    ) -> None:
        if bucket is None:
            return

        label = str(
            bucket
        ).strip()

        if not label:
            return

        groups.setdefault(
            feature,
            {},
        ).setdefault(
            label,
            [],
        ).append(row)

    for row in joined:
        outcome, entry = row

        add_group(
            "strategy_version",
            entry.get(
                "strategy_version"
            ),
            row,
        )

        add_group(
            "signal",
            entry.get("signal"),
            row,
        )

        add_group(
            "trend",
            entry.get("trend"),
            row,
        )

        add_group(
            "trend_strength",
            entry.get(
                "trend_strength"
            ),
            row,
        )

        add_group(
            "risk",
            entry.get("risk"),
            row,
        )

        add_group(
            "market_regime",
            entry.get(
                "market_regime"
            ),
            row,
        )

        add_group(
            "news_sentiment",
            entry.get(
                "news_sentiment"
            ),
            row,
        )

        momentum_candidate = (
            entry.get(
                "momentum_30_candidate"
            )
        )

        if momentum_candidate is not None:
            add_group(
                "momentum_candidate",
                (
                    "yes"
                    if bool(
                        momentum_candidate
                    )
                    else "no"
                ),
                row,
            )

        def numeric_bucket(
            value: Any,
            ranges: list[
                tuple[
                    float | None,
                    float | None,
                    str,
                ]
            ],
        ) -> str | None:
            number = finite_number(
                value
            )

            if number is None:
                return None

            for lower, upper, label in ranges:
                lower_pass = (
                    lower is None
                    or number >= lower
                )

                upper_pass = (
                    upper is None
                    or number < upper
                )

                if (
                    lower_pass
                    and upper_pass
                ):
                    return label

            return None

        add_group(
            "scanner_rank",
            numeric_bucket(
                entry.get(
                    "scanner_rank"
                ),
                [
                    (None, 6, "1-5"),
                    (6, 11, "6-10"),
                    (11, 21, "11-20"),
                    (21, None, "21+"),
                ],
            ),
            row,
        )

        add_group(
            "score",
            numeric_bucket(
                entry.get("score"),
                [
                    (None, 70, "<70"),
                    (70, 75, "70-74"),
                    (75, 80, "75-79"),
                    (80, 90, "80-89"),
                    (90, None, "90+"),
                ],
            ),
            row,
        )

        add_group(
            "confidence",
            numeric_bucket(
                entry.get(
                    "confidence"
                ),
                [
                    (None, 70, "<70"),
                    (70, 80, "70-79"),
                    (80, 90, "80-89"),
                    (90, None, "90+"),
                ],
            ),
            row,
        )

        add_group(
            "rsi",
            numeric_bucket(
                entry.get("rsi"),
                [
                    (None, 30, "<30"),
                    (30, 50, "30-49"),
                    (50, 60, "50-59"),
                    (60, 70, "60-69"),
                    (70, 80, "70-79"),
                    (80, None, "80+"),
                ],
            ),
            row,
        )

        add_group(
            "volume_ratio",
            numeric_bucket(
                entry.get(
                    "volume_ratio"
                ),
                [
                    (None, 0.7, "<0.7x"),
                    (0.7, 1.0, "0.7-0.99x"),
                    (1.0, 1.5, "1.0-1.49x"),
                    (1.5, 2.0, "1.5-1.99x"),
                    (2.0, 5.0, "2.0-4.99x"),
                    (5.0, None, "5.0x+"),
                ],
            ),
            row,
        )

        add_group(
            "atr_percent",
            numeric_bucket(
                entry.get(
                    "atr_percent"
                ),
                [
                    (None, 1.0, "<1%"),
                    (1.0, 2.0, "1-1.99%"),
                    (2.0, 4.0, "2-3.99%"),
                    (4.0, 6.0, "4-5.99%"),
                    (6.0, 8.0, "6-7.99%"),
                    (8.0, None, "8%+"),
                ],
            ),
            row,
        )

        add_group(
            "spread_percent",
            numeric_bucket(
                entry.get(
                    "spread_percent"
                ),
                [
                    (None, 0.10, "<0.10%"),
                    (0.10, 0.25, "0.10-0.24%"),
                    (0.25, 0.50, "0.25-0.49%"),
                    (0.50, 1.0, "0.50-0.99%"),
                    (1.0, None, "1%+"),
                ],
            ),
            row,
        )

        add_group(
            "momentum_move_percent",
            numeric_bucket(
                entry.get(
                    "momentum_move_percent"
                ),
                [
                    (None, 30.0, "<30%"),
                    (30.0, 50.0, "30-49%"),
                    (50.0, 100.0, "50-99%"),
                    (100.0, 200.0, "100-199%"),
                    (200.0, None, "200%+"),
                ],
            ),
            row,
        )

        add_group(
            "one_day_change",
            numeric_bucket(
                entry.get(
                    "one_day_change"
                ),
                [
                    (None, 0.0, "negative"),
                    (0.0, 5.0, "0-4.99%"),
                    (5.0, 15.0, "5-14.99%"),
                    (15.0, 30.0, "15-29.99%"),
                    (30.0, None, "30%+"),
                ],
            ),
            row,
        )

        one_day_value = finite_number(
            entry.get("one_day_change")
        )
        volume_ratio_value = finite_number(
            entry.get("volume_ratio")
        )

        if (
            one_day_value is not None
            and volume_ratio_value is not None
        ):
            add_group(
                "negative_day_weak_volume",
                (
                    "negative_day_and_volume_below_0.60x"
                    if (
                        one_day_value < 0.0
                        and volume_ratio_value < 0.60
                    )
                    else "other"
                ),
                row,
            )

        if "negative_day_weak_volume_shadow" in entry:
            add_group(
                "negative_day_weak_volume_shadow_forward",
                (
                    "matched"
                    if bool(entry.get("negative_day_weak_volume_shadow"))
                    else "not_matched"
                ),
                row,
            )

        macd = finite_number(
            entry.get("macd")
        )

        macd_signal = finite_number(
            entry.get(
                "macd_signal"
            )
        )

        if (
            macd is not None
            and macd_signal is not None
        ):
            add_group(
                "macd_position",
                (
                    "above_signal"
                    if macd > macd_signal
                    else (
                        "below_signal"
                        if macd < macd_signal
                        else "equal_signal"
                    )
                ),
                row,
            )

    feature_performance: dict[
        str,
        dict[str, Any],
    ] = {}

    for feature, buckets in groups.items():
        feature_performance[
            feature
        ] = {
            label: summarize(
                bucket_rows
            )
            for label, bucket_rows
            in buckets.items()
        }

    # Build entry-time feature fingerprints so we can
    # evaluate combinations instead of isolated indicators.
    # These remain observational and read-only.
    combination_features = (
        "scanner_rank",
        "rsi",
        "volume_ratio",
        "atr_percent",
        "spread_percent",
        "one_day_change",
        "macd_position",
        "trend",
        "risk",
    )

    row_feature_labels: dict[
        int,
        dict[str, str],
    ] = {}

    for feature in combination_features:
        for label, bucket_rows in groups.get(
            feature,
            {},
        ).items():
            for row in bucket_rows:
                row_feature_labels.setdefault(
                    id(row),
                    {},
                )[feature] = label

    two_feature_groups: dict[
        str,
        list[
            tuple[
                dict[str, Any],
                dict[str, Any],
            ]
        ],
    ] = {}

    three_feature_groups: dict[
        str,
        list[
            tuple[
                dict[str, Any],
                dict[str, Any],
            ]
        ],
    ] = {}

    for row in joined:
        labels = row_feature_labels.get(
            id(row),
            {},
        )

        available = [
            (
                feature,
                labels[feature],
            )
            for feature in combination_features
            if feature in labels
        ]

        for first_index in range(
            len(available)
        ):
            first_feature, first_label = (
                available[first_index]
            )

            for second_index in range(
                first_index + 1,
                len(available),
            ):
                second_feature, second_label = (
                    available[second_index]
                )

                two_key = (
                    f"{first_feature}={first_label}"
                    " | "
                    f"{second_feature}={second_label}"
                )

                two_feature_groups.setdefault(
                    two_key,
                    [],
                ).append(row)

                for third_index in range(
                    second_index + 1,
                    len(available),
                ):
                    third_feature, third_label = (
                        available[third_index]
                    )

                    three_key = (
                        f"{first_feature}={first_label}"
                        " | "
                        f"{second_feature}={second_label}"
                        " | "
                        f"{third_feature}={third_label}"
                    )

                    three_feature_groups.setdefault(
                        three_key,
                        [],
                    ).append(row)

    def summarize_combinations(
        combination_groups: dict[
            str,
            list[
                tuple[
                    dict[str, Any],
                    dict[str, Any],
                ]
            ],
        ],
    ) -> list[dict[str, Any]]:
        results: list[
            dict[str, Any]
        ] = []

        for fingerprint, rows in (
            combination_groups.items()
        ):
            summary = summarize(rows)

            if not summary.get(
                "enough_data"
            ):
                continue

            results.append({
                "fingerprint": fingerprint,
                **summary,
            })

        # This ordering is for analysis/display only.
        # It does not select trades or modify strategy.
        results.sort(
            key=lambda item: (
                finite_number(
                    item.get(
                        "average_return_percent"
                    )
                )
                or 0.0,
                int(
                    item.get(
                        "sample_size",
                        0,
                    )
                ),
            ),
            reverse=True,
        )

        return results

    two_feature_performance = (
        summarize_combinations(
            two_feature_groups
        )
    )

    three_feature_performance = (
        summarize_combinations(
            three_feature_groups
        )
    )

    trade_diagnostics: list[dict[str, Any]] = []

    for outcome, entry in joined:
        mfe = finite_number(
            outcome.get("mfe_percent")
        )
        mae = finite_number(
            outcome.get("mae_percent")
        )
        realized_return = finite_number(
            outcome.get(
                "realized_return_percent"
            )
        )

        if (
            mfe is None
            or mae is None
            or realized_return is None
        ):
            continue

        observed_giveback = max(
            0.0,
            mfe - realized_return,
        )

        if (
            mfe >= 0.5
            and realized_return <= 0.0
        ):
            diagnostic_class = (
                "gave_back_profit"
            )
        elif mfe <= 0.0:
            diagnostic_class = (
                "never_profitable"
            )
        elif realized_return > 0.0:
            diagnostic_class = (
                "profitable_exit"
            )
        else:
            diagnostic_class = (
                "limited_favorable_move"
            )

        profit_capture = None

        if mfe >= 0.5:
            profit_capture = (
                realized_return
                / mfe
            ) * 100.0

        trade_diagnostics.append(
            {
                "trade_book_id": outcome.get(
                    "trade_book_id"
                ),
                "symbol": outcome.get(
                    "symbol"
                ),
                "diagnostic_class": (
                    diagnostic_class
                ),
                "entry_price": finite_number(
                    outcome.get(
                        "entry_price"
                    )
                ),
                "exit_price": finite_number(
                    outcome.get(
                        "exit_price"
                    )
                ),
                "shares": finite_number(
                    outcome.get("shares")
                ),
                "realized_profit_loss": (
                    finite_number(
                        outcome.get(
                            "realized_profit_loss"
                        )
                    )
                ),
                "realized_return_percent": (
                    realized_return
                ),
                "exit_reason": outcome.get(
                    "exit_reason"
                ),
                "holding_seconds": (
                    finite_number(
                        outcome.get(
                            "holding_seconds"
                        )
                    )
                ),
                "mfe_percent": mfe,
                "mae_percent": mae,
                "observed_giveback_percent": (
                    observed_giveback
                ),
                "profit_capture_percent": (
                    profit_capture
                ),
                "excursion_max_price": (
                    finite_number(
                        outcome.get(
                            "excursion_max_price"
                        )
                    )
                ),
                "excursion_min_price": (
                    finite_number(
                        outcome.get(
                            "excursion_min_price"
                        )
                    )
                ),
                "excursion_observation_count": (
                    outcome.get(
                        "excursion_observation_count"
                    )
                ),
                "excursion_first_observed_at": (
                    outcome.get(
                        "excursion_first_observed_at"
                    )
                ),
                "excursion_last_observed_at": (
                    outcome.get(
                        "excursion_last_observed_at"
                    )
                ),
                "strategy_version": entry.get(
                    "strategy_version"
                ),
                "scanner_rank": entry.get(
                    "scanner_rank"
                ),
                "score": entry.get("score"),
                "confidence": entry.get(
                    "confidence"
                ),
                "rsi": entry.get("rsi"),
                "volume_ratio": entry.get(
                    "volume_ratio"
                ),
                "atr_percent": entry.get(
                    "atr_percent"
                ),
                "spread_percent": entry.get(
                    "spread_percent"
                ),
                "one_day_change": entry.get(
                    "one_day_change"
                ),
                "trend": entry.get("trend"),
                "risk": entry.get("risk"),
                "market_regime": entry.get(
                    "market_regime"
                ),
                "news_sentiment": entry.get(
                    "news_sentiment"
                ),
                "momentum_30_candidate": (
                    entry.get(
                        "momentum_30_candidate"
                    )
                ),
            }
        )

    trade_diagnostics.sort(
        key=lambda item: (
            item.get(
                "observed_giveback_percent"
            )
            or 0.0
        ),
        reverse=True,
    )

    return {
        "paper": True,
        "read_only": True,
        "automatic_strategy_changes": False,
        "minimum_group_size": (
            safe_minimum
        ),
        "completed_learning_outcomes": (
            len(outcomes)
        ),
        "joined_entry_outcomes": (
            len(joined)
        ),
        "missing_entry_snapshot_count": (
            len(outcomes)
            - len(joined)
        ),
        "overall": summarize(
            joined
        ),
        "features": (
            feature_performance
        ),
        "feature_combinations": {
            "two_feature": (
                two_feature_performance
            ),
            "three_feature": (
                three_feature_performance
            ),
        },
        "combination_feature_set": list(
            combination_features
        ),
        "trade_diagnostics": (
            trade_diagnostics
        ),
        "trade_diagnostic_count": (
            len(trade_diagnostics)
        ),
        "warning": (
            "Feature results are observational paper-trading "
            "evidence, not proof that a feature causes profit. "
            "Small samples should not be used to change strategy."
        ),
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
