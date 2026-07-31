import math
from typing import Any

import pandas as pd


def safe_float(value: Any, default: float | None = None) -> float | None:
    """
    Safely convert a value into a finite float.
    """
    try:
        value = float(value)

        if not math.isfinite(value):
            return default

        return value

    except (TypeError, ValueError):
        return default


def percentage_change(
    current_value: float,
    previous_value: float,
) -> float:
    """
    Calculate percentage change safely.
    """
    current = safe_float(current_value)
    previous = safe_float(previous_value)

    if current is None or previous is None:
        return 0.0

    if previous <= 0:
        return 0.0

    return ((current - previous) / previous) * 100


def calculate_sma(
    prices: pd.Series,
    period: int,
) -> float | None:
    """
    Calculate Simple Moving Average.
    """
    if period <= 0:
        return None

    prices = prices.dropna()

    if len(prices) < period:
        return None

    return safe_float(prices.tail(period).mean())


def calculate_ema(
    prices: pd.Series,
    period: int,
) -> float | None:
    """
    Calculate Exponential Moving Average.
    """
    if period <= 0:
        return None

    prices = prices.dropna()

    if len(prices) < period:
        return None

    ema = prices.ewm(
        span=period,
        adjust=False,
    ).mean()

    return safe_float(ema.iloc[-1])


def calculate_rsi(
    prices: pd.Series,
    period: int = 14,
) -> float | None:
    """
    Calculate Relative Strength Index using Wilder's method.
    """
    if period <= 0:
        return None

    prices = prices.dropna()

    if len(prices) <= period:
        return None

    delta = prices.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    gain = safe_float(avg_gain.iloc[-1])
    loss = safe_float(avg_loss.iloc[-1])

    if gain is None or loss is None:
        return None

    if loss == 0:
        return 100.0

    rs = gain / loss

    return safe_float(100 - (100 / (1 + rs)))


def calculate_macd(
    prices: pd.Series,
):
    """
    Calculate MACD, Signal Line and Histogram.
    """
    prices = prices.dropna()

    if len(prices) < 35:
        return None

    ema12 = prices.ewm(
        span=12,
        adjust=False,
    ).mean()

    ema26 = prices.ewm(
        span=26,
        adjust=False,
    ).mean()

    macd = ema12 - ema26

    signal = macd.ewm(
        span=9,
        adjust=False,
    ).mean()

    latest_macd = safe_float(macd.iloc[-1])
    latest_signal = safe_float(signal.iloc[-1])

    if latest_macd is None or latest_signal is None:
        return None

    return {
        "macd": latest_macd,
        "signal": latest_signal,
        "histogram": latest_macd - latest_signal,
    }


def calculate_atr(
    history: pd.DataFrame,
    period: int = 14,
) -> float | None:
    """
    Calculate Average True Range.
    """
    required = {"High", "Low", "Close"}

    if not required.issubset(history.columns):
        return None

    if len(history) <= period:
        return None

    high = history["High"]
    low = history["Low"]
    close = history["Close"]

    previous_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.rolling(period).mean()

    return safe_float(atr.iloc[-1])


def calculate_bollinger_bands(
    prices: pd.Series,
    period: int = 20,
    standard_deviations: float = 2.0,
):
    """
    Calculate Bollinger Bands.
    """
    if period <= 0:
        return None

    prices = prices.dropna()

    if len(prices) < period:
        return None

    recent = prices.tail(period)

    middle = safe_float(recent.mean())
    deviation = safe_float(recent.std())

    if middle is None or deviation is None:
        return None

    return {
        "upper": middle + (standard_deviations * deviation),
        "middle": middle,
        "lower": middle - (standard_deviations * deviation),
    }


def calculate_volume_ratio(
    volumes: pd.Series,
    period: int = 20,
):
    """
    Compare latest volume against average volume.
    """
    if period <= 0:
        return None

    volumes = volumes.dropna()

    if len(volumes) < period:
        return None

    average = safe_float(
        volumes.tail(period).mean()
    )

    latest = safe_float(
        volumes.iloc[-1]
    )

    if (
        average is None
        or latest is None
        or average <= 0
    ):
        return None

    return {
        "ratio": latest / average,
        "latest": latest,
        "average": average,
    }