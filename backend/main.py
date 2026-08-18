import asyncio
import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import requests
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


APP_VERSION = "2.6.0"

AUTO_PORTFOLIO_REFRESH_SECONDS = 300

# Paper trading only. This intentionally does not read a live-trading
# base URL from an environment variable, which prevents an accidental
# switch to the live brokerage endpoint while we are testing.
ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_ORDER_POLL_SECONDS = 0.25
ALPACA_ORDER_POLL_TIMEOUT_SECONDS = 8.0

# =========================================================
# Paper execution risk controls
# =========================================================
#
# These limits apply only to Alpaca PAPER orders. They are deliberately
# conservative while the strategy and execution pipeline are being tested.
RISK_MAX_ORDER_EQUITY_PERCENT = 2.0
RISK_MAX_POSITION_EQUITY_PERCENT = 5.0
RISK_MAX_OPEN_POSITIONS = 10
RISK_MAX_SPREAD_PERCENT = 1.0
RISK_BUYING_POWER_BUFFER_DOLLARS = 25.0

# For now, market orders are submitted only during the regular market
# session. Alpaca requires limit orders for extended-hours eligibility.
RISK_REQUIRE_REGULAR_MARKET_OPEN = True

# The first version of the bot is long-only.
RISK_BLOCK_SHORT_SELLING = True



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
    refresh_task = asyncio.create_task(
        portfolio_refresh_loop()
    )

    try:
        yield
    finally:
        refresh_task.cancel()

        try:
            await refresh_task
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

    response = requests.request(
        method=method,
        url=f"{ALPACA_PAPER_BASE_URL}{path}",
        headers=get_alpaca_headers(),
        json=json_body,
        params=params,
        timeout=timeout,
    )

    try:
        payload = response.json()
    except ValueError:
        payload = None

    if not response.ok:
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

        raise RuntimeError(message)

    return payload


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

    raw_orders = fetch_alpaca_paper_orders(
        limit=100
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


@app.get("/portfolio-history")
def portfolio_history() -> Any:
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
# Execution risk preview
# =========================================================

@app.get("/execution-risk/{symbol}")
def execution_risk(
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
def buy(data: dict[str, Any]) -> dict[str, Any]:
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
def sell(data: dict[str, Any]) -> dict[str, Any]:
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
