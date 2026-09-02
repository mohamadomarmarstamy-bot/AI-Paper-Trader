import asyncio
import json
import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any
import hashlib
import hmac
import secrets

import requests
import yfinance as yf
from fastapi import FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from chart_data import get_chart_data
from database import (
    calculate_learning_summary,
    close_trade_book_entry,
    create_trade_book_entry,
    initialize_database,
    load_open_trade_book_entry,
    load_trade_book,
    load_trade_book_events,
    record_trade_book_event,
    save_learning_outcome,
)
from indicators import (
    calculate_rsi,
    calculate_sma,
    calculate_volume_ratio,
    percentage_change,
    safe_float,
)
from paper_trader import PaperTrader
from scanner import (
    get_market_regime,
    get_symbol_news_context,
    scan_market,
    score_symbol_news_context,
)


APP_VERSION = "2.7.1"
AUTO_PORTFOLIO_REFRESH_SECONDS = 300

# Alpaca paper trading only.
ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_ORDER_POLL_SECONDS = 0.25
ALPACA_ORDER_POLL_TIMEOUT_SECONDS = 8.0
ALPACA_READ_RETRY_ATTEMPTS = 3
ALPACA_READ_RETRY_DELAY_SECONDS = 0.75


# ============================================================
# RISK CONTROLS
# ============================================================

RISK_MAX_ORDER_EQUITY_PERCENT = 2.5
RISK_MAX_POSITION_EQUITY_PERCENT = 5.0
RISK_MAX_OPEN_POSITIONS = 50
RISK_MAX_SPREAD_PERCENT = 2.0
RISK_BUYING_POWER_BUFFER_DOLLARS = 25.0
RISK_REQUIRE_REGULAR_MARKET_OPEN = True
RISK_BLOCK_SHORT_SELLING = True


# ============================================================
# AUTO TRADER
# ============================================================

# How often the trader checks the latest scanner results.
AUTO_TRADER_SCAN_SECONDS = 15

# Base entry requirements.
AUTO_TRADER_ENTRY_SCORE_MIN = 75
AUTO_TRADER_ENTRY_CONFIDENCE_MIN = 70

# Exit signal requirements.
AUTO_TRADER_EXIT_SCORE_MAX = 40
AUTO_TRADER_EXIT_CONFIDENCE_MIN = 80

# Target entry size.
# On a ~$100k paper account this is roughly $2,000.
AUTO_TRADER_ENTRY_EQUITY_PERCENT = 2.0

# Initial protection.
AUTO_TRADER_STOP_LOSS_PERCENT = 2.0
AUTO_TRADER_TAKE_PROFIT_PERCENT = 4.0

# Profit protection.
AUTO_TRADER_PROFIT_LOCK_TRIGGER_PERCENT = 0.75
AUTO_TRADER_PROFIT_LOCK_PERCENT = 0.25
AUTO_TRADER_PROFIT_TRAIL_PERCENT = 1.0

# Emergency individual-position loss protection.
AUTO_TRADER_HARD_MAX_LOSS_PERCENT = 2.5


# ============================================================
# DAILY PORTFOLIO PROTECTION
# ============================================================

AUTO_TRADER_DAILY_PROFIT_ARM_DOLLARS = 25.0
AUTO_TRADER_DAILY_PROFIT_GIVEBACK_DOLLARS = 15.0
AUTO_TRADER_DAILY_PROFIT_GIVEBACK_PERCENT = 40.0

# NEW:
# Once the paper account loses this much during the trading day,
# stop opening NEW positions for the rest of that daily session.
# Existing positions can still be managed/exited.
AUTO_TRADER_DAILY_LOSS_LIMIT_DOLLARS = 300.0


# ============================================================
# COOLDOWNS / OVERTRADING PROTECTION
# ============================================================

# Normal cooldown after interacting with a symbol.
AUTO_TRADER_SYMBOL_COOLDOWN_SECONDS = 60 * 60

# NEW:
# A losing symbol will eventually receive a longer cooldown.
# We will wire this into the entry/exit logic in the next step.
AUTO_TRADER_LOSS_COOLDOWN_SECONDS = 4 * 60 * 60

# Don't let one scanner cycle flood the portfolio.
AUTO_TRADER_MAX_NEW_POSITIONS_PER_CYCLE = 2


# ============================================================
# AUTOMATIC ENTRY QUALITY FILTERS
# ============================================================

# The global risk system still allows up to 2% spread,
# but the automatic trader will eventually use the tighter 1%.
AUTO_TRADER_MAX_ENTRY_SPREAD_PERCENT = 1.0

# Avoid extremely volatile automatic entries.
# We will wire ATR into the entry logic after the basic fixes.
AUTO_TRADER_MAX_ENTRY_ATR_PERCENT = 5.0


# ============================================================
# LOGGING / LEARNING JOURNAL
# ============================================================

AUTO_TRADER_LOG_LIMIT = 250

AUTO_TRADER_LOG_FILE = os.getenv(
    "AUTO_TRADER_LOG_FILE",
    "/tmp/auto_trader_log.json",
)

AUTO_TRADER_JOURNAL_FILE = os.getenv(
    "AUTO_TRADER_JOURNAL_FILE",
    "/data/auto_trader_journal.json",
)

AUTO_TRADER_JOURNAL_LIMIT = 5000

_auto_trader_enabled = (
    os.getenv(
        "AUTO_TRADER_START_ENABLED",
        "false",
    ).strip().lower()
    in {
        "1",
        "true",
        "yes",
        "on",
    }
)

APP_ACCESS_PASSWORD = os.getenv(
    "APP_ACCESS_PASSWORD",
    "",
)

APP_SESSION_SECRET = os.getenv(
    "APP_SESSION_SECRET",
    "",
)

APP_SESSION_COOKIE = "ai_paper_trader_session"
APP_SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7

_auto_trader_cycle_running = False
_auto_trader_last_cycle_at: float | None = None
_auto_trader_last_cycle_result: dict[str, Any] | None = None
_auto_trader_symbol_cooldowns: dict[str, float] = {}
_auto_trader_log: list[dict[str, Any]] = []
_auto_trader_journal: list[dict[str, Any]] = []
_auto_trader_seen_exit_order_ids: set[str] = set()
_auto_trader_daily_pl_high_water = 0.0
_auto_trader_daily_pl_date: str | None = None
_auto_trader_defensive_mode = False


async def portfolio_refresh_loop() -> None:
    """
    Refresh open-position prices and record portfolio changes
    automatically while the API is running.
    """
    while True:
        try:
            await asyncio.to_thread(
                refresh_portfolio_prices
            )
        except Exception as error:
            print(
                "Automatic portfolio refresh error: "
                f"{clean_error_message(error)}"
            )

        await asyncio.sleep(
            AUTO_PORTFOLIO_REFRESH_SECONDS
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_auto_trader_log()
    load_auto_trader_journal()
    initialize_seen_exit_order_ids()

    refresh_task = asyncio.create_task(
        portfolio_refresh_loop()
    )

    auto_trade_task = asyncio.create_task(
        auto_trader_loop()
    )

    try:
        yield
    finally:
        refresh_task.cancel()
        auto_trade_task.cancel()

        for task in (
            refresh_task,
            auto_trade_task,
        ):
            try:
                await task
            except asyncio.CancelledError:
                pass

app = FastAPI(
    title="AI Paper Trader",
    lifespan=lifespan,
    description=(
        "Paper trading, portfolio tracking, stock search, "
        "chart data, strategy analysis, risk management, and market scanning API."
    ),
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

FRONTEND_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "frontend",
    )
)

app.mount(
    "/js",
    StaticFiles(
        directory=os.path.join(
            FRONTEND_DIR,
            "js",
        ),
    ),
    name="js",
)

