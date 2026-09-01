from __future__ import annotations

import copy
import logging
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from collections.abc import Iterator
from typing import Any

import pandas as pd
import yfinance as yf

from market_universe import load_market_universe


# =========================================================
# Logging
# =========================================================

logger = logging.getLogger(__name__)


# =========================================================
# Scanner configuration
# =========================================================

SCAN_CACHE_SECONDS = 1 * 60

DOWNLOAD_PERIOD = "6mo"
DOWNLOAD_INTERVAL = "1d"
DOWNLOAD_BATCH_SIZE = 25
DOWNLOAD_TIMEOUT_SECONDS = 30

MINIMUM_HISTORY_DAYS = 55
MINIMUM_PRICE = 0.25
MINIMUM_AVERAGE_VOLUME = 50_000

# Broad-market context used by the auto-trader.
MARKET_REGIME_SYMBOLS = ("SPY", "QQQ")
MARKET_REGIME_CACHE_SECONDS = 5 * 60
MARKET_REGIME_MINIMUM_HISTORY_DAYS = 55

# Recent company-news context.
NEWS_CACHE_SECONDS = 5 * 60
NEWS_MAX_ARTICLES = 8
NEWS_LOOKBACK_HOURS = 72

NEWS_POSITIVE_TERMS = (
    "beats",
    "beat estimates",
    "raises guidance",
    "raised guidance",
    "upgrade",
    "upgraded",
    "price target raised",
    "record revenue",
    "record profit",
    "strong demand",
    "partnership",
    "contract win",
    "approval",
    "approved",
    "launch",
)

NEWS_NEGATIVE_TERMS = (
    "misses",
    "missed estimates",
    "cuts guidance",
    "cut guidance",
    "downgrade",
    "downgraded",
    "price target cut",
    "lawsuit",
    "investigation",
    "recall",
    "layoffs",
    "weak demand",
    "warning",
    "fraud",
)

# Preserve a useful mix of BUY, HOLD, and SELL candidates.
MAX_RESULTS_PER_SIGNAL = 30
SIGNAL_ORDER = ("BUY", "HOLD", "SELL")


# =========================================================
# Scanner state
# =========================================================

_scan_cache: dict[str, Any] = {
    "results": [],
    "updated_at": 0.0,
}

_market_regime_cache: dict[str, Any] = {
    "result": None,
    "updated_at": 0.0,
}

_news_cache: dict[str, dict[str, Any]] = {}

_cache_lock = threading.RLock()

# Prevent two requests from launching separate full-market scans at once.
_scan_lock = threading.Lock()


# =========================================================
# General helpers
# =========================================================

def clean_symbol(symbol: str) -> str:
    """
    Convert a symbol to Yahoo Finance format.

    Wikipedia uses dots for some share classes, while Yahoo Finance
    normally uses hyphens, such as BRK-B and BF-B.
    """
    return str(symbol or "").strip().upper().replace(".", "-")


def safe_float(value: Any) -> float | None:
    """Convert a value to a finite float."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def percentage_change(
    current_value: float,
    previous_value: float,
) -> float:
    """Return a percentage change while avoiding division by zero."""
    if previous_value == 0:
        return 0.0

    return ((current_value - previous_value) / previous_value) * 100.0


def split_into_batches(
    symbols: list[str],
    batch_size: int,
) -> Iterator[list[str]]:
    """Yield symbol batches."""
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    for index in range(0, len(symbols), batch_size):
        yield symbols[index:index + batch_size]


def _copy_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a deep copy so callers cannot mutate cached nested values."""
    return copy.deepcopy(results)


def _get_cached_results(
    *,
    allow_stale: bool,
) -> list[dict[str, Any]] | None:
    """
    Return cached results.

    When allow_stale is False, the cache must still be within its TTL.
    """
    current_time = time.time()

    with _cache_lock:
        cached_results = _scan_cache.get("results", [])
        cached_time = float(_scan_cache.get("updated_at", 0.0) or 0.0)

        if not cached_results:
            return None

        cache_is_fresh = current_time - cached_time < SCAN_CACHE_SECONDS

        if not allow_stale and not cache_is_fresh:
            return None

        return _copy_results(cached_results)


def _set_cached_results(results: list[dict[str, Any]]) -> None:
    """Replace the scanner cache atomically."""
    with _cache_lock:
        _scan_cache["results"] = _copy_results(results)
        _scan_cache["updated_at"] = time.time()


