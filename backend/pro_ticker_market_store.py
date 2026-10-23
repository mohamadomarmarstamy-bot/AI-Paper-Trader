"""Isolated SQLite storage for read-only Pro Ticker market research runs.

This is not an order journal, trading authorization, or AI training dataset.
The caller owns the SQLite connection and transaction.
"""
import json
import sqlite3
from datetime import datetime, timezone

def initialize_market_research_store(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS pro_ticker_market_research_runs (
        run_id TEXT PRIMARY KEY, recorded_at TEXT NOT NULL,
        requested INTEGER NOT NULL, accepted INTEGER NOT NULL,
        failed INTEGER NOT NULL, payload_json TEXT NOT NULL
    )""")

def persist_market_research_run(connection, run_id, result, *, recorded_at=None):
    if not isinstance(run_id, str) or not (1 <= len(run_id) <= 80) or not all(c.isalnum() or c in "-_" for c in run_id):
        raise ValueError("Invalid run ID")
    if not isinstance(result, dict) or result.get("paper") is not True or result.get("research_only") is not True or result.get("order_execution_authorized") is not False:
        raise ValueError("Only non-executable research results can be saved")
    requested, accepted, failed = (result.get(key) for key in ("requested", "accepted", "failed"))
    if any(type(v) is not int or v < 0 for v in (requested, accepted, failed)) or accepted + failed != requested:
        raise ValueError("Invalid run counts")
    candidates = result.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > 100 or len(json.dumps(result)) > 250000:
        raise ValueError("Invalid research payload")
    timestamp = recorded_at or datetime.now(timezone.utc).isoformat()
    if not isinstance(timestamp, str) or len(timestamp) > 50:
        raise ValueError("Invalid record time")
    initialize_market_research_store(connection)
    serialized = json.dumps(result, sort_keys=True, allow_nan=False)
    try:
        connection.execute("INSERT INTO pro_ticker_market_research_runs VALUES (?, ?, ?, ?, ?, ?)",
                           (run_id, timestamp, requested, accepted, failed, serialized))
    except sqlite3.IntegrityError:
        row = connection.execute("SELECT payload_json FROM pro_ticker_market_research_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None or row[0] != serialized:
            raise ValueError("Conflicting research run ID") from None
    return run_id

def latest_market_research_run(connection):
    try:
        row = connection.execute("""SELECT run_id, recorded_at, payload_json
            FROM pro_ticker_market_research_runs ORDER BY recorded_at DESC, run_id DESC LIMIT 1""").fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return None
        raise
    if row is None:
        return None
    return {"run_id": row[0], "recorded_at": row[1], "result": json.loads(row[2])}