@app.get("/style.css")
def frontend_style():
    return FileResponse(
        os.path.join(
            FRONTEND_DIR,
            "style.css",
        )
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

def create_app_session_token() -> str:
    timestamp = str(int(time.time()))

    signature = hmac.new(
        APP_SESSION_SECRET.encode("utf-8"),
        timestamp.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return f"{timestamp}.{signature}"


def verify_app_session_token(
    token: str | None,
) -> bool:
    if (
        not token
        or not APP_SESSION_SECRET
    ):
        return False

    try:
        timestamp_text, signature = token.split(
            ".",
            1,
        )
        timestamp = int(timestamp_text)
    except (ValueError, TypeError):
        return False

    if (
        time.time() - timestamp
        > APP_SESSION_MAX_AGE_SECONDS
    ):
        return False

    expected_signature = hmac.new(
        APP_SESSION_SECRET.encode("utf-8"),
        timestamp_text.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(
        signature,
        expected_signature,
    )

def request_has_valid_app_session(
    request: Request,
) -> bool:
    token = request.cookies.get(
        APP_SESSION_COOKIE
    )

    return verify_app_session_token(
        token
    )

def require_app_session(
    request: Request,
) -> None:
    if not request_has_valid_app_session(
        request
    ):
        raise HTTPException(
            status_code=401,
            detail="Authentication required.",
        )

def build_login_page(
    *,
    error_message: str = "",
) -> str:
    safe_error = (
        f"<p style='color:#ef4444;'>{error_message}</p>"
        if error_message
        else ""
    )

    return f"""
    <!doctype html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <meta
            name="viewport"
            content="width=device-width, initial-scale=1"
        >
        <title>AI Paper Trader Login</title>
        <style>
            body {{
                margin: 0;
                min-height: 100vh;
                display: grid;
                place-items: center;
                font-family: Arial, sans-serif;
                background: #0b1220;
                color: white;
            }}
            .card {{
                width: min(92vw, 420px);
                padding: 28px;
                border-radius: 18px;
                background: #111827;
                border: 1px solid #243047;
            }}
            input {{
                width: 100%;
                box-sizing: border-box;
                padding: 12px;
                margin-top: 10px;
                border-radius: 10px;
                border: 1px solid #334155;
                background: #0f172a;
                color: white;
            }}
            button {{
                width: 100%;
                margin-top: 14px;
                padding: 12px;
                border: 0;
                border-radius: 10px;
                font-weight: 700;
                cursor: pointer;
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>AI Paper Trader</h1>
            <p>Enter the access password.</p>
            {safe_error}
            <form method="post" action="/login">
                <input
                    type="password"
                    name="password"
                    placeholder="Password"
                    required
                    autocomplete="current-password"
                >
                <button type="submit">
                    Sign in
                </button>
            </form>
        </div>
    </body>
    </html>
    """

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
# Alpaca paper-trading helpers
# =========================================================

def get_alpaca_credentials() -> tuple[str, str] | None:
    """
    Return the Alpaca key pair stored in Railway environment variables.
    """
    api_key = os.getenv("ALPACA_API_KEY", "").strip()
    secret_key = os.getenv("ALPACA_SECRET_KEY", "").strip()

    if not api_key or not secret_key:
        return None

    return api_key, secret_key


def get_alpaca_headers() -> dict[str, str]:
    """
    Build authentication headers without exposing credentials.
    """
    credentials = get_alpaca_credentials()

    if credentials is None:
        raise RuntimeError(
            "Alpaca API credentials are not configured."
        )

    api_key, secret_key = credentials

    return {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def extract_alpaca_error(payload: Any) -> str:
    """
    Extract a readable error message from an Alpaca response body.
    """
    if isinstance(payload, dict):
        for key in (
            "message",
            "error",
            "detail",
        ):
            value = payload.get(key)

            if value:
                return str(value)

    return "Alpaca rejected the request."


def alpaca_paper_request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> Any:
    """
    Call Alpaca's PAPER Trading API.

    The base URL is hard-coded to paper-api.alpaca.markets so this helper
    cannot accidentally submit a live-money order.
    """
    if ALPACA_PAPER_BASE_URL != "https://paper-api.alpaca.markets":
        raise RuntimeError(
            "Paper-trading safety check failed."
        )

    normalized_method = str(method).strip().upper()

    attempts = (
        ALPACA_READ_RETRY_ATTEMPTS
        if normalized_method == "GET"
        else 1
    )

    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            response = requests.request(
                method=normalized_method,
                url=f"{ALPACA_PAPER_BASE_URL}{path}",
                headers=get_alpaca_headers(),
                json=json_body,
                params=params,
                timeout=timeout,
            )
        except requests.RequestException as error:
            last_error = error

            if (
                normalized_method == "GET"
                and attempt < attempts - 1
            ):
                time.sleep(
                    ALPACA_READ_RETRY_DELAY_SECONDS
                    * (attempt + 1)
                )
                continue

            raise

        try:
            payload = response.json()
        except ValueError:
            payload = None

        if response.ok:
            return payload

        request_id = response.headers.get(
            "X-Request-ID",
            "",
        )

        message = extract_alpaca_error(
            payload
        )

        if request_id:
            message = (
                f"{message} "
                f"(Alpaca request ID: {request_id})"
            )

        if (
            normalized_method == "GET"
            and response.status_code >= 500
            and attempt < attempts - 1
        ):
            time.sleep(
                ALPACA_READ_RETRY_DELAY_SECONDS
                * (attempt + 1)
            )
            continue

        raise RuntimeError(message)

    if last_error is not None:
        raise last_error

    raise RuntimeError(
        "Alpaca request failed without a response."
    )


def get_alpaca_paper_order(
    order_id: str,
) -> dict[str, Any]:
    """
    Retrieve a single paper order from Alpaca.
    """
    payload = alpaca_paper_request(
        "GET",
        f"/v2/orders/{order_id}",
    )

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Alpaca returned an invalid order response."
        )

    return payload


def wait_for_alpaca_paper_order(
    order: dict[str, Any],
) -> dict[str, Any]:
    """
    Briefly poll a newly submitted paper order so the frontend can receive
    Alpaca's actual simulated fill price when the order fills quickly.

    If the market is closed, a normal DAY market order can remain queued.
    In that case the latest order state is returned without resubmitting it.
    """
    order_id = str(
        order.get("id", "")
    ).strip()

    if not order_id:
        return order

    terminal_statuses = {
        "filled",
        "canceled",
        "expired",
        "rejected",
        "replaced",
        "done_for_day",
    }

    latest_order = order
    deadline = (
        time.monotonic()
        + ALPACA_ORDER_POLL_TIMEOUT_SECONDS
    )

    while time.monotonic() < deadline:
        status = str(
            latest_order.get(
                "status",
                "",
            )
        ).strip().lower()

        filled_price = safe_float(
            latest_order.get(
                "filled_avg_price"
            )
        )

        if (
            status in terminal_statuses
            or (
                filled_price is not None
                and filled_price > 0
            )
        ):
            break

        time.sleep(
            ALPACA_ORDER_POLL_SECONDS
        )

        try:
            latest_order = (
                get_alpaca_paper_order(
                    order_id
                )
            )
        except Exception as error:
            print(
                "Alpaca order-status refresh failed "
                f"for {order_id}: "
                f"{clean_error_message(error)}"
            )
            break

    return latest_order



def fetch_alpaca_market_clock() -> dict[str, Any]:
    """
    Return Alpaca's current US market clock for the PAPER account.
    """
    payload = alpaca_paper_request(
        "GET",
        "/v2/clock",
    )

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Alpaca returned an invalid market-clock response."
        )

    return payload


def fetch_alpaca_risk_quote(
    symbol: str,
) -> dict[str, Any]:
    """
    Fetch the latest Alpaca IEX bid/ask quote used for execution risk checks.
    """
    normalized_symbol = clean_symbol(symbol)

    if not normalized_symbol:
        raise RuntimeError(
            "A valid symbol is required for a risk quote."
        )

    credentials = get_alpaca_credentials()

    if credentials is None:
        raise RuntimeError(
            "Alpaca API credentials are not configured."
        )

    api_key, secret_key = credentials

    response = requests.get(
        (
            "https://data.alpaca.markets/v2/stocks/"
            f"{normalized_symbol}/quotes/latest"
        ),
        headers={
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
        },
        params={
            "feed": "iex",
        },
        timeout=10,
    )

    try:
        payload = response.json()
    except ValueError:
        payload = None

    if not response.ok:
        raise RuntimeError(
            extract_alpaca_error(payload)
        )

    quote = (
        payload.get("quote", {})
        if isinstance(payload, dict)
        else {}
    )

    bid = safe_float(
        quote.get("bp")
        if isinstance(quote, dict)
        else None
    )

    ask = safe_float(
        quote.get("ap")
        if isinstance(quote, dict)
        else None
    )

    if (
        bid is None
        or ask is None
        or bid <= 0
        or ask <= 0
        or ask < bid
    ):
        raise RuntimeError(
            f"A valid bid/ask quote is unavailable for "
            f"{normalized_symbol}."
        )

    midpoint = (
        bid
        + ask
    ) / 2

    spread = (
        ask
        - bid
    )

    spread_percent = (
        (
            spread
            / midpoint
        )
        * 100
        if midpoint > 0
        else math.inf
    )

    return {
        "symbol": normalized_symbol,
        "bid": round(
            bid,
            4,
        ),
        "ask": round(
            ask,
            4,
        ),
        "midpoint": round(
            midpoint,
            4,
        ),
        "spread": round(
            spread,
            4,
        ),
        "spread_percent": round(
            spread_percent,
            4,
        ),
        "timestamp": (
            quote.get("t")
            if isinstance(quote, dict)
            else None
        ),
    }


def fetch_alpaca_open_orders_for_symbol(
    symbol: str,
) -> list[dict[str, Any]]:
    """
    Return currently open PAPER orders for one symbol.
    """
    normalized_symbol = clean_symbol(
        symbol
    )

    payload = alpaca_paper_request(
        "GET",
        "/v2/orders",
        params={
            "status": "open",
            "limit": 100,
            "direction": "desc",
            "symbols": normalized_symbol,
            "nested": "true",
        },
    )

    if not isinstance(payload, list):
        raise RuntimeError(
            "Alpaca returned an invalid open-orders response."
        )

    return [
        order
        for order in payload
        if (
            isinstance(order, dict)
            and clean_symbol(
                order.get("symbol")
            ) == normalized_symbol
        )
    ]


def get_alpaca_position_for_symbol(
    symbol: str,
    positions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Find one current Alpaca PAPER position by symbol.
    """
    normalized_symbol = clean_symbol(
        symbol
    )

    for position in positions:
        if (
            isinstance(position, dict)
            and clean_symbol(
                position.get("symbol")
            ) == normalized_symbol
        ):
            return position

    return None


def validate_alpaca_paper_order_risk(
    *,
    symbol: str,
    shares: int,
    side: str,
) -> dict[str, Any]:
    """
    Validate a manual PAPER order against conservative execution controls.

    Controls:
      - regular-market session only for market orders
      - no duplicate open orders for the same symbol
      - spread ceiling
      - max order size as a percent of account equity
      - max total position size as a percent of account equity
      - max number of simultaneous positions
      - buying-power check with a small cash buffer
      - long-only selling; no accidental short sale
    """
    normalized_symbol = clean_symbol(
        symbol
    )

    normalized_side = str(
        side or ""
    ).strip().lower()

    if normalized_side not in {
        "buy",
        "sell",
    }:
        return {
            "approved": False,
            "error": (
                "Trade side must be buy or sell."
            ),
        }

    if shares <= 0:
        return {
            "approved": False,
            "error": (
                "Share quantity must be greater than zero."
            ),
        }

    account = fetch_alpaca_paper_account()
    positions = fetch_alpaca_paper_positions()

    equity = safe_float(
        account.get("equity")
    )

    buying_power = safe_float(
        account.get("buying_power")
    )

    if (
        equity is None
        or equity <= 0
    ):
        return {
            "approved": False,
            "error": (
                "Account equity is unavailable, so the "
                "risk check cannot approve this order."
            ),
        }

    if buying_power is None:
        buying_power = 0.0

    if RISK_REQUIRE_REGULAR_MARKET_OPEN:
        clock = fetch_alpaca_market_clock()

        if not bool(
            clock.get("is_open")
        ):
            next_open = clock.get(
                "next_open"
            )

            return {
                "approved": False,
                "error": (
                    "The regular stock market is closed. "
                    "This bot currently blocks market orders "
                    "outside regular hours to avoid queued or "
                    "unexpected fills."
                ),
                "next_open": next_open,
            }

    open_orders = (
        fetch_alpaca_open_orders_for_symbol(
            normalized_symbol
        )
    )

    if open_orders:
        open_sides = sorted({
            str(
                order.get(
                    "side",
                    "",
                )
            ).strip().lower()
            for order in open_orders
        })

        return {
            "approved": False,
            "error": (
                f"{normalized_symbol} already has an open "
                f"paper order. Wait for it to fill or cancel "
                f"before submitting another order."
            ),
            "open_order_sides": open_sides,
        }

    quote = fetch_alpaca_risk_quote(
        normalized_symbol
    )

    spread_percent = safe_float(
        quote.get(
            "spread_percent"
        )
    )

    if (
        spread_percent is None
        or spread_percent >
            RISK_MAX_SPREAD_PERCENT
    ):
        return {
            "approved": False,
            "error": (
                f"{normalized_symbol}'s bid/ask spread is "
                f"{(spread_percent or 0):.2f}%, above the "
                f"{RISK_MAX_SPREAD_PERCENT:.2f}% safety limit."
            ),
            "quote": quote,
        }

    midpoint = safe_float(
        quote.get("midpoint")
    )

    if (
        midpoint is None
        or midpoint <= 0
    ):
        return {
            "approved": False,
            "error": (
                "A valid execution reference price is unavailable."
            ),
        }

    estimated_notional = (
        midpoint
        * shares
    )

    max_order_notional = (
        equity
        * (
            RISK_MAX_ORDER_EQUITY_PERCENT
            / 100
        )
    )

    current_position = (
        get_alpaca_position_for_symbol(
            normalized_symbol,
            positions,
        )
    )

    current_qty = 0.0
    current_market_value = 0.0

    if current_position is not None:
        current_qty = safe_float(
            current_position.get("qty")
        ) or 0.0

        current_market_value = abs(
            safe_float(
                current_position.get(
                    "market_value"
                )
            ) or 0.0
        )

    max_position_notional = (
        equity
        * (
            RISK_MAX_POSITION_EQUITY_PERCENT
            / 100
        )
    )

    if normalized_side == "buy":
        if (
            estimated_notional >
            max_order_notional
        ):
            max_allowed_shares = max(
                0,
                math.floor(
                    max_order_notional
                    / midpoint
                ),
            )

            return {
                "approved": False,
                "error": (
                    f"Order size is about "
                    f"${estimated_notional:,.2f}, above the "
                    f"{RISK_MAX_ORDER_EQUITY_PERCENT:.1f}% "
                    f"per-order risk limit."
                ),
                "max_allowed_shares": max_allowed_shares,
                "estimated_notional": round(
                    estimated_notional,
                    2,
                ),
            }

        projected_position_value = (
            current_market_value
            + estimated_notional
        )

        if (
            projected_position_value >
            max_position_notional
        ):
            remaining_capacity = max(
                0.0,
                max_position_notional
                - current_market_value,
            )

            max_allowed_shares = max(
                0,
                math.floor(
                    remaining_capacity
                    / midpoint
                ),
            )

            return {
                "approved": False,
                "error": (
                    f"This order would make the "
                    f"{normalized_symbol} position larger "
                    f"than {RISK_MAX_POSITION_EQUITY_PERCENT:.1f}% "
                    f"of account equity."
                ),
                "max_allowed_shares": max_allowed_shares,
            }

        if (
            current_position is None
            and len(positions) >=
                RISK_MAX_OPEN_POSITIONS
        ):
            return {
                "approved": False,
                "error": (
                    f"The account already has "
                    f"{len(positions)} open positions, which "
                    f"meets the {RISK_MAX_OPEN_POSITIONS}-position "
                    f"safety limit."
                ),
            }

        required_buying_power = (
            estimated_notional
            + RISK_BUYING_POWER_BUFFER_DOLLARS
        )

        if (
            buying_power <
            required_buying_power
        ):
            return {
                "approved": False,
                "error": (
                    "Available buying power is too low for "
                    "this order plus the safety buffer."
                ),
                "buying_power": round(
                    buying_power,
                    2,
                ),
                "estimated_notional": round(
                    estimated_notional,
                    2,
                ),
            }

    else:
        if (
            RISK_BLOCK_SHORT_SELLING
            and (
                current_position is None
                or current_qty <= 0
            )
        ):
            return {
                "approved": False,
                "error": (
                    f"No long {normalized_symbol} position is "
                    f"available to sell. Short selling is "
                    f"disabled."
                ),
            }

        if (
            RISK_BLOCK_SHORT_SELLING
            and shares > current_qty
        ):
            return {
                "approved": False,
                "error": (
                    f"You requested to sell {shares} shares, "
                    f"but the paper account holds only "
                    f"{current_qty:g} shares."
                ),
                "max_allowed_shares": math.floor(
                    current_qty
                ),
            }

    return {
        "approved": True,
        "symbol": normalized_symbol,
        "side": normalized_side,
        "shares": shares,
        "equity": round(
            equity,
            2,
        ),
        "buying_power": round(
            buying_power,
            2,
        ),
        "estimated_notional": round(
            estimated_notional,
            2,
        ),
        "quote": quote,
        "limits": {
            "max_order_equity_percent":
                RISK_MAX_ORDER_EQUITY_PERCENT,
            "max_position_equity_percent":
                RISK_MAX_POSITION_EQUITY_PERCENT,
            "max_open_positions":
                RISK_MAX_OPEN_POSITIONS,
            "max_spread_percent":
                RISK_MAX_SPREAD_PERCENT,
            "buying_power_buffer_dollars":
                RISK_BUYING_POWER_BUFFER_DOLLARS,
            "regular_market_only":
                RISK_REQUIRE_REGULAR_MARKET_OPEN,
            "short_selling_disabled":
                RISK_BLOCK_SHORT_SELLING,
        },
    }


def normalize_alpaca_paper_order(
    order: dict[str, Any],
    *,
    requested_symbol: str,
    requested_shares: int,
    requested_side: str,
) -> dict[str, Any]:
    """
    Convert Alpaca's order object into the structure trades.js already
    understands.
    """
    status = str(
        order.get(
            "status",
            "",
        )
    ).strip().lower()

    symbol = clean_symbol(
        order.get("symbol")
        or requested_symbol
    )

    side = str(
        order.get("side")
        or requested_side
    ).strip().lower()

    filled_qty = safe_float(
        order.get(
            "filled_qty"
        )
    )

    submitted_qty = safe_float(
        order.get("qty")
    )

    execution_price = safe_float(
        order.get(
            "filled_avg_price"
        )
    )

    displayed_shares = (
        int(filled_qty)
        if (
            filled_qty is not None
            and filled_qty > 0
            and float(filled_qty).is_integer()
        )
        else (
            int(submitted_qty)
            if (
                submitted_qty is not None
                and submitted_qty > 0
                and float(submitted_qty).is_integer()
            )
            else requested_shares
        )
    )

    total = None

    if (
        execution_price is not None
        and execution_price > 0
        and filled_qty is not None
        and filled_qty > 0
    ):
        total = round(
            execution_price
            * filled_qty,
            2,
        )

    failed_statuses = {
        "canceled",
        "expired",
        "rejected",
    }

    success = (
        status not in failed_statuses
    )

    if status == "filled":
        message = (
            f"Alpaca paper order filled: "
            f"{side.upper()} {displayed_shares} "
            f"{symbol}."
        )
    elif success:
        message = (
            f"Alpaca paper order submitted. "
            f"Current status: "
            f"{status or 'accepted'}."
        )
    else:
        message = (
            f"Alpaca paper order was not completed. "
            f"Status: "
            f"{status or 'unknown'}."
        )

    trade = {
        "id": order.get("id"),
        "client_order_id": order.get(
            "client_order_id"
        ),
        "symbol": symbol,
        "shares": displayed_shares,
        "requested_shares": requested_shares,
        "filled_shares": (
            filled_qty
            if filled_qty is not None
            else 0
        ),
        "action": side.upper(),
        "side": side,
        "price": (
            round(
                execution_price,
                4,
            )
            if (
                execution_price is not None
                and execution_price > 0
            )
            else None
        ),
        "execution_price": (
            round(
                execution_price,
                4,
            )
            if (
                execution_price is not None
                and execution_price > 0
            )
            else None
        ),
        "total": total,
        "status": status,
        "type": order.get("type"),
        "time_in_force": order.get(
            "time_in_force"
        ),
        "submitted_at": order.get(
            "submitted_at"
        ),
        "filled_at": order.get(
            "filled_at"
        ),
        "paper": True,
    }

    return {
        "success": success,
        "paper": True,
        "message": message,
        "trade": trade,
        "order": order,
    }


def submit_alpaca_paper_market_order(
    *,
    symbol: str,
    shares: int,
    side: str,
) -> dict[str, Any]:
    """
    Submit a whole-share DAY market order to Alpaca PAPER Trading.

    This intentionally uses the paper endpoint only.
    """
    normalized_symbol = clean_symbol(
        symbol
    )

    normalized_side = str(
        side or ""
    ).strip().lower()

    if not normalized_symbol:
        return {
            "success": False,
            "error": "A stock symbol is required.",
        }

    if shares <= 0:
        return {
            "success": False,
            "error": (
                "Share quantity must be greater "
                "than zero."
            ),
        }

    if normalized_side not in {
        "buy",
        "sell",
    }:
        return {
            "success": False,
            "error": "Trade side must be buy or sell.",
        }

    try:
        risk_check = validate_alpaca_paper_order_risk(
            symbol=normalized_symbol,
            shares=shares,
            side=normalized_side,
        )

    except Exception as error:
        print(
            f"Paper risk-check error for "
            f"{normalized_symbol}: "
            f"{clean_error_message(error)}"
        )

        return {
            "success": False,
            "paper": True,
            "error": (
                "The trade was blocked because the "
                "execution risk check could not be completed."
            ),
        }

    if not risk_check.get("approved"):
        return {
            "success": False,
            "paper": True,
            "error": str(
                risk_check.get(
                    "error",
                    "The order was blocked by risk controls.",
                )
            ),
            "risk": risk_check,
        }

    request_body = {
        "symbol": normalized_symbol,
        "qty": str(shares),
        "side": normalized_side,
        "type": "market",
        "time_in_force": "day",
        "client_order_id": (
            f"paper-{normalized_side}-"
            f"{normalized_symbol.lower()}-"
            f"{uuid.uuid4().hex[:12]}"
        ),
    }

    try:
        payload = alpaca_paper_request(
            "POST",
            "/v2/orders",
            json_body=request_body,
        )

        if not isinstance(
            payload,
            dict,
        ):
            return {
                "success": False,
                "error": (
                    "Alpaca returned an invalid "
                    "paper-order response."
                ),
            }

        latest_order = (
            wait_for_alpaca_paper_order(
                payload
            )
        )

        result = normalize_alpaca_paper_order(
            latest_order,
            requested_symbol=normalized_symbol,
            requested_shares=shares,
            requested_side=normalized_side,
        )

        result["risk"] = risk_check

        return result

    except Exception as error:
        print(
            f"Alpaca PAPER {normalized_side} "
            f"order error for "
            f"{normalized_symbol}: "
            f"{clean_error_message(error)}"
        )

        return {
            "success": False,
            "paper": True,
            "error": clean_error_message(
                error
            ),
        }


# =========================================================
# Alpaca paper-account dashboard helpers
# =========================================================

def fetch_alpaca_paper_account() -> dict[str, Any]:
    """
    Fetch the current Alpaca PAPER account.
    """
    payload = alpaca_paper_request(
        "GET",
        "/v2/account",
    )

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Alpaca returned an invalid account response."
        )

    return payload


def fetch_alpaca_paper_positions() -> list[dict[str, Any]]:
    """
    Fetch all currently open Alpaca PAPER positions.
    """
    payload = alpaca_paper_request(
        "GET",
        "/v2/positions",
    )

    if not isinstance(payload, list):
        raise RuntimeError(
            "Alpaca returned an invalid positions response."
        )

    return [
        position
        for position in payload
        if isinstance(position, dict)
    ]


def fetch_alpaca_paper_orders(
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    Fetch recent Alpaca PAPER orders for dashboard trade history.
    """
    safe_limit = max(
        1,
        min(
            int(limit),
            500,
        ),
    )

    payload = alpaca_paper_request(
        "GET",
        "/v2/orders",
        params={
            "status": "all",
            "limit": safe_limit,
            "direction": "desc",
        },
    )

    if not isinstance(payload, list):
        raise RuntimeError(
            "Alpaca returned an invalid orders response."
        )

    return [
        order
        for order in payload
        if isinstance(order, dict)
    ]

def fetch_alpaca_paper_trade_history(
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Fetch a deeper Alpaca PAPER order history for dashboard trade history.
    """

    safe_limit = max(
        1,
        min(
            int(limit),
            500,
        ),
    )

    payload = alpaca_paper_request(
        "GET",
        "/v2/orders",
        params={
            "status": "all",
            "limit": safe_limit,
            "direction": "desc",
        },
    )

    if not isinstance(payload, list):
        raise RuntimeError(
            "Alpaca returned an invalid trade-history response."
        )

    return [
        order
        for order in payload
        if isinstance(order, dict)
    ]

def normalize_alpaca_position(
    position: dict[str, Any],
) -> dict[str, Any]:
    """
    Convert Alpaca's position fields into the structure account.js expects.
    """
    symbol = clean_symbol(
        position.get("symbol")
    )

    qty = safe_float(
        position.get("qty")
    )

    entry_price = safe_float(
        position.get(
            "avg_entry_price"
        )
    )

    current_price = safe_float(
        position.get(
            "current_price"
        )
    )

    market_value = safe_float(
        position.get(
            "market_value"
        )
    )

    cost_basis = safe_float(
        position.get(
            "cost_basis"
        )
    )

    unrealized_pl = safe_float(
        position.get(
            "unrealized_pl"
        )
    )

    unrealized_plpc = safe_float(
        position.get(
            "unrealized_plpc"
        )
    )

    side = str(
        position.get(
            "side",
            "",
        )
    ).strip().lower()

    shares = qty or 0.0

    if side == "short" and shares > 0:
        shares = -shares

    return {
        "symbol": symbol,
        "shares": shares,
        "qty": shares,
        "side": side,
        "entry_price": entry_price or 0.0,
        "avg_entry_price": entry_price or 0.0,
        "current_price": current_price or 0.0,
        "market_price": current_price or 0.0,
        "position_value": (
            abs(market_value)
            if market_value is not None
            else abs(
                shares
                * (
                    current_price
                    or 0.0
                )
            )
        ),
        "market_value": (
            market_value
            if market_value is not None
            else shares
            * (
                current_price
                or 0.0
            )
        ),
        "cost_basis": (
            abs(cost_basis)
            if cost_basis is not None
            else abs(
                shares
                * (
                    entry_price
                    or 0.0
                )
            )
        ),
        "unrealized_profit": (
            unrealized_pl
            if unrealized_pl is not None
            else 0.0
        ),
        "unrealized_profit_percent": (
            unrealized_plpc * 100
            if unrealized_plpc is not None
            else 0.0
        ),
        "asset_id": position.get(
            "asset_id"
        ),
        "exchange": position.get(
            "exchange"
        ),
    }


def normalize_alpaca_order_for_history(
    order: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Convert filled Alpaca PAPER orders into account.js trade-history rows.
    """
    status = str(
        order.get(
            "status",
            "",
        )
    ).strip().lower()

    filled_qty = safe_float(
        order.get(
            "filled_qty"
        )
    )

    filled_price = safe_float(
        order.get(
            "filled_avg_price"
        )
    )

    if (
        status != "filled"
        or filled_qty is None
        or filled_qty <= 0
        or filled_price is None
        or filled_price <= 0
    ):
        return None

    symbol = clean_symbol(
        order.get(
            "symbol"
        )
    )

    side = str(
        order.get(
            "side",
            "",
        )
    ).strip().upper()

    total = round(
        filled_qty
        * filled_price,
        2,
    )

    timestamp = (
        order.get(
            "filled_at"
        )
        or order.get(
            "submitted_at"
        )
        or order.get(
            "created_at"
        )
    )

    return {
        "id": order.get("id"),
        "symbol": symbol,
        "side": side,
        "action": side,
        "shares": filled_qty,
        "qty": filled_qty,
        "price": round(
            filled_price,
            4,
        ),
        "execution_price": round(
            filled_price,
            4,
        ),
        "total": total,
        "timestamp": timestamp,
        "executed_at": timestamp,
        "status": status,
        "paper": True,
    }


def build_alpaca_live_account_snapshot() -> dict[str, Any]:
    """
    Lightweight read-only dashboard snapshot for frequent frontend polling.
    This does not download recent order history and does not change trading logic.
    """
    account = fetch_alpaca_paper_account()
    raw_positions = fetch_alpaca_paper_positions()

    positions = [
        normalize_alpaca_position(position)
        for position in raw_positions
        if isinstance(position, dict)
    ]

    positions = [
        position
        for position in positions
        if position.get("symbol")
    ]

    cash = safe_float(account.get("cash")) or 0.0
    equity = safe_float(account.get("equity"))
    portfolio_value = equity if equity is not None else cash

    last_equity = safe_float(account.get("last_equity"))
    if last_equity is None or last_equity <= 0:
        last_equity = portfolio_value

    unrealized_profit_loss = sum(
        safe_float(position.get("unrealized_profit")) or 0.0
        for position in positions
    )

    profit_loss = portfolio_value - last_equity
    profit_loss_percent = (
        (profit_loss / last_equity) * 100
        if last_equity > 0
        else 0.0
    )

    invested_value = sum(
        abs(safe_float(position.get("position_value")) or 0.0)
        for position in positions
    )

    allocation_total = cash + invested_value

    return {
        "source": "alpaca_paper",
        "paper": True,
        "timestamp": time.time(),
        "cash": round(cash, 2),
        "buying_power": safe_float(account.get("buying_power")) or 0.0,
        "portfolio_value": round(portfolio_value, 2),
        "total_value": round(portfolio_value, 2),
        "equity": round(portfolio_value, 2),
        "starting_balance": round(last_equity, 2),
        "starting_cash": round(last_equity, 2),
        "profit_loss": round(profit_loss, 2),
        "total_profit_loss": round(profit_loss, 2),
        "profit_loss_percent": round(profit_loss_percent, 4),
        "total_return_percent": round(profit_loss_percent, 4),
        "unrealized_profit_loss": round(unrealized_profit_loss, 2),
        "cash_percent": round(
            (cash / allocation_total) * 100
            if allocation_total > 0
            else 0.0,
            4,
        ),
        "invested_percent": round(
            (invested_value / allocation_total) * 100
            if allocation_total > 0
            else 0.0,
            4,
        ),
        "open_positions": len(positions),
        "position_count": len(positions),
        "positions": positions,
    }


def build_alpaca_dashboard_account() -> dict[str, Any]:
    """
    Build the /account response entirely from Alpaca PAPER data.

    This keeps the existing frontend working while making Alpaca the
    source of truth for cash, equity, open positions, and recent fills.
    """
    account = fetch_alpaca_paper_account()

    raw_positions = (
        fetch_alpaca_paper_positions()
    )

    raw_orders = fetch_alpaca_paper_trade_history(
        limit=500
    )

    positions = [
        normalize_alpaca_position(
            position
        )
        for position in raw_positions
    ]

    positions = [
        position
        for position in positions
        if position.get("symbol")
    ]

    history: list[dict[str, Any]] = []

    for order in raw_orders:
        trade = (
            normalize_alpaca_order_for_history(
                order
            )
        )

        if trade is not None:
            history.append(trade)

    buy_lots_by_symbol: dict[str, list[dict[str, float]]] = {}

    for trade in sorted(
        history,
        key=lambda item: str(
            item.get("timestamp") or ""
        ),
    ):
        symbol = clean_symbol(
            trade.get("symbol")
        )

        side = str(
            trade.get("side", "")
        ).strip().upper()

        shares = safe_float(
            trade.get("shares")
        )

        price = safe_float(
            trade.get("price")
        )

        if (
            not symbol
            or shares is None
            or shares <= 0
            or price is None
            or price <= 0
        ):
            continue

        if side == "BUY":
            buy_lots_by_symbol.setdefault(
                symbol,
                [],
            ).append({
                "shares": shares,
                "price": price,
            })

            trade["profit_loss_dollars"] = None
            trade["profit_loss_percent"] = None

            continue

        if side != "SELL":
            continue

        remaining_to_match = shares
        realized_pl = 0.0
        matched_cost = 0.0

        lots = buy_lots_by_symbol.setdefault(
            symbol,
            [],
        )

        while (
            remaining_to_match > 0
            and lots
        ):
            lot = lots[0]

            lot_shares = safe_float(
                lot.get("shares")
            ) or 0.0

            lot_price = safe_float(
                lot.get("price")
            ) or 0.0

            matched_shares = min(
                remaining_to_match,
                lot_shares,
            )

            realized_pl += (
                price - lot_price
            ) * matched_shares

            matched_cost += (
                lot_price
                * matched_shares
            )

            remaining_to_match -= (
                matched_shares
            )

            lot["shares"] = (
                lot_shares
                - matched_shares
            )

            if lot["shares"] <= 0:
                lots.pop(0)

        if matched_cost > 0:
            trade["profit_loss_dollars"] = round(
                realized_pl,
                2,
            )

            trade["profit_loss_percent"] = round(
                (
                    realized_pl
                    / matched_cost
                )
                * 100,
                4,
            )
        else:
            trade["profit_loss_dollars"] = None
            trade["profit_loss_percent"] = None

    cash = safe_float(
        account.get("cash")
    ) or 0.0

    equity = safe_float(
        account.get("equity")
    )

    portfolio_value = (
        equity
        if equity is not None
        else cash
    )

    last_equity = safe_float(
        account.get(
            "last_equity"
        )
    )

    if (
        last_equity is None
        or last_equity <= 0
    ):
        last_equity = portfolio_value

    unrealized_profit_loss = sum(
        safe_float(
            position.get(
                "unrealized_profit"
            )
        ) or 0.0
        for position in positions
    )

    # Alpaca's account endpoint exposes current and prior equity, but
    # not a simple all-time realized-P/L field. For now the dashboard's
    # realized metric remains zero until we add activity-based accounting.
    realized_profit_loss = 0.0

    profit_loss = (
        portfolio_value
        - last_equity
    )

    profit_loss_percent = (
        (
            profit_loss
            / last_equity
        )
        * 100
        if last_equity > 0
        else 0.0
    )

    invested_value = sum(
        abs(
            safe_float(
                position.get(
                    "position_value"
                )
            ) or 0.0
        )
        for position in positions
    )

    allocation_total = (
        cash
        + invested_value
    )

    cash_percent = (
        (
            cash
            / allocation_total
        )
        * 100
        if allocation_total > 0
        else 0.0
    )

    invested_percent = (
        (
            invested_value
            / allocation_total
        )
        * 100
        if allocation_total > 0
        else 0.0
    )

    closed_sells = sum(
        1
        for trade in history
        if str(
            trade.get(
                "side",
                "",
            )
        ).upper() == "SELL"
    )

    return {
        "source": "alpaca_paper",
        "paper": True,
        "account_id": account.get("id"),
        "status": account.get("status"),
        "cash": round(cash, 2),
        "buying_power": safe_float(
            account.get(
                "buying_power"
            )
        ) or 0.0,
        "portfolio_value": round(
            portfolio_value,
            2,
        ),
        "total_value": round(
            portfolio_value,
            2,
        ),
        "equity": round(
            portfolio_value,
            2,
        ),
        "starting_balance": round(
            last_equity,
            2,
        ),
        "starting_cash": round(
            last_equity,
            2,
        ),
        "profit_loss": round(
            profit_loss,
            2,
        ),
        "total_profit_loss": round(
            profit_loss,
            2,
        ),
        "profit_loss_percent": round(
            profit_loss_percent,
            4,
        ),
        "total_return_percent": round(
            profit_loss_percent,
            4,
        ),
        "realized_profit_loss": round(
            realized_profit_loss,
            2,
        ),
        "unrealized_profit_loss": round(
            unrealized_profit_loss,
            2,
        ),
        "win_rate": 0.0,
        "closed_trades": closed_sells,
        "cash_percent": round(
            cash_percent,
            4,
        ),
        "invested_percent": round(
            invested_percent,
            4,
        ),
        "positions": positions,
        "history": history,
        "trades": history,
        "performance": {
            "highest_value": round(
                max(
                    portfolio_value,
                    last_equity,
                ),
                2,
            ),
        },
    }


def fetch_alpaca_portfolio_history() -> list[dict[str, Any]]:
    """
    Return Alpaca PAPER portfolio equity history in the shape portfolio.js
    already expects: timestamp + value.
    """
    payload = alpaca_paper_request(
        "GET",
        "/v2/account/portfolio/history",
        params={
            "period": "1A",
            "timeframe": "1D",
        },
    )

    if not isinstance(payload, dict):
        return []

    timestamps = payload.get(
        "timestamp"
    )

    equity_values = payload.get(
        "equity"
    )

    if (
        not isinstance(
            timestamps,
            list,
        )
        or not isinstance(
            equity_values,
            list,
        )
    ):
        return []

    history: list[dict[str, Any]] = []

    for timestamp, value in zip(
        timestamps,
        equity_values,
    ):
        equity = safe_float(value)

        if (
            equity is None
            or equity <= 0
        ):
            continue

        history.append({
            "timestamp": timestamp,
            "time": timestamp,
            "value": round(
                equity,
                2,
            ),
            "equity": round(
                equity,
                2,
            ),
        })

    return history


# =========================================================
# Yahoo Finance helpers
# =========================================================

def fetch_current_price(symbol: str) -> float | None:
    """
    Return the latest available stock price.

    Alpaca Market Data is used first. On the free plan this normally
    uses the IEX feed. The latest quote midpoint is preferred because
    it can continue updating during extended-hours trading even when
    the latest trade is unchanged. Alpaca's latest trade is the next
    fallback, followed by Yahoo Finance.
    """
    normalized_symbol = clean_symbol(symbol)

    if not normalized_symbol:
        return None

    alpaca_key = os.getenv("ALPACA_API_KEY", "").strip()
    alpaca_secret = os.getenv("ALPACA_SECRET_KEY", "").strip()

    if alpaca_key and alpaca_secret:
        headers = {
            "APCA-API-KEY-ID": alpaca_key,
            "APCA-API-SECRET-KEY": alpaca_secret,
            "Accept": "application/json",
        }

        try:
            response = requests.get(
                f"https://data.alpaca.markets/v2/stocks/{normalized_symbol}/quotes/latest",
                headers=headers,
                params={"feed": "iex"},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
            quote = payload.get("quote", {}) if isinstance(payload, dict) else {}
            bid = safe_float(quote.get("bp") if isinstance(quote, dict) else None)
            ask = safe_float(quote.get("ap") if isinstance(quote, dict) else None)
            if bid is not None and ask is not None and bid > 0 and ask > 0 and ask >= bid:
                midpoint = (bid + ask) / 2
                if is_valid_price(midpoint):
                    return round(float(midpoint), 2)
        except Exception as error:
            print(
                f"Alpaca latest-quote error for {normalized_symbol}: "
                f"{clean_error_message(error)}"
            )

        try:
            response = requests.get(
                f"https://data.alpaca.markets/v2/stocks/{normalized_symbol}/trades/latest",
                headers=headers,
                params={"feed": "iex"},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
            trade = payload.get("trade", {}) if isinstance(payload, dict) else {}
            price = safe_float(trade.get("p") if isinstance(trade, dict) else None)
            if is_valid_price(price):
                return round(float(price), 2)
        except Exception as error:
            print(
                f"Alpaca latest-trade error for {normalized_symbol}: "
                f"{clean_error_message(error)}"
            )
    else:
        print(
            "Alpaca API credentials are not configured. "
            "Falling back to Yahoo Finance."
        )

    try:
        ticker = yf.Ticker(normalized_symbol)

        try:
            history = ticker.history(
                period="1d",
                interval="1m",
                prepost=True,
                auto_adjust=False,
                actions=False,
                repair=False,
                timeout=15,
            )
            if history is not None and not history.empty and "Close" in history.columns:
                closes = history["Close"].dropna()
                if not closes.empty:
                    price = safe_float(closes.iloc[-1])
                    if is_valid_price(price):
                        return round(float(price), 2)
        except Exception as error:
            print(
                f"Yahoo intraday-price error for {normalized_symbol}: "
                f"{clean_error_message(error)}"
            )

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
        except Exception as error:
            print(
                f"Yahoo fast-info error for {normalized_symbol}: "
                f"{clean_error_message(error)}"
            )

        try:
            history = ticker.history(
                period="5d",
                interval="1d",
                auto_adjust=False,
                actions=False,
                repair=False,
                timeout=15,
            )
            if history is not None and not history.empty and "Close" in history.columns:
                closes = history["Close"].dropna()
                if not closes.empty:
                    price = safe_float(closes.iloc[-1])
                    if is_valid_price(price):
                        return round(float(price), 2)
        except Exception as error:
            print(
                f"Yahoo daily-price fallback error for {normalized_symbol}: "
                f"{clean_error_message(error)}"
            )

    except Exception as error:
        print(
            f"Current-price error for {normalized_symbol}: "
            f"{clean_error_message(error)}"
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
# Automatic PAPER trader
# =========================================================

def auto_trader_automation_allowed() -> bool:
    """
    Railway must explicitly allow automatic PAPER orders.

    This is a hard kill switch separate from the runtime enable/disable flag.
    """
    return (
        os.getenv(
            "AUTO_TRADER_ALLOW_AUTOMATION",
            "",
        )
        .strip()
        .lower()
        in {
            "1",
            "true",
            "yes",
            "on",
        }
    )


def get_auto_trader_control_token() -> str:
    return os.getenv(
        "AUTO_TRADER_CONTROL_TOKEN",
        "",
    ).strip()


def auto_trader_control_authorized(
    supplied_token: str | None,
) -> bool:
    configured_token = (
        get_auto_trader_control_token()
    )

    if not configured_token:
        return False

    return (
        str(
            supplied_token
            or ""
        ).strip()
        == configured_token
    )

def save_auto_trader_log() -> None:
    try:
        directory = os.path.dirname(
            AUTO_TRADER_LOG_FILE
        )

        if directory:
            os.makedirs(
                directory,
                exist_ok=True,
            )

        with open(
            AUTO_TRADER_LOG_FILE,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                _auto_trader_log[
                    -AUTO_TRADER_LOG_LIMIT:
                ],
                file,
            )

    except Exception as error:
        print(
            "Auto-trader log save error: "
            f"{clean_error_message(error)}"
        )

def save_auto_trader_journal() -> None:
    try:
        directory = os.path.dirname(
            AUTO_TRADER_JOURNAL_FILE
        )

        if directory:
            os.makedirs(
                directory,
                exist_ok=True,
            )

        with open(
            AUTO_TRADER_JOURNAL_FILE,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                _auto_trader_journal[
                    -AUTO_TRADER_JOURNAL_LIMIT:
                ],
                file,
            )

    except Exception as error:
        print(
            "Auto-trader journal save error: "
            f"{clean_error_message(error)}"
        )

def load_auto_trader_log() -> None:
    if not os.path.exists(
        AUTO_TRADER_LOG_FILE
    ):
        return

    try:
        with open(
            AUTO_TRADER_LOG_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            saved_logs = json.load(
                file
            )

        if not isinstance(
            saved_logs,
            list,
        ):
            return

        _auto_trader_log.clear()

        _auto_trader_log.extend(
            entry
            for entry in saved_logs[
                -AUTO_TRADER_LOG_LIMIT:
            ]
            if isinstance(
                entry,
                dict,
            )
        )

    except Exception as error:
        print(
            "Auto-trader log load error: "
            f"{clean_error_message(error)}"
        )

def load_auto_trader_journal() -> None:
    if not os.path.exists(
        AUTO_TRADER_JOURNAL_FILE
    ):
        return

    try:
        with open(
            AUTO_TRADER_JOURNAL_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            saved_journal = json.load(
                file
            )

        if not isinstance(
            saved_journal,
            list,
        ):
            return

        _auto_trader_journal.clear()

        _auto_trader_journal.extend(
            entry
            for entry in saved_journal[
                -AUTO_TRADER_JOURNAL_LIMIT:
            ]
            if isinstance(
                entry,
                dict,
            )
        )

    except Exception as error:
        print(
            "Auto-trader journal load error: "
            f"{clean_error_message(error)}"
        )

def add_auto_trader_log(
    event: str,
    *,
    symbol: str | None = None,
    message: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    entry = {
        "timestamp": time.time(),
        "event": str(event),
        "symbol": (
            clean_symbol(symbol)
            if symbol
            else None
        ),
        "message": str(message),
        "details": (
            details
            if isinstance(
                details,
                dict,
            )
            else {}
        ),
    }

    _auto_trader_log.append(
        entry
    )

    save_auto_trader_log()

    if (
        len(_auto_trader_log)
        > AUTO_TRADER_LOG_LIMIT
    ):
        del _auto_trader_log[
            :-AUTO_TRADER_LOG_LIMIT
        ]

def add_auto_trader_journal_entry(
    *,
    symbol: str,
    event: str,
    details: dict[str, Any] | None = None,
) -> None:
    entry = {
        "timestamp": time.time(),
        "symbol": clean_symbol(symbol),
        "event": str(event),
        "details": (
            details
            if isinstance(details, dict)
            else {}
        ),
    }

    _auto_trader_journal.append(
        entry
    )

    if (
        len(_auto_trader_journal)
        > AUTO_TRADER_JOURNAL_LIMIT
    ):
        del _auto_trader_journal[
            :-AUTO_TRADER_JOURNAL_LIMIT
        ]

    save_auto_trader_journal()

def get_auto_trader_last_symbol_action_time(
    symbol: str,
) -> float | None:
    """
    Return the most recent automatic-trader action time
    for a symbol.

    This checks both the in-memory cooldown dictionary and
    the persisted trading journal so cooldown protection
    survives Railway/app restarts.
    """
    normalized_symbol = clean_symbol(
        symbol
    )

    if not normalized_symbol:
        return None

    latest_timestamp = (
        _auto_trader_symbol_cooldowns.get(
            normalized_symbol
        )
    )

    for journal_entry in reversed(
        _auto_trader_journal
    ):
        if not isinstance(
            journal_entry,
            dict,
        ):
            continue

        journal_symbol = clean_symbol(
            journal_entry.get(
                "symbol"
            )
        )

        if (
            journal_symbol
            != normalized_symbol
        ):
            continue

        event = str(
            journal_entry.get(
                "event",
                "",
            )
        ).strip().lower()

        if event not in {
            "entry",
            "exit",
        }:
            continue

        timestamp = safe_float(
            journal_entry.get(
                "timestamp"
            )
        )

        if timestamp is None:
            continue

        if (
            latest_timestamp is None
            or timestamp
            > latest_timestamp
        ):
            latest_timestamp = (
                timestamp
            )

        # Because the journal is newest-first when
        # scanning in reverse order, once we find the
        # newest matching action we do not need to keep
        # searching.
        break

    return latest_timestamp


def get_auto_trader_last_protective_exit_time(
    symbol: str,
) -> float | None:
    """
    Return the most recent stop/loss-related exit for a
    symbol.

    These exits receive a longer cooldown so the bot
    cannot immediately buy the same daily BUY signal
    again after being stopped out.
    """
    normalized_symbol = clean_symbol(
        symbol
    )

    if not normalized_symbol:
        return None

    protected_exit_reasons = {
        "protective_stop_fill",
        "hard_max_loss_exit",
        "defensive_portfolio_exit",
    }

    for journal_entry in reversed(
        _auto_trader_journal
    ):
        if not isinstance(
            journal_entry,
            dict,
        ):
            continue

        if clean_symbol(
            journal_entry.get(
                "symbol"
            )
        ) != normalized_symbol:
            continue

        if str(
            journal_entry.get(
                "event",
                "",
            )
        ).strip().lower() != "exit":
            continue

        details = journal_entry.get(
            "details"
        )

        if not isinstance(
            details,
            dict,
        ):
            continue

        reason = str(
            details.get(
                "reason",
                "",
            )
        ).strip().lower()

        if reason not in (
            protected_exit_reasons
        ):
            continue

        timestamp = safe_float(
            journal_entry.get(
                "timestamp"
            )
        )

        if timestamp is not None:
            return timestamp

    return None


def auto_trader_symbol_on_cooldown(
    symbol: str,
) -> tuple[bool, float, str]:
    """
    Check both normal and longer protective-exit
    cooldowns.

    Returns:
        (
            cooldown_active,
            seconds_remaining,
            cooldown_type,
        )
    """
    normalized_symbol = clean_symbol(
        symbol
    )

    if not normalized_symbol:
        return False, 0.0, ""

    now = time.time()

    # -------------------------------------------------
    # Longer cooldown after stop/loss-related exits.
    # -------------------------------------------------
    last_protective_exit = (
        get_auto_trader_last_protective_exit_time(
            normalized_symbol
        )
    )

    if last_protective_exit is not None:
        remaining = (
            AUTO_TRADER_LOSS_COOLDOWN_SECONDS
            - (
                now
                - last_protective_exit
            )
        )

        if remaining > 0:
            return (
                True,
                remaining,
                "protective_exit_cooldown",
            )

    # -------------------------------------------------
    # Normal symbol cooldown.
    # -------------------------------------------------
    last_action = (
        get_auto_trader_last_symbol_action_time(
            normalized_symbol
        )
    )

    if last_action is None:
        return False, 0.0, ""

    remaining = (
        AUTO_TRADER_SYMBOL_COOLDOWN_SECONDS
        - (
            now
            - last_action
        )
    )

    if remaining > 0:
        return (
            True,
            remaining,
            "symbol_cooldown",
        )

    return False, 0.0, ""


def mark_auto_trader_symbol_cooldown(
    symbol: str,
) -> None:
    """
    Mark a symbol action immediately in memory.

    The persisted journal provides restart-safe
    cooldown history.
    """
    normalized_symbol = clean_symbol(
        symbol
    )

    if not normalized_symbol:
        return

    _auto_trader_symbol_cooldowns[
        normalized_symbol
    ] = time.time()


def cancel_alpaca_open_orders_for_symbol(
    symbol: str,
) -> list[str]:
    """
    Cancel current PAPER orders for one symbol.

    This is mainly used before a scanner-driven early exit when a bracket
    order already has protective child orders working.
    """
    canceled_ids: list[str] = []

    for order in (
        fetch_alpaca_open_orders_for_symbol(
            symbol
        )
    ):
        order_id = str(
            order.get(
                "id",
                "",
            )
        ).strip()

        if not order_id:
            continue

        try:
            alpaca_paper_request(
                "DELETE",
                f"/v2/orders/{order_id}",
            )

            canceled_ids.append(
                order_id
            )

        except Exception as error:
            print(
                f"Could not cancel PAPER order "
                f"{order_id} for {symbol}: "
                f"{clean_error_message(error)}"
            )

    if canceled_ids:
        remaining_orders = []

        for _ in range(10):
            try:
                remaining_orders = (
                    fetch_alpaca_open_orders_for_symbol(
                        symbol
                    )
                )
            except Exception as error:
                print(
                    f"Could not confirm PAPER order "
                    f"cancellation for {symbol}: "
                    f"{clean_error_message(error)}"
                )
                return []

            if not remaining_orders:
                break

            time.sleep(0.5)

        if remaining_orders:
            return []

    return canceled_ids

def submit_alpaca_recovery_oco(
    *,
    symbol: str,
    shares: int,
    current_price: float,
) -> dict[str, Any]:
    """
    Restore protective PAPER exits for an existing long position.

    This is used when a position exists but has no open protective
    sell orders. Recovery protection is based on the current price,
    rather than stale entry thresholds from an expired DAY bracket.
    """
    normalized_symbol = clean_symbol(
        symbol
    )

    if not normalized_symbol:
        return {
            "success": False,
            "paper": True,
            "error": "A valid symbol is required.",
        }

    if shares <= 0:
        return {
            "success": False,
            "paper": True,
            "error": (
                "Recovery protection requires "
                "at least one share."
            ),
        }

    if (
        current_price is None
        or current_price <= 0
    ):
        return {
            "success": False,
            "paper": True,
            "error": (
                "A valid current price is required "
                "for recovery protection."
            ),
        }

    try:
        open_orders = (
            fetch_alpaca_open_orders_for_symbol(
                normalized_symbol
            )
        )
    except Exception as error:
        return {
            "success": False,
            "paper": True,
            "error": clean_error_message(
                error
            ),
        }

    all_open_orders = []

    for order in open_orders:
        if not isinstance(
            order,
            dict,
        ):
            continue

        all_open_orders.append(
            order
        )

        legs = order.get(
            "legs"
        )

        if isinstance(
            legs,
            list,
        ):
            all_open_orders.extend(
                leg
                for leg in legs
                if isinstance(
                    leg,
                    dict,
                )
            )

    protective_stop_orders = [
        order
        for order in all_open_orders
        if (
            str(
                order.get(
                    "side",
                    "",
                )
            ).strip().lower() == "sell"
            and (
                str(
                    order.get(
                        "type",
                        "",
                    )
                ).strip().lower() == "stop"
                or safe_float(
                    order.get(
                        "stop_price"
                    )
                ) is not None
            )
        )
    ]

    matching_protective_stop_orders = []

    for order in protective_stop_orders:
        protective_qty = safe_float(
            order.get(
                "qty"
            )
        )

        if (
            protective_qty is not None
            and abs(
                protective_qty - shares
            ) < 0.000001
        ):
            matching_protective_stop_orders.append(
                order
            )

    if (
        len(protective_stop_orders) == 1
        and len(
            matching_protective_stop_orders
        ) == 1
    ):
        return {
            "success": True,
            "paper": True,
            "already_protected": True,
            "symbol": normalized_symbol,
            "position_shares": shares,
            "open_protective_orders": len(
                protective_stop_orders
            ),
            "matching_protective_orders": len(
                matching_protective_stop_orders
            ),
        }

    if open_orders:
        non_sell_open_orders = [
            order
            for order in open_orders
            if str(
                order.get(
                    "side",
                    "",
                )
            ).strip().lower() != "sell"
        ]

        if non_sell_open_orders:
            return {
                "success": False,
                "paper": True,
                "error": (
                    f"{normalized_symbol} has a non-sell "
                    "open order, so recovery protection "
                    "was not changed."
                ),
            }

        canceled_ids = (
            cancel_alpaca_open_orders_for_symbol(
                normalized_symbol
            )
        )

        if not canceled_ids:
            return {
                "success": False,
                "paper": True,
                "error": (
                    f"{normalized_symbol} had incomplete "
                    "sell protection, but the existing "
                    "order could not be canceled."
                ),
            }

        remaining_open_orders = []

        for _ in range(10):
            try:
                remaining_open_orders = (
                    fetch_alpaca_open_orders_for_symbol(
                        normalized_symbol
                    )
                )
            except Exception as error:
                return {
                    "success": False,
                    "paper": True,
                    "error": (
                        "Existing protection was canceled, "
                        "but Alpaca could not confirm the "
                        "order state before replacement: "
                        f"{clean_error_message(error)}"
                    ),
                }

            if not remaining_open_orders:
                break

            time.sleep(0.5)

        if remaining_open_orders:
            return {
                "success": False,
                "paper": True,
                "error": (
                    f"{normalized_symbol} still has an "
                    "open order after cancellation, so "
                    "replacement protection was not added."
                ),
            }

    stop_price = round(
        current_price
        * (
            1
            - (
                AUTO_TRADER_STOP_LOSS_PERCENT
                / 100
            )
        ),
        2,
    )

    take_profit_price = round(
        current_price
        * (
            1
            + (
                AUTO_TRADER_TAKE_PROFIT_PERCENT
                / 100
            )
        ),
        2,
    )

    if (
        stop_price <= 0
        or stop_price >= current_price
        or take_profit_price <= current_price
    ):
        return {
            "success": False,
            "paper": True,
            "error": (
                "Recovery protection prices "
                "were invalid."
            ),
        }

    request_body = {
        "symbol": normalized_symbol,
        "qty": str(shares),
        "side": "sell",
        "type": "limit",
        "time_in_force": "gtc",

        "order_class": "oco",
        "take_profit": {
            "limit_price": (
                f"{take_profit_price:.2f}"
            ),
        },
        "stop_loss": {
            "stop_price": (
                f"{stop_price:.2f}"
            ),
        },
        "client_order_id": (
            f"auto-protect-"
            f"{normalized_symbol.lower()}-"
            f"{uuid.uuid4().hex[:12]}"
        ),
    }

    try:
        payload = alpaca_paper_request(
            "POST",
            "/v2/orders",
            json_body=request_body,
        )

        if not isinstance(
            payload,
            dict,
        ):
            raise RuntimeError(
                "Alpaca returned an invalid "
                "recovery-protection response."
            )

        return {
            "success": True,
            "paper": True,
            "automatic": True,
            "symbol": normalized_symbol,
            "shares": shares,
            "reference_price": round(
                current_price,
                4,
            ),
            "stop_price": stop_price,
            "take_profit_price": (
                take_profit_price
            ),
            "order": payload,
        }

    except Exception as error:
        return {
            "success": False,
            "paper": True,
            "automatic": True,
            "symbol": normalized_symbol,
            "error": clean_error_message(
                error
            ),
        }


def reconcile_unprotected_positions(
    positions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Find existing PAPER positions without protective sell orders
    and attach a GTC OCO recovery bracket.
    """
    results: list[
        dict[str, Any]
    ] = []

    for position in positions:
        if not isinstance(
            position,
            dict,
        ):
            continue

        symbol = clean_symbol(
            position.get(
                "symbol"
            )
        )

        if not symbol:
            continue

        qty = safe_float(
            position.get(
                "qty"
            )
        )

        if (
            qty is None
            or qty <= 0
        ):
            continue

        shares = math.floor(
            qty
        )

        if shares <= 0:
            continue

        current_price = safe_float(
            position.get(
                "current_price"
            )
        )

        if (
            current_price is None
            or current_price <= 0
        ):
            current_price = (
                fetch_current_price(
                    symbol
                )
            )

        if (
            current_price is None
            or current_price <= 0
        ):
            result = {
                "success": False,
                "paper": True,
                "symbol": symbol,
                "error": (
                    "Current price was unavailable."
                ),
            }
        else:
            result = (
                submit_alpaca_recovery_oco(
                    symbol=symbol,
                    shares=shares,
                    current_price=current_price,
                )
            )

        results.append({
            "symbol": symbol,
            "result": result,
        })

        if result.get(
            "already_protected"
        ):
            continue

        if result.get(
            "success"
        ):
            add_auto_trader_log(
                "protection_restored",
                symbol=symbol,
                message=(
                    "Automatic PAPER protection "
                    "was restored with a GTC OCO."
                ),
                details={
                    "shares": shares,
                    "reference_price": (
                        current_price
                    ),
                    "stop_price": (
                        result.get(
                            "stop_price"
                        )
                    ),
                    "take_profit_price": (
                        result.get(
                            "take_profit_price"
                        )
                    ),
                    "result_success": True,
                },
            )

        else:
            add_auto_trader_log(
                "protection_restore_failed",
                symbol=symbol,
                message=(
                    "Automatic PAPER protection "
                    "could not be restored."
                ),
                details={
                    "shares": shares,
                    "result_success": False,
                    "error": (
                        result.get(
                            "error"
                        )
                    ),
                },
            )

    return results

def calculate_auto_entry_shares(
    *,
    symbol: str,
    account: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """
    Size an automatic entry using a small percent of account equity.

    The normal execution risk layer still runs afterward, so this sizing
    cannot bypass the global 2% order / 5% position limits.
    """
    equity = safe_float(
        account.get("equity")
    )

    if (
        equity is None
        or equity <= 0
    ):
        return 0, {}

    quote = fetch_alpaca_risk_quote(
        symbol
    )

    ask = safe_float(
        quote.get("ask")
    )

    if (
        ask is None
        or ask <= 0
    ):
        return 0, quote

    budget = (
        equity
        * (
            AUTO_TRADER_ENTRY_EQUITY_PERCENT
            / 100
        )
    )

    shares = max(
        0,
        math.floor(
            budget
            / ask
        ),
    )

    return shares, quote


def submit_alpaca_auto_bracket_buy(
    *,
    symbol: str,
    shares: int,
    reference_price: float,
    scanner_result: dict[str, Any],
    entry_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Submit an automatic PAPER buy with broker-native stop-loss and
    take-profit exit orders.

    Alpaca remains the source of truth for the actual simulated fill.
    """
    normalized_symbol = clean_symbol(
        symbol
    )

    risk_check = (
        validate_alpaca_paper_order_risk(
            symbol=normalized_symbol,
            shares=shares,
            side="buy",
        )
    )

    if not risk_check.get(
        "approved"
    ):
        return {
            "success": False,
            "paper": True,
            "error": str(
                risk_check.get(
                    "error",
                    "Automatic entry was blocked by risk controls.",
                )
            ),
            "risk": risk_check,
        }

    stop_price = round(
        reference_price
        * (
            1
            - (
                AUTO_TRADER_STOP_LOSS_PERCENT
                / 100
            )
        ),
        2,
    )

    take_profit_price = round(
        reference_price
        * (
            1
            + (
                AUTO_TRADER_TAKE_PROFIT_PERCENT
                / 100
            )
        ),
        2,
    )

    if (
        stop_price <= 0
        or take_profit_price <= stop_price
    ):
        return {
            "success": False,
            "paper": True,
            "error": (
                "Automatic bracket prices were invalid."
            ),
        }

    request_body = {
        "symbol": normalized_symbol,
        "qty": str(shares),
        "side": "buy",
        "type": "market",
        "time_in_force": "gtc",
        "order_class": "bracket",
        "take_profit": {
            "limit_price": (
                f"{take_profit_price:.2f}"
            ),
        },
        "stop_loss": {
            "stop_price": (
                f"{stop_price:.2f}"
            ),
        },
        "client_order_id": (
            f"auto-entry-"
            f"{normalized_symbol.lower()}-"
            f"{uuid.uuid4().hex[:12]}"
        ),
    }

    try:
        payload = alpaca_paper_request(
            "POST",
            "/v2/orders",
            json_body=request_body,
        )

        if not isinstance(
            payload,
            dict,
        ):
            raise RuntimeError(
                "Alpaca returned an invalid automatic order response."
            )

        latest_order = (
            wait_for_alpaca_paper_order(
                payload
            )
        )

        result = normalize_alpaca_paper_order(
            latest_order,
            requested_symbol=normalized_symbol,
            requested_shares=shares,
            requested_side="buy",
        )

        result["risk"] = risk_check
        result["automatic"] = True
        result["scanner"] = {
            "score": scanner_result.get(
                "score"
            ),
            "confidence": scanner_result.get(
                "confidence"
            ),
            "signal": scanner_result.get(
                "signal"
            ),
            "rank": (
                scanner_result.get(
                    "scanner_rank"
                )
                or scanner_result.get(
                    "rank"
                )
            ),
        }
        result["bracket"] = {
            "stop_loss_percent": (
                AUTO_TRADER_STOP_LOSS_PERCENT
            ),
            "take_profit_percent": (
                AUTO_TRADER_TAKE_PROFIT_PERCENT
            ),
            "stop_price": stop_price,
            "take_profit_price": (
                take_profit_price
            ),
        }

        trade_result = result.get("trade")

        if (
            isinstance(trade_result, dict)
            and trade_result.get("status") == "filled"
        ):
            try:
                entry_price = safe_float(
                    trade_result.get("execution_price")
                    or latest_order.get("filled_avg_price")
                )

                entry_timestamp = str(
                    trade_result.get("filled_at")
                    or latest_order.get("filled_at")
                    or ""
                ).strip()

                entry_order_id = str(
                    trade_result.get("id")
                    or latest_order.get("id")
                    or ""
                ).strip()

                filled_shares = safe_float(
                    trade_result.get("filled_shares")
                )

                if (
                    entry_price is not None
                    and entry_price > 0
                    and entry_timestamp
                    and filled_shares is not None
                    and filled_shares > 0
                ):
                    trade_book_id = (
                        create_trade_book_entry(
                            symbol=normalized_symbol,
                            shares=filled_shares,
                            entry_price=entry_price,
                            entry_timestamp=entry_timestamp,
                            created_at=entry_timestamp,
                            entry_order_id=(
                                entry_order_id
                                or None
                            ),
                            entry_reason="auto_trader_entry",
                            strategy="scanner_auto_trader",
                        )
                    )

                    entry_event_details = {
                        "score": scanner_result.get(
                            "score"
                        ),
                        "confidence": scanner_result.get(
                            "confidence"
                        ),
                        "signal": scanner_result.get(
                            "signal"
                        ),
                        "scanner_rank": (
                            scanner_result.get(
                                "scanner_rank"
                            )
                            or scanner_result.get(
                                "rank"
                            )
                        ),
                        "stop_loss_percent": (
                            AUTO_TRADER_STOP_LOSS_PERCENT
                        ),
                        "take_profit_percent": (
                            AUTO_TRADER_TAKE_PROFIT_PERCENT
                        ),
                        "stop_price": stop_price,
                        "take_profit_price": (
                            take_profit_price
                        ),
                    }

                    if isinstance(
                        entry_context,
                        dict,
                    ):
                        entry_event_details.update(
                            entry_context
                        )

                    record_trade_book_event(
                        trade_book_id=trade_book_id,
                        symbol=normalized_symbol,
                        event="entry",
                        timestamp=entry_timestamp,
                        details=entry_event_details,
                    )

                    result["trade_book_id"] = (
                        trade_book_id
                    )

            except Exception as error:
                print(
                    f"Could not record trade-book entry "
                    f"for {normalized_symbol}: "
                    f"{clean_error_message(error)}"
                )

        return result

    except Exception as error:
        return {
            "success": False,
            "paper": True,
            "automatic": True,
            "error": clean_error_message(
                error
            ),
        }


def get_scanner_result_by_symbol(
    scanner_results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        clean_symbol(
            item.get("symbol")
        ): item
        for item in scanner_results
        if (
            isinstance(
                item,
                dict,
            )
            and clean_symbol(
                item.get("symbol")
            )
        )
    }

def get_open_protective_stop_order(
    symbol: str,
) -> dict[str, Any] | None:
    normalized_symbol = clean_symbol(
        symbol
    )

    if not normalized_symbol:
        return None

    try:
        open_orders = (
            fetch_alpaca_open_orders_for_symbol(
                normalized_symbol
            )
        )
    except Exception:
        return None

    candidate_orders: list[
        dict[str, Any]
    ] = []

    for order in open_orders:
        if not isinstance(
            order,
            dict,
        ):
            continue

        candidate_orders.append(
            order
        )

        legs = order.get(
            "legs"
        )

        if isinstance(
            legs,
            list,
        ):
            candidate_orders.extend(
                leg
                for leg in legs
                if isinstance(
                    leg,
                    dict,
                )
            )

    for order in candidate_orders:
        side = str(
            order.get(
                "side",
                "",
            )
        ).strip().lower()

        order_type = str(
            order.get(
                "type",
                "",
            )
        ).strip().lower()

        stop_price = safe_float(
            order.get(
                "stop_price"
            )
        )

        if (
            side == "sell"
            and order_type == "stop"
            and stop_price is not None
            and stop_price > 0
        ):
            return order

    return None

def calculate_profit_lock_stop(
    *,
    entry_price: float,
    current_price: float,
    existing_stop_price: float | None,
) -> float | None:
    if (
        entry_price <= 0
        or current_price <= 0
    ):
        return None

    gain_percent = (
        (
            current_price
            - entry_price
        )
        / entry_price
    ) * 100

    if (
        gain_percent
        < AUTO_TRADER_PROFIT_LOCK_TRIGGER_PERCENT
    ):
        return None

    minimum_locked_stop = (
        entry_price
        * (
            1
            + (
                AUTO_TRADER_PROFIT_LOCK_PERCENT
                / 100
            )
        )
    )

    trailing_stop = (
        current_price
        * (
            1
            - (
                AUTO_TRADER_PROFIT_TRAIL_PERCENT
                / 100
            )
        )
    )

    new_stop = max(
        minimum_locked_stop,
        trailing_stop,
    )

    if (
        existing_stop_price is not None
        and new_stop <= existing_stop_price
    ):
        return None

    if new_stop >= current_price:
        return None

    return round(
        new_stop,
        2,
    )

def raise_protective_stop(
    *,
    symbol: str,
    new_stop_price: float,
) -> dict[str, Any]:
    normalized_symbol = clean_symbol(
        symbol
    )

    if (
        not normalized_symbol
        or new_stop_price <= 0
    ):
        return {
            "success": False,
            "paper": True,
            "error": (
                "A valid symbol and stop price "
                "are required."
            ),
        }

    stop_order = (
        get_open_protective_stop_order(
            normalized_symbol
        )
    )

    if stop_order is None:
        return {
            "success": False,
            "paper": True,
            "error": (
                "No open protective stop order "
                f"was found for {normalized_symbol}."
            ),
        }

    order_id = str(
        stop_order.get(
            "id",
            "",
        )
    ).strip()

    old_stop_price = safe_float(
        stop_order.get(
            "stop_price"
        )
    )

    if not order_id:
        return {
            "success": False,
            "paper": True,
            "error": (
                "Protective stop order ID "
                "was unavailable."
            ),
        }

    if (
        old_stop_price is not None
        and new_stop_price <= old_stop_price
    ):
        return {
            "success": True,
            "paper": True,
            "changed": False,
            "symbol": normalized_symbol,
            "old_stop_price": old_stop_price,
            "new_stop_price": old_stop_price,
        }

    try:
        payload = alpaca_paper_request(
            "PATCH",
            f"/v2/orders/{order_id}",
            json_body={
                "stop_price": (
                    f"{new_stop_price:.2f}"
                ),
            },
        )

        if not isinstance(
            payload,
            dict,
        ):
            raise RuntimeError(
                "Alpaca returned an invalid "
                "stop-replacement response."
            )

        return {
            "success": True,
            "paper": True,
            "changed": True,
            "symbol": normalized_symbol,
            "old_stop_price": old_stop_price,
            "new_stop_price": new_stop_price,
            "order": payload,
        }

    except Exception as error:
        return {
            "success": False,
            "paper": True,
            "changed": False,
            "symbol": normalized_symbol,
            "old_stop_price": old_stop_price,
            "new_stop_price": new_stop_price,
            "error": clean_error_message(
                error
            ),
        }

def calculate_position_return_percent(
    *,
    entry_price: float,
    current_price: float,
) -> float | None:
    if (
        entry_price <= 0
        or current_price <= 0
    ):
        return None

    return (
        (
            current_price
            - entry_price
        )
        / entry_price
    ) * 100


def should_hard_max_loss_exit(
    *,
    entry_price: float,
    current_price: float,
) -> bool:
    return_percent = (
        calculate_position_return_percent(
            entry_price=entry_price,
            current_price=current_price,
        )
    )

    if return_percent is None:
        return False

    return (
        return_percent
        <= -AUTO_TRADER_HARD_MAX_LOSS_PERCENT
    )
def detect_new_broker_exit_fills() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    try:
        orders = fetch_alpaca_paper_orders(
            limit=100
        )
    except Exception as error:
        add_auto_trader_log(
            "broker_exit_scan_error",
            message=(
                "Could not scan recent PAPER sell fills."
            ),
            details={
                "error": clean_error_message(
                    error
                ),
            },
        )
        return results

    for order in orders:
        if not isinstance(
            order,
            dict,
        ):
            continue

        order_id = str(
            order.get(
                "id",
                "",
            )
        ).strip()

        if not order_id:
            continue

        side = str(
            order.get(
                "side",
                "",
            )
        ).strip().lower()

        status = str(
            order.get(
                "status",
                "",
            )
        ).strip().lower()

        filled_qty = safe_float(
            order.get(
                "filled_qty"
            )
        )

        filled_price = safe_float(
            order.get(
                "filled_avg_price"
            )
        )

        if (
            side != "sell"
            or status != "filled"
            or filled_qty is None
            or filled_qty <= 0
            or filled_price is None
            or filled_price <= 0
        ):
            continue

        if (
            order_id
            in _auto_trader_seen_exit_order_ids
        ):
            continue

        symbol = clean_symbol(
            order.get(
                "symbol"
            )
        )

        order_type = str(
            order.get(
                "type",
                "",
            )
        ).strip().lower()

        stop_price = safe_float(
            order.get(
                "stop_price"
            )
        )

        limit_price = safe_float(
            order.get(
                "limit_price"
            )
        )

        exit_reason = "broker_sell_fill"

        if order_type == "stop":
            exit_reason = "protective_stop_fill"
        elif (
            order_type == "limit"
            and limit_price is not None
        ):
            exit_reason = "take_profit_fill"

        result = {
            "order_id": order_id,
            "symbol": symbol,
            "shares": filled_qty,
            "filled_price": filled_price,
            "filled_at": order.get(
                "filled_at"
            ),
            "order_type": order_type,
            "stop_price": stop_price,
            "limit_price": limit_price,
            "reason": exit_reason,
        }

        _auto_trader_seen_exit_order_ids.add(
            order_id
        )

        results.append(
            result
        )

    return results

def log_new_broker_exit_fills() -> list[dict[str, Any]]:
    fills = detect_new_broker_exit_fills()

    for fill in fills:
        symbol = clean_symbol(
            fill.get(
                "symbol"
            )
        )

        reason = str(
            fill.get(
                "reason",
                "broker_sell_fill",
            )
        )

        filled_price = safe_float(
            fill.get(
                "filled_price"
            )
        )

        shares = safe_float(
            fill.get(
                "shares"
            )
        )

        filled_at = str(
            fill.get(
                "filled_at"
            )
            or ""
        ).strip()

        order_id = str(
            fill.get(
                "order_id"
            )
            or ""
        ).strip()

        if reason == "protective_stop_fill":
            message = (
                "Protective stop filled and "
                "closed the PAPER position."
            )
        elif reason == "take_profit_fill":
            message = (
                "Take-profit order filled and "
                "closed the PAPER position."
            )
        else:
            message = (
                "Broker sell order filled and "
                "closed the PAPER position."
            )

        add_auto_trader_log(
            reason,
            symbol=symbol,
            message=message,
            details={
                "shares": shares,
                "filled_price": filled_price,
                "filled_at": filled_at,
                "order_id": order_id,
                "order_type": fill.get(
                    "order_type"
                ),
                "stop_price": fill.get(
                    "stop_price"
                ),
                "limit_price": fill.get(
                    "limit_price"
                ),
            },
        )

        add_auto_trader_journal_entry(
            symbol=symbol,
            event="exit",
            details={
                "reason": reason,
                "shares": shares,
                "exit_price": filled_price,
                "filled_at": filled_at,
                "order_id": order_id,
                "order_type": fill.get(
                    "order_type"
                ),
                "stop_price": fill.get(
                    "stop_price"
                ),
                "limit_price": fill.get(
                    "limit_price"
                ),
                "broker_detected": True,
            },
        )

        # -------------------------------------------------
        # Close the matching trade-book record and save the
        # completed broker exit into learning memory.
        # -------------------------------------------------
        try:
            open_book_entry = (
                load_open_trade_book_entry(
                    symbol
                )
            )

            if (
                open_book_entry
                and filled_price is not None
                and filled_price > 0
                and filled_at
            ):
                trade_book_id = int(
                    open_book_entry["id"]
                )

                closed_book_entry = (
                    close_trade_book_entry(
                        trade_book_id,
                        exit_price=filled_price,
                        exit_timestamp=filled_at,
                        updated_at=filled_at,
                        exit_order_id=(
                            order_id
                            or None
                        ),
                        exit_reason=reason,
                    )
                )

                record_trade_book_event(
                    trade_book_id=trade_book_id,
                    symbol=symbol,
                    event="exit",
                    timestamp=filled_at,
                    details={
                        "exit_reason": reason,
                        "broker_detected": True,
                        "shares": shares,
                        "exit_price": filled_price,
                        "order_id": order_id,
                        "order_type": fill.get(
                            "order_type"
                        ),
                        "stop_price": fill.get(
                            "stop_price"
                        ),
                        "limit_price": fill.get(
                            "limit_price"
                        ),
                    },
                )

                entry_events = (
                    load_trade_book_events(
                        trade_book_id=trade_book_id,
                        limit=50,
                    )
                )

                entry_details: dict[str, Any] = {}

                for event in entry_events:
                    if (
                        str(
                            event.get(
                                "event",
                                "",
                            )
                        ).strip().lower()
                        == "entry"
                    ):
                        details = event.get(
                            "details"
                        )

                        if isinstance(
                            details,
                            dict,
                        ):
                            entry_details = details

                        break

                save_learning_outcome(
                    trade_book_id=trade_book_id,
                    symbol=symbol,
                    entry_price=float(
                        closed_book_entry[
                            "entry_price"
                        ]
                    ),
                    exit_price=float(
                        closed_book_entry[
                            "exit_price"
                        ]
                    ),
                    shares=float(
                        closed_book_entry[
                            "shares"
                        ]
                    ),
                    realized_profit_loss=float(
                        closed_book_entry[
                            "realized_profit_loss"
                        ]
                    ),
                    realized_return_percent=float(
                        closed_book_entry[
                            "realized_return_percent"
                        ]
                    ),
                    created_at=filled_at,
                    entry_score=safe_float(
                        entry_details.get(
                            "score"
                        )
                    ),
                    entry_confidence=safe_float(
                        entry_details.get(
                            "confidence"
                        )
                    ),
                    entry_signal=str(
                        entry_details.get(
                            "signal",
                            "",
                        )
                    ).strip()
                    or None,
                    scanner_rank=safe_float(
                        entry_details.get(
                            "scanner_rank"
                        )
                    ),
                    spread_percent=safe_float(
                        entry_details.get(
                            "spread_percent"
                        )
                    ),
                    stop_loss_percent=safe_float(
                        entry_details.get(
                            "stop_loss_percent"
                        )
                    ),
                    take_profit_percent=safe_float(
                        entry_details.get(
                            "take_profit_percent"
                        )
                    ),
                    exit_reason=reason,
                    metadata={
                        "broker_detected": True,
                        "order_id": order_id,
                        "order_type": fill.get(
                            "order_type"
                        ),
                        "stop_price": fill.get(
                            "stop_price"
                        ),
                        "limit_price": fill.get(
                            "limit_price"
                        ),
                    },
                )

                learning_summary = (
                    calculate_learning_summary(
                        minimum_required=20
                    )
                )

                add_auto_trader_log(
                    "learning_outcome_recorded",
                    symbol=symbol,
                    message=(
                        "Broker exit was added "
                        "to learning memory."
                    ),
                    details={
                        "trade_book_id": (
                            trade_book_id
                        ),
                        "realized_profit_loss": (
                            closed_book_entry[
                                "realized_profit_loss"
                            ]
                        ),
                        "realized_return_percent": (
                            closed_book_entry[
                                "realized_return_percent"
                            ]
                        ),
                        "learning_summary": (
                            learning_summary
                        ),
                    },
                )

        except Exception as error:
            print(
                f"Could not record broker exit "
                f"learning outcome for {symbol}: "
                f"{clean_error_message(error)}"
            )

        mark_auto_trader_symbol_cooldown(
            symbol
        )

    return fills

def initialize_seen_exit_order_ids() -> None:
    try:
        orders = fetch_alpaca_paper_orders(
            limit=100
        )
    except Exception as error:
        print(
            "Could not initialize broker exit history: "
            f"{clean_error_message(error)}"
        )
        return

    for order in orders:
        if not isinstance(
            order,
            dict,
        ):
            continue

        side = str(
            order.get(
                "side",
                "",
            )
        ).strip().lower()

        status = str(
            order.get(
                "status",
                "",
            )
        ).strip().lower()

        order_id = str(
            order.get(
                "id",
                "",
            )
        ).strip()

        if (
            side == "sell"
            and status == "filled"
            and order_id
        ):
            _auto_trader_seen_exit_order_ids.add(
                order_id
            )

def run_auto_trader_cycle() -> dict[str, Any]:
    """
    Run one automatic PAPER-trading decision cycle.

    Entry:
      BUY signal, score >= 70, confidence >= 80.
      Position sizing targets 0.5% of equity, then all global risk checks run.
      Automatic entries attach a 2% stop and 4% take-profit bracket.

    Early exit:
      Existing position receives a strong SELL scanner result with
      score <= 30 and confidence >= 80. Protective bracket orders are
      canceled before the market exit.

    The function opens at most one new position per cycle.
    """
    global _auto_trader_cycle_running
    global _auto_trader_last_cycle_at
    global _auto_trader_last_cycle_result

    if _auto_trader_cycle_running:
        return {
            "success": False,
            "skipped": True,
            "reason": (
                "An automatic trading cycle is already running."
            ),
        }

    if not _auto_trader_enabled:
        return {
            "success": False,
            "skipped": True,
            "reason": (
                "Automatic paper trading is disabled."
            ),
        }

    if not auto_trader_automation_allowed():
        return {
            "success": False,
            "skipped": True,
            "reason": (
                "Railway has not enabled the automatic "
                "paper-trading hard switch."
            ),
        }

    _auto_trader_cycle_running = True
    _auto_trader_last_cycle_at = (
        time.time()
    )

    cycle_result: dict[str, Any] = {
        "success": True,
        "paper": True,
        "entries": [],
        "exits": [],
        "skipped_candidates": [],
    }

    try:
        clock = fetch_alpaca_market_clock()

        if not bool(
            clock.get(
                "is_open"
            )
        ):
            cycle_result.update({
                "success": True,
                "skipped": True,
                "reason": (
                    "Regular market is closed."
                ),
                "next_open": (
                    clock.get(
                        "next_open"
                    )
                ),
            })

            return cycle_result

        cycle_result[
            "broker_exit_fills"
        ] = (
            log_new_broker_exit_fills()
        )

        market_regime = get_market_regime(
            force_refresh=False
        )

        cycle_result[
            "market_regime"
        ] = market_regime

        scanner_results = scan_market(
            force_refresh=False
        )

        if not isinstance(
            scanner_results,
            list,
        ):
            scanner_results = []

        scanner_results = [
            item
            for item in scanner_results
            if isinstance(
                item,
                dict,
            )
        ]

        account = (
            fetch_alpaca_paper_account()
        )

        global _auto_trader_daily_pl_high_water
        global _auto_trader_daily_pl_date
        global _auto_trader_defensive_mode

        current_equity = safe_float(
            account.get("equity")
        ) or 0.0

        last_equity = safe_float(
            account.get("last_equity")
        ) or current_equity

        current_daily_pl = (
            current_equity
            - last_equity
        )

        current_date = str(
            clock.get("timestamp", "")
        )[:10]

        if (
            _auto_trader_daily_pl_date
            != current_date
        ):
            _auto_trader_daily_pl_date = (
                current_date
            )
            _auto_trader_daily_pl_high_water = (
                max(
                    0.0,
                    current_daily_pl,
                )
            )
            _auto_trader_defensive_mode = False

        if (
            current_daily_pl
            > _auto_trader_daily_pl_high_water
        ):
            _auto_trader_daily_pl_high_water = (
                current_daily_pl
            )

        high_water_armed = (
            _auto_trader_daily_pl_high_water
            >= AUTO_TRADER_DAILY_PROFIT_ARM_DOLLARS
        )

        giveback_dollars = (
            _auto_trader_daily_pl_high_water
            - current_daily_pl
        )

        giveback_percent = (
            (
                giveback_dollars
                / _auto_trader_daily_pl_high_water
            )
            * 100
            if _auto_trader_daily_pl_high_water > 0
            else 0.0
        )

        if (
            high_water_armed
            and (
                giveback_dollars
                >= AUTO_TRADER_DAILY_PROFIT_GIVEBACK_DOLLARS
                or giveback_percent
                >= AUTO_TRADER_DAILY_PROFIT_GIVEBACK_PERCENT
            )
        ):
            _auto_trader_defensive_mode = True

        positions = (
            fetch_alpaca_paper_positions()
        )

        scanner_by_symbol = (
            get_scanner_result_by_symbol(
                scanner_results
            )
        )
        sqlite_learning_summary = (
            calculate_learning_summary(
                minimum_required=20
            )
        )

        journal_learning_summary = (
            calculate_auto_trader_journal_learning_summary(
                minimum_required=20
            )
        )

        if (
            not sqlite_learning_summary.get(
                "enough_data"
            )
            and journal_learning_summary.get(
                "enough_data"
            )
        ):
            learning_summary = {
                "completed_trades": (
                    journal_learning_summary.get(
                        "completed_trades",
                        0,
                    )
                ),
                "minimum_required": (
                    journal_learning_summary.get(
                        "minimum_required",
                        20,
                    )
                ),
                "enough_data": True,
                "wins": (
                    journal_learning_summary.get(
                        "wins",
                        0,
                    )
                ),
                "losses": (
                    journal_learning_summary.get(
                        "losses",
                        0,
                    )
                ),
                "win_rate_percent": (
                    journal_learning_summary.get(
                        "win_rate_percent",
                        0.0,
                    )
                ),
                "average_return_percent": (
                    journal_learning_summary.get(
                        "average_return_percent",
                        0.0,
                    )
                ),
                "average_profit_loss": (
                    sqlite_learning_summary.get(
                        "average_profit_loss",
                        0.0,
                    )
                ),
                "source": "journal_fallback",
            }
        else:
            learning_summary = dict(
                sqlite_learning_summary
            )

            learning_summary[
                "source"
            ] = "sqlite"

        cycle_result["learning"] = (
            learning_summary
        )

        # -------------------------------------------------
        # Strong scanner SELL = early exit.
        # Broker-native stop / take-profit orders remain the
        # primary exit protection.
        # -------------------------------------------------
        for position in positions:
            symbol = clean_symbol(
                position.get(
                    "symbol"
                )
            )

            if not symbol:
                continue

            candidate = (
                scanner_by_symbol.get(
                    symbol
                )
            )

            signal = ""
            score = None
            confidence = None

            if candidate:
                signal = str(
                    candidate.get(
                        "signal",
                        "",
                    )
                ).strip().upper()

                score = safe_float(
                    candidate.get(
                        "score"
                    )
                )

                confidence = safe_float(
                    candidate.get(
                        "confidence"
                    )
                )

            scanner_exit = (
                signal == "SELL"
                and score is not None
                and score <=
                    AUTO_TRADER_EXIT_SCORE_MAX
                and confidence is not None
                and confidence >=
                    AUTO_TRADER_EXIT_CONFIDENCE_MIN
            )

            entry_price = safe_float(
                position.get(
                    "avg_entry_price"
                )
            )

            current_price = safe_float(
                position.get(
                    "current_price"
                )
            )

            hard_loss_exit = False
            return_percent = None

            if (
                entry_price is not None
                and entry_price > 0
                and current_price is not None
                and current_price > 0
            ):
                return_percent = (
                    calculate_position_return_percent(
                        entry_price=entry_price,
                        current_price=current_price,
                    )
                )

                hard_loss_exit = (
                    should_hard_max_loss_exit(
                        entry_price=entry_price,
                        current_price=current_price,
                    )
                )

            defensive_exit = (
                _auto_trader_defensive_mode
                and return_percent is not None
                and return_percent <= 0
            )

            if not (
                scanner_exit
                or hard_loss_exit
                or defensive_exit
            ):
                continue

            exit_reason = (
                "hard_max_loss_exit"
                if hard_loss_exit
                else (
                    "defensive_portfolio_exit"
                    if defensive_exit
                    else "scanner_exit"
                )
            )

            qty = safe_float(
                position.get(
                    "qty"
                )
            )

            if (
                qty is None
                or qty <= 0
            ):
                continue

            shares = math.floor(
                qty
            )

            if shares <= 0:
                continue
            existing_open_orders = (
                fetch_alpaca_open_orders_for_symbol(
                    symbol
                )
            )

            canceled_orders = []

            if existing_open_orders:
                canceled_orders = (
                    cancel_alpaca_open_orders_for_symbol(
                        symbol
                    )
                )

                if not canceled_orders:
                    exit_result = {
                        "success": False,
                        "paper": True,
                        "error": (
                            f"{symbol} had open protective orders, "
                            "but their cancellation could not be "
                            "confirmed before the automatic exit."
                        ),
                    }
                else:
                    exit_result = (
                        submit_alpaca_paper_market_order(
                            symbol=symbol,
                            shares=shares,
                            side="sell",
                        )
                    )
            else:
                exit_result = (
                    submit_alpaca_paper_market_order(
                        symbol=symbol,
                        shares=shares,
                        side="sell",
                    )
                )

            mark_auto_trader_symbol_cooldown(
                symbol
            )

            cycle_result["exits"].append({
                "reason": exit_reason,
                "return_percent": return_percent,
                "symbol": symbol,
                "scanner": {
                    "score": score,
                    "confidence": (
                        confidence
                    ),
                    "signal": signal,
                },
                "canceled_protective_orders": (
                    canceled_orders
                ),
                "result": exit_result,
            })

            add_auto_trader_log(
                exit_reason,
                symbol=symbol,
                message=(
                    (
                        "Hard max-loss rule triggered an "
                        "automatic PAPER exit."
                    )
                    if hard_loss_exit
                    else (
                        (
                            "Daily profit giveback protection "
                            "triggered an automatic PAPER exit."
                        )
                        if defensive_exit
                        else (
                            "Strong SELL signal triggered an "
                            "automatic PAPER exit."
                        )
                    )
                ),
                details={
                    "score": score,
                    "confidence": confidence,
                    "return_percent": return_percent,
                    "hard_loss_exit": hard_loss_exit,
                    "scanner_exit": scanner_exit,
                        "result_success": (
                            exit_result.get(
                                "success"
                            )
                        ),
                    },
                )

            if (
                exit_result.get("success")
                and isinstance(
                    exit_result.get("trade"),
                    dict,
                )
                and str(
                    exit_result.get(
                        "trade",
                        {},
                    ).get(
                        "status",
                        "",
                    )
                ).strip().lower() == "filled"
            ):
                add_auto_trader_journal_entry(
                    symbol=symbol,
                    event="exit",
                    details={
                        "reason": exit_reason,
                        "return_percent": return_percent,
                        "score": score,
                        "confidence": confidence,
                        "signal": signal,
                        "daily_pl_at_exit": (
                            current_daily_pl
                        ),
                        "daily_pl_high_water": (
                            _auto_trader_daily_pl_high_water
                        ),
                        "defensive_mode": (
                            _auto_trader_defensive_mode
                        ),
                        "shares": shares,
                        "entry_price": entry_price,
                        "profit_loss_dollars": (
                            (
                                (
                                    exit_result.get("trade", {}).get(
                                        "execution_price"
                                    )
                                    - entry_price
                                )
                                * shares
                            )
                            if (
                                isinstance(
                                    exit_result.get("trade"),
                                    dict,
                                )
                                and safe_float(
                                    exit_result.get("trade", {}).get(
                                        "execution_price"
                                    )
                                ) is not None
                                and entry_price is not None
                            )
                            else None
                        ),
                        "exit_price": (
                            exit_result.get("trade", {}).get(
                                "execution_price"
                            )
                            if isinstance(
                                exit_result.get("trade"),
                                dict,
                            )
                            else None
                        ),
                        "order_id": (
                            exit_result.get("trade", {}).get(
                                "id"
                            )
                            if isinstance(
                                exit_result.get("trade"),
                                dict,
                            )
                            else None
                        ),
                    },
                )
                exit_order_id = str(
                    exit_result.get(
                        "trade",
                        {},
                    ).get(
                        "id",
                        "",
                    )
                ).strip()
                try:
                    trade_result = (
                        exit_result.get("trade")
                        if isinstance(
                            exit_result.get("trade"),
                            dict,
                        )
                        else {}
                    )

                    exit_price = safe_float(
                        trade_result.get("execution_price")
                    )

                    exit_timestamp = str(
                        trade_result.get("filled_at")
                        or exit_result.get("order", {}).get(
                            "filled_at"
                        )
                        or ""
                    ).strip()

                    open_book_entry = (
                        load_open_trade_book_entry(
                            symbol
                        )
                    )

                    if (
                        open_book_entry
                        and exit_price is not None
                        and exit_price > 0
                        and exit_timestamp
                    ):
                        trade_book_id = int(
                            open_book_entry["id"]
                        )

                        closed_book_entry = (
                            close_trade_book_entry(
                                trade_book_id,
                                exit_price=exit_price,
                                exit_timestamp=exit_timestamp,
                                updated_at=exit_timestamp,
                                exit_order_id=(
                                    trade_result.get("id")
                                    or None
                                ),
                                exit_reason=exit_reason,
                            )
                        )

                        record_trade_book_event(
                            trade_book_id=trade_book_id,
                            symbol=symbol,
                            event="exit",
                            timestamp=exit_timestamp,
                            details={
                                "exit_reason": exit_reason,
                                "score": score,
                                "confidence": confidence,
                                "signal": signal,
                                "return_percent": (
                                    return_percent
                                ),
                                "daily_pl_at_exit": (
                                    current_daily_pl
                                ),
                                "daily_pl_high_water": (
                                    _auto_trader_daily_pl_high_water
                                ),
                                "defensive_mode": (
                                    _auto_trader_defensive_mode
                                ),
                            },
                        )

                        entry_events = (
                            load_trade_book_events(
                                trade_book_id=trade_book_id,
                                limit=50,
                            )
                        )

                        entry_details: dict[str, Any] = {}

                        for event in entry_events:
                            if (
                                str(
                                    event.get(
                                        "event",
                                        "",
                                    )
                                ).strip().lower()
                                == "entry"
                            ):
                                details = event.get(
                                    "details"
                                )

                                if isinstance(
                                    details,
                                    dict,
                                ):
                                    entry_details = details

                                break

                        save_learning_outcome(
                            trade_book_id=trade_book_id,
                            symbol=symbol,
                            entry_price=float(
                                closed_book_entry[
                                    "entry_price"
                                ]
                            ),
                            exit_price=float(
                                closed_book_entry[
                                    "exit_price"
                                ]
                            ),
                            shares=float(
                                closed_book_entry[
                                    "shares"
                                ]
                            ),
                            realized_profit_loss=float(
                                closed_book_entry[
                                    "realized_profit_loss"
                                ]
                            ),
                            realized_return_percent=float(
                                closed_book_entry[
                                    "realized_return_percent"
                                ]
                            ),
                            created_at=exit_timestamp,
                            entry_score=safe_float(
                                entry_details.get(
                                    "score"
                                )
                            ),
                            entry_confidence=safe_float(
                                entry_details.get(
                                    "confidence"
                                )
                            ),
                            entry_signal=str(
                                entry_details.get(
                                    "signal",
                                    "",
                                )
                            ).strip()
                            or None,
                            scanner_rank=safe_float(
                                entry_details.get(
                                    "scanner_rank"
                                )
                            ),
                            stop_loss_percent=safe_float(
                                entry_details.get(
                                    "stop_loss_percent"
                                )
                            ),
                            take_profit_percent=safe_float(
                                entry_details.get(
                                    "take_profit_percent"
                                )
                            ),
                            exit_reason=exit_reason,
                            metadata={
                                "daily_pl_at_exit": (
                                    current_daily_pl
                                ),
                                "daily_pl_high_water": (
                                    _auto_trader_daily_pl_high_water
                                ),
                                "defensive_mode": (
                                    _auto_trader_defensive_mode
                                ),
                            },
                        )

                        learning_summary = (
                            calculate_learning_summary()
                        )

                        add_auto_trader_log(
                            "learning_outcome_recorded",
                            symbol=symbol,
                            message=(
                                "Completed trade was added "
                                "to learning memory."
                            ),
                            details={
                                "trade_book_id": (
                                    trade_book_id
                                ),
                                "realized_profit_loss": (
                                    closed_book_entry[
                                        "realized_profit_loss"
                                    ]
                                ),
                                "realized_return_percent": (
                                    closed_book_entry[
                                        "realized_return_percent"
                                    ]
                                ),
                                "learning_summary": (
                                    learning_summary
                                ),
                            },
                        )

                except Exception as error:
                    print(
                        f"Could not close trade book "
                        f"for {symbol}: "
                        f"{clean_error_message(error)}"
                    )
                if exit_order_id:
                    _auto_trader_seen_exit_order_ids.add(
                        exit_order_id
                    )

        # Refresh positions after any exits.
        positions = (
            fetch_alpaca_paper_positions()
        )

        cycle_result[
            "protection_reconciliation"
        ] = (
            reconcile_unprotected_positions(
                positions
            )
        )

        # Refresh positions again after protection
        # orders are restored.
        positions = (
            fetch_alpaca_paper_positions()
        )
        cycle_result[
            "profit_lock_updates"
        ] = []

        for position in positions:
            symbol = clean_symbol(
                position.get(
                    "symbol"
                )
            )

            if not symbol:
                continue

            entry_price = safe_float(
                position.get(
                    "avg_entry_price"
                )
            )

            current_price = safe_float(
                position.get(
                    "current_price"
                )
            )

            if (
                entry_price is None
                or entry_price <= 0
                or current_price is None
                or current_price <= 0
            ):
                continue

            stop_order = (
                get_open_protective_stop_order(
                    symbol
                )
            )

            if stop_order is None:
                continue

            existing_stop_price = (
                safe_float(
                    stop_order.get(
                        "stop_price"
                    )
                )
            )

            new_stop_price = (
                calculate_profit_lock_stop(
                    entry_price=entry_price,
                    current_price=current_price,
                    existing_stop_price=(
                        existing_stop_price
                    ),
                )
            )

            if new_stop_price is None:
                continue

            update_result = (
                raise_protective_stop(
                    symbol=symbol,
                    new_stop_price=(
                        new_stop_price
                    ),
                )
            )

            cycle_result[
                "profit_lock_updates"
            ].append({
                "symbol": symbol,
                "entry_price": entry_price,
                "current_price": current_price,
                "old_stop_price": (
                    existing_stop_price
                ),
                "new_stop_price": (
                    new_stop_price
                ),
                "result": update_result,
            })

            if (
                update_result.get(
                    "success"
                )
                and update_result.get(
                    "changed"
                )
            ):
                add_auto_trader_log(
                    "profit_lock_updated",
                    symbol=symbol,
                    message=(
                        "Automatic PAPER profit lock "
                        "raised the protective stop."
                    ),
                    details={
                        "entry_price": entry_price,
                        "current_price": (
                            current_price
                        ),
                        "old_stop_price": (
                            existing_stop_price
                        ),
                        "new_stop_price": (
                            new_stop_price
                        ),
                    },
                )

        existing_symbols = {
            clean_symbol(
                position.get(
                    "symbol"
                )
            )
            for position in positions
            if clean_symbol(
                position.get(
                    "symbol"
                )
            )
        }

        # -------------------------------------------------
        # Entries.
        # -------------------------------------------------

        # Daily loss circuit breaker.
        #
        # This ONLY prevents new positions from being opened.
        # All exit management above this section still runs,
        # including protective stops, profit locks, scanner exits,
        # defensive exits, and the hard maximum-loss protection.
        daily_loss_limit_hit = (
            current_daily_pl
            <= -AUTO_TRADER_DAILY_LOSS_LIMIT_DOLLARS
        )

        cycle_result["daily_pl"] = round(
            current_daily_pl,
            2,
        )

        cycle_result["daily_loss_limit"] = (
            AUTO_TRADER_DAILY_LOSS_LIMIT_DOLLARS
        )

        cycle_result["daily_loss_limit_hit"] = (
            daily_loss_limit_hit
        )

        new_positions = 0

        for candidate in scanner_results:

            if daily_loss_limit_hit:
                cycle_result[
                    "entries_paused"
                ] = True

                cycle_result[
                    "entries_paused_reason"
                ] = (
                    "Daily paper-trading loss limit reached."
                )

                break

            if _auto_trader_defensive_mode:
                cycle_result[
                    "entries_paused"
                ] = True

                cycle_result[
                    "entries_paused_reason"
                ] = (
                    "Daily profit giveback protection is active."
                )

                break

            if (
                new_positions
                >= AUTO_TRADER_MAX_NEW_POSITIONS_PER_CYCLE
            ):
                break

            symbol = clean_symbol(
                candidate.get(
                    "symbol"
                )
            )

            if not symbol:
                continue

            signal = str(
                candidate.get(
                    "signal",
                    "",
                )
            ).strip().upper()

            score = safe_float(
                candidate.get(
                    "score"
                )
            )

            confidence = safe_float(
                candidate.get(
                    "confidence"
                )
            )

            # -------------------------------------------------
            # Learning-adjusted entry requirements.
            # -------------------------------------------------
            #
            # Start with the normal configured requirements.
            # If the learning history has enough completed
            # trades and performance is weak, become more
            # selective about NEW entries.
            #
            # This is intentionally bounded. The learner can
            # tighten entry quality, but it cannot freely
            # rewrite the strategy or make large uncontrolled
            # changes.
            learning_score_min = (
                AUTO_TRADER_ENTRY_SCORE_MIN
            )

            learning_confidence_min = (
                AUTO_TRADER_ENTRY_CONFIDENCE_MIN
            )

            if learning_summary.get(
                "enough_data"
            ):
                win_rate = safe_float(
                    learning_summary.get(
                        "win_rate_percent"
                    )
                ) or 0.0

                average_return = safe_float(
                    learning_summary.get(
                        "average_return_percent"
                    )
                ) or 0.0

                if (
                    win_rate < 45.0
                    or average_return < 0
                ):
                    learning_score_min = min(
                        90.0,
                        AUTO_TRADER_ENTRY_SCORE_MIN
                        + 5.0,
                    )

            learning_adjusted = (
                learning_score_min
                > AUTO_TRADER_ENTRY_SCORE_MIN
            )

            # -------------------------------------------------
            # Broad-market regime adjustment.
            # -------------------------------------------------
            #
            # Market context may only TIGHTEN the entry
            # requirement. It never lowers the normal or
            # learning-adjusted minimum.
            market_regime_name = str(
                market_regime.get(
                    "regime"
                )
                or "UNKNOWN"
            ).upper()

            market_regime_adjusted = False

            if market_regime_name == "BEARISH":
                previous_score_min = (
                    learning_score_min
                )

                learning_score_min = min(
                    90.0,
                    learning_score_min + 5.0,
                )

                market_regime_adjusted = (
                    learning_score_min
                    > previous_score_min
                )

            # -------------------------------------------------
            # Recent symbol-news adjustment.
            # -------------------------------------------------
            #
            # Only fetch news for a candidate that already
            # passes the technical, learning, and market
            # requirements. This avoids requesting news for
            # every symbol returned by the scanner.
            #
            # Negative news may only TIGHTEN the required
            # score. Positive news never lowers the minimum
            # or creates a BUY signal by itself.
            news_context = None
            news_score = None
            news_adjusted = False

            preliminary_entry_pass = (
                signal == "BUY"
                and score is not None
                and score >= learning_score_min
                and confidence is not None
                and confidence
                >= learning_confidence_min
            )

            if preliminary_entry_pass:
                news_context = (
                    get_symbol_news_context(
                        symbol,
                        force_refresh=False,
                    )
                )

                news_score = (
                    score_symbol_news_context(
                        news_context
                    )
                )

                if (
                    news_score.get("sentiment")
                    == "NEGATIVE"
                ):
                    previous_score_min = (
                        learning_score_min
                    )

                    learning_score_min = min(
                        90.0,
                        learning_score_min + 5.0,
                    )

                    news_adjusted = (
                        learning_score_min
                        > previous_score_min
                    )

            # Every candidate must pass the FINAL,
            # learning, regime, and news-adjusted
            # requirements.
            if not (
                signal == "BUY"
                and score is not None
                and score >= learning_score_min
                and confidence is not None
                and confidence
                >= learning_confidence_min
            ):
                failed_requirements = []

                if signal != "BUY":
                    failed_requirements.append(
                        "signal_not_buy"
                    )

                if (
                    score is None
                    or score < learning_score_min
                ):
                    failed_requirements.append(
                        "score_below_adjusted_minimum"
                    )

                if (
                    confidence is None
                    or confidence
                    < learning_confidence_min
                ):
                    failed_requirements.append(
                        "confidence_below_minimum"
                    )

                cycle_result[
                    "skipped_candidates"
                ].append({
                    "symbol": symbol,
                    "reason": (
                        "entry requirements not met"
                    ),
                    "failed_requirements": (
                        failed_requirements
                    ),
                    "signal": signal,
                    "score": score,
                    "confidence": confidence,
                    "required_score": (
                        learning_score_min
                    ),
                    "required_confidence": (
                        learning_confidence_min
                    ),
                    "learning_adjusted": (
                        learning_adjusted
                    ),
                    "market_regime": (
                        market_regime_name
                    ),
                    "market_regime_adjusted": (
                        market_regime_adjusted
                    ),
                    "news_sentiment": (
                        news_score.get(
                            "sentiment"
                        )
                        if news_score
                        else None
                    ),
                    "news_score": (
                        news_score.get(
                            "score"
                        )
                        if news_score
                        else None
                    ),
                    "news_adjusted": (
                        news_adjusted
                    ),
                })

                continue

            if symbol in existing_symbols:
                cycle_result[
                    "skipped_candidates"
                ].append({
                    "symbol": symbol,
                    "reason": (
                        "position already open"
                    ),
                })
                continue

            (
                cooldown_active,
                cooldown_seconds_remaining,
                cooldown_type,
            ) = auto_trader_symbol_on_cooldown(
                symbol
            )

            if cooldown_active:
                cycle_result[
                    "skipped_candidates"
                ].append({
                    "symbol": symbol,
                    "reason": (
                        "symbol cooldown active"
                    ),
                    "cooldown_type": (
                        cooldown_type
                    ),
                    "cooldown_seconds_remaining": round(
                        cooldown_seconds_remaining,
                        1,
                    ),
                    "cooldown_minutes_remaining": round(
                        cooldown_seconds_remaining
                        / 60.0,
                        1,
                    ),
                })

                continue

            try:
                shares, quote = (
                    calculate_auto_entry_shares(
                        symbol=symbol,
                        account=account,
                    )
                )

                reference_price = safe_float(
                    quote.get(
                        "ask"
                    )
                )

                spread_percent = safe_float(
                    quote.get(
                        "spread_percent"
                    )
                )

                atr_percent = safe_float(
                    candidate.get(
                        "atr_percent"
                    )
                )

                if (
                    atr_percent is not None
                    and atr_percent
                    > AUTO_TRADER_MAX_ENTRY_ATR_PERCENT
                ):
                    cycle_result[
                        "skipped_candidates"
                    ].append({
                        "symbol": symbol,
                        "reason": (
                            "automatic entry volatility too high"
                        ),
                        "atr_percent": round(
                            atr_percent,
                            4,
                        ),
                        "maximum_atr_percent": (
                            AUTO_TRADER_MAX_ENTRY_ATR_PERCENT
                        ),
                    })
                    continue

                if (
                    spread_percent is None
                    or spread_percent
                    > AUTO_TRADER_MAX_ENTRY_SPREAD_PERCENT
                ):
                    cycle_result[
                        "skipped_candidates"
                    ].append({
                        "symbol": symbol,
                        "reason": (
                            "automatic entry spread too wide"
                        ),
                        "spread_percent": (
                            round(
                                spread_percent,
                                4,
                            )
                            if spread_percent is not None
                            else None
                        ),
                        "maximum_spread_percent": (
                            AUTO_TRADER_MAX_ENTRY_SPREAD_PERCENT
                        ),
                    })
                    continue

                if (
                    shares <= 0
                    or reference_price is None
                    or reference_price <= 0
                ):
                    cycle_result[
                        "skipped_candidates"
                    ].append({
                        "symbol": symbol,
                        "reason": (
                            "automatic position sizing "
                            "returned zero shares"
                        ),
                    })
                    continue

                entry_result = (
                    submit_alpaca_auto_bracket_buy(
                        symbol=symbol,
                        shares=shares,
                        reference_price=(
                            reference_price
                        ),
                        scanner_result=candidate,
                        entry_context={
                            # Execution / market quality.
                            "reference_price": (
                                reference_price
                            ),
                            "spread_percent": (
                                spread_percent
                            ),

                            # Technical indicators.
                            "rsi": safe_float(
                                candidate.get("rsi")
                            ),
                            "macd": safe_float(
                                candidate.get("macd")
                            ),
                            "macd_signal": safe_float(
                                candidate.get(
                                    "macd_signal"
                                )
                            ),
                            "macd_histogram": safe_float(
                                candidate.get(
                                    "macd_histogram"
                                )
                            ),
                            "volume_ratio": safe_float(
                                candidate.get(
                                    "volume_ratio"
                                )
                            ),
                            "average_volume": safe_float(
                                candidate.get(
                                    "average_volume"
                                )
                            ),

                            # Momentum.
                            "one_day_change": safe_float(
                                candidate.get("change")
                            ),
                            "five_day_change": safe_float(
                                candidate.get(
                                    "five_day_change"
                                )
                            ),
                            "twenty_day_change": safe_float(
                                candidate.get(
                                    "twenty_day_change"
                                )
                            ),

                            # Volatility / trend.
                            "atr": safe_float(
                                candidate.get("atr")
                            ),
                            "atr_percent": (
                                atr_percent
                            ),
                            "trend": candidate.get(
                                "trend"
                            ),
                            "trend_strength": (
                                candidate.get(
                                    "trend_strength"
                                )
                            ),
                            "risk": (
                                candidate.get("risk")
                                or candidate.get(
                                    "risk_level"
                                )
                            ),

                            # Moving averages.
                            "ma20": safe_float(
                                candidate.get("ma20")
                            ),
                            "ma50": safe_float(
                                candidate.get("ma50")
                            ),

                            # Entry requirements.
                            "required_score": (
                                learning_score_min
                            ),
                            "required_confidence": (
                                learning_confidence_min
                            ),
                            "learning_adjusted": (
                                learning_adjusted
                            ),
                            "market_regime_adjusted": (
                                market_regime_adjusted
                            ),
                            "news_adjusted": (
                                news_adjusted
                            ),

                            # Market / catalyst context.
                            "market_regime": (
                                market_regime_name
                            ),
                            "market_regime_score": (
                                market_regime.get(
                                    "score"
                                )
                            ),
                            "news_sentiment": (
                                news_score.get(
                                    "sentiment"
                                )
                                if news_score
                                else None
                            ),
                            "news_score": (
                                news_score.get("score")
                                if news_score
                                else None
                            ),
                            "news_raw_score": (
                                news_score.get(
                                    "raw_score"
                                )
                                if news_score
                                else None
                            ),
                            "news_positive_hits": (
                                news_score.get(
                                    "positive_hits"
                                )
                                if news_score
                                else []
                            ),
                            "news_negative_hits": (
                                news_score.get(
                                    "negative_hits"
                                )
                                if news_score
                                else []
                            ),
                        },
                    )
                )

            except Exception as error:
                entry_result = {
                    "success": False,
                    "error": (
                        clean_error_message(
                            error
                        )
                    ),
                }

            mark_auto_trader_symbol_cooldown(
                symbol
            )

            cycle_result["entries"].append({
                "symbol": symbol,
                "shares": shares,
                "score": score,
                "confidence": (
                    confidence
                ),
                "required_score": (
                    learning_score_min
                ),
                "required_confidence": (
                    learning_confidence_min
                ),
                "learning_adjusted": (
                    learning_adjusted
                ),
                "market_regime": (
                    market_regime_name
                ),
                "market_regime_adjusted": (
                    market_regime_adjusted
                ),
                "news_sentiment": (
                    news_score.get(
                        "sentiment"
                    )
                    if news_score
                    else None
                ),
                "news_score": (
                    news_score.get(
                        "score"
                    )
                    if news_score
                    else None
                ),
                "news_adjusted": (
                    news_adjusted
                ),
                "result": entry_result,
            })

            add_auto_trader_log(
                "entry_attempt",
                symbol=symbol,
                message=(
                    "Automatic PAPER entry candidate "
                    "was processed."
                ),
                details={
                    "shares": shares,
                    "score": score,
                    "confidence": (
                        confidence
                    ),
                    "result_success": (
                        entry_result.get(
                            "success"
                        )
                    ),
                    "error": (
                        entry_result.get(
                            "error"
                        )
                    ),
                },
            )
            if entry_result.get("success"):
                add_auto_trader_journal_entry(
                    symbol=symbol,
                    event="entry",
                    details={
                        "shares": shares,

                        # Entry decision.
                        "score": score,
                        "confidence": confidence,
                        "signal": candidate.get(
                            "signal"
                        ),
                        "scanner_rank": (
                            candidate.get(
                                "scanner_rank"
                            )
                            or candidate.get(
                                "rank"
                            )
                        ),

                        # Execution / market quality.
                        "reference_price": reference_price,
                        "spread_percent": spread_percent,

                        # Technical indicators at entry.
                        "rsi": safe_float(
                            candidate.get(
                                "rsi"
                            )
                        ),
                        "macd": safe_float(
                            candidate.get(
                                "macd"
                            )
                        ),
                        "macd_signal": safe_float(
                            candidate.get(
                                "macd_signal"
                            )
                        ),
                        "macd_histogram": safe_float(
                            candidate.get(
                                "macd_histogram"
                            )
                        ),
                        "volume_ratio": safe_float(
                            candidate.get(
                                "volume_ratio"
                            )
                        ),
                        "average_volume": safe_float(
                            candidate.get(
                                "average_volume"
                            )
                        ),

                        # Momentum at entry.
                        "one_day_change": safe_float(
                            candidate.get(
                                "change"
                            )
                        ),
                        "five_day_change": safe_float(
                            candidate.get(
                                "five_day_change"
                            )
                        ),
                        "twenty_day_change": safe_float(
                            candidate.get(
                                "twenty_day_change"
                            )
                        ),

                        # Volatility / trend.
                        "atr": safe_float(
                            candidate.get(
                                "atr"
                            )
                        ),
                        "atr_percent": safe_float(
                            candidate.get(
                                "atr_percent"
                            )
                        ),
                        "trend": candidate.get(
                            "trend"
                        ),
                        "trend_strength": candidate.get(
                            "trend_strength"
                        ),
                        "risk": (
                            candidate.get(
                                "risk"
                            )
                            or candidate.get(
                                "risk_level"
                            )
                        ),

                        # Moving averages.
                        "ma20": safe_float(
                            candidate.get(
                                "ma20"
                            )
                        ),
                        "ma50": safe_float(
                            candidate.get(
                                "ma50"
                            )
                        ),

                        # Trade protection.
                        "stop_loss_percent": (
                            AUTO_TRADER_STOP_LOSS_PERCENT
                        ),
                        "take_profit_percent": (
                            AUTO_TRADER_TAKE_PROFIT_PERCENT
                        ),

                        # Portfolio state at entry.
                        "daily_pl_at_entry": (
                            current_daily_pl
                        ),
                        "daily_pl_high_water": (
                            _auto_trader_daily_pl_high_water
                        ),

                        # Entry requirement context.
                        "required_score": (
                            learning_score_min
                        ),
                        "required_confidence": (
                            learning_confidence_min
                        ),
                        "learning_adjusted": (
                            learning_adjusted
                        ),
                        "market_regime_adjusted": (
                            market_regime_adjusted
                        ),
                        "news_adjusted": (
                            news_adjusted
                        ),

                        # Market / catalyst context.
                        "market_regime": (
                            market_regime_name
                        ),
                        "market_regime_score": (
                            market_regime.get(
                                "score"
                            )
                        ),
                        "news_sentiment": (
                            news_score.get(
                                "sentiment"
                            )
                            if news_score
                            else None
                        ),
                        "news_score": (
                            news_score.get(
                                "score"
                            )
                            if news_score
                            else None
                        ),
                        "news_raw_score": (
                            news_score.get(
                                "raw_score"
                            )
                            if news_score
                            else None
                        ),
                        "news_positive_hits": (
                            news_score.get(
                                "positive_hits"
                            )
                            if news_score
                            else []
                        ),
                        "news_negative_hits": (
                            news_score.get(
                                "negative_hits"
                            )
                            if news_score
                            else []
                        ),

                        # Actual broker execution.
                        "entry_price": (
                            entry_result.get(
                                "trade",
                                {},
                            ).get(
                                "execution_price"
                            )
                            if isinstance(
                                entry_result.get(
                                    "trade"
                                ),
                                dict,
                            )
                            else None
                        ),
                        "order_id": (
                            entry_result.get(
                                "trade",
                                {},
                            ).get(
                                "id"
                            )
                            if isinstance(
                                entry_result.get(
                                    "trade"
                                ),
                                dict,
                            )
                            else None
                        ),
                    },
                )
            if entry_result.get(
                "success"
            ):
                new_positions += 1
                existing_symbols.add(
                    symbol
                )

        cycle_result[
            "scanner_result_count"
        ] = len(
            scanner_results
        )

        cycle_result[
            "new_positions_opened"
        ] = new_positions

        return cycle_result

    except Exception as error:
        cycle_result = {
            "success": False,
            "paper": True,
            "error": (
                clean_error_message(
                    error
                )
            ),
        }

        add_auto_trader_log(
            "cycle_error",
            message=(
                clean_error_message(
                    error
                )
            ),
        )

        return cycle_result

    finally:
        _auto_trader_cycle_running = False
        _auto_trader_last_cycle_result = (
            cycle_result
        )


async def auto_trader_loop() -> None:
    """
    Background PAPER automation loop.

    Runtime automation starts disabled after every Railway restart.
    """
    while True:
        try:
            if (
                _auto_trader_enabled
                and auto_trader_automation_allowed()
            ):
                await asyncio.to_thread(
                    run_auto_trader_cycle
                )

        except Exception as error:
            add_auto_trader_log(
                "background_error",
                message=(
                    clean_error_message(
                        error
                    )
                ),
            )

        await asyncio.sleep(
            AUTO_TRADER_SCAN_SECONDS
        )


def get_auto_trader_status() -> dict[str, Any]:
    return {
        "paper": True,
        "enabled": (
            _auto_trader_enabled
        ),
        "hard_switch_allowed": (
            auto_trader_automation_allowed()
        ),
        "control_token_configured": bool(
            get_auto_trader_control_token()
        ),
        "cycle_running": (
            _auto_trader_cycle_running
        ),
        "last_cycle_at": (
            _auto_trader_last_cycle_at
        ),
        "last_cycle_result": (
            _auto_trader_last_cycle_result
        ),
        "settings": {
            "scan_seconds": (
                AUTO_TRADER_SCAN_SECONDS
            ),
            "entry_score_min": (
                AUTO_TRADER_ENTRY_SCORE_MIN
            ),
            "entry_confidence_min": (
                AUTO_TRADER_ENTRY_CONFIDENCE_MIN
            ),
            "exit_score_max": (
                AUTO_TRADER_EXIT_SCORE_MAX
            ),
            "exit_confidence_min": (
                AUTO_TRADER_EXIT_CONFIDENCE_MIN
            ),
            "entry_equity_percent": (
                AUTO_TRADER_ENTRY_EQUITY_PERCENT
            ),
            "stop_loss_percent": (
                AUTO_TRADER_STOP_LOSS_PERCENT
            ),
            "take_profit_percent": (
                AUTO_TRADER_TAKE_PROFIT_PERCENT
            ),
            "symbol_cooldown_seconds": (
                AUTO_TRADER_SYMBOL_COOLDOWN_SECONDS
            ),
            "max_new_positions_per_cycle": (
                AUTO_TRADER_MAX_NEW_POSITIONS_PER_CYCLE
            ),
        },
    }


# =========================================================
# Basic API routes
# =========================================================

@app.get("/learning-summary")
def learning_summary(
    request: Request,
) -> dict[str, Any]:
    require_app_session(
        request
    )

    summary = calculate_learning_summary(
        minimum_required=20
    )

    enough_data = bool(
        summary.get("enough_data")
    )

    win_rate = safe_float(
        summary.get(
            "win_rate_percent"
        )
    ) or 0.0

    average_return = safe_float(
        summary.get(
            "average_return_percent"
        )
    ) or 0.0

    tightened = (
        enough_data
        and (
            win_rate < 45.0
            or average_return < 0
        )
    )

    return {
        "success": True,
        "paper": True,
        "learning": summary,
        "entry_adjustment": {
            "active": tightened,
            "base_score_min": (
                AUTO_TRADER_ENTRY_SCORE_MIN
            ),
            "base_confidence_min": (
                AUTO_TRADER_ENTRY_CONFIDENCE_MIN
            ),
            "current_score_min": (
                AUTO_TRADER_ENTRY_SCORE_MIN + 5
                if tightened
                else AUTO_TRADER_ENTRY_SCORE_MIN
            ),
            "current_confidence_min": (
                AUTO_TRADER_ENTRY_CONFIDENCE_MIN
            ),
        },
    }

@app.get(
    "/login",
    response_class=HTMLResponse,
)
def app_login_page(
    request: Request,
):
    if request_has_valid_app_session(
        request
    ):
        return HTMLResponse(
            """
            <script>
                window.location.replace("/");
            </script>
            """
        )

    return HTMLResponse(
        build_login_page()
    )

@app.post(
    "/login",
    response_class=HTMLResponse,
)
def app_login_submit(
    request: Request,
    password: str = Form(...),
):
    if (
        not APP_ACCESS_PASSWORD
        or not APP_SESSION_SECRET
    ):
        return HTMLResponse(
            build_login_page(
                error_message=(
                    "App access is not configured."
                )
            ),
            status_code=503,
        )

    if not hmac.compare_digest(
        password,
        APP_ACCESS_PASSWORD,
    ):
        return HTMLResponse(
            build_login_page(
                error_message=(
                    "Incorrect password."
                )
            ),
            status_code=401,
        )

    response = HTMLResponse(
        """
        <script>
            window.location.replace("/");
        </script>
        """
    )

    response.set_cookie(
        key=APP_SESSION_COOKIE,
        value=create_app_session_token(),
        max_age=APP_SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
    )

    return response

@app.get("/")
def home(
    request: Request,
):
    if not request_has_valid_app_session(
        request
    ):
        return HTMLResponse(
            """
            <script>
                window.location.replace("/login");
            </script>
            """
        )

    return FileResponse(
        os.path.join(
            FRONTEND_DIR,
            "index.html",
        )
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "version": APP_VERSION,
    }


# =========================================================
# Account and portfolio routes
# =========================================================

@app.get("/account/live")
def account_live(
    request: Request,
) -> dict[str, Any]:
    require_app_session(
        request
    )
    """Read-only live account snapshot for dashboard polling."""
    try:
        return build_alpaca_live_account_snapshot()

    except Exception as error:
        print(
            "Alpaca PAPER live-account endpoint error: "
            f"{clean_error_message(error)}"
        )

        return {
            "error": (
                "The live Alpaca paper account snapshot "
                "could not be loaded."
            )
        }


@app.get("/account")
def account(
    request: Request,
) -> dict[str, Any]:
    require_app_session(
        request
    )
    try:
        return build_alpaca_dashboard_account()

    except Exception as error:
        print(
            "Alpaca PAPER account endpoint error: "
            f"{clean_error_message(error)}"
        )

        return {
            "error": (
                "The Alpaca paper account could not be loaded."
            )
        }

@app.get("/account/pl-audit")
def account_pl_audit(
    request: Request,
) -> dict[str, Any]:
    require_app_session(
        request
    )

    account = fetch_alpaca_paper_account()
    raw_positions = (
        fetch_alpaca_paper_positions()
    )

    equity = safe_float(
        account.get("equity")
    )

    last_equity = safe_float(
        account.get("last_equity")
    )

    cash = safe_float(
        account.get("cash")
    )

    if equity is None:
        equity = cash or 0.0

    if (
        last_equity is None
        or last_equity <= 0
    ):
        last_equity = equity

    daily_pl = (
        equity
        - last_equity
    )

    daily_pl_percent = (
        (
            daily_pl
            / last_equity
        )
        * 100
        if last_equity > 0
        else 0.0
    )

    raw_unrealized_pl = sum(
        safe_float(
            position.get(
                "unrealized_pl"
            )
        ) or 0.0
        for position in raw_positions
        if isinstance(
            position,
            dict,
        )
    )

    normalized_positions = [
        normalize_alpaca_position(
            position
        )
        for position in raw_positions
        if isinstance(
            position,
            dict,
        )
    ]

    normalized_unrealized_pl = sum(
        safe_float(
            position.get(
                "unrealized_profit"
            )
        ) or 0.0
        for position in normalized_positions
    )

    unrealized_difference = (
        normalized_unrealized_pl
        - raw_unrealized_pl
    )

    return {
        "paper": True,
        "source": "alpaca_paper",
        "equity": round(
            equity,
            2,
        ),
        "last_equity": round(
            last_equity,
            2,
        ),
        "daily_pl": round(
            daily_pl,
            2,
        ),
        "daily_pl_percent": round(
            daily_pl_percent,
            4,
        ),
        "alpaca_unrealized_pl": round(
            raw_unrealized_pl,
            2,
        ),
        "dashboard_unrealized_pl": round(
            normalized_unrealized_pl,
            2,
        ),
        "unrealized_difference": round(
            unrealized_difference,
            4,
        ),
        "position_count": len(
            normalized_positions
        ),
        "unrealized_matches": (
            abs(
                unrealized_difference
            ) < 0.01
        ),
        "note": (
            "Daily P/L is equity minus prior-day equity. "
            "Unrealized P/L is open-position P/L since entry."
        ),
    }

@app.get("/portfolio-history")
def portfolio_history(
    request: Request,
) -> Any:
    require_app_session(
        request
    )

    try:
        return fetch_alpaca_portfolio_history()

    except Exception as error:
        print(
            "Alpaca PAPER portfolio-history error: "
            f"{clean_error_message(error)}"
        )

        return []


# =========================================================
# Market scanner routes
# =========================================================

@app.get("/market-scan")
def market_scan(
    request: Request,
    limit: int = Query(default=10, ge=1, le=30),
    signal: str | None = Query(default=None),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    require_app_session(
        request
    )
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
    request: Request,
    limit: int = Query(default=10, ge=1, le=30),
    signal: str | None = Query(default=None),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    require_app_session(
        request
    )
    """
    Backward-compatible alias for /market-scan.
    """
    return market_scan(
        request=request,
        limit=limit,
        signal=signal,
        refresh=refresh,
)

@app.get("/market-regime")
def market_regime(
    request: Request,
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    require_app_session(
        request
    )

    result = get_market_regime(
        force_refresh=refresh
    )

    return {
        "paper": True,
        **result,
    }

@app.get("/symbol-news/{symbol}")
def symbol_news(
    symbol: str,
    request: Request,
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    require_app_session(
        request
    )

    result = get_symbol_news_context(
        symbol,
        force_refresh=refresh,
    )

    news_score = score_symbol_news_context(
        result
    )

    return {
        "paper": True,
        **result,
        "news_score": news_score,
    }

# =========================================================
# Automatic PAPER trader routes
# =========================================================

@app.get("/auto-trader/status")
def auto_trader_status(
    request: Request,
) -> dict[str, Any]:
    require_app_session(
        request
    )

    return get_auto_trader_status()


@app.get("/auto-trader/logs")
def auto_trader_logs(
    request: Request,
    limit: int = Query(
        default=50,
        ge=1,
        le=AUTO_TRADER_LOG_LIMIT,
    ),
) -> dict[str, Any]:
    require_app_session(
        request
    )
    return {
        "paper": True,
        "count": min(
            limit,
            len(
                _auto_trader_log
            ),
        ),
        "logs": (
            _auto_trader_log[
                -limit:
            ][::-1]
        ),
    }

@app.get("/auto-trader/journal")
def auto_trader_journal(
    request: Request,
    limit: int = Query(
        default=100,
        ge=1,
        le=AUTO_TRADER_JOURNAL_LIMIT,
    ),
) -> dict[str, Any]:
    require_app_session(
        request
    )

    return {
        "paper": True,
        "count": min(
            limit,
            len(
                _auto_trader_journal
            ),
        ),
        "journal": (
            _auto_trader_journal[
                -limit:
            ][::-1]
        ),
    }

@app.get("/auto-trader/history")
def auto_trader_history(
    request: Request,
    limit: int = Query(
        default=200,
        ge=1,
        le=5000,
    ),
) -> dict[str, Any]:
    require_app_session(
        request
    )

    trades = load_trade_book(
        status="CLOSED",
        limit=limit,
    )

    history: list[dict[str, Any]] = []
    wins = 0
    losses = 0
    breakeven = 0
    total_profit_loss = 0.0
    return_values: list[float] = []

    for trade in trades:
        realized_profit_loss = trade.get(
            "realized_profit_loss"
        )
        realized_return_percent = trade.get(
            "realized_return_percent"
        )

        if isinstance(
            realized_profit_loss,
            (int, float),
        ):
            profit_loss = float(
                realized_profit_loss
            )
            total_profit_loss += profit_loss

            if profit_loss > 0:
                wins += 1
            elif profit_loss < 0:
                losses += 1
            else:
                breakeven += 1

        if isinstance(
            realized_return_percent,
            (int, float),
        ):
            return_values.append(
                float(
                    realized_return_percent
                )
            )

        history.append(
            {
                "id": trade.get("id"),
                "symbol": trade.get("symbol"),
                "status": trade.get("status"),
                "shares": trade.get("shares"),
                "entry_price": trade.get(
                    "entry_price"
                ),
                "exit_price": trade.get(
                    "exit_price"
                ),
                "realized_profit_loss": (
                    realized_profit_loss
                ),
                "realized_return_percent": (
                    realized_return_percent
                ),
                "entry_timestamp": trade.get(
                    "entry_timestamp"
                ),
                "exit_timestamp": trade.get(
                    "exit_timestamp"
                ),
                "entry_reason": trade.get(
                    "entry_reason"
                ),
                "exit_reason": trade.get(
                    "exit_reason"
                ),
                "strategy": trade.get(
                    "strategy"
                ),
                "entry_order_id": trade.get(
                    "entry_order_id"
                ),
                "exit_order_id": trade.get(
                    "exit_order_id"
                ),
            }
        )

    completed = len(history)

    win_rate_percent = (
        (wins / completed) * 100.0
        if completed > 0
        else 0.0
    )

    average_return_percent = (
        sum(return_values) / len(return_values)
        if return_values
        else 0.0
    )

    return {
        "paper": True,
        "read_only": True,
        "source": "sqlite_trade_book",
        "count": completed,
        "summary": {
            "completed_trades": completed,
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "win_rate_percent": round(
                win_rate_percent,
                2,
            ),
            "total_realized_profit_loss": round(
                total_profit_loss,
                2,
            ),
            "average_return_percent": round(
                average_return_percent,
                4,
            ),
        },
        "trades": history,
    }

def calculate_auto_trader_journal_learning_summary(
    *,
    minimum_required: int = 10,
) -> dict[str, Any]:
    entries = [
        item
        for item in _auto_trader_journal
        if (
            isinstance(item, dict)
            and item.get("event") == "entry"
        )
    ]

    exits = [
        item
        for item in _auto_trader_journal
        if (
            isinstance(item, dict)
            and item.get("event") == "exit"
        )
    ]

    completed_returns: list[float] = []

    open_entries: dict[
        str,
        dict[str, Any],
    ] = {}

    for item in _auto_trader_journal:
        if not isinstance(
            item,
            dict,
        ):
            continue

        symbol = clean_symbol(
            item.get("symbol")
        )

        if not symbol:
            continue

        event = str(
            item.get(
                "event",
                "",
            )
        )

        details = (
            item.get("details", {})
            if isinstance(
                item.get("details"),
                dict,
            )
            else {}
        )

        if event == "entry":
            open_entries[symbol] = item
            continue

        if event != "exit":
            continue

        entry_item = open_entries.pop(
            symbol,
            None,
        )

        return_percent = None

        if entry_item is not None:
            entry_details = (
                entry_item.get(
                    "details",
                    {},
                )
                if isinstance(
                    entry_item.get("details"),
                    dict,
                )
                else {}
            )

            entry_price = safe_float(
                entry_details.get(
                    "entry_price"
                )
            )

            exit_price = safe_float(
                details.get(
                    "exit_price"
                )
            )

            if (
                entry_price is not None
                and entry_price > 0
                and exit_price is not None
                and exit_price > 0
            ):
                return_percent = (
                    calculate_position_return_percent(
                        entry_price=entry_price,
                        current_price=exit_price,
                    )
                )

        if return_percent is None:
            return_percent = safe_float(
                details.get(
                    "return_percent"
                )
            )

        if return_percent is not None:
            completed_returns.append(
                return_percent
            )

    wins = sum(
        1
        for value in completed_returns
        if value > 0
    )

    losses = sum(
        1
        for value in completed_returns
        if value < 0
    )

    average_return = (
        sum(completed_returns)
        / len(completed_returns)
        if completed_returns
        else 0.0
    )

    win_rate = (
        (
            wins
            / len(completed_returns)
        )
        * 100
        if completed_returns
        else 0.0
    )

    minimum = max(
        1,
        int(minimum_required),
    )

    recommendations = []

    if len(completed_returns) < minimum:
        recommendations.append({
            "type": "collect_more_data",
            "confidence": "low",
            "message": (
                "Not enough completed trades "
                "for strategy changes yet."
            ),
            "completed_trades": len(
                completed_returns
            ),
            "minimum_required": minimum,
        })

    else:
        if win_rate < 45:
            recommendations.append({
                "type": "review_entry_quality",
                "confidence": "medium",
                "message": (
                    "Win rate is below 45%. "
                    "Review entry filters before "
                    "loosening trade criteria."
                ),
            })

        if average_return < 0:
            recommendations.append({
                "type": "negative_expectancy_warning",
                "confidence": "medium",
                "message": (
                    "Average completed-trade "
                    "return is negative. "
                    "Do not increase risk."
                ),
            })

        if (
            win_rate >= 55
            and average_return > 0
        ):
            recommendations.append({
                "type": "positive_performance",
                "confidence": "medium",
                "message": (
                    "Current paper-trading sample "
                    "is showing positive results. "
                    "Continue collecting data "
                    "before changing risk limits."
                ),
            })

    return {
        "journal_entries": len(
            _auto_trader_journal
        ),
        "entry_records": len(entries),
        "exit_records": len(exits),
        "completed_trades": len(
            completed_returns
        ),
        "minimum_required": minimum,
        "enough_data": (
            len(completed_returns)
            >= minimum
        ),
        "wins": wins,
        "losses": losses,
        "win_rate_percent": round(
            win_rate,
            2,
        ),
        "average_return_percent": round(
            average_return,
            4,
        ),
        "recommendations": recommendations,
    }


@app.get("/auto-trader/learning-summary")
def auto_trader_learning_summary(
    request: Request,
) -> dict[str, Any]:
    require_app_session(
        request
    )

    summary = (
        calculate_auto_trader_journal_learning_summary(
            minimum_required=10,
        )
    )

    return {
        "paper": True,
        "shadow_mode": True,
        "recommendations": summary.get(
            "recommendations",
            [],
        ),
        "journal_entries": summary.get(
            "journal_entries",
            0,
        ),
        "entry_records": summary.get(
            "entry_records",
            0,
        ),
        "exit_records": summary.get(
            "exit_records",
            0,
        ),
        "completed_returns": summary.get(
            "completed_trades",
            0,
        ),
        "wins": summary.get(
            "wins",
            0,
        ),
        "losses": summary.get(
            "losses",
            0,
        ),
        "win_rate_percent": summary.get(
            "win_rate_percent",
            0.0,
        ),
        "average_return_percent": summary.get(
            "average_return_percent",
            0.0,
        ),
        "message": (
            "Learning engine is collecting "
            "paper-trading outcomes. "
            "Recommendations remain read-only."
        ),
    }

@app.get("/auto-trader/trade-book")
def auto_trader_trade_book(
    request: Request,
    status: str | None = None,
    symbol: str | None = None,
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
) -> dict[str, Any]:
    require_app_session(
        request
    )

    records = load_trade_book(
        status=status,
        symbol=symbol,
        limit=limit,
    )

    return {
        "paper": True,
        "count": len(records),
        "records": records,
    }


@app.get("/auto-trader/protection-audit")
def auto_trader_protection_audit(
    request: Request,
) -> dict[str, Any]:
    require_app_session(
        request
    )

    raw_positions = (
        fetch_alpaca_paper_positions()
    )

    results = []
    protected_count = 0
    unprotected_count = 0

    for position in raw_positions:
        if not isinstance(
            position,
            dict,
        ):
            continue

        symbol = clean_symbol(
            position.get(
                "symbol"
            )
        )

        if not symbol:
            continue

        try:
            open_orders = (
                fetch_alpaca_open_orders_for_symbol(
                    symbol
                )
            )
        except Exception as error:
            results.append({
                "symbol": symbol,
                "protected": False,
                "error": clean_error_message(
                    error
                ),
            })
            unprotected_count += 1
            continue

        all_open_orders = []

        for order in open_orders:
            if not isinstance(
                order,
                dict,
            ):
                continue

            all_open_orders.append(
                order
            )

            legs = order.get(
                "legs"
            )

            if isinstance(
                legs,
                list,
            ):
                all_open_orders.extend(
                    leg
                    for leg in legs
                    if isinstance(
                        leg,
                        dict,
                    )
                )

        protective_sell_orders = [
            order
            for order in all_open_orders
            if str(
                order.get(
                    "side",
                    "",
                )
            ).strip().lower() == "sell"
        ]

        stop_orders = [
            order
            for order in protective_sell_orders
            if (
                str(
                    order.get(
                        "type",
                        "",
                    )
                ).strip().lower() == "stop"
                or safe_float(
                    order.get(
                        "stop_price"
                    )
                ) is not None
            )
        ]

        take_profit_orders = [
            order
            for order in protective_sell_orders
            if (
                str(
                    order.get(
                        "type",
                        "",
                    )
                ).strip().lower() == "limit"
                and safe_float(
                    order.get(
                        "limit_price"
                    )
                ) is not None
            )
        ]

        is_protected = bool(
            stop_orders
        )

        if is_protected:
            protected_count += 1
        else:
            unprotected_count += 1

        results.append({
            "symbol": symbol,
            "protected": is_protected,
            "stop_order_count": len(
                stop_orders
            ),
            "take_profit_order_count": len(
                take_profit_orders
            ),
            "open_sell_order_count": len(
                protective_sell_orders
            ),
        })

    return {
        "paper": True,
        "position_count": len(
            results
        ),
        "protected_count": protected_count,
        "unprotected_count": (
            unprotected_count
        ),
        "all_positions_protected": (
            unprotected_count == 0
        ),
        "positions": results,
    }

@app.get("/auto-trader/debug-open-orders/{symbol}")
def auto_trader_debug_open_orders(
    request: Request,
    symbol: str,
) -> dict[str, Any]:
    require_app_session(
        request
    )

    normalized_symbol = clean_symbol(
        symbol
    )

    orders = (
        fetch_alpaca_open_orders_for_symbol(
            normalized_symbol
        )
    )

    return {
        "paper": True,
        "symbol": normalized_symbol,
        "count": len(orders),
        "orders": orders,
    }

@app.post("/auto-trader/reconcile-protection")
def auto_trader_reconcile_protection(
    request: Request,
    x_auto_trader_token: str | None = Header(
        default=None
    ),
) -> dict[str, Any]:
    require_app_session(
        request
    )

    if not auto_trader_control_authorized(
        x_auto_trader_token
    ):
        return {
            "success": False,
            "error": (
                "Auto-trader control authorization failed."
            ),
        }

    positions = (
        fetch_alpaca_paper_positions()
    )

    results = (
        reconcile_unprotected_positions(
            positions
        )
    )

    return {
        "success": True,
        "paper": True,
        "position_count": len(
            positions
        ),
        "results": results,
    }

@app.post("/auto-trader/enable")
def auto_trader_enable(
    request: Request,
    x_auto_trader_token: str | None = Header(
        default=None
    ),
) -> dict[str, Any]:
    require_app_session(
        request
    )
    global _auto_trader_enabled

    if not auto_trader_control_authorized(
        x_auto_trader_token
    ):
        return {
            "success": False,
            "error": (
                "Auto-trader control authorization failed."
            ),
        }

    if not auto_trader_automation_allowed():
        return {
            "success": False,
            "error": (
                "Set AUTO_TRADER_ALLOW_AUTOMATION=true "
                "in Railway before enabling automation."
            ),
        }

    _auto_trader_enabled = True

    add_auto_trader_log(
        "enabled",
        message=(
            "Automatic PAPER trading was enabled."
        ),
    )

    return {
        "success": True,
        **get_auto_trader_status(),
    }


@app.post("/auto-trader/disable")
def auto_trader_disable(
    request: Request,
    x_auto_trader_token: str | None = Header(
        default=None
    ),
) -> dict[str, Any]:
    require_app_session(
        request
    )
    global _auto_trader_enabled

    if not auto_trader_control_authorized(
        x_auto_trader_token
    ):
        return {
            "success": False,
            "error": (
                "Auto-trader control authorization failed."
            ),
        }

    _auto_trader_enabled = False

    add_auto_trader_log(
        "disabled",
        message=(
            "Automatic PAPER trading was disabled."
        ),
    )

    return {
        "success": True,
        **get_auto_trader_status(),
    }


@app.post("/auto-trader/run-once")
def auto_trader_run_once(
    request: Request,
    x_auto_trader_token: str | None = Header(
        default=None
    ),
) -> dict[str, Any]:
    require_app_session(
        request
    )
    if not auto_trader_control_authorized(
        x_auto_trader_token
    ):
        return {
            "success": False,
            "error": (
                "Auto-trader control authorization failed."
            ),
        }

    return run_auto_trader_cycle()


# =========================================================
# Chart and quote routes
# =========================================================

@app.get("/chart/{symbol}")
def chart(
    request: Request,
    symbol: str,
) -> dict[str, Any]:
    require_app_session(
        request
    )

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
def get_quote(
    request: Request,
    symbol: str,
) -> dict[str, Any]:
    require_app_session(
        request
    )
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
    request: Request,
    query: str = Query(min_length=1),
) -> list[dict[str, str]]:
    require_app_session(
        request
    )
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
def get_strategy(
    request: Request,
    symbol: str,
) -> dict[str, Any]:
    require_app_session(
        request
    )

    return analyze_symbol(
        symbol
    )


# =========================================================
# Risk-management route
# =========================================================

@app.get("/risk-plan/{symbol}")
def get_risk_plan(
    request: Request,
    symbol: str,
) -> dict[str, Any]:
    require_app_session(
        request
    )
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
# Execution risk preview
# =========================================================

@app.get("/execution-risk/{symbol}")
def execution_risk(
    request: Request,
    symbol: str,
    shares: int = Query(
        default=1,
        ge=1,
        le=1_000_000,
    ),
    side: str = Query(
        default="buy",
    ),
) -> dict[str, Any]:
    """
    Preview the PAPER execution risk decision without placing an order.
    """
    require_app_session(
        request
    )

    normalized_symbol = clean_symbol(
        symbol
    )

    normalized_side = str(
        side or ""
    ).strip().lower()

    if normalized_side not in {
        "buy",
        "sell",
    }:
        return {
            "approved": False,
            "error": (
                "Side must be buy or sell."
            ),
        }

    try:
        return validate_alpaca_paper_order_risk(
            symbol=normalized_symbol,
            shares=shares,
            side=normalized_side,
        )

    except Exception as error:
        print(
            f"Execution-risk endpoint error for "
            f"{normalized_symbol}: "
            f"{clean_error_message(error)}"
        )

        return {
            "approved": False,
            "error": (
                "The execution risk check could not be completed."
            ),
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
def buy(
    data: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    require_app_session(
        request
    )
    symbol, shares = parse_trade_request(data)

    if symbol is None or shares is None:
        return {
            "success": False,
            "error": (
                "Enter a valid stock symbol and a "
                "whole number of shares greater than zero."
            ),
        }

    return submit_alpaca_paper_market_order(
        symbol=symbol,
        shares=shares,
        side="buy",
    )


@app.post("/sell")
def sell(
    data: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    require_app_session(
        request
    )
    symbol, shares = parse_trade_request(data)

    if symbol is None or shares is None:
        return {
            "success": False,
            "error": (
                "Enter a valid stock symbol and a "
                "whole number of shares greater than zero."
            ),
        }

    return submit_alpaca_paper_market_order(
        symbol=symbol,
        shares=shares,
        side="sell",
    )