# =========================================================
# Indicator calculations
# =========================================================

def calculate_rsi(
    closes: pd.Series,
    period: int = 14,
) -> float | None:
    """Calculate Wilder-style RSI."""
    if period <= 0 or len(closes) < period + 1:
        return None

    numeric_closes = pd.to_numeric(closes, errors="coerce").dropna()

    if len(numeric_closes) < period + 1:
        return None

    changes = numeric_closes.diff()
    gains = changes.clip(lower=0)
    losses = -changes.clip(upper=0)

    average_gain = gains.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    average_loss = losses.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    latest_gain = safe_float(average_gain.iloc[-1])
    latest_loss = safe_float(average_loss.iloc[-1])

    if latest_gain is None or latest_loss is None:
        return None

    # A completely flat market is neutral, not overbought.
    if latest_gain == 0 and latest_loss == 0:
        return 50.0

    if latest_loss == 0:
        return 100.0

    if latest_gain == 0:
        return 0.0

    relative_strength = latest_gain / latest_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def calculate_macd(
    closes: pd.Series,
) -> tuple[float, float, float] | None:
    """Return MACD, signal line, and histogram."""
    numeric_closes = pd.to_numeric(closes, errors="coerce").dropna()

    if len(numeric_closes) < 35:
        return None

    ema_12 = numeric_closes.ewm(span=12, adjust=False).mean()
    ema_26 = numeric_closes.ewm(span=26, adjust=False).mean()

    macd_line = ema_12 - ema_26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line

    macd = safe_float(macd_line.iloc[-1])
    signal = safe_float(signal_line.iloc[-1])
    hist = safe_float(histogram.iloc[-1])

    if macd is None or signal is None or hist is None:
        return None

    return macd, signal, hist


def calculate_atr(
    history: pd.DataFrame,
    period: int = 14,
) -> float | None:
    """Calculate Wilder-style Average True Range."""
    if period <= 0 or len(history) < period + 1:
        return None

    required_columns = {"High", "Low", "Close"}

    if not required_columns.issubset(history.columns):
        return None

    high = pd.to_numeric(history["High"], errors="coerce")
    low = pd.to_numeric(history["Low"], errors="coerce")
    close = pd.to_numeric(history["Close"], errors="coerce")

    previous_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr_series = true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    return safe_float(atr_series.iloc[-1])


def calculate_bollinger_bands(
    closes: pd.Series,
    period: int = 20,
) -> tuple[float, float, float] | None:
    """Return upper, middle, and lower Bollinger Bands."""
    if period <= 1:
        return None

    numeric_closes = pd.to_numeric(closes, errors="coerce").dropna()

    if len(numeric_closes) < period:
        return None

    window = numeric_closes.tail(period)

    middle = safe_float(window.mean())
    standard_deviation = safe_float(window.std(ddof=1))

    if middle is None or standard_deviation is None:
        return None

    upper = middle + (2.0 * standard_deviation)
    lower = middle - (2.0 * standard_deviation)

    return upper, middle, lower


# =========================================================
# Yahoo Finance download and extraction
# =========================================================

