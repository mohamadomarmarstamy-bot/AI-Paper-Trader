import math
import time
from typing import Any

import yfinance as yf


CACHE_DURATION_SECONDS = 300

_chart_cache: dict[str, dict[str, Any]] = {}


def _is_valid_number(value: Any) -> bool:
    """Return True when a value can safely be converted to a finite float."""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _get_cached_chart(symbol: str) -> dict[str, Any] | None:
    """Return cached chart data when it has not expired."""
    cached_result = _chart_cache.get(symbol)

    if not cached_result:
        return None

    saved_at = cached_result.get("saved_at", 0)
    cache_age = time.time() - saved_at

    if cache_age >= CACHE_DURATION_SECONDS:
        _chart_cache.pop(symbol, None)
        return None

    return cached_result.get("data")


def get_chart_data(symbol: str) -> dict[str, Any] | None:
    """
    Download six months of daily chart data for a stock symbol.

    Returns candlestick, volume, 20-day moving average, and
    50-day moving average data formatted for Lightweight Charts.
    """

    if not isinstance(symbol, str):
        return None

    symbol = symbol.strip().upper()

    if not symbol:
        return None

    cached_data = _get_cached_chart(symbol)

    if cached_data is not None:
        return cached_data

    history = None

    # ---------------------------------------------------------
    # Primary Yahoo Finance method
    # ---------------------------------------------------------

    try:
        stock = yf.Ticker(symbol)

        history = stock.history(
            period="6mo",
            interval="1d",
            auto_adjust=False,
            actions=False,
            repair=False,
            timeout=20,
            raise_errors=True,
        )

    except Exception as error:
        print(
            f"Ticker.history failed for {symbol}: "
            f"{type(error).__name__}: {error}"
        )
        history = None

    # ---------------------------------------------------------
    # Fallback Yahoo Finance method
    # ---------------------------------------------------------

    if history is None or history.empty:
        try:
            print(
                f"Trying yf.download fallback for {symbol}..."
            )

            history = yf.download(
                symbol,
                period="6mo",
                interval="1d",
                auto_adjust=False,
                actions=False,
                repair=False,
                progress=False,
                threads=False,
                timeout=20,
                multi_level_index=False,
            )

        except Exception as error:
            print(
                f"yf.download failed for {symbol}: "
                f"{type(error).__name__}: {error}"
            )
            history = None

    # ---------------------------------------------------------
    # Make sure Yahoo actually returned data
    # ---------------------------------------------------------

    if history is None or history.empty:
        print(
            f"No chart data was returned for {symbol} "
            f"using either Yahoo Finance method."
        )
        return None

    required_columns = {
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    }

    if not required_columns.issubset(history.columns):
        print(
            f"Chart data for {symbol} is missing required columns: "
            f"{required_columns - set(history.columns)}"
        )
        return None

    history = history.sort_index()

    closes = history["Close"]

    ma20_values = closes.rolling(
        window=20,
        min_periods=20,
    ).mean()

    ma50_values = closes.rolling(
        window=50,
        min_periods=50,
    ).mean()

    candles: list[dict[str, Any]] = []
    volume: list[dict[str, Any]] = []
    ma20: list[dict[str, Any]] = []
    ma50: list[dict[str, Any]] = []

    # ---------------------------------------------------------
    # Convert Yahoo rows into Lightweight Charts format
    # ---------------------------------------------------------

    for index, row in history.iterrows():
        open_price = row["Open"]
        high_price = row["High"]
        low_price = row["Low"]
        close_price = row["Close"]
        volume_value = row["Volume"]

        if not all(
            _is_valid_number(value)
            for value in (
                open_price,
                high_price,
                low_price,
                close_price,
            )
        ):
            continue

        open_price = float(open_price)
        high_price = float(high_price)
        low_price = float(low_price)
        close_price = float(close_price)

        try:
            date = index.strftime("%Y-%m-%d")
        except AttributeError:
            date = str(index)[:10]

        candles.append(
            {
                "time": date,
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(close_price, 2),
            }
        )

        if _is_valid_number(volume_value):
            safe_volume = max(
                0,
                int(float(volume_value)),
            )
        else:
            safe_volume = 0

        volume.append(
            {
                "time": date,
                "value": safe_volume,
                "color": (
                    "rgba(34, 197, 94, 0.55)"
                    if close_price >= open_price
                    else "rgba(239, 68, 68, 0.55)"
                ),
            }
        )

        average_20 = ma20_values.loc[index]
        average_50 = ma50_values.loc[index]

        if _is_valid_number(average_20):
            ma20.append(
                {
                    "time": date,
                    "value": round(
                        float(average_20),
                        2,
                    ),
                }
            )

        if _is_valid_number(average_50):
            ma50.append(
                {
                    "time": date,
                    "value": round(
                        float(average_50),
                        2,
                    ),
                }
            )

    # ---------------------------------------------------------
    # Final validation
    # ---------------------------------------------------------

    if not candles:
        print(
            f"No usable chart rows were found for {symbol}."
        )
        return None

    chart_data = {
        "symbol": symbol,
        "candles": candles,
        "volume": volume,
        "ma20": ma20,
        "ma50": ma50,
    }

    # ---------------------------------------------------------
    # Cache successful chart result
    # ---------------------------------------------------------

    _chart_cache[symbol] = {
        "saved_at": time.time(),
        "data": chart_data,
    }

    return chart_data