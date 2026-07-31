from typing import Any


def clamp(
    value: float,
    minimum: float,
    maximum: float,
) -> float:
    """
    Keep a number inside a range.
    """
    return max(minimum, min(maximum, value))


def get_number(
    indicators: dict[str, Any],
    key: str,
    default: float = 0.0,
) -> float:
    """
    Safely retrieve a numeric indicator.
    """
    value = indicators.get(key, default)

    try:
        value = float(value)

        if value != value:  # NaN
            return default

        if value == float("inf"):
            return default

        if value == float("-inf"):
            return default

        return value

    except (TypeError, ValueError):
        return default


def rank_stock(
    indicators: dict[str, Any],
) -> dict[str, Any]:
    """
    Score a stock using technical indicators.

    Scores range from 0–100.

    This is intended for ranking opportunities within
    the paper-trading scanner and is not a prediction.
    """

    score = 50.0
    reasons: list[str] = []

    price = get_number(indicators, "price")
    sma_20 = get_number(indicators, "sma_20")
    sma_50 = get_number(indicators, "sma_50")
    rsi = get_number(indicators, "rsi")

    one_day_change = get_number(
        indicators,
        "one_day_change",
    )

    five_day_change = get_number(
        indicators,
        "five_day_change",
    )

    twenty_day_change = get_number(
        indicators,
        "twenty_day_change",
    )

    volume_ratio = get_number(
        indicators,
        "volume_ratio",
        1.0,
    )

    macd = get_number(indicators, "macd")
    macd_signal = get_number(
        indicators,
        "macd_signal",
    )

    bollinger_upper = get_number(
        indicators,
        "bollinger_upper",
    )

    bollinger_lower = get_number(
        indicators,
        "bollinger_lower",
    )

    atr_percent = get_number(
        indicators,
        "atr_percent",
    )

    # =====================================================
    # Trend
    # =====================================================

    if price > sma_20:
        score += 8
        reasons.append(
            "Price is above the 20-day moving average."
        )
    else:
        score -= 8
        reasons.append(
            "Price is below the 20-day moving average."
        )

    if price > sma_50:
        score += 10
        reasons.append(
            "Price is above the 50-day moving average."
        )
    else:
        score -= 10
        reasons.append(
            "Price is below the 50-day moving average."
        )

    if sma_20 > sma_50:
        score += 12
        reasons.append(
            "The short-term trend is stronger than the medium-term trend."
        )
    else:
        score -= 10
        reasons.append(
            "The short-term trend is weaker than the medium-term trend."
        )

    # =====================================================
    # RSI
    # =====================================================

    if 50 <= rsi <= 65:
        score += 9
        reasons.append(
            f"RSI shows healthy momentum at {rsi:.1f}."
        )

    elif 40 <= rsi < 50:
        score += 3
        reasons.append(
            f"RSI is neutral at {rsi:.1f}."
        )

    elif 30 <= rsi < 40:
        score += 1
        reasons.append(
            f"RSI is approaching oversold territory at {rsi:.1f}."
        )

    elif rsi < 30:
        score -= 4
        reasons.append(
            f"RSI is deeply oversold at {rsi:.1f}."
        )

    elif 65 < rsi <= 72:
        score += 3
        reasons.append(
            f"RSI shows strong momentum at {rsi:.1f}."
        )

    else:
        score -= 9
        reasons.append(
            f"RSI may be overextended at {rsi:.1f}."
        )

    # =====================================================
    # Momentum
    # =====================================================

    if 0 < five_day_change <= 8:
        score += 8
        reasons.append(
            f"Five-day momentum is positive at {five_day_change:.1f}%."
        )

    elif five_day_change > 8:
        score += 3
        reasons.append(
            f"Five-day momentum is unusually strong at {five_day_change:.1f}%."
        )

    elif five_day_change <= -8:
        score -= 10
        reasons.append(
            f"Five-day momentum is weak at {five_day_change:.1f}%."
        )

    else:
        score -= 3
        reasons.append(
            f"Five-day momentum is slightly negative at {five_day_change:.1f}%."
        )

    if twenty_day_change > 0:
        score += 5
        reasons.append(
            f"Twenty-day performance is positive at {twenty_day_change:.1f}%."
        )
    else:
        score -= 5
        reasons.append(
            f"Twenty-day performance is negative at {twenty_day_change:.1f}%."
        )

    if one_day_change > 5:
        score -= 2
        reasons.append(
            "The stock made a large one-day move and may be temporarily extended."
        )

    # =====================================================
    # Volume
    # =====================================================

    if volume_ratio >= 1.5:
        score += 8
        reasons.append(
            "Volume is significantly above its 20-day average."
        )

    elif volume_ratio >= 1.1:
        score += 4
        reasons.append(
            "Volume is above its 20-day average."
        )

    elif volume_ratio < 0.6:
        score -= 4
        reasons.append(
            "Volume is well below its 20-day average."
        )

    else:
        reasons.append(
            "Volume is near its 20-day average."
        )

    # =====================================================
    # MACD
    # =====================================================

    if macd > macd_signal:
        score += 7
        reasons.append(
            "MACD is above its signal line."
        )
    else:
        score -= 7
        reasons.append(
            "MACD is below its signal line."
        )

    # =====================================================
    # Bollinger Bands
    # =====================================================

    if price > bollinger_upper:
        score -= 4
        reasons.append(
            "Price is above the upper Bollinger Band and may be extended."
        )

    elif price < bollinger_lower:
        score += 2
        reasons.append(
            "Price is below the lower Bollinger Band."
        )

    # =====================================================
    # Volatility
    # =====================================================

    if atr_percent > 8:
        score -= 6
        reasons.append(
            "Volatility is extremely high."
        )

    elif atr_percent > 5:
        score -= 2
        reasons.append(
            "Volatility is elevated."
        )

    elif atr_percent < 1:
        score -= 2
        reasons.append(
            "Volatility is unusually low."
        )

    else:
        score += 2

    score = clamp(score, 0, 100)

    if score >= 80:
        signal = "BUY"
        rating = "Strong Opportunity"

    elif score >= 68:
        signal = "BUY"
        rating = "Bullish"

    elif score >= 55:
        signal = "HOLD"
        rating = "Watch"

    elif score >= 40:
        signal = "HOLD"
        rating = "Neutral"

    else:
        signal = "SELL"
        rating = "Weak"

    confidence = clamp(
        55 + abs(score - 50),
        55,
        95,
    )

    return {
        "score": round(score, 1),
        "signal": signal,
        "rating": rating,
        "confidence": round(confidence),
        "reasons": reasons,
    }