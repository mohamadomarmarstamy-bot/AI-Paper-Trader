from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def calculate_intraday_momentum(
    bars: list[dict[str, Any]],
) -> dict[str, Any]:
    """Calculate momentum from 16 consecutive one-minute bars.

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

    if len(valid_bars) < 16:
        return {
            "available": False,
            "reason": "insufficient_minute_bars",
            "bars_available": len(valid_bars),
        }

    recent = valid_bars[-16:]

    for previous, current in zip(recent, recent[1:]):
        seconds = (current[0] - previous[0]).total_seconds()
        if seconds != 60:
            return {
                "available": False,
                "reason": "nonconsecutive_minute_bars",
            }

    closes = [price for _, price in recent]
    current_price = closes[-1]

    def change(minutes: int) -> float:
        previous = closes[-(minutes + 1)]
        return (current_price / previous - 1.0) * 100.0

    change_1m = change(1)
    change_5m = change(5)
    change_15m = change(15)

    return {
        "available": True,
        "bars_available": len(valid_bars),
        "current_price": round(current_price, 4),
        "change_1m_percent": round(change_1m, 3),
        "change_5m_percent": round(change_5m, 3),
        "change_15m_percent": round(change_15m, 3),
        "acceleration_5m": round(
            change_5m - change_15m / 3.0, 3
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

    if result.get("available"):
        try:
            latest_bar = datetime.fromisoformat(
                str(bars[-1]["t"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError):
            latest_bar = None

        # Alpaca timestamps identify the START of each minute.
        # A bar is complete one minute after its timestamp.
        if (
            latest_bar is None
            or latest_bar > completed_minute - timedelta(minutes=1)
            or latest_bar < completed_minute - timedelta(minutes=3)
        ):
            result = {
                "available": False,
                "reason": "stale_or_incomplete_minute_bars",
            }

    result["symbol"] = normalized_symbol
    result["data_source"] = "alpaca_iex"
    result["research_only"] = True

    return result
