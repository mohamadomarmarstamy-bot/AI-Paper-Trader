import math
from typing import Any

import yfinance as yf
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from chart_data import get_chart_data
from database import initialize_database
from indicators import (
    calculate_rsi,
    calculate_sma,
    calculate_volume_ratio,
    percentage_change,
    safe_float,
)
from paper_trader import PaperTrader
from scanner import scan_market


APP_VERSION = "2.3.0"

app = FastAPI(
    title="AI Paper Trader",
    description=(
        "Paper trading, portfolio tracking, stock search, "
        "chart data, strategy analysis, risk management, and market scanning API."
    ),
    version=APP_VERSION,
)

# Create the required SQLite tables before the API begins serving requests.
initialize_database()

# The frontend may run locally on port 5500 or be deployed separately.
# Credentials are disabled, so wildcard origins are valid here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

trader = PaperTrader()


# =========================================================
# General helpers
# =========================================================

def clean_symbol(symbol: Any) -> str:
    """
    Normalize a stock symbol for Yahoo Finance.

    Yahoo uses a dash for share classes, such as BRK-B.
    """
    return str(symbol or "").strip().upper().replace(".", "-")


def clean_error_message(error: Exception) -> str:
    """Create a readable message from an exception."""
    message = str(error).strip()
    return message or error.__class__.__name__


def is_valid_price(value: Any) -> bool:
    """Return True when a value is a finite, positive price."""
    price = safe_float(value)
    return price is not None and price > 0


def normalize_reason_list(reason: Any) -> list[str]:
    """Normalize strategy or scanner reasons into a list of strings."""
    if isinstance(reason, list):
        return [
            str(item).strip()
            for item in reason
            if str(item).strip()
        ]

    if isinstance(reason, str) and reason.strip():
        return [reason.strip()]

    return []


# =========================================================
# Yahoo Finance helpers
# =========================================================

def fetch_current_price(symbol: str) -> float | None:
    """
    Retrieve the most recent available price for a symbol.

    Fast info is attempted first. Recent historical data is used as
    a fallback because Yahoo may not always return fast-info data.
    """
    normalized_symbol = clean_symbol(symbol)

    if not normalized_symbol:
        return None

    try:
        ticker = yf.Ticker(normalized_symbol)

        # Fast-info lookup
        try:
            fast_info = ticker.fast_info
            price = None

            if hasattr(fast_info, "get"):
                price = fast_info.get("last_price")

            if price is None:
                try:
                    price = fast_info["last_price"]
                except Exception:
                    price = None

            if is_valid_price(price):
                return round(float(price), 2)

        except Exception:
            # Historical data below acts as the fallback.
            pass

        history = ticker.history(
            period="5d",
            interval="1d",
            auto_adjust=False,
            actions=False,
            timeout=15,
        )

        if (
            history is None
            or history.empty
            or "Close" not in history.columns
        ):
            return None

        closes = history["Close"].dropna()

        if closes.empty:
            return None

        price = safe_float(closes.iloc[-1])

        if price is None or price <= 0:
            return None

        return round(price, 2)

    except Exception as error:
        print(
            f"Current-price error for "
            f"{normalized_symbol}: {clean_error_message(error)}"
        )
        return None


def fetch_strategy_history(symbol: str):
    try:
        ticker = yf.Ticker(symbol)

        history = ticker.history(
            period="6mo",
            interval="1d",
            auto_adjust=False,
            actions=False,
        )

        print(f"History for {symbol}:")
        print(history.head())
        print(history.tail())
        print("Rows:", len(history))

        if history is None or history.empty:
            return None

        return history.sort_index()

    except Exception as error:
        print(f"Strategy-history error for {symbol}: {error}")
        return None


# =========================================================
# Strategy analysis
# =========================================================

