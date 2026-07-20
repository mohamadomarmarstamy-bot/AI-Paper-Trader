from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf

from paper_trader import PaperTrader
from scanner import scan_market
from chart_data import get_chart_data
from database import initialize_database
app = FastAPI(title="AI Paper Trader")
initialize_database()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


trader = PaperTrader()


FALLBACK_STOCKS = [
    {"symbol": "AAPL", "name": "Apple Inc."},
    {"symbol": "AMD", "name": "Advanced Micro Devices, Inc."},
    {"symbol": "AMZN", "name": "Amazon.com, Inc."},
    {"symbol": "ARM", "name": "Arm Holdings plc"},
    {"symbol": "AVGO", "name": "Broadcom Inc."},
    {"symbol": "BA", "name": "The Boeing Company"},
    {"symbol": "BAC", "name": "Bank of America Corporation"},
    {"symbol": "BABA", "name": "Alibaba Group Holding Limited"},
    {"symbol": "COIN", "name": "Coinbase Global, Inc."},
    {"symbol": "COST", "name": "Costco Wholesale Corporation"},
    {"symbol": "CRM", "name": "Salesforce, Inc."},
    {"symbol": "CRWV", "name": "CoreWeave, Inc."},
    {"symbol": "CVNA", "name": "Carvana Co."},
    {"symbol": "DIS", "name": "The Walt Disney Company"},
    {"symbol": "GOOG", "name": "Alphabet Inc. Class C"},
    {"symbol": "GOOGL", "name": "Alphabet Inc. Class A"},
    {"symbol": "HD", "name": "The Home Depot, Inc."},
    {"symbol": "HOOD", "name": "Robinhood Markets, Inc."},
    {"symbol": "INTC", "name": "Intel Corporation"},
    {"symbol": "JPM", "name": "JPMorgan Chase & Co."},
    {"symbol": "KO", "name": "The Coca-Cola Company"},
    {"symbol": "MCD", "name": "McDonald's Corporation"},
    {"symbol": "META", "name": "Meta Platforms, Inc."},
    {"symbol": "MSFT", "name": "Microsoft Corporation"},
    {"symbol": "MSTR", "name": "Strategy Inc."},
    {"symbol": "NFLX", "name": "Netflix, Inc."},
    {"symbol": "NKE", "name": "NIKE, Inc."},
    {"symbol": "NVDA", "name": "NVIDIA Corporation"},
    {"symbol": "NVO", "name": "Novo Nordisk A/S"},
    {"symbol": "ORCL", "name": "Oracle Corporation"},
    {"symbol": "PEP", "name": "PepsiCo, Inc."},
    {"symbol": "PLTR", "name": "Palantir Technologies Inc."},
    {"symbol": "QCOM", "name": "QUALCOMM Incorporated"},
    {"symbol": "RBLX", "name": "Roblox Corporation"},
    {"symbol": "SBUX", "name": "Starbucks Corporation"},
    {"symbol": "SHOP", "name": "Shopify Inc."},
    {"symbol": "SNAP", "name": "Snap Inc."},
    {"symbol": "SPOT", "name": "Spotify Technology S.A."},
    {"symbol": "T", "name": "AT&T Inc."},
    {"symbol": "TSLA", "name": "Tesla, Inc."},
    {"symbol": "UBER", "name": "Uber Technologies, Inc."},
    {"symbol": "V", "name": "Visa Inc."},
    {"symbol": "VZ", "name": "Verizon Communications Inc."},
    {"symbol": "WMT", "name": "Walmart Inc."},
]


def fetch_current_price(symbol: str):
    """
    Retrieve the newest available price for one symbol.

    This first tries fast_info. If that fails, it uses the latest
    non-empty closing price from recent history.
    """
    symbol = symbol.strip().upper()

    if not symbol:
        return None

    try:
        ticker = yf.Ticker(symbol)

        try:
            fast_info = ticker.fast_info
            price = fast_info.get("last_price")

            if price is not None and float(price) > 0:
                return round(float(price), 2)

        except Exception:
            pass

        history = ticker.history(
            period="5d",
            interval="1d",
            auto_adjust=False
        )

        if history.empty:
            return None

        close_prices = history["Close"].dropna()

        if close_prices.empty:
            return None

        price = float(close_prices.iloc[-1])

        if price <= 0:
            return None

        return round(price, 2)

    except Exception as error:
        print(
            f"Current-price error for {symbol}: "
            f"{error}"
        )

        return None


