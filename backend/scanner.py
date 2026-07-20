import yfinance as yf


WATCHLIST = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "TSLA",
    "AMD"
]


def calculate_rsi(prices, period=14):

    changes = prices.diff()

    gains = changes.clip(lower=0)
    losses = -changes.clip(upper=0)

    average_gain = gains.rolling(period).mean()
    average_loss = losses.rolling(period).mean()

    rs = average_gain / average_loss

    rsi = 100 - (100 / (1 + rs))

    return rsi.iloc[-1]


def scan_market():

    results = []

    for symbol in WATCHLIST:

        try:

            stock = yf.Ticker(symbol)

            history = stock.history(
                period="6mo",
                interval="1d"
            )

            if history.empty or len(history) < 50:
                continue

            closes = history["Close"]
            volumes = history["Volume"]

            price = float(closes.iloc[-1])
            previous_close = float(closes.iloc[-2])

            change = (
                (price - previous_close)
                / previous_close
            ) * 100

            ma20 = float(
                closes.rolling(20).mean().iloc[-1]
            )

            ma50 = float(
                closes.rolling(50).mean().iloc[-1]
            )

            rsi = float(calculate_rsi(closes))

            average_volume = float(
                volumes.rolling(20).mean().iloc[-1]
            )

            current_volume = float(
                volumes.iloc[-1]
            )

            volume_ratio = 0

            if average_volume > 0:
                volume_ratio = (
                    current_volume / average_volume
                )

            score = 50

            if price > ma20:
                score += 10

            if price > ma50:
                score += 15

            if ma20 > ma50:
                score += 10

            if 45 <= rsi <= 65:
                score += 10
            elif rsi > 75:
                score -= 10

            if volume_ratio > 1.2:
                score += 5

            score = max(0, min(100, score))

            signals = []

            signals.append(
                "Above 20-day average"
                if price > ma20
                else "Below 20-day average"
            )

            signals.append(
                "Above 50-day average"
                if price > ma50
                else "Below 50-day average"
            )

            signals.append(
                f"RSI {round(rsi, 1)}"
            )

            results.append({
                "symbol": symbol,
                "price": round(price, 2),
                "change": round(change, 2),
                "score": round(score, 1),
                "rsi": round(rsi, 1),
                "ma20": round(ma20, 2),
                "ma50": round(ma50, 2),
                "volume_ratio": round(
                    volume_ratio,
                    2
                ),
                "signals": signals
            })

        except Exception as error:

            print(
                f"Scanner error for {symbol}: {error}"
            )

    results.sort(
        key=lambda stock: stock["score"],
        reverse=True
    )

    return results