def analyze_symbol(symbol: str) -> dict[str, Any]:
    """
    Analyze one stock using trend, RSI, momentum, and volume.

    The returned structure is designed to match the frontend Strategy
    Lab table:
        symbol
        price
        signal
        confidence
        score
        indicators
        reason
    """
    normalized_symbol = clean_symbol(symbol)

    if not normalized_symbol:
        return {"error": "Symbol is required."}

    history = fetch_strategy_history(normalized_symbol)

    if history is None or history.empty:
        return {
            "error": (
                f"No market data was found for "
                f"{normalized_symbol}."
            )
        }

    required_columns = {"Close"}

    if not required_columns.issubset(history.columns):
        return {
            "error": (
                f"Closing-price data is unavailable for "
                f"{normalized_symbol}."
            )
        }

    close = history["Close"].dropna()

    if len(close) < 50:
        return {
            "error": (
                f"Not enough market data is available for "
                f"{normalized_symbol}."
            )
        }

    current_price = fetch_current_price(normalized_symbol)

    if current_price is None:
        # Use the latest historical close as a final fallback.
        latest_close = safe_float(close.iloc[-1])

        if latest_close is None or latest_close <= 0:
            return {
                "error": (
                    f"Could not retrieve the current price for "
                    f"{normalized_symbol}."
                )
            }

        current_price = round(latest_close, 2)

    sma_20 = calculate_sma(close, 20)
    sma_50 = calculate_sma(close, 50)
    rsi = calculate_rsi(close, 14)

    if sma_20 is None or sma_50 is None or rsi is None:
        return {
            "error": (
                f"Technical indicators could not be calculated for "
                f"{normalized_symbol}."
            )
        }

    five_day_momentum = 0.0

    if len(close) >= 6:
        current_close = safe_float(close.iloc[-1])
        previous_close = safe_float(close.iloc[-6])

        if current_close is not None and previous_close is not None:
            five_day_momentum = percentage_change(
                current_close,
                previous_close,
            )

    volume_data = None

    if "Volume" in history.columns:
        volume_data = calculate_volume_ratio(
            history["Volume"],
            period=20,
        )

    volume_ratio = (
        safe_float(volume_data.get("ratio"))
        if isinstance(volume_data, dict)
        else None
    )

    score = 0
    reasons: list[str] = []

    # -----------------------------------------------------
    # Price trend
    # -----------------------------------------------------

    if current_price > sma_20:
        score += 1
        reasons.append(
            "Price is above the 20-day moving average."
        )
    else:
        score -= 1
        reasons.append(
            "Price is below the 20-day moving average."
        )

    if sma_20 > sma_50:
        score += 1
        reasons.append(
            "The 20-day moving average is above the "
            "50-day moving average."
        )
    else:
        score -= 1
        reasons.append(
            "The 20-day moving average is below the "
            "50-day moving average."
        )

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------

    if rsi < 30:
        score += 2
        reasons.append(
            f"RSI is {rsi:.1f}, indicating strongly "
            "oversold conditions."
        )

    elif rsi < 40:
        score += 1
        reasons.append(
            f"RSI is {rsi:.1f}, indicating mildly "
            "oversold conditions."
        )

    elif rsi > 75:
        score -= 2
        reasons.append(
            f"RSI is {rsi:.1f}, indicating strongly "
            "overbought conditions."
        )

    elif rsi > 70:
        score -= 1
        reasons.append(
            f"RSI is {rsi:.1f}, indicating mildly "
            "overbought conditions."
        )

    else:
        reasons.append(
            f"RSI is neutral at {rsi:.1f}."
        )

    # -----------------------------------------------------
    # Five-day momentum
    # -----------------------------------------------------

    if five_day_momentum >= 5:
        score += 1
        reasons.append(
            f"Five-day momentum is positive at "
            f"{five_day_momentum:.1f}%."
        )

    elif five_day_momentum <= -5:
        score -= 1
        reasons.append(
            f"Five-day momentum is negative at "
            f"{five_day_momentum:.1f}%."
        )

    else:
        reasons.append(
            f"Five-day momentum is moderate at "
            f"{five_day_momentum:.1f}%."
        )

    # -----------------------------------------------------
    # Volume confirmation
    # -----------------------------------------------------

    if volume_ratio is not None:
        if volume_ratio >= 1.5:
            reasons.append(
                "Trading volume is significantly above "
                "its 20-day average."
            )

            if five_day_momentum > 0:
                score += 1
            elif five_day_momentum < 0:
                score -= 1

        elif volume_ratio < 0.7:
            reasons.append(
                "Trading volume is below its "
                "20-day average."
            )

        else:
            reasons.append(
                "Trading volume is near its "
                "20-day average."
            )
    else:
        reasons.append(
            "Volume confirmation was unavailable."
        )

    # -----------------------------------------------------
    # Final signal
    # -----------------------------------------------------

    if score >= 3:
        signal = "BUY"
        confidence = min(65 + score * 5, 95)

    elif score <= -3:
        signal = "SELL"
        confidence = min(65 + abs(score) * 5, 95)

    else:
        signal = "HOLD"
        confidence = min(55 + abs(score) * 5, 75)

    return {
        "symbol": normalized_symbol,
        "price": round(current_price, 2),
        "signal": signal,
        "confidence": int(confidence),
        "score": int(score),
        "indicators": {
            "sma_20": round(sma_20, 2),
            "sma_50": round(sma_50, 2),
            "rsi": round(rsi, 2),
            "five_day_momentum": round(
                five_day_momentum,
                2,
            ),
            "volume_ratio": (
                round(volume_ratio, 2)
                if volume_ratio is not None
                else None
            ),
        },
        "reason": reasons,
        # Included for compatibility with frontend code that uses
        # either "reason" or "reasons".
        "reasons": reasons,
    }


