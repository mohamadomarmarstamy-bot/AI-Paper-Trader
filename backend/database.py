import sqlite3
from pathlib import Path
from typing import Any


DATABASE_PATH = Path(__file__).resolve().parent / "trader.db"


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                shares INTEGER NOT NULL,
                price REAL NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('BUY', 'SELL')),
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
            VALUES (1, 100000.0)
            """
        )

        connection.commit()


def save_trade(
    symbol: str,
    shares: int,
    price: float,
    action: str,
    timestamp: str,
) -> int:
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
                symbol.upper(),
                shares,
                price,
                action.upper(),
                timestamp,
            ),
        )

        connection.commit()
        return int(cursor.lastrowid)


def load_trades() -> list[dict[str, Any]]:
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

    return [dict(row) for row in rows]


def save_position(
    symbol: str,
    shares: int,
    average_cost: float,
) -> None:
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
                symbol.upper(),
                shares,
                average_cost,
            ),
        )

        connection.commit()


def delete_position(symbol: str) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            DELETE FROM positions
            WHERE symbol = ?
            """,
            (symbol.upper(),),
        )

        connection.commit()


def load_positions() -> dict[str, dict[str, float | int]]:
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
        positions[row["symbol"]] = {
            "shares": int(row["shares"]),
            "average_cost": float(row["average_cost"]),
        }

    return positions


def save_portfolio_snapshot(
    timestamp: str,
    value: float,
) -> int:
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
                timestamp,
                value,
            ),
        )

        connection.commit()
        return int(cursor.lastrowid)


def load_portfolio_history() -> list[dict[str, Any]]:
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
            "time": row["time"],
            "value": float(row["value"]),
        }
        for row in rows
    ]


def save_cash(cash: float) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE account
            SET cash = ?
            WHERE id = 1
            """,
            (cash,),
        )

        connection.commit()


def load_cash(default_cash: float = 100000.0) -> float:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT cash
            FROM account
            WHERE id = 1
            """
        ).fetchone()

    if row is None:
        return default_cash

    return float(row["cash"])