def download_batch(
    symbols: list[str],
) -> pd.DataFrame | None:
    """Download one batch of historical data."""
    if not symbols:
        return None

    try:
        data = yf.download(
            tickers=" ".join(symbols),
            period=DOWNLOAD_PERIOD,
            interval=DOWNLOAD_INTERVAL,
            group_by="column",
            auto_adjust=False,
            progress=False,
            threads=True,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.exception(
            "Scanner batch download failed for %s symbol(s).",
            len(symbols),
        )
        return None

    if not isinstance(data, pd.DataFrame) or data.empty:
        logger.warning(
            "Yahoo Finance returned no scanner data for %s symbol(s).",
            len(symbols),
        )
        return None

    return data


def extract_symbol_history(
    downloaded_data: pd.DataFrame,
    symbol: str,
    batch_size: int,
) -> pd.DataFrame | None:
    """
    Extract one symbol from a yfinance response.

    Supports:
    - single-symbol flat columns
    - field -> ticker MultiIndex
    - ticker -> field MultiIndex
    """
    if downloaded_data is None or downloaded_data.empty:
        return None

    required_columns = {
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    }

    try:
        if not isinstance(downloaded_data.columns, pd.MultiIndex):
            if batch_size != 1:
                return None

            history = downloaded_data.copy()
        else:
            level_zero = {
                str(value)
                for value in downloaded_data.columns.get_level_values(0)
            }
            level_one = {
                str(value)
                for value in downloaded_data.columns.get_level_values(1)
            }

            if symbol in level_one:
                history = downloaded_data.xs(
                    symbol,
                    axis=1,
                    level=1,
                    drop_level=True,
                ).copy()
            elif symbol in level_zero:
                history = downloaded_data.xs(
                    symbol,
                    axis=1,
                    level=0,
                    drop_level=True,
                ).copy()
            else:
                return None

        if isinstance(history.columns, pd.MultiIndex):
            history.columns = history.columns.get_level_values(0)

        history.columns = [
            str(column).strip()
            for column in history.columns
        ]

        if not required_columns.issubset(set(history.columns)):
            return None

        history = history[
            ["Open", "High", "Low", "Close", "Volume"]
        ].copy()

        for column in history.columns:
            history[column] = pd.to_numeric(
                history[column],
                errors="coerce",
            )

        history = history.dropna(subset=["Close"])

        if history.empty:
            return None

        return history

    except (KeyError, IndexError, TypeError, ValueError):
        logger.exception("History extraction failed for %s.", symbol)
        return None


# =========================================================
# Scanner presentation and risk helpers
# =========================================================

def classify_risk_level(atr_percent: float) -> str:
    """Classify volatility from ATR as a percentage of price."""
    if atr_percent < 2.0:
        return "LOW"
    if atr_percent < 4.0:
        return "MEDIUM"
    return "HIGH"


def classify_trend(
    *,
    price: float,
    sma_20: float,
    sma_50: float,
    macd: float,
    macd_signal: float,
    twenty_day_change: float,
) -> str:
    """Return a frontend-friendly technical trend label."""
    bullish_structure = price > sma_20 > sma_50
    bearish_structure = price < sma_20 < sma_50

    if (
        bullish_structure
        and macd > macd_signal
        and twenty_day_change >= 5.0
    ):
        return "STRONG BULLISH"

    if (
        bearish_structure
        and macd < macd_signal
        and twenty_day_change <= -5.0
    ):
        return "STRONG BEARISH"

    if price > sma_20 and (sma_20 >= sma_50 or macd > macd_signal):
        return "BULLISH"

    if price < sma_20 and (sma_20 <= sma_50 or macd < macd_signal):
        return "BEARISH"

    return "NEUTRAL"


def recommendation_from_score(score: int) -> str:
    """Return the recommendation labels expected by the V3 frontend."""
    if score >= 90:
        return "STRONG BUY"
    if score >= 75:
        return "BUY"
    if score >= 55:
        return "WATCH"
    if score >= 35:
        return "AVOID"
    return "STRONG SELL"


def build_trade_plan(price: float) -> dict[str, float]:
    """
    Build the scanner's default paper-trade plan.

    These values intentionally match the current Trade Center defaults:
    a 5% stop and a 10% target, which produces a 2:1 reward/risk ratio.
    """
    stop_loss = price * 0.95
    take_profit = price * 1.10

    risk_per_share = max(price - stop_loss, 0.0)
    reward_per_share = max(take_profit - price, 0.0)

    risk_reward_ratio = (
        reward_per_share / risk_per_share
        if risk_per_share > 0
        else 0.0
    )

    return {
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
        "risk_reward_ratio": round(risk_reward_ratio, 2),
    }


# =========================================================
# Ranking
# =========================================================

def rank_candidate(
    price: float,
    sma_20: float,
    sma_50: float,
    rsi: float,
    macd: float,
    macd_signal: float,
    macd_histogram: float,
    one_day_change: float,
    five_day_change: float,
    twenty_day_change: float,
    volume_ratio: float,
    bollinger_upper: float,
    bollinger_lower: float,
) -> dict[str, Any]:
    """
    Score a stock from technical conditions.

    The score is intentionally balanced so the scanner returns useful
    BUY, HOLD, and SELL candidates.
    """
    score = 50
    reasons: list[str] = []

    # Trend
    if price > sma_20:
        score += 8
        reasons.append("Price is above its 20-day moving average.")
    else:
        score -= 8
        reasons.append("Price is below its 20-day moving average.")

    if sma_20 > sma_50:
        score += 10
        reasons.append(
            "The short-term trend is above the medium-term trend."
        )
    else:
        score -= 10
        reasons.append(
            "The short-term trend is below the medium-term trend."
        )

    # MACD
    if macd > macd_signal:
        score += 8
        reasons.append("MACD is above its signal line.")
    else:
        score -= 8
        reasons.append("MACD is below its signal line.")

    if macd_histogram > 0:
        score += 3
    elif macd_histogram < 0:
        score -= 3

    # RSI
    if 45 <= rsi <= 65:
        score += 5
        reasons.append("RSI supports healthy momentum.")
    elif 30 <= rsi < 45:
        score += 2
        reasons.append("RSI is slightly weak but not deeply oversold.")
    elif rsi < 30:
        score += 4
        reasons.append("RSI is in oversold territory.")
    elif 65 < rsi <= 75:
        score -= 2
        reasons.append("RSI is becoming elevated.")
    else:
        score -= 7
        reasons.append("RSI is strongly overbought.")

    # Momentum
    if five_day_change > 2:
        score += 5
        reasons.append("Five-day price momentum is positive.")
    elif five_day_change < -2:
        score -= 5
        reasons.append("Five-day price momentum is negative.")

    if twenty_day_change > 5:
        score += 5
        reasons.append("Twenty-day trend is positive.")
    elif twenty_day_change < -5:
        score -= 5
        reasons.append("Twenty-day trend is negative.")

    if one_day_change > 5:
        score -= 2
        reasons.append("The stock made an unusually large one-day move.")
    elif one_day_change < -5:
        score -= 2
        reasons.append("The stock had a sharp one-day decline.")

    # Volume
    if volume_ratio >= 1.5:
        if one_day_change >= 0:
            score += 5
            reasons.append(
                "Positive movement is supported by strong volume."
            )
        else:
            score -= 5
            reasons.append(
                "Selling pressure is supported by strong volume."
            )
    elif volume_ratio < 0.60:
        score -= 2
        reasons.append("Current volume is well below average.")

    # Bollinger position
    if price < bollinger_lower:
        score += 3
        reasons.append("Price is below the lower Bollinger Band.")
    elif price > bollinger_upper:
        score -= 3
        reasons.append("Price is above the upper Bollinger Band.")

    score = max(0, min(100, int(round(score))))

    if score >= 65:
        signal = "BUY"
    elif score <= 35:
        signal = "SELL"
    else:
        signal = "HOLD"

    rating = recommendation_from_score(score)

    confidence = min(
        95,
        max(
            50,
            50 + int(abs(score - 50) * 1.5),
        ),
    )

    return {
        "score": score,
        "signal": signal,
        "rating": rating,
        "confidence": confidence,
        "reasons": reasons[:6],
    }


# =========================================================
# Stock analysis
# =========================================================

def analyze_stock(
    symbol: str,
    history: pd.DataFrame,
) -> dict[str, Any] | None:
    """Analyze one stock and return a scanner candidate."""
    if history is None or len(history) < MINIMUM_HISTORY_DAYS:
        return None

    closes = pd.to_numeric(
        history["Close"],
        errors="coerce",
    ).dropna()

    volumes = pd.to_numeric(
        history["Volume"],
        errors="coerce",
    ).dropna()

    if len(closes) < MINIMUM_HISTORY_DAYS or len(volumes) < 20:
        return None

    price = safe_float(closes.iloc[-1])
    previous_close = safe_float(closes.iloc[-2])

    if price is None or previous_close is None:
        return None

    if price < MINIMUM_PRICE:
        return None

    average_volume = safe_float(volumes.tail(20).mean())
    latest_volume = safe_float(volumes.iloc[-1])

    if average_volume is None or latest_volume is None:
        return None

    if average_volume < MINIMUM_AVERAGE_VOLUME:
        return None

    sma_20 = safe_float(closes.tail(20).mean())
    sma_50 = safe_float(closes.tail(50).mean())
    rsi = calculate_rsi(closes)
    macd_values = calculate_macd(closes)
    bollinger = calculate_bollinger_bands(closes)
    atr = calculate_atr(history)

    if (
        sma_20 is None
        or sma_50 is None
        or rsi is None
        or macd_values is None
        or bollinger is None
        or atr is None
    ):
        return None

    macd, macd_signal, macd_histogram = macd_values
    bollinger_upper, bollinger_middle, bollinger_lower = bollinger

    volume_ratio = (
        latest_volume / average_volume
        if average_volume > 0
        else 0.0
    )

    one_day_change = percentage_change(
        price,
        previous_close,
    )

    five_day_change = 0.0

    if len(closes) >= 6:
        five_day_price = safe_float(closes.iloc[-6])

        if five_day_price is not None:
            five_day_change = percentage_change(
                price,
                five_day_price,
            )

    twenty_day_change = 0.0

    if len(closes) >= 21:
        twenty_day_price = safe_float(closes.iloc[-21])

        if twenty_day_price is not None:
            twenty_day_change = percentage_change(
                price,
                twenty_day_price,
            )

    atr_percent = (
        (atr / price) * 100.0
        if price > 0
        else 0.0
    )

    ranking = rank_candidate(
        price=price,
        sma_20=sma_20,
        sma_50=sma_50,
        rsi=rsi,
        macd=macd,
        macd_signal=macd_signal,
        macd_histogram=macd_histogram,
        one_day_change=one_day_change,
        five_day_change=five_day_change,
        twenty_day_change=twenty_day_change,
        volume_ratio=volume_ratio,
        bollinger_upper=bollinger_upper,
        bollinger_lower=bollinger_lower,
    )

    reasons = list(ranking["reasons"])

    risk_level = classify_risk_level(atr_percent)

    trend = classify_trend(
        price=price,
        sma_20=sma_20,
        sma_50=sma_50,
        macd=macd,
        macd_signal=macd_signal,
        twenty_day_change=twenty_day_change,
    )

    trade_plan = build_trade_plan(price)

    return {
        "symbol": symbol,
        "price": round(price, 2),
        "change": round(one_day_change, 2),
        "five_day_change": round(five_day_change, 2),
        "twenty_day_change": round(twenty_day_change, 2),
        "score": ranking["score"],
        "scanner_score": ranking["score"],
        "signal": ranking["signal"],
        "rating": ranking["rating"],
        "recommendation": ranking["rating"],
        "confidence": ranking["confidence"],
        "trend": trend,
        "trend_strength": trend,
        "risk": risk_level,
        "risk_level": risk_level,
        "rsi": round(rsi, 2),
        "ma20": round(sma_20, 2),
        "ma50": round(sma_50, 2),
        "macd": round(macd, 4),
        "macd_signal": round(macd_signal, 4),
        "macd_histogram": round(macd_histogram, 4),
        "volume_ratio": round(volume_ratio, 2),
        "average_volume": int(round(average_volume)),
        "atr": round(atr, 2),
        "atr_percent": round(atr_percent, 2),
        "stop_loss": trade_plan["stop_loss"],
        "take_profit": trade_plan["take_profit"],
        "risk_reward": trade_plan["risk_reward_ratio"],
        "risk_reward_ratio": trade_plan["risk_reward_ratio"],
        "bollinger_upper": round(bollinger_upper, 2),
        "bollinger_middle": round(bollinger_middle, 2),
        "bollinger_lower": round(bollinger_lower, 2),
        "signals": reasons,
        "reason": list(reasons),
    }


# =========================================================
# Result selection
# =========================================================

def _candidate_sort_key(
    stock: dict[str, Any],
) -> tuple[float, float, float]:
    """
    Return a signal-aware sort key.

    BUY candidates rank from strongest to weakest.
    SELL candidates rank from most bearish to least bearish.
    HOLD candidates rank by distance from neutral.
    """
    signal = str(stock.get("signal", "HOLD")).upper()
    score = float(stock.get("score", 50) or 50)
    volume_ratio = float(stock.get("volume_ratio", 0) or 0)
    five_day_change = float(stock.get("five_day_change", 0) or 0)

    if signal == "SELL":
        return (
            100.0 - score,
            volume_ratio,
            -five_day_change,
        )

    if signal == "HOLD":
        return (
            abs(score - 50.0),
            volume_ratio,
            abs(five_day_change),
        )

    return (
        score,
        volume_ratio,
        five_day_change,
    )


def _select_balanced_results(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep the strongest candidates for every signal category."""
    grouped: dict[str, list[dict[str, Any]]] = {
        signal: []
        for signal in SIGNAL_ORDER
    }

    for stock in results:
        signal = str(stock.get("signal", "HOLD")).upper()

        if signal not in grouped:
            signal = "HOLD"

        grouped[signal].append(stock)

    selected: list[dict[str, Any]] = []

    for signal in SIGNAL_ORDER:
        group = grouped[signal]
        group.sort(
            key=_candidate_sort_key,
            reverse=True,
        )

        selected.extend(group[:MAX_RESULTS_PER_SIGNAL])

    # Preserve predictable top-level ordering for clients.
    selected.sort(
        key=lambda stock: (
            SIGNAL_ORDER.index(
                str(stock.get("signal", "HOLD")).upper()
                if str(stock.get("signal", "HOLD")).upper() in SIGNAL_ORDER
                else "HOLD"
            ),
            -_candidate_sort_key(stock)[0],
            -_candidate_sort_key(stock)[1],
            -_candidate_sort_key(stock)[2],
        )
    )

    for index, stock in enumerate(selected, start=1):
        stock["rank"] = index
        stock["scanner_rank"] = index

    return selected


# =========================================================
# Scanner
# =========================================================

def scan_market(
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """
    Scan the market universe, rank valid stocks, and return balanced results.

    Fresh results are cached for 15 minutes. If a refresh fails, the most
    recent stale cache is returned when available.
    """
    if not force_refresh:
        fresh_cache = _get_cached_results(allow_stale=False)

        if fresh_cache is not None:
            logger.info("Returning fresh cached scanner results.")
            return fresh_cache

    # Only one request may perform the expensive full-market scan.
    with _scan_lock:
        # Another request may have completed a scan while this request waited.
        if not force_refresh:
            fresh_cache = _get_cached_results(allow_stale=False)

            if fresh_cache is not None:
                logger.info(
                    "Returning scanner results refreshed by another request."
                )
                return fresh_cache

        stale_cache = _get_cached_results(allow_stale=True)

        try:
            raw_symbols = load_market_universe()
        except Exception:
            logger.exception("The scanner could not load its market universe.")

            if stale_cache is not None:
                logger.warning(
                    "Returning stale scanner cache after universe failure."
                )
                return stale_cache

            return []

        symbols: list[str] = []
        seen_symbols: set[str] = set()

        for raw_symbol in raw_symbols or []:
            symbol = clean_symbol(raw_symbol)

            if symbol and symbol not in seen_symbols:
                symbols.append(symbol)
                seen_symbols.add(symbol)

        if not symbols:
            logger.error("The scanner market universe is empty.")

            if stale_cache is not None:
                logger.warning(
                    "Returning stale scanner cache because the universe is empty."
                )
                return stale_cache

            return []

        logger.info("Beginning scan of %s stocks.", len(symbols))

        results: list[dict[str, Any]] = []
        extraction_count = 0
        analyzed_count = 0
        successful_batch_count = 0

        batches = list(
            split_into_batches(
                symbols,
                DOWNLOAD_BATCH_SIZE,
            )
        )

        for batch_index, batch in enumerate(batches, start=1):
            logger.info(
                "Scanning batch %s/%s with %s symbols.",
                batch_index,
                len(batches),
                len(batch),
            )

            downloaded_data = download_batch(batch)

            if downloaded_data is None:
                continue

            successful_batch_count += 1

            for symbol in batch:
                history = extract_symbol_history(
                    downloaded_data,
                    symbol,
                    len(batch),
                )

                if history is None:
                    continue

                extraction_count += 1

                try:
                    candidate = analyze_stock(
                        symbol,
                        history,
                    )
                except Exception:
                    logger.exception(
                        "Scanner analysis failed for %s.",
                        symbol,
                    )
                    continue

                if candidate is not None:
                    analyzed_count += 1
                    results.append(candidate)

        if successful_batch_count == 0:
            logger.error("Every Yahoo Finance scanner batch failed.")

            if stale_cache is not None:
                logger.warning(
                    "Returning stale scanner cache after download failure."
                )
                return stale_cache

            return []

        if not results:
            logger.warning("The scanner produced no valid candidates.")

            if stale_cache is not None:
                logger.warning(
                    "Returning stale scanner cache because no candidates passed."
                )
                return stale_cache

            return []

        final_results = _select_balanced_results(results)
        _set_cached_results(final_results)

        logger.info(
            (
                "Market scan completed. Extracted %s histories, "
                "%s stocks passed filters, returning %s results."
            ),
            extraction_count,
            analyzed_count,
            len(final_results),
        )

        return _copy_results(final_results)


def clear_scanner_cache() -> None:
    """Clear scanner results manually."""
    with _cache_lock:
        _scan_cache["results"] = []
        _scan_cache["updated_at"] = 0.0

def get_market_regime(
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Classify the broad U.S. market using SPY and QQQ daily trend data.

    Returns one of:
    - BULLISH
    - NEUTRAL
    - BEARISH
    - UNKNOWN
    """
    current_time = time.time()

    with _cache_lock:
        cached_result = _market_regime_cache.get("result")
        cached_time = float(
            _market_regime_cache.get("updated_at", 0.0) or 0.0
        )

        cache_is_fresh = (
            cached_result is not None
            and current_time - cached_time
            < MARKET_REGIME_CACHE_SECONDS
        )

        if cache_is_fresh and not force_refresh:
            return copy.deepcopy(cached_result)

    downloaded_data = download_batch(
        list(MARKET_REGIME_SYMBOLS)
    )

    if downloaded_data is None:
        with _cache_lock:
            cached_result = _market_regime_cache.get("result")

        if cached_result is not None:
            return copy.deepcopy(cached_result)

        return {
            "regime": "UNKNOWN",
            "score": 0,
            "symbols": {},
            "reason": "Market data download failed.",
        }

    symbol_results: dict[str, Any] = {}
    market_score = 0

    for symbol in MARKET_REGIME_SYMBOLS:
        history = extract_symbol_history(
            downloaded_data,
            symbol,
            len(MARKET_REGIME_SYMBOLS),
        )

        if (
            history is None
            or len(history) < MARKET_REGIME_MINIMUM_HISTORY_DAYS
        ):
            symbol_results[symbol] = {
                "available": False,
            }
            continue

        closes = pd.to_numeric(
            history["Close"],
            errors="coerce",
        ).dropna()

        if len(closes) < MARKET_REGIME_MINIMUM_HISTORY_DAYS:
            symbol_results[symbol] = {
                "available": False,
            }
            continue

        price = safe_float(closes.iloc[-1])
        sma_20 = safe_float(
            closes.rolling(20).mean().iloc[-1]
        )
        sma_50 = safe_float(
            closes.rolling(50).mean().iloc[-1]
        )

        change_5d = None
        change_20d = None

        if len(closes) >= 6:
            change_5d = percentage_change(
                float(closes.iloc[-1]),
                float(closes.iloc[-6]),
            )

        if len(closes) >= 21:
            change_20d = percentage_change(
                float(closes.iloc[-1]),
                float(closes.iloc[-21]),
            )

        symbol_score = 0

        if (
            price is not None
            and sma_20 is not None
            and price > sma_20
        ):
            symbol_score += 1
        else:
            symbol_score -= 1

        if (
            sma_20 is not None
            and sma_50 is not None
            and sma_20 > sma_50
        ):
            symbol_score += 1
        else:
            symbol_score -= 1

        if change_20d is not None:
            if change_20d > 0:
                symbol_score += 1
            elif change_20d < 0:
                symbol_score -= 1

        market_score += symbol_score

        symbol_results[symbol] = {
            "available": True,
            "price": round(price, 2)
            if price is not None
            else None,
            "sma_20": round(sma_20, 2)
            if sma_20 is not None
            else None,
            "sma_50": round(sma_50, 2)
            if sma_50 is not None
            else None,
            "change_5d_percent": round(change_5d, 2)
            if change_5d is not None
            else None,
            "change_20d_percent": round(change_20d, 2)
            if change_20d is not None
            else None,
            "score": symbol_score,
        }

    available_count = sum(
        1
        for result in symbol_results.values()
        if result.get("available")
    )

    if available_count == 0:
        regime = "UNKNOWN"
        reason = "No usable SPY or QQQ history."
    elif market_score >= 4:
        regime = "BULLISH"
        reason = "SPY and QQQ show strong positive trend alignment."
    elif market_score <= -4:
        regime = "BEARISH"
        reason = "SPY and QQQ show strong negative trend alignment."
    else:
        regime = "NEUTRAL"
        reason = "Broad-market trend signals are mixed."

    result = {
        "regime": regime,
        "score": market_score,
        "symbols": symbol_results,
        "reason": reason,
        "updated_at": time.time(),
    }

    with _cache_lock:
        _market_regime_cache["result"] = copy.deepcopy(result)
        _market_regime_cache["updated_at"] = time.time()

    return result

def get_symbol_news_context(
    symbol: str,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Load recent Yahoo Finance news for one symbol.

    This is observation-only context. It does not place,
    block, or modify trades by itself.
    """
    normalized_symbol = clean_symbol(symbol)
    current_time = time.time()

    with _cache_lock:
        cached = _news_cache.get(normalized_symbol)

        if cached is not None:
            cached_time = float(
                cached.get("updated_at", 0.0) or 0.0
            )

            if (
                not force_refresh
                and current_time - cached_time
                < NEWS_CACHE_SECONDS
            ):
                return copy.deepcopy(
                    cached["result"]
                )

    try:
        raw_news = yf.Ticker(
            normalized_symbol
        ).news
    except Exception:
        logger.exception(
            "News download failed for %s.",
            normalized_symbol,
        )
        raw_news = []

    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(hours=NEWS_LOOKBACK_HOURS)
    )

    articles: list[dict[str, Any]] = []

    for item in raw_news or []:
        if not isinstance(item, dict):
            continue

        content = item.get("content")

        if not isinstance(content, dict):
            continue

        title = str(
            content.get("title") or ""
        ).strip()

        summary = str(
            content.get("summary") or ""
        ).strip()

        published_text = str(
            content.get("pubDate") or ""
        ).strip()

        try:
            published_at = datetime.fromisoformat(
                published_text.replace(
                    "Z",
                    "+00:00",
                )
            )
        except ValueError:
            continue

        if published_at < cutoff:
            continue

        provider = content.get("provider")
        provider_name = None

        if isinstance(provider, dict):
            provider_name = (
                provider.get("displayName")
            )

        canonical_url = content.get(
            "canonicalUrl"
        )
        url = None

        if isinstance(canonical_url, dict):
            url = canonical_url.get("url")

        articles.append({
            "title": title,
            "summary": summary,
            "published_at": (
                published_at.isoformat()
            ),
            "provider": provider_name,
            "url": url,
            "content_type": (
                content.get("contentType")
            ),
        })

        if len(articles) >= NEWS_MAX_ARTICLES:
            break

    result = {
        "symbol": normalized_symbol,
        "available": bool(articles),
        "article_count": len(articles),
        "lookback_hours": NEWS_LOOKBACK_HOURS,
        "articles": articles,
        "updated_at": time.time(),
    }

    with _cache_lock:
        _news_cache[
            normalized_symbol
        ] = {
            "result": copy.deepcopy(result),
            "updated_at": time.time(),
        }

    return result

def score_symbol_news_context(
    news_context: dict[str, Any],
) -> dict[str, Any]:
    """
    Score recent headlines using simple bounded keyword matching.

    This is observation-only context and does not directly
    place, block, or modify trades.
    """
    articles = news_context.get("articles") or []

    positive_hits: list[str] = []
    negative_hits: list[str] = []
    raw_score = 0

    for article in articles:
        title = str(
            article.get("title") or ""
        ).lower()

        summary = str(
            article.get("summary") or ""
        ).lower()

        combined_text = f"{title} {summary}"

        for term in NEWS_POSITIVE_TERMS:
            if term in combined_text:
                positive_hits.append(term)
                raw_score += 1

        for term in NEWS_NEGATIVE_TERMS:
            if term in combined_text:
                negative_hits.append(term)
                raw_score -= 1

    bounded_score = max(
        -5,
        min(5, raw_score),
    )

    if bounded_score >= 2:
        sentiment = "POSITIVE"
    elif bounded_score <= -2:
        sentiment = "NEGATIVE"
    else:
        sentiment = "NEUTRAL"

    return {
        "sentiment": sentiment,
        "score": bounded_score,
        "raw_score": raw_score,
        "positive_hits": sorted(
            set(positive_hits)
        ),
        "negative_hits": sorted(
            set(negative_hits)
        ),
    }