# =========================================================
# Portfolio price refresh
# =========================================================

def refresh_portfolio_prices() -> None:
    """
    Refresh current prices for every open position.

    PaperTrader validates each price update. A portfolio snapshot is
    recorded only when the total account value changes by at least one cent.
    """
    positions = getattr(trader, "positions", {})

    if not positions:
        return

    previous_value = None
    portfolio_history = trader.get_portfolio_history()

    if portfolio_history:
        previous_value = safe_float(
            portfolio_history[-1].get("value")
        )

    updated_any_price = False

    for symbol in list(positions.keys()):
        price = fetch_current_price(symbol)

        if price is None:
            continue

        old_price = safe_float(
            trader.current_prices.get(symbol)
        )

        accepted = trader.update_current_price(
            symbol,
            price,
        )

        if not accepted:
            continue

        if (
            old_price is None
            or abs(old_price - price) >= 0.01
        ):
            updated_any_price = True

    if not updated_any_price:
        return

    try:
        new_value = trader.calculate_portfolio_value()

        if (
            previous_value is None
            or abs(previous_value - new_value) >= 0.01
        ):
            trader.record_portfolio_value()

    except Exception as error:
        print(
            "Portfolio-value refresh error: "
            f"{clean_error_message(error)}"
        )


# =========================================================
# Basic API routes
# =========================================================

@app.get("/")
def home() -> dict[str, Any]:
    return {
        "message": "AI Paper Trader API is running.",
        "version": APP_VERSION,
        "docs": "/docs",
        "health": "/health",
        "features": [
            "paper trading",
            "portfolio tracking",
            "stock search",
            "technical strategy analysis",
            "chart data",
            "automatic market scanning",
            "risk-based position sizing",
            "stop-loss and take-profit planning",
        ],
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "version": APP_VERSION,
    }


# =========================================================
# Account and portfolio routes
# =========================================================

@app.get("/account")
def account() -> dict[str, Any]:
    try:
        refresh_portfolio_prices()
        return trader.account()

    except Exception as error:
        print(
            "Account endpoint error: "
            f"{clean_error_message(error)}"
        )
        return {
            "error": "The account could not be loaded."
        }


@app.get("/portfolio-history")
def portfolio_history() -> Any:
    try:
        return trader.get_portfolio_history()

    except Exception as error:
        print(
            "Portfolio-history endpoint error: "
            f"{clean_error_message(error)}"
        )
        return []


# =========================================================
# Market scanner routes
# =========================================================