def refresh_portfolio_prices():
    """
    Update every open position with its newest available market price.

    A portfolio-history point is recorded only when the account value
    actually changes. This avoids filling the chart with duplicate
    $100,000 points every time the page refreshes.
    """
    if not trader.positions:
        return

    updated_any_price = False

    for symbol in list(trader.positions.keys()):
        price = fetch_current_price(symbol)

        if price is None:
            continue

        old_price = trader.current_prices.get(symbol)

        trader.current_prices[symbol] = price

        if old_price is None or abs(old_price - price) >= 0.01:
            updated_any_price = True

    if not updated_any_price:
        return

    new_value = round(
        trader.calculate_portfolio_value(),
        2
    )

    previous_value = None

    if trader.portfolio_history:
        previous_value = trader.portfolio_history[-1].get(
            "value"
        )

    if (
        previous_value is None
        or abs(float(previous_value) - new_value) >= 0.01
    ):
        trader.record_portfolio_value()


@app.get("/")
def home():
    return {
        "message": "AI Paper Trader API is running."
    }


@app.get("/account")
def account():
    refresh_portfolio_prices()
    return trader.account()


@app.get("/portfolio-history")
def portfolio_history():
    return trader.get_portfolio_history()


@app.get("/scanner")
def scanner():
    return scan_market()


@app.get("/chart/{symbol}")
def chart(symbol: str):
    symbol = symbol.strip().upper()

    if not symbol:
        return {
            "error": "Symbol is required."
        }

    data = get_chart_data(symbol)

    if data is None:
        return {
            "error": "Symbol not found."
        }

    return data


@app.get("/search")
def search_stocks(query: str):
    clean_query = query.strip()

    if not clean_query:
        return []

    try:
        search = yf.Search(
            clean_query,
            max_results=15,
            news_count=0,
            lists_count=0,
            include_cb=False,
            include_nav_links=False,
            include_research=False,
            enable_fuzzy_query=True,
            recommended=0,
            timeout=10,
            raise_errors=True,
        )

        results = []
        seen_symbols = set()

        for quote in search.quotes:
            symbol = str(
                quote.get("symbol", "")
            ).strip().upper()

            if not symbol or symbol in seen_symbols:
                continue

            quote_type = str(
                quote.get("quoteType", "")
            ).strip().upper()

            allowed_types = {
                "EQUITY",
                "ETF",
            }

            if (
                quote_type
                and quote_type not in allowed_types
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

        if results:
            return results[:8]

    except Exception as error:
        print(f"Stock search error: {error}")

    query_upper = clean_query.upper()
    fallback_results = []

    for stock in FALLBACK_STOCKS:
        symbol = stock["symbol"].upper()
        name = stock["name"].upper()

        if (
            symbol.startswith(query_upper)
            or query_upper in symbol
            or name.startswith(query_upper)
            or query_upper in name
        ):
            fallback_results.append(stock)

    fallback_results.sort(
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

    return fallback_results[:8]


@app.get("/quote/{symbol}")
def get_quote(symbol: str):
    symbol = symbol.strip().upper()

    if not symbol:
        return {
            "error": "Symbol is required."
        }

    price = fetch_current_price(symbol)

    print(f"Quote request: {symbol} -> {price}")

    if price is None:
        return {
            "error": f"Could not retrieve the price for {symbol}."
        }

    return {
        "symbol": symbol,
        "price": price
    }


@app.post("/buy")
def buy(data: dict):
    try:
        symbol = str(
            data.get("symbol", "")
        ).strip().upper()

        shares = int(
            data.get("shares", 0)
        )

    except (TypeError, ValueError):
        return {
            "error": "Invalid trade information."
        }

    if not symbol:
        return {
            "error": "Symbol is required."
        }

    if shares <= 0:
        return {
            "error": "Shares must be greater than zero."
        }

    price = fetch_current_price(symbol)

    if price is None:
        return {
            "error": "Unable to retrieve the current market price."
        }

    return trader.buy(
        symbol,
        shares,
        price
    )

@app.post("/sell")
def sell(data: dict):
    try:
        symbol = str(
            data.get("symbol", "")
        ).strip().upper()

        shares = int(
            data.get("shares", 0)
        )

    except (TypeError, ValueError):
        return {
            "error": "Invalid trade information."
        }

    if not symbol:
        return {
            "error": "Symbol is required."
        }

    if shares <= 0:
        return {
            "error": "Shares must be greater than zero."
        }

    price = fetch_current_price(symbol)

    if price is None:
        return {
            "error": "Unable to retrieve the current market price."
        }

    return trader.sell(
        symbol,
        shares,
        price
    )