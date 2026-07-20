import time

import yfinance as yf


CACHE_DURATION_SECONDS = 300
_chart_cache = {}


def get_chart_data(symbol: str):

    symbol = symbol.strip().upper()

    if not symbol:
        return None

    current_time = time.time()
    cached_result = _chart_cache.get(symbol)

    if cached_result:
        cache_age = current_time - cached_result["saved_at"]

        if cache_age < CACHE_DURATION_SECONDS:
            return cached_result["data"]

    try:
        stock = yf.Ticker(symbol)

        history = stock.history(
            period="6mo",
            interval="1d",
            auto_adjust=False
        )

    except Exception as error:
        print(f"Chart request failed for {symbol}: {error}")
        return None

    if history.empty:
        return None

    closes = history["Close"]
    ma20_values = closes.rolling(window=20).mean()
    ma50_values = closes.rolling(window=50).mean()

    candles = []
    volume = []
    ma20 = []
    ma50 = []

    for index, row in history.iterrows():

        date = index.strftime("%Y-%m-%d")

        open_price = row["Open"]
        high_price = row["High"]
        low_price = row["Low"]
        close_price = row["Close"]
        volume_value = row["Volume"]

        # Skip incomplete market rows.
        if any(
            value != value
            for value in [
                open_price,
                high_price,
                low_price,
                close_price
            ]
        ):
            continue

        candles.append({
            "time": date,
            "open": round(float(open_price), 2),
            "high": round(float(high_price), 2),
            "low": round(float(low_price), 2),
            "close": round(float(close_price), 2)
        })

        volume.append({
            "time": date,
            "value": int(volume_value)
            if volume_value == volume_value
            else 0,

            # Lightweight Charts uses this to color volume bars.
            "color": (
                "rgba(34, 197, 94, 0.55)"
                if close_price >= open_price
                else "rgba(239, 68, 68, 0.55)"
            )
        })

        average_20 = ma20_values.loc[index]
        average_50 = ma50_values.loc[index]

        if average_20 == average_20:
            ma20.append({
                "time": date,
                "value": round(float(average_20), 2)
            })

        if average_50 == average_50:
            ma50.append({
                "time": date,
                "value": round(float(average_50), 2)
            })

    if not candles:
        return None

    chart_data = {
        "symbol": symbol,
        "candles": candles,
        "volume": volume,
        "ma20": ma20,
        "ma50": ma50
    }

    _chart_cache[symbol] = {
        "saved_at": current_time,
        "data": chart_data
    }

    return chart_data