@app.get("/market-scan")
def market_scan(
    limit: int = Query(default=10, ge=1, le=30),
    signal: str | None = Query(default=None),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        results = scan_market(
            force_refresh=refresh
        )

        if not isinstance(results, list):
            print(
                "Scanner returned an unexpected value: "
                f"{type(results).__name__}"
            )
            results = []

        clean_results = [
            result
            for result in results
            if isinstance(result, dict)
        ]

        requested_signal = str(
            signal or ""
        ).strip().upper()

        if requested_signal in {"BUY", "HOLD", "SELL"}:
            clean_results = [
                stock
                for stock in clean_results
                if str(
                    stock.get("signal", "")
                ).strip().upper() == requested_signal
            ]

        final_results = clean_results[:limit]

        return {
            "results": final_results,
            "count": len(final_results),
            "scanned_universe": "S&P 500",
            "refresh_requested": refresh,
        }

    except Exception as error:
        print(
            "Market-scan endpoint error: "
            f"{clean_error_message(error)}"
        )

        return {
            "error": (
                "The market scan could not be completed."
            ),
            "results": [],
            "count": 0,
            "scanned_universe": "S&P 500",
            "refresh_requested": refresh,
        }


@app.get("/scanner")
def scanner(
    limit: int = Query(default=10, ge=1, le=30),
    signal: str | None = Query(default=None),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    """
    Backward-compatible alias for /market-scan.
    """
    return market_scan(
        limit=limit,
        signal=signal,
        refresh=refresh,
    )


# =========================================================
# Chart and quote routes
# =========================================================

@app.get("/chart/{symbol}")
def chart(symbol: str) -> dict[str, Any]:
    normalized_symbol = clean_symbol(symbol)

    if not normalized_symbol:
        return {"error": "Symbol is required."}

    try:
        data = get_chart_data(normalized_symbol)

    except Exception as error:
        print(
            f"Chart endpoint error for "
            f"{normalized_symbol}: "
            f"{clean_error_message(error)}"
        )
        return {
            "error": (
                f"Chart data could not be loaded for "
                f"{normalized_symbol}."
            )
        }

    if data is None:
        return {
            "error": (
                f"Chart data was not found for "
                f"{normalized_symbol}."
            )
        }

    return data


@app.get("/quote/{symbol}")
def get_quote(symbol: str) -> dict[str, Any]:
    normalized_symbol = clean_symbol(symbol)

    if not normalized_symbol:
        return {"error": "Symbol is required."}

    price = fetch_current_price(
        normalized_symbol
    )

    if price is None:
        return {
            "error": (
                f"Could not retrieve the price for "
                f"{normalized_symbol}."
            )
        }

    return {
        "symbol": normalized_symbol,
        "price": price,
    }


# =========================================================
# Stock search
# =========================================================

@app.get("/search")
def search_stocks(
    query: str = Query(min_length=1),
) -> list[dict[str, str]]:
    clean_query = str(query or "").strip()

    if not clean_query:
        return []

    try:
        search = yf.Search(
            clean_query,
            max_results=15,
            news_count=0,
        )

        quotes = getattr(
            search,
            "quotes",
            [],
        ) or []

        results: list[dict[str, str]] = []
        seen_symbols: set[str] = set()

        for quote in quotes:
            if not isinstance(quote, dict):
                continue

            symbol = clean_symbol(
                quote.get("symbol", "")
            )

            if (
                not symbol
                or symbol in seen_symbols
            ):
                continue

            quote_type = str(
                quote.get("quoteType", "")
            ).strip().upper()

            if (
                quote_type
                and quote_type not in {
                    "EQUITY",
                    "ETF",
                }
            ):
                continue

            name = (
                quote.get("longname")
                or quote.get("shortname")
                or quote.get("displayName")
                or symbol
            )

            exchange = (
                quote.get("exchDisp")
                or quote.get("exchange")
                or ""
            )

            results.append({
                "symbol": symbol,
                "name": str(name),
                "exchange": str(exchange),
                "type": quote_type or "EQUITY",
            })

            seen_symbols.add(symbol)

        query_upper = clean_query.upper()

        results.sort(
            key=lambda stock: (
                stock["symbol"] != query_upper,
                not stock["symbol"].startswith(
                    query_upper
                ),
                not stock["name"].upper().startswith(
                    query_upper
                ),
                stock["symbol"],
            )
        )

        return results[:8]

    except Exception as error:
        print(
            "Stock-search error: "
            f"{clean_error_message(error)}"
        )
        return []


# =========================================================
# Strategy route
# =========================================================

@app.get("/strategy/{symbol}")
def get_strategy(symbol: str) -> dict[str, Any]:
    return analyze_symbol(symbol)


# =========================================================
# Risk-management route
# =========================================================

@app.get("/risk-plan/{symbol}")
def get_risk_plan(symbol: str) -> dict[str, Any]:
    """
    Return a suggested position size, stop-loss, take-profit,
    risk/reward ratio, and maximum allowed position value.
    """
    normalized_symbol = clean_symbol(symbol)

    if not normalized_symbol:
        return {
            "success": False,
            "error": "Symbol is required.",
            "message": "Enter a valid stock symbol.",
        }

    price = fetch_current_price(normalized_symbol)

    if price is None:
        message = (
            f"Could not retrieve the current market price "
            f"for {normalized_symbol}."
        )

        return {
            "success": False,
            "error": message,
            "message": message,
        }

    try:
        return trader.get_trade_plan(
            symbol=normalized_symbol,
            entry_price=price,
        )

    except Exception as error:
        print(
            f"Risk-plan endpoint error for "
            f"{normalized_symbol}: "
            f"{clean_error_message(error)}"
        )

        message = (
            f"The risk plan for {normalized_symbol} "
            "could not be created."
        )

        return {
            "success": False,
            "error": message,
            "message": message,
        }


# =========================================================
# Trading routes
# =========================================================

def parse_trade_request(
    data: Any,
) -> tuple[str, int] | tuple[None, None]:
    """Extract and validate symbol and share count from a request."""
    if not isinstance(data, dict):
        return None, None

    symbol = clean_symbol(
        data.get("symbol", "")
    )

    raw_shares = data.get("shares", 0)

    # Prevent booleans because bool is a subclass of int in Python.
    if isinstance(raw_shares, bool):
        return None, None

    try:
        shares_float = float(raw_shares)
    except (TypeError, ValueError):
        return None, None

    if (
        not math.isfinite(shares_float)
        or not shares_float.is_integer()
    ):
        return None, None

    shares = int(shares_float)

    if not symbol or shares <= 0:
        return None, None

    return symbol, shares


@app.post("/buy")
def buy(data: dict[str, Any]) -> dict[str, Any]:
    symbol, shares = parse_trade_request(data)

    if symbol is None or shares is None:
        return {
            "error": (
                "Enter a valid stock symbol and a "
                "whole number of shares greater than zero."
            )
        }

    price = fetch_current_price(symbol)

    if price is None:
        return {
            "error": (
                f"Unable to retrieve the current market "
                f"price for {symbol}."
            )
        }

    try:
        return trader.buy(
            symbol,
            shares,
            price,
        )

    except Exception as error:
        print(
            f"Buy endpoint error for {symbol}: "
            f"{clean_error_message(error)}"
        )
        return {
            "error": (
                f"The purchase of {symbol} could not "
                "be completed."
            )
        }


@app.post("/sell")
def sell(data: dict[str, Any]) -> dict[str, Any]:
    symbol, shares = parse_trade_request(data)

    if symbol is None or shares is None:
        return {
            "error": (
                "Enter a valid stock symbol and a "
                "whole number of shares greater than zero."
            )
        }

    price = fetch_current_price(symbol)

    if price is None:
        return {
            "error": (
                f"Unable to retrieve the current market "
                f"price for {symbol}."
            )
        }

    try:
        return trader.sell(
            symbol,
            shares,
            price,
        )

    except Exception as error:
        print(
            f"Sell endpoint error for {symbol}: "
            f"{clean_error_message(error)}"
        )
        return {
            "error": (
                f"The sale of {symbol} could not "
                "be completed."
            )
        }