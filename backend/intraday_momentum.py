from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def calculate_intraday_momentum(
    bars: list[dict[str, Any]],
) -> dict[str, Any]:
    """Calculate timestamp-based intraday momentum.

    Missing candles are never replaced with invented prices.
    Research-only. Does not authorize trades.
    """
    valid_bars = []

    for bar in bars:
        try:
            price = float(bar["c"])
            timestamp = datetime.fromisoformat(
                str(bar["t"]).replace("Z", "+00:00")
            )
            if timestamp.tzinfo is None:
                raise ValueError("Timezone required")
            timestamp = timestamp.astimezone(timezone.utc)
            if price <= 0 or not (price < float("inf")):
                raise ValueError("Invalid price")
        except (KeyError, TypeError, ValueError, OverflowError):
            return {
                "available": False,
                "reason": "invalid_minute_bar",
            }

        valid_bars.append((timestamp, price))

    if len(valid_bars) < 2:
        return {
            "available": False,
            "reason": "insufficient_minute_bars",
            "bars_available": len(valid_bars),
        }

    for previous, current in zip(valid_bars, valid_bars[1:]):
        seconds = (current[0] - previous[0]).total_seconds()

        if seconds <= 0 or seconds % 60 != 0:
            return {
                "available": False,
                "reason": "nonconsecutive_minute_bars",
                "previous_timestamp": previous[0].isoformat(),
                "current_timestamp": current[0].isoformat(),
                "gap_seconds": seconds,
                "bars_available": len(valid_bars),
            }

    latest_timestamp, current_price = valid_bars[-1]
    prices_by_time = dict(valid_bars)

    missing_reference_minutes = [
        minutes
        for minutes in (1, 5, 15)
        if latest_timestamp - timedelta(minutes=minutes)
        not in prices_by_time
    ]

    first_timestamp = valid_bars[0][0]
    expected_candle_count = int(
        (latest_timestamp - first_timestamp).total_seconds() / 60
    ) + 1

    missing_candle_count = (
        expected_candle_count - len(valid_bars)
    )

    changes = {}

    for minutes in (1, 5, 15):
        target = latest_timestamp - timedelta(minutes=minutes)
        previous_price = prices_by_time.get(target)

        changes[minutes] = (
            round((current_price / previous_price - 1.0) * 100.0, 3)
            if previous_price is not None
            else None
        )

    change_1m = changes[1]
    change_5m = changes[5]
    change_15m = changes[15]

    available = all(
        change is not None
        for change in (change_1m, change_5m, change_15m)
    )

    return {
        "available": available,
        "reason": None if available else "missing_timeframe_candles",
        "bars_available": len(valid_bars),
        "missing_reference_minutes": missing_reference_minutes,
        "missing_candle_count": missing_candle_count,
        "current_price": round(current_price, 4),
        "change_1m_percent": change_1m,
        "change_5m_percent": change_5m,
        "change_15m_percent": change_15m,
        "acceleration_5m": (
            round(change_5m - change_15m / 3.0, 3)
            if change_5m is not None and change_15m is not None
            else None
        ),
        "research_only": True,
    }


def fetch_intraday_momentum(
    symbol: str,
    market_data_request_func,
    *,
    now=None,
) -> dict[str, Any]:
    """Fetch recent Alpaca IEX bars and calculate research-only momentum."""
    from datetime import timedelta

    normalized_symbol = str(symbol).strip().upper()

    if not normalized_symbol or not normalized_symbol.isalnum():
        return {
            "available": False,
            "reason": "invalid_symbol",
        }

    current_time = now or datetime.now(timezone.utc)

    if current_time.tzinfo is None:
        return {
            "available": False,
            "reason": "invalid_current_time",
        }

    current_time = current_time.astimezone(timezone.utc)

    # Exclude the current potentially incomplete minute.
    completed_minute = current_time.replace(
        second=0,
        microsecond=0,
    )

    start_time = completed_minute - timedelta(minutes=30)

    try:
        payload = market_data_request_func(
            "GET",
            f"/v2/stocks/{normalized_symbol}/bars",
            params={
                "timeframe": "1Min",
                "start": start_time.isoformat(),
                "end": completed_minute.isoformat(),
                "limit": 100,
                "adjustment": "raw",
                "feed": "iex",
                "sort": "asc",
            },
        )
    except Exception as error:
        return {
            "available": False,
            "reason": "market_data_request_failed",
            "error_type": type(error).__name__,
        }

    if not isinstance(payload, dict):
        return {
            "available": False,
            "reason": "invalid_market_data_response",
        }

    bars = payload.get("bars")

    if not isinstance(bars, list):
        return {
            "available": False,
            "reason": "missing_minute_bars",
        }

    result = calculate_intraday_momentum(bars)

    latest_bar = None

    if bars:
        try:
            latest_bar = datetime.fromisoformat(
                str(bars[-1]["t"]).replace("Z", "+00:00")
            )
            if latest_bar.tzinfo is None:
                latest_bar = None
            else:
                latest_bar = latest_bar.astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError, OverflowError):
            latest_bar = None

    result["latest_bar_timestamp"] = (
        latest_bar.isoformat() if latest_bar is not None else None
    )
    result["latest_bar_age_seconds"] = (
        round((current_time - latest_bar).total_seconds(), 3)
        if latest_bar is not None else None
    )

    if latest_bar is None:
        result["data_freshness"] = "unknown"
    elif latest_bar > completed_minute - timedelta(minutes=1):
        result["data_freshness"] = "incomplete"
    elif latest_bar < completed_minute - timedelta(minutes=3):
        result["data_freshness"] = "stale"
    else:
        result["data_freshness"] = "fresh"

    if (
        result.get("available")
        or result.get("reason") == "missing_timeframe_candles"
    ) and result["data_freshness"] != "fresh":
        result["available"] = False
        result["reason"] = "stale_or_incomplete_minute_bars"

    result["symbol"] = normalized_symbol
    result["data_source"] = "alpaca_iex"
    result["research_only"] = True

    return result
