"""Read-only dataset utilities for adaptive scanner research."""

from __future__ import annotations

from typing import Any

from database import get_connection


def load_adaptive_scanner_dataset(
    horizon_minutes: int = 15,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Load historical PAPER scanner observations and measured outcomes."""

    if not 1 <= horizon_minutes <= 1440:
        raise ValueError("Invalid forward horizon")

    if not 1 <= limit <= 10000:
        raise ValueError("Invalid research limit")

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                observations.id AS scanner_observation_id,
                observations.symbol,
                observations.observed_at,
                observations.signal,
                observations.score,
                observations.confidence,
                observations.scanner_rank,
                observations.rsi,
                observations.volume_ratio,
                observations.one_day_change,
                observations.five_day_change,
                observations.twenty_day_change,
                observations.atr_percent,
                observations.spread_percent,
                observations.risk,
                observations.momentum_candidate,
                observations.market_regime,
                observations.selected_for_entry,
                observations.strategy_version,
                outcomes.return_percent,
                outcomes.target_at,
                outcomes.evaluated_at
            FROM scanner_observations AS observations
            JOIN scanner_forward_outcomes AS outcomes
              ON outcomes.scanner_observation_id = observations.id
            WHERE outcomes.horizon_minutes = ?
              AND outcomes.status = 'measured'
              AND outcomes.return_percent IS NOT NULL
              AND UPPER(COALESCE(observations.signal, '')) = 'BUY'
            ORDER BY observations.observed_at DESC
            LIMIT ?
            """,
            (horizon_minutes, limit),
        ).fetchall()

    records = [dict(row) for row in rows]

    records.sort(
        key=lambda row: (
            str(row["observed_at"]),
            int(row["scanner_observation_id"]),
        )
    )

    return records

def export_adaptive_scanner_dataset(
    horizon_minutes: int = 15,
    limit: int = 5000,
) -> dict[str, Any]:
    """Return a bounded, read-only PAPER scanner research dataset."""

    records = load_adaptive_scanner_dataset(
        horizon_minutes=horizon_minutes,
        limit=limit,
    )

    return {
        "success": True,
        "paper": True,
        "read_only": True,
        "horizon_minutes": horizon_minutes,
        "count": len(records),
        "records": records,
    }
