"""Research-only evaluation of intraday stock momentum."""

from datetime import datetime, timezone
from typing import Any, Callable

from intraday_momentum import fetch_intraday_momentum


def evaluate_momentum_candidates(
    candidates: list[dict[str, Any]],
    market_data_request_func: Callable[..., Any],
    *,
    limit: int = 10,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate unique scanner candidates without placing orders."""

    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("limit must be an integer")

    if not 1 <= limit <= 30:
        raise ValueError("limit must be between 1 and 30")

    observed_at = now or datetime.now(timezone.utc)

    if observed_at.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    results = []
    seen = set()

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        symbol = str(candidate.get("symbol") or "").strip().upper()

        if not symbol or not symbol.isalnum() or symbol in seen:
            continue

        seen.add(symbol)

        try:
            momentum = fetch_intraday_momentum(
                symbol,
                market_data_request_func,
                now=observed_at,
            )
        except Exception as error:
            momentum = {
                "symbol": symbol,
                "available": False,
                "reason": "momentum_evaluation_failed",
                "error_type": type(error).__name__,
                "research_only": True,
            }

        results.append({
            "symbol": symbol,
            "scanner_rank": candidate.get("scanner_rank"),
            "scanner_signal": candidate.get("signal"),
            "scanner_score": candidate.get("score"),
            "observed_at": observed_at.isoformat(),
            "momentum": momentum,
        })

        if len(results) >= limit:
            break

    return {
        "research_only": True,
        "data_source": "alpaca_iex",
        "observed_at": observed_at.isoformat(),
        "requested_limit": limit,
        "evaluated_count": len(results),
        "results": results,
    }
