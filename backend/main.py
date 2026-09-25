import asyncio
import base64
import json
import math
import os
import time
from zoneinfo import ZoneInfo
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
import hashlib
import hmac
import secrets

import requests
import yfinance as yf
from fastapi import FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from reporting_metrics import (account_equity_metrics, summarize_trades, daily_realized_summaries, annotate_order_history, timestamp_sort_key)
from entry_quality import evaluate_entry_quality, tighten_score_minimum
from chart_data import get_chart_data
from database import (
    calculate_feature_performance,
    calculate_learning_summary,
    close_trade_book_entry,
    create_trade_book_entry,
    create_trade_book_order_link,
    initialize_database,
    load_jarvis_profile,
    save_jarvis_profile,
    load_due_scanner_forward_observations,
    load_open_trade_book_entry,
    load_trade_book_by_order_link,
    load_all_trade_book_order_links,
    load_trade_book_entry_by_order_id,
    load_trade_book,
    load_trade_book_events,
    load_trade_excursions,
    load_learning_outcomes,
    load_pro_ticker_research,
    load_broker_fills,
    get_broker_fill,
    get_scheduler_state,
    load_trade_excursions,
    upsert_broker_fill,
    mark_scanner_observation_selected,
    record_trade_book_event,
    record_scanner_observation,
    save_learning_outcome,
    save_scanner_forward_outcome,
    set_scheduler_state,
    upsert_trade_excursion,
)
from indicators import (
    calculate_rsi,
    calculate_sma,
    calculate_volume_ratio,
    percentage_change,
    safe_float,
)
from paper_trader import PaperTrader
from trade_reports import build_trade_report_pdf
from pro_ticker_collector import (
    collect_pro_ticker_article,
    run_pro_ticker_fresh_scan,
    run_pro_ticker_historical_backfill,
)
from scanner import (
    get_market_regime,
    get_symbol_news_context,
    scan_market,
    score_symbol_news_context,
)


APP_VERSION = "2.7.2"
AUTO_PORTFOLIO_REFRESH_SECONDS = 300


def calculate_holding_seconds(
    entry_timestamp: Any,
    exit_timestamp: Any,
) -> float | None:
    if not isinstance(entry_timestamp, str):
        return None

    if not isinstance(exit_timestamp, str):
        return None

    try:
        entry_time = datetime.fromisoformat(
            entry_timestamp.replace(
                "Z",
                "+00:00",
            )
        )
        exit_time = datetime.fromisoformat(
            exit_timestamp.replace(
                "Z",
                "+00:00",
            )
        )

        holding_seconds = (
            exit_time - entry_time
        ).total_seconds()

        if holding_seconds < 0:
            return None

        return holding_seconds

    except (TypeError, ValueError):
        return None

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

# Auto-trader health watchdog.
AUTO_TRADER_HEALTH_STALE_SECONDS = 30 * 60
AUTO_TRADER_HEALTH_CHECK_SECONDS = 60

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

AUTO_TRADER_DAILY_PROFIT_CEILING_DOLLARS = 5000.0
AUTO_TRADER_DAILY_PROFIT_GIVEBACK_ENABLED = False
AUTO_TRADER_DAILY_PROFIT_ARM_DOLLARS = 100.0
AUTO_TRADER_DAILY_PROFIT_GIVEBACK_DOLLARS = 25.0
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
AUTO_TRADER_MAX_ENTRY_ATR_PERCENT = 8.0

# PAPER-trade analysis showed materially weaker
# results for scanner ranks above 20.
AUTO_TRADER_MAX_ENTRY_SCANNER_RANK = 20

# Version tag for new PAPER trades so strategy
# performance can be compared over time.
AUTO_TRADER_STRATEGY_VERSION = "rank20_v1"


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

_auto_trader_enabled_at: float | None = (
    time.time()
    if _auto_trader_enabled
    else None
)

APP_ACCESS_PASSWORD = os.getenv(
    "APP_ACCESS_PASSWORD",
    "",
)

APP_SESSION_SECRET = os.getenv(
    "APP_SESSION_SECRET",
    "",
)

# ============================================================
# JARVIS AI ASSISTANT
# ============================================================

OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY",
    "",
).strip()

JARVIS_MODEL = os.getenv(
    "JARVIS_MODEL",
    "gpt-5.6",
).strip()

APP_SESSION_COOKIE = "ai_paper_trader_session"
APP_SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7

_auto_trader_cycle_running = False
_auto_trader_last_cycle_at: float | None = None
_auto_trader_last_successful_cycle_at: float | None = None
_auto_trader_last_cycle_result: dict[str, Any] | None = None
_auto_trader_last_scan_at: float | None = None
_auto_trader_last_trade_at: float | None = None
_auto_trader_daily_health_email_date: str | None = None
_auto_trader_last_market_clock: dict[str, Any] | None = None
_auto_trader_last_market_clock_at: float | None = None
_auto_trader_health_alert_active = False
_auto_trader_health_alerted_at: float | None = None

AUTO_TRADER_ERROR_EMAIL_COOLDOWN_SECONDS = 30 * 60
AUTO_TRADER_MARKET_CLOCK_CACHE_SECONDS = 60 * 60
_auto_trader_error_email_last_sent: dict[str, float] = {}
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


_pro_ticker_last_hourly_key: str | None = None
_pro_ticker_last_daily_date: str | None = None
_pro_ticker_last_backfill_date: str | None = None

_trade_report_last_weekly_key: str | None = None
_trade_report_last_monthly_key: str | None = None
_trade_report_month_end_check_date: str | None = None
_trade_report_month_end_check_result = False

_broker_fill_backup_last_key: str | None = None
_broker_fill_backup_last_success_at: str | None = None
_broker_fill_backup_last_result: dict[str, Any] | None = None
_broker_fill_backup_last_error: str | None = None


def restore_persistent_scheduler_state() -> None:
    """
    Restore scheduler markers after a process restart.
    """

    global _trade_report_last_weekly_key
    global _trade_report_last_monthly_key
    global _broker_fill_backup_last_key
    global _broker_fill_backup_last_success_at
    global _broker_fill_backup_last_result
    global _broker_fill_backup_last_error

    weekly_key = get_scheduler_state(
        "trade_report_last_weekly_key"
    )

    if isinstance(weekly_key, str) and weekly_key.strip():
        _trade_report_last_weekly_key = weekly_key.strip()

    monthly_key = get_scheduler_state(
        "trade_report_last_monthly_key"
    )

    if isinstance(monthly_key, str) and monthly_key.strip():
        _trade_report_last_monthly_key = monthly_key.strip()

    broker_state = get_scheduler_state(
        "broker_fill_backup_state",
        {},
    )

    if isinstance(broker_state, dict):
        last_key = broker_state.get("last_key")

        if isinstance(last_key, str) and last_key.strip():
            _broker_fill_backup_last_key = last_key.strip()

        last_success_at = broker_state.get(
            "last_success_at"
        )

        if (
            isinstance(last_success_at, str)
            and last_success_at.strip()
        ):
            _broker_fill_backup_last_success_at = (
                last_success_at.strip()
            )

        last_result = broker_state.get(
            "last_result"
        )

        if isinstance(last_result, dict):
            _broker_fill_backup_last_result = last_result

        last_error = broker_state.get(
            "last_error"
        )

        if (
            isinstance(last_error, str)
            and last_error.strip()
        ):
            _broker_fill_backup_last_error = (
                last_error.strip()
            )
        else:
            _broker_fill_backup_last_error = None

    print(
        "Persistent scheduler state restored: "
        f"weekly={_trade_report_last_weekly_key}, "
        f"monthly={_trade_report_last_monthly_key}, "
        f"broker_sync={_broker_fill_backup_last_key}"
    )


def persist_broker_fill_backup_state() -> None:
    """
    Persist current broker-fill recovery status.
    """

    set_scheduler_state(
        "broker_fill_backup_state",
        {
            "last_key": _broker_fill_backup_last_key,
            "last_success_at": (
                _broker_fill_backup_last_success_at
            ),
            "last_result": (
                _broker_fill_backup_last_result
            ),
            "last_error": (
                _broker_fill_backup_last_error
            ),
        },
    )


def parse_trade_timestamp(
    value: Any,
) -> datetime | None:
    if not isinstance(value, str):
        return None

    cleaned = value.strip()

    if not cleaned:
        return None

    try:
        parsed = datetime.fromisoformat(
            cleaned.replace(
                "Z",
                "+00:00",
            )
        )
    except Exception:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed


def format_holding_duration(
    entry_timestamp: Any,
    exit_timestamp: Any,
) -> str:
    entry_time = parse_trade_timestamp(
        entry_timestamp
    )

    exit_time = parse_trade_timestamp(
        exit_timestamp
    )

    if (
        entry_time is None
        or exit_time is None
    ):
        return "Unknown"

    seconds = max(
        0,
        int(
            (
                exit_time
                - entry_time
            ).total_seconds()
        ),
    )

    hours, remainder = divmod(
        seconds,
        3600,
    )

    minutes, seconds = divmod(
        remainder,
        60,
    )

    if hours:
        return (
            f"{hours}h "
            f"{minutes}m"
        )

    if minutes:
        return (
            f"{minutes}m "
            f"{seconds}s"
        )

    return f"{seconds}s"


def load_today_closed_trades(
    eastern_date: str,
) -> list[dict[str, Any]]:
    eastern = ZoneInfo(
        "America/New_York"
    )

    rows = load_trade_book(
        status="CLOSED",
        limit=5000,
    )

    results: list[
        dict[str, Any]
    ] = []

    for row in rows:
        exit_time = (
            parse_trade_timestamp(
                row.get(
                    "exit_timestamp"
                )
            )
        )

        if exit_time is None:
            continue

        local_exit = exit_time.astimezone(
            eastern
        )

        if (
            local_exit.date().isoformat()
            != eastern_date
        ):
            continue

        item = dict(row)
        item[
            "_exit_eastern"
        ] = local_exit

        results.append(
            item
        )

    results.sort(
        key=lambda item: item[
            "_exit_eastern"
        ]
    )

    return results


def build_daily_market_recap_email(
    *,
    research_result: dict[str, Any],
    eastern_date: str,
) -> tuple[str, str]:
    trades = load_today_closed_trades(
        eastern_date
    )

    excursions = (
        load_trade_excursions(
            limit=1000
        )
    )

    excursion_map = {
        int(item["trade_book_id"]): item
        for item in excursions
        if item.get(
            "trade_book_id"
        ) is not None
    }

    completed = len(trades)

    wins = sum(
        1
        for trade in trades
        if float(
            trade.get(
                "realized_profit_loss"
            )
            or 0.0
        ) > 0
    )

    losses = sum(
        1
        for trade in trades
        if float(
            trade.get(
                "realized_profit_loss"
            )
            or 0.0
        ) < 0
    )

    flat = (
        completed
        - wins
        - losses
    )

    total_pl = sum(
        float(
            trade.get(
                "realized_profit_loss"
            )
            or 0.0
        )
        for trade in trades
    )

    win_rate = (
        (wins / completed) * 100.0
        if completed
        else 0.0
    )

    best_trade = (
        max(
            trades,
            key=lambda trade: float(
                trade.get(
                    "realized_profit_loss"
                )
                or 0.0
            ),
        )
        if trades
        else None
    )

    worst_trade = (
        min(
            trades,
            key=lambda trade: float(
                trade.get(
                    "realized_profit_loss"
                )
                or 0.0
            ),
        )
        if trades
        else None
    )

    lines = [
        "AI Paper Trader - Market Close Daily Recap",
        "",
        f"Trading date: {eastern_date}",
        "",
        "DAILY PERFORMANCE",
        "-----------------",
        f"Completed trades: {completed}",
        f"Wins: {wins}",
        f"Losses: {losses}",
        f"Flat: {flat}",
        f"Win rate: {win_rate:.2f}%",
        f"Realized P/L: ${total_pl:,.2f}",
    ]

    if best_trade is not None:
        lines.append(
            "Best trade: "
            f"{best_trade.get('symbol')} "
            f"${float(best_trade.get('realized_profit_loss') or 0):,.2f}"
        )

    if worst_trade is not None:
        lines.append(
            "Worst trade: "
            f"{worst_trade.get('symbol')} "
            f"${float(worst_trade.get('realized_profit_loss') or 0):,.2f}"
        )

    lines.extend(
        [
            "",
            "COMPLETED TRADES",
            "----------------",
        ]
    )

    if not trades:
        lines.append(
            "No completed paper trades today."
        )

    for index, trade in enumerate(
        trades,
        start=1,
    ):
        trade_id = trade.get("id")

        excursion = (
            excursion_map.get(
                int(trade_id)
            )
            if trade_id is not None
            else None
        )

        pnl = float(
            trade.get(
                "realized_profit_loss"
            )
            or 0.0
        )

        return_pct = float(
            trade.get(
                "realized_return_percent"
            )
            or 0.0
        )

        lines.extend(
            [
                "",
                (
                    f"{index}. "
                    f"{trade.get('symbol', 'UNKNOWN')}"
                ),
                (
                    "Shares: "
                    f"{trade.get('shares', 'Unknown')}"
                ),
                (
                    "Entry: $"
                    f"{float(trade.get('entry_price') or 0):,.4f}"
                ),
                (
                    "Exit: $"
                    f"{float(trade.get('exit_price') or 0):,.4f}"
                ),
                (
                    "Realized P/L: "
                    f"${pnl:,.2f}"
                ),
                (
                    "Return: "
                    f"{return_pct:.3f}%"
                ),
                (
                    "Holding time: "
                    + format_holding_duration(
                        trade.get(
                            "entry_timestamp"
                        ),
                        trade.get(
                            "exit_timestamp"
                        ),
                    )
                ),
                (
                    "Exit reason: "
                    f"{trade.get('exit_reason') or 'Unknown'}"
                ),
            ]
        )

        if excursion:
            lines.append(
                "MFE / MAE: "
                f"{float(excursion.get('mfe_percent') or 0):.3f}% / "
                f"{float(excursion.get('mae_percent') or 0):.3f}%"
            )

    lines.extend(
        [
            "",
            "PRO TICKER RESEARCH",
            "-------------------",
            (
                "Closing pages scanned: "
                f"{research_result.get('pages_scanned', 0)}"
            ),
            (
                "Recaps collected: "
                f"{research_result.get('collected_count', 0)}"
            ),
            (
                "Research errors: "
                f"{research_result.get('error_count', 0)}"
            ),
        ]
    )

    collected = research_result.get(
        "collected",
        [],
    )

    if isinstance(collected, list):
        for item in collected:
            if not isinstance(
                item,
                dict,
            ):
                continue

            features = item.get(
                "raw_features",
                {},
            )

            if not isinstance(
                features,
                dict,
            ):
                features = {}

            learned = [
                label
                for key, label in (
                    (
                        "breakout_present",
                        "Breakout",
                    ),
                    (
                        "bull_flag_present",
                        "Bull flag",
                    ),
                    (
                        "consolidation_present",
                        "Consolidation",
                    ),
                    (
                        "higher_lows_present",
                        "Higher lows",
                    ),
                    (
                        "relative_volume_present",
                        "Relative volume",
                    ),
                    (
                        "rsi_present",
                        "RSI",
                    ),
                    (
                        "vwap_present",
                        "VWAP",
                    ),
                    (
                        "vwma_present",
                        "VWMA",
                    ),
                    (
                        "exhaustion_present",
                        "Exhaustion",
                    ),
                    (
                        "parabolic_present",
                        "Parabolic",
                    ),
                )
                if features.get(key)
            ]

            lines.extend(
                [
                    "",
                    (
                        f"{item.get('symbol') or 'UNKNOWN'}"
                    ),
                    (
                        "Reported move: "
                        f"{item.get('reported_move_percent') or 'Unknown'}%"
                    ),
                    (
                        "Characteristics: "
                        + (
                            ", ".join(learned)
                            if learned
                            else "None extracted"
                        )
                    ),
                ]
            )

    lines.extend(
        [
            "",
            "LEARNING NOTE",
            "-------------",
            (
                "Pro Ticker research remains "
                "research-only and does not directly "
                "trigger paper trades."
            ),
        ]
    )

    subject = (
        "AI Paper Trader - "
        f"Daily Market Recap {eastern_date}"
    )

    return (
        subject,
        "\n".join(lines),
    )


def build_scheduled_trade_report(
    *,
    report_type: str,
    start_date: datetime,
    end_date: datetime,
) -> dict[str, Any]:
    normalized_type = str(
        report_type
    ).strip().lower()

    if normalized_type not in {
        "weekly",
        "monthly",
    }:
        raise ValueError(
            "report_type must be weekly or monthly."
        )

    trades = _trade_report_history()

    selected: list[
        dict[str, Any]
    ] = []

    start_day = start_date.date()
    end_day = end_date.date()

    for trade in trades:
        local_date = (
            _trade_report_local_date(
                trade.get(
                    "exit_timestamp"
                )
                or trade.get(
                    "entry_timestamp"
                )
            )
        )

        if not local_date:
            continue

        try:
            trade_day = (
                datetime.strptime(
                    local_date,
                    "%Y-%m-%d",
                ).date()
            )

        except ValueError:
            continue

        if (
            start_day
            <= trade_day
            <= end_day
        ):
            selected.append(
                trade
            )

    incomplete_count = sum(
        1
        for trade in selected
        if (
            trade.get(
                "pnl_complete"
            )
            is False
            or trade.get(
                "status"
            )
            == "CLOSED_INCOMPLETE"
        )
    )

    known_realized_pl = 0.0

    for trade in selected:
        try:
            known_realized_pl += float(
                trade.get(
                    "realized_profit_loss"
                )
                or 0.0
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

    if normalized_type == "weekly":
        title = (
            "AI Paper Trader - Weekly Report"
        )

        subtitle = (
            start_day.strftime(
                "%B %d, %Y"
            )
            + " - "
            + end_day.strftime(
                "%B %d, %Y"
            )
        )

        filename = (
            "ai-paper-trader-week-"
            f"{start_day.isoformat()}.pdf"
        )

        subject = (
            "AI Paper Trader - Weekly "
            f"Trade Report {start_day.isoformat()}"
        )

    else:
        title = (
            "AI Paper Trader - Monthly Report"
        )

        subtitle = (
            start_day.strftime(
                "%B %Y"
            )
        )

        filename = (
            "ai-paper-trader-"
            f"{start_day.strftime('%Y-%m')}.pdf"
        )

        subject = (
            "AI Paper Trader - Monthly "
            "Trade Report "
            f"{start_day.strftime('%Y-%m')}"
        )

    pdf = build_trade_report_pdf(
        title=title,
        subtitle=subtitle,
        trades=selected,
        compact=True,
    )

    pnl_label = (
        "Known realized P/L"
        if incomplete_count
        else "Net realized P/L"
    )

    message_lines = [
        title,
        subtitle,
        "",
        (
            "Completed trades: "
            f"{len(selected)}"
        ),
        (
            f"{pnl_label}: "
            f"${known_realized_pl:,.2f}"
        ),
        (
            "Incomplete trades: "
            f"{incomplete_count}"
        ),
        "",
    ]

    if incomplete_count:
        message_lines.extend([
            (
                "Some broker history is incomplete, "
                "so the P/L above includes only the "
                "broker-confirmed amounts currently "
                "known."
            ),
            "",
        ])

    message_lines.extend([
        (
            "The detailed PDF report is attached."
        ),
        "",
        (
            "Paper-trading report only. "
            "Not an official brokerage statement "
            "or tax document."
        ),
    ])

    return {
        "subject": subject,
        "message": "\n".join(
            message_lines
        ),
        "filename": filename,
        "pdf": pdf,
        "trade_count": len(
            selected
        ),
        "incomplete_count": (
            incomplete_count
        ),
        "known_realized_pl": round(
            known_realized_pl,
            2,
        ),
    }



async def pro_ticker_scheduler_loop() -> None:
    global _pro_ticker_last_hourly_key
    global _pro_ticker_last_daily_date
    global _pro_ticker_last_backfill_date
    global _trade_report_last_weekly_key
    global _trade_report_last_monthly_key
    global _trade_report_month_end_check_date
    global _trade_report_month_end_check_result
    global _broker_fill_backup_last_key
    global _broker_fill_backup_last_success_at
    global _broker_fill_backup_last_result
    global _broker_fill_backup_last_error

    eastern = ZoneInfo(
        "America/New_York"
    )

    while True:
        try:
            now = datetime.now(
                eastern
            )

            today = (
                now.date().isoformat()
            )

            calendar_entry = (
                await asyncio.to_thread(
                    fetch_alpaca_market_calendar_today
                )
            )

            market_open = (
                market_is_open_from_calendar(
                    calendar_entry
                )
            )

            # ----------------------------------
            # Broker-fill automatic backup sync
            #
            # Immediate BUY/SELL persistence remains
            # the primary path. This is the recovery
            # layer if an immediate write was missed.
            #
            # Market open:
            #   once per 15-minute bucket
            #
            # After close:
            #   one final recovery pass for the day
            # ----------------------------------

            backup_sync_key: str | None = None

            if market_open:
                backup_bucket = (
                    now.minute // 15
                )

                backup_sync_key = (
                    f"{today}:open:"
                    f"{now.hour:02d}:"
                    f"{backup_bucket}"
                )

            elif (
                calendar_entry
                and now.hour >= 16
            ):
                backup_sync_key = (
                    f"{today}:post-close"
                )

            if (
                backup_sync_key
                and _broker_fill_backup_last_key
                != backup_sync_key
            ):
                try:
                    backup_result = (
                        await asyncio.to_thread(
                            sync_alpaca_broker_fills,
                            limit=500,
                        )
                    )

                    _broker_fill_backup_last_result = (
                        backup_result
                    )

                    _broker_fill_backup_last_success_at = (
                        datetime.now(
                            eastern
                        ).isoformat()
                    )

                    _broker_fill_backup_last_error = None

                    # Mark the bucket only after the
                    # sync itself succeeds.
                    _broker_fill_backup_last_key = (
                        backup_sync_key
                    )

                    await asyncio.to_thread(
                        persist_broker_fill_backup_state
                    )

                    print(
                        "Broker-fill backup sync "
                        "completed: "
                        f"{backup_sync_key}"
                    )


                    try:
                        coverage_result = (
                            await asyncio.to_thread(
                                check_broker_fill_coverage_and_alert,
                                limit=50,
                            )
                        )

                        print(
                            "Broker-fill coverage check: "
                            f"{coverage_result.get('coverage_percent')}% "
                            f"coverage, "
                            f"{coverage_result.get('missing_after_recovery')} "
                            "missing after recovery."
                        )

                    except Exception as coverage_error:
                        print(
                            "Broker-fill coverage check "
                            "failed: "
                            f"{clean_error_message(coverage_error)}"
                        )

                except Exception as error:
                    _broker_fill_backup_last_error = (
                        clean_error_message(
                            error
                        )
                    )

                    try:
                        await asyncio.to_thread(
                            persist_broker_fill_backup_state
                        )
                    except Exception as state_error:
                        print(
                            "Failed to persist broker-fill "
                            "backup error state: "
                            f"{clean_error_message(state_error)}"
                        )

                    print(
                        "Broker-fill backup sync "
                        "failed: "
                        f"{_broker_fill_backup_last_error}"
                    )

            hourly_key = (
                f"{today}:{now.hour}"
            )

            if (
                market_open
                and now.minute >= 5
                and _pro_ticker_last_hourly_key
                != hourly_key
            ):
                result = (
                    await asyncio.to_thread(
                        run_pro_ticker_fresh_scan,
                        pages=10,
                    )
                )

                subject, message = (
                    build_pro_ticker_scan_email(
                        result,
                        label="Hourly",
                    )
                )

                await asyncio.to_thread(
                    send_health_alert_email,
                    subject,
                    message,
                )

                _pro_ticker_last_hourly_key = (
                    hourly_key
                )

            if (
                calendar_entry
                and now.hour == 16
                and now.minute >= 5
                and _pro_ticker_last_daily_date
                != today
            ):
                result = (
                    await asyncio.to_thread(
                        run_pro_ticker_fresh_scan,
                        pages=40,
                    )
                )

                subject, message = (
                    build_daily_market_recap_email(
                        research_result=result,
                        eastern_date=today,
                    )
                )

                await asyncio.to_thread(
                    send_health_alert_email,
                    subject,
                    message,
                )

                _pro_ticker_last_daily_date = (
                    today
                )

            # ----------------------------------
            # Weekly canonical trade report
            #
            # Friday at/after 4:10 PM Eastern.
            # This intentionally does not require a
            # calendar entry so holiday Fridays can
            # still produce the Mon-Fri report.
            # ----------------------------------

            week_start_date = (
                now.date()
                - timedelta(
                    days=now.weekday()
                )
            )

            weekly_key = (
                week_start_date.isoformat()
            )

            if (
                now.weekday() == 4
                and now.hour == 16
                and now.minute >= 10
                and _trade_report_last_weekly_key
                != weekly_key
            ):
                week_start = datetime.combine(
                    week_start_date,
                    datetime.min.time(),
                    tzinfo=eastern,
                )

                week_end = (
                    week_start
                    + timedelta(days=4)
                )

                weekly_report = (
                    await asyncio.to_thread(
                        build_scheduled_trade_report,
                        report_type="weekly",
                        start_date=week_start,
                        end_date=week_end,
                    )
                )

                weekly_sent = (
                    await asyncio.to_thread(
                        send_health_alert_email,
                        weekly_report["subject"],
                        weekly_report["message"],
                        [
                            {
                                "filename": (
                                    weekly_report[
                                        "filename"
                                    ]
                                ),
                                "content": (
                                    weekly_report[
                                        "pdf"
                                    ]
                                ),
                            }
                        ],
                    )
                )

                if weekly_sent:
                    _trade_report_last_weekly_key = (
                        weekly_key
                    )

                    await asyncio.to_thread(
                        set_scheduler_state,
                        "trade_report_last_weekly_key",
                        weekly_key,
                    )

                    print(
                        "Weekly trade report email "
                        f"sent for {weekly_key}."
                    )

                else:
                    print(
                        "Weekly trade report email "
                        "failed; scheduler will retry."
                    )

            # ----------------------------------
            # Monthly canonical trade report
            #
            # At/after 4:15 PM Eastern on the
            # actual final Alpaca trading day.
            #
            # The month-end calendar result is
            # cached for the date so Alpaca is not
            # queried every minute.
            # ----------------------------------

            month_key = now.strftime(
                "%Y-%m"
            )

            if (
                calendar_entry
                and now.hour == 16
                and now.minute >= 15
                and _trade_report_last_monthly_key
                != month_key
            ):
                if (
                    _trade_report_month_end_check_date
                    != today
                ):
                    (
                        _trade_report_month_end_check_result
                    ) = await asyncio.to_thread(
                        is_final_trading_day_of_month,
                        today,
                    )

                    (
                        _trade_report_month_end_check_date
                    ) = today

                if (
                    _trade_report_month_end_check_result
                ):
                    month_start_date = (
                        now.date().replace(
                            day=1
                        )
                    )

                    month_start = (
                        datetime.combine(
                            month_start_date,
                            datetime.min.time(),
                            tzinfo=eastern,
                        )
                    )

                    month_end = (
                        datetime.combine(
                            now.date(),
                            datetime.max.time(),
                            tzinfo=eastern,
                        )
                    )

                    monthly_report = (
                        await asyncio.to_thread(
                            build_scheduled_trade_report,
                            report_type="monthly",
                            start_date=month_start,
                            end_date=month_end,
                        )
                    )

                    monthly_sent = (
                        await asyncio.to_thread(
                            send_health_alert_email,
                            monthly_report[
                                "subject"
                            ],
                            monthly_report[
                                "message"
                            ],
                            [
                                {
                                    "filename": (
                                        monthly_report[
                                            "filename"
                                        ]
                                    ),
                                    "content": (
                                        monthly_report[
                                            "pdf"
                                        ]
                                    ),
                                }
                            ],
                        )
                    )

                    if monthly_sent:
                        (
                            _trade_report_last_monthly_key
                        ) = month_key

                        await asyncio.to_thread(
                            set_scheduler_state,
                            "trade_report_last_monthly_key",
                            month_key,
                        )

                        print(
                            "Monthly trade report "
                            "email sent for "
                            f"{month_key}."
                        )

                    else:
                        print(
                            "Monthly trade report "
                            "email failed; scheduler "
                            "will retry."
                        )

            if (
                calendar_entry
                and now.hour == 16
                and now.minute >= 20
                and _pro_ticker_last_backfill_date
                != today
            ):
                await asyncio.to_thread(
                    run_pro_ticker_historical_backfill,
                    pages_per_run=10,
                )

                _pro_ticker_last_backfill_date = (
                    today
                )

        except Exception as error:
            print(
                "Pro Ticker scheduler error: "
                f"{clean_error_message(error)}"
            )

        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_auto_trader_log()
    load_auto_trader_journal()
    initialize_seen_exit_order_ids()
    restore_persistent_scheduler_state()

    refresh_task = asyncio.create_task(
        portfolio_refresh_loop()
    )

    auto_trade_task = asyncio.create_task(
        auto_trader_loop()
    )

    health_watchdog_task = asyncio.create_task(
        auto_trader_health_watchdog()
    )

    pro_ticker_scheduler_task = asyncio.create_task(
        pro_ticker_scheduler_loop()
    )

    try:
        yield
    finally:
        refresh_task.cancel()
        auto_trade_task.cancel()
        health_watchdog_task.cancel()
        pro_ticker_scheduler_task.cancel()

        for task in (
            refresh_task,
            auto_trade_task,
            health_watchdog_task,
            pro_ticker_scheduler_task,
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

@app.post("/auto-trader/report/test-email")
def auto_trader_test_report_email(
    request: Request,
    report_type: str = Query(
        default="weekly",
        pattern=r"^(weekly|monthly)$",
    ),
) -> dict[str, Any]:
    """
    Manually test the scheduled trade-report
    PDF + email pipeline.

    Uses the same canonical history, PDF builder,
    and email attachment path as the scheduler.
    """

    require_app_session(request)

    eastern = ZoneInfo(
        "America/New_York"
    )

    now = datetime.now(
        eastern
    )

    if report_type == "weekly":
        start_date = (
            now
            - timedelta(
                days=now.weekday()
            )
        ).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

        end_date = (
            start_date
            + timedelta(days=4)
        ).replace(
            hour=23,
            minute=59,
            second=59,
        )

    else:
        start_date = now.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

        end_date = now

    report = build_scheduled_trade_report(
        report_type=report_type,
        start_date=start_date,
        end_date=end_date,
    )

    sent = send_health_alert_email(
        report["subject"],
        report["message"],
        [
            {
                "filename": report[
                    "filename"
                ],
                "content": report[
                    "pdf"
                ],
            }
        ],
    )

    return {
        "success": sent,
        "paper": True,
        "report_type": report_type,
        "period_start": (
            start_date.date().isoformat()
        ),
        "period_end": (
            end_date.date().isoformat()
        ),
        "trade_count": report[
            "trade_count"
        ],
        "incomplete_count": report[
            "incomplete_count"
        ],
        "known_realized_pl": report[
            "known_realized_pl"
        ],
        "filename": report[
            "filename"
        ],
        "email_sent": sent,
    }



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

        message = (
            f"{normalized_method} {path} failed with "
            f"HTTP {response.status_code}: {message}"
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


def alpaca_market_data_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> Any:
    """
    Call Alpaca's Market Data API.

    This helper is read-only and is used for market-data
    requests such as movers and most-active symbols.
    """
    normalized_method = str(method).strip().upper()

    if normalized_method != "GET":
        raise RuntimeError(
            "Alpaca market-data helper only supports GET requests."
        )

    attempts = ALPACA_READ_RETRY_ATTEMPTS
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            response = requests.request(
                method=normalized_method,
                url=f"https://data.alpaca.markets{path}",
                headers=get_alpaca_headers(),
                params=params,
                timeout=timeout,
            )
        except requests.RequestException as error:
            last_error = error

            if attempt < attempts - 1:
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

        message = (
            f"{normalized_method} {path} failed with "
            f"HTTP {response.status_code}: {message}"
        )

        if request_id:
            message = (
                f"{message} "
                f"(Alpaca request ID: {request_id})"
            )

        if (
            response.status_code >= 500
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
        "Alpaca market-data request failed without a response."
    )


def fetch_forward_research_bar(
    symbol: str,
    target_at: str,
    *,
    search_minutes: int = 5,
) -> dict[str, Any] | None:
    """
    Fetch the first completed 1-minute IEX bar whose close is at or
    after the research target, within the bounded search window.

    This helper is read-only and does not affect trading
    decisions.
    """
    normalized_symbol = clean_symbol(symbol)

    if not normalized_symbol:
        raise ValueError(
            "Forward research symbol is required."
        )

    try:
        target_datetime = datetime.fromisoformat(
            str(target_at).replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Forward research target timestamp is invalid."
        ) from error

    if target_datetime.tzinfo is None:
        target_datetime = target_datetime.replace(
            tzinfo=timezone.utc
        )

    target_datetime = target_datetime.astimezone(
        timezone.utc
    )

    normalized_search_minutes = max(
        1,
        int(search_minutes),
    )

    end_datetime = target_datetime + timedelta(
        minutes=normalized_search_minutes
    )

    payload = alpaca_market_data_request(
        "GET",
        f"/v2/stocks/{normalized_symbol}/bars",
        params={
            "timeframe": "1Min",
            "start": (target_datetime - timedelta(minutes=1)).isoformat(),
            "end": end_datetime.isoformat(),
            "limit": normalized_search_minutes + 2,
            "adjustment": "raw",
            "feed": "iex",
            "sort": "asc",
        },
    )

    if not isinstance(payload, dict):
        return None

    bars = payload.get("bars")

    if not isinstance(bars, list):
        return None

    for bar in bars:
        if not isinstance(bar, dict):
            continue

        bar_timestamp = bar.get("t")
        close_price = safe_float(bar.get("c"))

        if (
            not bar_timestamp
            or close_price is None
            or close_price <= 0
        ):
            continue

        try:
            bar_datetime = datetime.fromisoformat(
                str(bar_timestamp).replace(
                    "Z",
                    "+00:00",
                )
            )
        except (TypeError, ValueError):
            continue

        if bar_datetime.tzinfo is None:
            bar_datetime = bar_datetime.replace(
                tzinfo=timezone.utc
            )

        bar_datetime = bar_datetime.astimezone(
            timezone.utc
        )

        bar_closed_at = bar_datetime + timedelta(minutes=1)
        if not target_datetime <= bar_closed_at <= end_datetime:
            continue
        if bar_closed_at > datetime.now(timezone.utc):
            continue

        return {
            "symbol": normalized_symbol,
            "target_at": target_datetime.isoformat(),
            "bar_at": bar_datetime.isoformat(),
            "bar_closed_at": bar_closed_at.isoformat(),
            "price": float(close_price),
            "source": "alpaca_iex_1min",
        }

    return None


def evaluate_scanner_forward_outcomes(
    *,
    horizon_minutes: int = 15,
    limit: int = 25,
) -> dict[str, Any]:
    """
    Evaluate due scanner observations using historical
    Alpaca 1-minute bars.

    Research-only: this function does not alter trading
    decisions or strategy settings.
    """
    now = datetime.now(timezone.utc)

    # Wait for the five-minute search window plus one minute for data arrival.
    # Otherwise a not-yet-published bar becomes permanently "unavailable".
    # Keep current research fresh while still draining
    # older observations that accumulated before this
    # forward-research evaluator was deployed.
    recent_limit = min(15, limit)
    backlog_limit = max(0, limit - recent_limit)

    recent_observations = (
        load_due_scanner_forward_observations(
            horizon_minutes=horizon_minutes,
            due_at=(now - timedelta(minutes=6)).isoformat(),
            limit=recent_limit,
            order="newest",
        )
    )

    observations = list(recent_observations)
    observation_ids = {
        int(observation["id"])
        for observation in observations
    }

    if backlog_limit > 0:
        backlog_candidates = (
            load_due_scanner_forward_observations(
                horizon_minutes=horizon_minutes,
                due_at=(now - timedelta(minutes=6)).isoformat(),
                limit=backlog_limit + recent_limit,
                order="oldest",
            )
        )

        for observation in backlog_candidates:
            observation_id = int(observation["id"])

            if observation_id in observation_ids:
                continue

            observations.append(observation)
            observation_ids.add(observation_id)

            if (
                len(observations)
                >= recent_limit + backlog_limit
            ):
                break

    result: dict[str, Any] = {
        "success": True,
        "horizon_minutes": horizon_minutes,
        "due": len(observations),
        "recent_due": len(recent_observations),
        "backlog_due": (
            len(observations)
            - len(recent_observations)
        ),
        "saved": 0,
        "unavailable": 0,
        "errors": 0,
    }

    for observation in observations:
        try:
            observed_at = datetime.fromisoformat(
                str(
                    observation["observed_at"]
                ).replace("Z", "+00:00")
            )

            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(
                    tzinfo=timezone.utc
                )

            observed_at = observed_at.astimezone(
                timezone.utc
            )

            target_at = observed_at + timedelta(
                minutes=horizon_minutes
            )

            forward_bar = fetch_forward_research_bar(
                str(observation["symbol"]),
                target_at.isoformat(),
            )

            if forward_bar is None:
                save_scanner_forward_outcome(
                    scanner_observation_id=int(
                        observation["id"]
                    ),
                    horizon_minutes=horizon_minutes,
                    target_at=target_at.isoformat(),
                    evaluated_at=now.isoformat(),
                    reference_price=float(
                        observation["reference_price"]
                    ),
                    outcome_price=None,
                    status="unavailable",
                )
                result["unavailable"] += 1
                continue

            save_scanner_forward_outcome(
                scanner_observation_id=int(
                    observation["id"]
                ),
                horizon_minutes=horizon_minutes,
                target_at=target_at.isoformat(),
                evaluated_at=str(
                    forward_bar["bar_closed_at"]
                ),
                reference_price=float(
                    observation["reference_price"]
                ),
                outcome_price=float(
                    forward_bar["price"]
                ),
            )

            result["saved"] += 1

        except Exception as error:
            result["errors"] += 1
            print(
                "Scanner forward outcome error for "
                f"{observation.get('symbol')}: "
                f"{clean_error_message(error)}"
            )

    return result

def get_alpaca_paper_order(
    order_id: str,
) -> dict[str, Any]:
    """
    Retrieve a single paper order from Alpaca.
    """
    payload = alpaca_paper_request(
        "GET",
        f"/v2/orders/{order_id}",
        params={
            "nested": "true",
        },
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
    global _auto_trader_last_market_clock
    global _auto_trader_last_market_clock_at

    payload = alpaca_paper_request(
        "GET",
        "/v2/clock",
    )

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Alpaca returned an invalid market-clock response."
        )

    _auto_trader_last_market_clock = dict(payload)
    _auto_trader_last_market_clock_at = time.time()

    return payload


def fetch_alpaca_market_calendar_today() -> dict[str, Any] | None:
    """
    Return today's Alpaca US market calendar entry, if one exists.
    """
    eastern_now = datetime.now(
        ZoneInfo("America/New_York")
    )

    today = eastern_now.date().isoformat()

    payload = alpaca_paper_request(
        "GET",
        "/v2/calendar",
        params={
            "start": today,
            "end": today,
        },
    )

    if not isinstance(payload, list):
        raise RuntimeError(
            "Alpaca returned an invalid market-calendar response."
        )

    if not payload:
        return None

    entry = payload[0]

    if not isinstance(entry, dict):
        raise RuntimeError(
            "Alpaca returned an invalid market-calendar entry."
        )

    return entry


def fetch_alpaca_market_calendar_range(
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    """
    Return Alpaca US market calendar entries
    for an inclusive date range.
    """

    payload = alpaca_paper_request(
        "GET",
        "/v2/calendar",
        params={
            "start": start_date,
            "end": end_date,
        },
    )

    if not isinstance(payload, list):
        raise RuntimeError(
            "Alpaca returned an invalid "
            "market-calendar response."
        )

    return [
        entry
        for entry in payload
        if isinstance(entry, dict)
    ]


def is_final_trading_day_of_month(
    target_date: str,
) -> bool:
    """
    Return True when target_date is the final
    Alpaca trading day of its calendar month.
    """

    try:
        parsed_date = datetime.strptime(
            target_date,
            "%Y-%m-%d",
        ).date()

    except ValueError:
        return False

    next_month = (
        parsed_date.replace(
            day=28
        )
        + timedelta(days=4)
    ).replace(day=1)

    month_end = (
        next_month
        - timedelta(days=1)
    )

    calendar_entries = (
        fetch_alpaca_market_calendar_range(
            parsed_date.isoformat(),
            month_end.isoformat(),
        )
    )

    trading_dates: list[str] = []

    for entry in calendar_entries:
        entry_date = str(
            entry.get("date")
            or ""
        ).strip()

        if entry_date:
            trading_dates.append(
                entry_date
            )

    if not trading_dates:
        return False

    return target_date == max(
        trading_dates
    )


def market_is_open_from_calendar(
    calendar_entry: dict[str, Any] | None,
) -> bool:
    if not calendar_entry:
        return False

    open_value = str(
        calendar_entry.get("open", "")
    ).strip()

    close_value = str(
        calendar_entry.get("close", "")
    ).strip()

    if not open_value or not close_value:
        return False

    eastern_now = datetime.now(
        ZoneInfo("America/New_York")
    )

    try:
        open_hour, open_minute = (
            int(part)
            for part in open_value.split(":", 1)
        )

        close_hour, close_minute = (
            int(part)
            for part in close_value.split(":", 1)
        )

    except Exception:
        raise RuntimeError(
            "Alpaca returned invalid calendar open/close times."
        )

    market_open = eastern_now.replace(
        hour=open_hour,
        minute=open_minute,
        second=0,
        microsecond=0,
    )

    market_close = eastern_now.replace(
        hour=close_hour,
        minute=close_minute,
        second=0,
        microsecond=0,
    )

    return (
        market_open
        <= eastern_now
        < market_close
    )


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
            "nested": "true",
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
            "nested": "true",
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

def fetch_alpaca_paper_trade_history_paginated(
    *,
    max_orders: int = 5000,
    batch_size: int = 500,
) -> list[dict[str, Any]]:
    """
    Fetch Alpaca PAPER order history across multiple pages.

    Uses before_order_id so each request walks backward
    through order history without overlapping the prior page.
    """

    safe_max_orders = max(
        1,
        min(
            int(max_orders),
            10000,
        ),
    )

    safe_batch_size = max(
        1,
        min(
            int(batch_size),
            500,
        ),
    )

    results: list[
        dict[str, Any]
    ] = []

    seen_order_ids: set[str] = set()

    before_order_id: str | None = None

    while len(results) < safe_max_orders:
        request_limit = min(
            safe_batch_size,
            safe_max_orders - len(results),
        )

        params: dict[str, Any] = {
            "status": "all",
            "limit": request_limit,
            "direction": "desc",
            "nested": "true",
        }

        if before_order_id:
            params[
                "before_order_id"
            ] = before_order_id

        payload = alpaca_paper_request(
            "GET",
            "/v2/orders",
            params=params,
        )

        if not isinstance(
            payload,
            list,
        ):
            raise RuntimeError(
                "Alpaca returned an invalid "
                "paginated trade-history response."
            )

        batch = [
            order
            for order in payload
            if isinstance(
                order,
                dict,
            )
        ]

        if not batch:
            break

        added_this_page = 0

        for order in batch:
            order_id = str(
                order.get("id") or ""
            ).strip()

            if not order_id:
                continue

            if order_id in seen_order_ids:
                continue

            seen_order_ids.add(
                order_id
            )

            results.append(
                order
            )

            added_this_page += 1

            if (
                len(results)
                >= safe_max_orders
            ):
                break

        if len(batch) < request_limit:
            break

        last_order_id = str(
            batch[-1].get("id") or ""
        ).strip()

        if not last_order_id:
            break

        if (
            last_order_id
            == before_order_id
        ):
            break

        before_order_id = (
            last_order_id
        )

        if added_this_page == 0:
            break

    return results

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


def sync_alpaca_broker_fills(
    *,
    limit: int = 500,
) -> dict[str, Any]:
    """
    Persist filled Alpaca PAPER orders into the broker-fill ledger.

    This does not modify trade_book or trading behavior.
    """

    raw_orders = fetch_alpaca_paper_trade_history_paginated(
        max_orders=limit,
        batch_size=500,
    )

    flattened_orders: list[dict[str, Any]] = []
    seen_order_ids: set[str] = set()

    for parent_order in raw_orders:
        if not isinstance(
            parent_order,
            dict,
        ):
            continue

        orders_to_add = [
            parent_order,
        ]

        legs = parent_order.get(
            "legs"
        )

        if isinstance(
            legs,
            list,
        ):
            orders_to_add.extend(
                leg
                for leg in legs
                if isinstance(
                    leg,
                    dict,
                )
            )

        for candidate_order in orders_to_add:
            candidate_order_id = str(
                candidate_order.get("id")
                or ""
            ).strip()

            if (
                candidate_order_id
                and candidate_order_id
                in seen_order_ids
            ):
                continue

            if candidate_order_id:
                seen_order_ids.add(
                    candidate_order_id
                )

            flattened_orders.append(
                candidate_order
            )

    raw_orders = flattened_orders

    examined = 0
    filled = 0
    saved = 0
    errors: list[dict[str, Any]] = []

    for order in raw_orders:
        if not isinstance(
            order,
            dict,
        ):
            continue

        examined += 1

        status = str(
            order.get("status") or ""
        ).strip().lower()

        if status != "filled":
            continue

        order_id = str(
            order.get("id") or ""
        ).strip()

        symbol = clean_symbol(
            order.get("symbol")
        )

        side = str(
            order.get("side") or ""
        ).strip().upper()

        shares = safe_float(
            order.get("filled_qty")
        )

        price = safe_float(
            order.get(
                "filled_avg_price"
            )
        )

        filled_at = str(
            order.get("filled_at") or ""
        ).strip()

        if (
            not order_id
            or not symbol
            or side not in {
                "BUY",
                "SELL",
            }
            or shares is None
            or shares <= 0
            or price is None
            or price <= 0
            or not filled_at
        ):
            errors.append(
                {
                    "order_id": order_id,
                    "symbol": symbol,
                    "error": (
                        "Filled Alpaca order "
                        "was missing required "
                        "execution fields."
                    ),
                }
            )
            continue

        filled += 1

        try:
            upsert_broker_fill(
                order_id=order_id,
                symbol=symbol,
                side=side,
                shares=shares,
                price=price,
                filled_at=filled_at,
                raw_order=order,
                source="alpaca_paper",
            )

            saved += 1

        except Exception as error:
            errors.append(
                {
                    "order_id": order_id,
                    "symbol": symbol,
                    "error": (
                        clean_error_message(
                            error
                        )
                    ),
                }
            )

    ledger = load_broker_fills(
        limit=10000
    )

    return {
        "success": len(errors) == 0,
        "paper": True,
        "read_only_trade_book": True,
        "orders_examined": examined,
        "filled_orders_seen": filled,
        "fills_saved_or_refreshed": saved,
        "ledger_count": len(ledger),
        "error_count": len(errors),
        "errors": errors,
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
    """Read-only equity snapshot; timestamp precedes requests to order UI updates."""
    snapshot_at = time.time()
    account = fetch_alpaca_paper_account()
    raw_positions = fetch_alpaca_paper_positions()
    positions = [normalize_alpaca_position(p) for p in raw_positions if isinstance(p, dict)]
    positions = [p for p in positions if p.get("symbol")]
    cash = safe_float(account.get("cash"))
    unrealized = sum(safe_float(p.get("unrealized_profit")) or 0.0 for p in positions)
    invested = sum(abs(safe_float(p.get("position_value")) or 0.0) for p in positions)
    allocation = (cash or 0.0) + invested
    return {
        "source": "alpaca_paper", "paper": True, "timestamp": snapshot_at,
        "cash": round(cash, 2) if cash is not None else None,
        "buying_power": safe_float(account.get("buying_power")),
        **account_equity_metrics(account),
        "unrealized_profit_loss": round(unrealized, 2),
        "cash_percent": round(cash / allocation * 100, 4) if cash is not None and allocation > 0 else None,
        "invested_percent": round(invested / allocation * 100, 4) if allocation > 0 else None,
        "open_positions": len(positions), "position_count": len(positions),
        "positions": positions,
    }


def build_alpaca_dashboard_account() -> dict[str, Any]:
    """Account movement and bot journal totals have distinct, explicit scopes."""
    snapshot = build_alpaca_live_account_snapshot()
    orders = fetch_alpaca_paper_trade_history(limit=500)
    history = [normalize_alpaca_order_for_history(o) for o in orders if isinstance(o, dict)]
    history = [row for row in history if row is not None]
    summary = None
    accounting_error = None
    canonical = []
    try:
        payload = auto_trader_canonical_broker_trades()
        canonical = payload.get("trades") or []
        summary = summarize_trades(canonical, ledger_summary=payload.get("summary"))
    except Exception:
        accounting_error = "Journal accounting is unavailable; no zero-profit estimate was substituted."
    annotate_order_history(history, canonical)
    return {
        **snapshot,
        "realized_profit_loss": summary["total_realized_profit_loss"] if summary else None,
        "realized_profit_loss_complete": summary["realized_profit_loss_complete"] if summary else False,
        "win_rate": summary["win_rate_percent"] if summary else None,
        "money_win_rate_percent": summary["money_win_rate_percent"] if summary else None,
        "closed_trades": summary["completed_trades"] if summary else None,
        "performance_summary": summary, "accounting_error": accounting_error,
        "history": history, "trades": history,
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

def auto_trader_event_needs_error_email(
    event: str,
) -> bool:
    normalized_event = str(event).strip().lower()

    return (
        normalized_event in {
            "cycle_error",
            "background_error",
            "broker_exit_scan_error",
            "health_watchdog_error",
        }
        or normalized_event.endswith("_failed")
    )


def maybe_send_auto_trader_error_email(
    *,
    event: str,
    symbol: str | None,
    message: str,
) -> None:
    if not auto_trader_event_needs_error_email(
        event
    ):
        return

    if not health_alert_env_enabled(
        "HEALTH_ALERT_EMAIL_ENABLED"
    ):
        return

    normalized_event = str(event).strip().lower()

    normalized_symbol = (
        clean_symbol(symbol)
        if symbol
        else ""
    )

    throttle_key = (
        f"{normalized_event}:"
        f"{normalized_symbol}"
    )

    now = time.time()

    last_sent_at = (
        _auto_trader_error_email_last_sent.get(
            throttle_key
        )
    )

    if (
        last_sent_at is not None
        and now - last_sent_at
        < AUTO_TRADER_ERROR_EMAIL_COOLDOWN_SECONDS
    ):
        return

    _auto_trader_error_email_last_sent[
        throttle_key
    ] = now

    subject = (
        "AI Paper Trader - Error Alert"
    )

    details = [
        f"Event: {event}",
    ]

    if normalized_symbol:
        details.append(
            f"Symbol: {normalized_symbol}"
        )

    details.append(
        f"Message: {message}"
    )

    email_message = "\n".join(
        details
    )

    def send_email() -> None:
        success = send_health_alert_email(
            subject,
            email_message,
        )

        if not success:
            # Allow the next occurrence to retry instead
            # of suppressing it for the full cooldown.
            _auto_trader_error_email_last_sent.pop(
                throttle_key,
                None,
            )

    threading.Thread(
        target=send_email,
        daemon=True,
    ).start()


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

    maybe_send_auto_trader_error_email(
        event=str(event),
        symbol=symbol,
        message=str(message),
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
            try:
                current_positions = (
                    fetch_alpaca_paper_positions()
                )

                position_still_open = any(
                    clean_symbol(
                        item.get("symbol")
                    ) == normalized_symbol
                    and (
                        safe_float(
                            item.get("qty")
                        ) or 0
                    ) > 0
                    for item in current_positions
                    if isinstance(item, dict)
                )

            except Exception as error:
                return {
                    "success": False,
                    "paper": True,
                    "error": (
                        f"{normalized_symbol} had incomplete "
                        "sell protection and the existing "
                        "order could not be canceled. "
                        "The position state also could not "
                        "be confirmed: "
                        f"{clean_error_message(error)}"
                    ),
                }

            if not position_still_open:
                return {
                    "success": True,
                    "paper": True,
                    "symbol": normalized_symbol,
                    "position_closed": True,
                    "reconciled": True,
                    "message": (
                        "The position closed while recovery "
                        "protection was being reconciled."
                    ),
                }

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

    # Re-fetch the position immediately before replacing
    # protection. The positions snapshot that started this
    # reconciliation may now be stale after order cancellation
    # or an execution.
    try:
        refreshed_positions = (
            fetch_alpaca_paper_positions()
        )
    except Exception as error:
        return {
            "success": False,
            "paper": True,
            "automatic": True,
            "symbol": normalized_symbol,
            "error": (
                "Protection was ready to be replaced, "
                "but the current Alpaca position quantity "
                "could not be confirmed: "
                f"{clean_error_message(error)}"
            ),
        }

    refreshed_position = next(
        (
            position
            for position in refreshed_positions
            if (
                isinstance(position, dict)
                and clean_symbol(
                    position.get("symbol")
                ) == normalized_symbol
            )
        ),
        None,
    )

    if refreshed_position is None:
        return {
            "success": True,
            "paper": True,
            "automatic": True,
            "symbol": normalized_symbol,
            "position_closed": True,
            "reconciled": True,
            "message": (
                "The position closed before replacement "
                "protection was submitted."
            ),
        }

    refreshed_qty = safe_float(
        refreshed_position.get("qty")
    )

    if (
        refreshed_qty is None
        or refreshed_qty <= 0
    ):
        return {
            "success": True,
            "paper": True,
            "automatic": True,
            "symbol": normalized_symbol,
            "position_closed": True,
            "reconciled": True,
            "message": (
                "No positive position quantity remained "
                "before replacement protection was submitted."
            ),
        }

    refreshed_position_shares = math.floor(
        refreshed_qty
    )

    if refreshed_position_shares <= 0:
        return {
            "success": False,
            "paper": True,
            "automatic": True,
            "symbol": normalized_symbol,
            "error": (
                "The refreshed position quantity was below "
                "one whole share, so recovery protection "
                "was not submitted."
            ),
        }

    shares = refreshed_position_shares

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
            "position_closed"
        ):
            add_auto_trader_log(
                "protection_reconciled",
                symbol=symbol,
                message=(
                    "Position closed while automatic PAPER "
                    "protection was being reconciled."
                ),
                details={
                    "shares": shares,
                    "result_success": True,
                    "position_closed": True,
                    "reconciled": True,
                },
            )
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
    strategy_version: str = AUTO_TRADER_STRATEGY_VERSION,
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
                    and entry_order_id
                    and filled_shares is not None
                    and filled_shares > 0
                ):
                    try:
                        upsert_broker_fill(
                            order_id=entry_order_id,
                            symbol=normalized_symbol,
                            side="BUY",
                            shares=filled_shares,
                            price=entry_price,
                            filled_at=entry_timestamp,
                            raw_order=latest_order,
                            source=(
                                "alpaca_paper_immediate"
                            ),
                        )

                    except Exception as error:
                        add_auto_trader_log(
                            "broker_fill_persist_error",
                            symbol=normalized_symbol,
                            message=(
                                "Confirmed BUY fill could "
                                "not be persisted immediately "
                                "to broker_fills."
                            ),
                            details={
                                "order_id": (
                                    entry_order_id
                                ),
                                "error": (
                                    clean_error_message(
                                        error
                                    )
                                ),
                            },
                        )

                    existing_trade_book_entry = (
                        load_trade_book_entry_by_order_id(
                            entry_order_id
                        )
                    )

                    trade_book_created = False

                    if existing_trade_book_entry is not None:
                        existing_symbol = str(
                            existing_trade_book_entry.get(
                                "symbol",
                                "",
                            )
                        ).strip().upper()
                        existing_shares = safe_float(
                            existing_trade_book_entry.get(
                                "shares"
                            )
                        )

                        if (
                            existing_symbol != normalized_symbol
                            or existing_shares is None
                            or abs(
                                existing_shares
                                - filled_shares
                            ) > 0.000001
                        ):
                            raise RuntimeError(
                                "Existing trade-book entry "
                                "does not match the confirmed "
                                "Alpaca BUY fill."
                            )

                        trade_book_id = int(
                            existing_trade_book_entry["id"]
                        )
                    else:
                        trade_book_created = True
                        trade_book_id = (
                            create_trade_book_entry(
                                symbol=normalized_symbol,
                                shares=filled_shares,
                                entry_price=entry_price,
                                entry_timestamp=entry_timestamp,
                                created_at=entry_timestamp,
                                entry_order_id=entry_order_id,
                                entry_reason="auto_trader_entry",
                                strategy=strategy_version,
                            )
                        )

                    entry_event_details = {
                        "strategy_version": strategy_version,
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

                    entry_event_details[
                        "strategy_version"
                    ] = strategy_version

                    if trade_book_created:
                        try:
                            create_trade_book_order_link(
                                trade_book_id=trade_book_id,
                                order_id=entry_order_id,
                                client_order_id=latest_order.get(
                                    "client_order_id"
                                ),
                                order_role="ENTRY",
                                created_at=entry_timestamp,
                            )
                        except Exception as error:
                            add_auto_trader_log(
                                "trade_book_order_link_error",
                                symbol=normalized_symbol,
                                message=(
                                    "Confirmed BUY fill was saved, "
                                    "but its trade-book order link "
                                    "could not be saved."
                                ),
                                details={
                                    "trade_book_id": trade_book_id,
                                    "order_id": entry_order_id,
                                    "error": (
                                        clean_error_message(
                                            error
                                        )
                                    ),
                                },
                            )

                        bracket_legs = (
                            latest_order.get("legs")
                            if isinstance(
                                latest_order,
                                dict,
                            )
                            else None
                        )

                        if isinstance(
                            bracket_legs,
                            list,
                        ):
                            for leg in bracket_legs:
                                if not isinstance(
                                    leg,
                                    dict,
                                ):
                                    continue

                                leg_side = str(
                                    leg.get("side")
                                    or ""
                                ).strip().upper()

                                leg_order_id = str(
                                    leg.get("id")
                                    or ""
                                ).strip()

                                if (
                                    leg_side != "SELL"
                                    or not leg_order_id
                                ):
                                    continue

                                try:
                                    create_trade_book_order_link(
                                        trade_book_id=trade_book_id,
                                        order_id=leg_order_id,
                                        client_order_id=leg.get(
                                            "client_order_id"
                                        ),
                                        order_role="EXIT",
                                        created_at=(
                                            leg.get(
                                                "created_at"
                                            )
                                            or entry_timestamp
                                        ),
                                    )
                                except Exception as error:
                                    add_auto_trader_log(
                                        "trade_book_order_link_error",
                                        symbol=normalized_symbol,
                                        message=(
                                            "Bracket EXIT leg could "
                                            "not be linked to its "
                                            "trade-book entry."
                                        ),
                                        details={
                                            "trade_book_id": (
                                                trade_book_id
                                            ),
                                            "entry_order_id": (
                                                entry_order_id
                                            ),
                                            "exit_order_id": (
                                                leg_order_id
                                            ),
                                            "error": (
                                                clean_error_message(
                                                    error
                                                )
                                            ),
                                        },
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
                    result["strategy_version"] = (
                        strategy_version
                    )

            except Exception as error:
                persistence_error = clean_error_message(
                    error
                )
                print(
                    f"Could not record trade-book entry "
                    f"for {normalized_symbol}: "
                    f"{persistence_error}"
                )
                add_auto_trader_log(
                    "trade_book_persist_error",
                    symbol=normalized_symbol,
                    message=(
                        "Confirmed BUY fill could not "
                        "be persisted completely to "
                        "the trade book."
                    ),
                    details={
                        "order_id": entry_order_id,
                        "error": persistence_error,
                    },
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

    flattened_orders: list[dict[str, Any]] = []
    seen_order_ids: set[str] = set()

    for parent_order in orders:
        if not isinstance(
            parent_order,
            dict,
        ):
            continue

        orders_to_scan = [
            parent_order,
        ]

        legs = parent_order.get(
            "legs"
        )

        if isinstance(
            legs,
            list,
        ):
            orders_to_scan.extend(
                leg
                for leg in legs
                if isinstance(
                    leg,
                    dict,
                )
            )

        for candidate_order in orders_to_scan:
            candidate_order_id = str(
                candidate_order.get("id")
                or ""
            ).strip()

            if (
                candidate_order_id
                and candidate_order_id
                in seen_order_ids
            ):
                continue

            if candidate_order_id:
                seen_order_ids.add(
                    candidate_order_id
                )

            flattened_orders.append(
                candidate_order
            )

    for order in flattened_orders:
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

        filled_at = str(
            order.get("filled_at")
            or ""
        ).strip()

        if filled_at:
            try:
                upsert_broker_fill(
                    order_id=order_id,
                    symbol=symbol,
                    side="SELL",
                    shares=filled_qty,
                    price=filled_price,
                    filled_at=filled_at,
                    raw_order=order,
                    source=(
                        "alpaca_paper_immediate"
                    ),
                )

            except Exception as error:
                add_auto_trader_log(
                    "broker_fill_persist_error",
                    symbol=symbol,
                    message=(
                        "Confirmed SELL fill could "
                        "not be persisted immediately "
                        "to broker_fills."
                    ),
                    details={
                        "order_id": order_id,
                        "error": (
                            clean_error_message(
                                error
                            )
                        ),
                    },
                )

        result = {
            "order_id": order_id,
            "symbol": symbol,
            "shares": filled_qty,
            "filled_price": filled_price,
            "filled_at": (
                filled_at
                or order.get("filled_at")
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
            linked_book_entry = None

            if order_id:
                linked_book_entry = (
                    load_trade_book_by_order_link(
                        order_id=order_id
                    )
                )

            open_book_entry = (
                linked_book_entry
                if linked_book_entry is not None
                else load_open_trade_book_entry(
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

                try:
                    create_trade_book_order_link(
                        trade_book_id=trade_book_id,
                        order_id=(
                            order_id
                            or None
                        ),
                        client_order_id=None,
                        order_role="EXIT",
                        created_at=filled_at,
                    )
                except Exception as error:
                    add_auto_trader_log(
                        "trade_book_order_link_error",
                        symbol=symbol,
                        message=(
                            "Confirmed SELL fill closed the "
                            "trade, but its trade-book order "
                            "link could not be saved."
                        ),
                        details={
                            "trade_book_id": trade_book_id,
                            "order_id": order_id,
                            "error": clean_error_message(
                                error
                            ),
                        },
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
                    holding_seconds=(
                        calculate_holding_seconds(
                            closed_book_entry.get(
                                "entry_timestamp"
                            ),
                            filled_at,
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
    global _auto_trader_last_successful_cycle_at
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
            force_refresh=False,
            request_func=alpaca_paper_request,
            market_data_request_func=alpaca_market_data_request,
        )

        global _auto_trader_last_scan_at
        _auto_trader_last_scan_at = time.time()

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

        # -------------------------------------------------
        # Scanner research observations.
        # -------------------------------------------------
        # Capture entry-time scanner information without
        # changing any PAPER-trading decision. The database
        # writer deduplicates each symbol to one observation
        # per 15-minute window.
        observation_timestamp = datetime.now(
            timezone.utc
        ).isoformat()

        scanner_observation_ids: dict[str, int] = {}

        for candidate in scanner_results:
            if candidate.get("scanner_stale") is not False:
                continue
            observation_symbol = clean_symbol(
                candidate.get("symbol")
            )

            observation_price = safe_float(
                candidate.get("research_reference_price", candidate.get("price"))
            )

            if (
                not observation_symbol
                or observation_price is None
                or observation_price <= 0
            ):
                continue

            try:
                observation_id = record_scanner_observation(
                    symbol=observation_symbol,
                    observed_at=observation_timestamp,
                    reference_price=observation_price,
                    signal=candidate.get("signal"),
                    score=candidate.get("score"),
                    confidence=candidate.get("confidence"),
                    scanner_rank=(
                        candidate.get("scanner_rank")
                        or candidate.get("rank")
                    ),
                    rsi=candidate.get("rsi"),
                    macd=candidate.get("macd"),
                    macd_signal=candidate.get(
                        "macd_signal"
                    ),
                    macd_histogram=candidate.get(
                        "macd_histogram"
                    ),
                    volume_ratio=candidate.get(
                        "volume_ratio"
                    ),
                    average_volume=candidate.get(
                        "average_volume"
                    ),
                    one_day_change=candidate.get(
                        "change"
                    ),
                    five_day_change=candidate.get(
                        "five_day_change"
                    ),
                    twenty_day_change=candidate.get(
                        "twenty_day_change"
                    ),
                    atr=candidate.get("atr"),
                    atr_percent=candidate.get(
                        "atr_percent"
                    ),
                    trend=candidate.get("trend"),
                    trend_strength=candidate.get(
                        "trend_strength"
                    ),
                    risk=(
                        candidate.get("risk")
                        or candidate.get("risk_level")
                    ),
                    ma20=candidate.get("ma20"),
                    ma50=candidate.get("ma50"),
                    ma200=candidate.get("ma200"),
                    momentum_candidate=bool(
                        candidate.get(
                            "momentum_30_candidate"
                        )
                    ),
                    momentum_move_percent=(
                        candidate.get(
                            "momentum_move_percent"
                        )
                    ),
                    market_regime=market_regime.get(
                        "regime"
                    ),
                    market_regime_score=(
                        market_regime.get("score")
                    ),
                    selected_for_entry=False,
                    strategy_version=(
                        "momentum_30_v1"
                        if bool(
                            candidate.get(
                                "momentum_30_candidate"
                            )
                        )
                        else AUTO_TRADER_STRATEGY_VERSION
                    ),
                    features={
                        "momentum_30_checks": (
                            candidate.get(
                                "momentum_30_checks",
                                {},
                            )
                        ),
                        "momentum_30_failed_checks": (
                            candidate.get(
                                "momentum_30_failed_checks",
                                [],
                            )
                        ),
                        "research_source": "auto_trader_scanner",
                        "reference_price_basis": "scanner_daily_bar_close",
                        "scanner_generated_at": candidate.get("scanner_generated_at"),
                    },
                )

                scanner_observation_ids[
                    observation_symbol
                ] = observation_id

            except Exception as error:
                print(
                    "Scanner observation error for "
                    f"{observation_symbol}: "
                    f"{clean_error_message(error)}"
                )

        # -------------------------------------------------
        # Scanner forward-outcome research.
        # -------------------------------------------------
        # Evaluate matured scanner observations without
        # changing any PAPER-trading decision.
        try:
            cycle_result[
                "scanner_forward_outcomes"
            ] = evaluate_scanner_forward_outcomes(
                horizon_minutes=15,
                limit=25,
            )
        except Exception as error:
            cycle_result[
                "scanner_forward_outcomes"
            ] = {
                "success": False,
                "error": clean_error_message(error),
            }
            print(
                "Scanner forward-outcome evaluator error: "
                f"{clean_error_message(error)}"
            )

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
            AUTO_TRADER_DAILY_PROFIT_GIVEBACK_ENABLED
            and high_water_armed
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
                try:
                    open_book_entry = (
                        load_open_trade_book_entry(
                            symbol
                        )
                    )

                    if open_book_entry:
                        upsert_trade_excursion(
                            trade_book_id=int(
                                open_book_entry["id"]
                            ),
                            symbol=symbol,
                            entry_price=entry_price,
                            current_price=current_price,
                            observed_at=(
                                datetime.now(
                                    timezone.utc
                                ).isoformat()
                            ),
                        )

                except Exception as error:
                    print(
                        "Could not update trade excursion "
                        f"for {symbol}: "
                        f"{clean_error_message(error)}"
                    )

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

                        try:
                            create_trade_book_order_link(
                                trade_book_id=trade_book_id,
                                order_id=(
                                    trade_result.get("id")
                                    or None
                                ),
                                client_order_id=(
                                    trade_result.get(
                                        "client_order_id"
                                    )
                                    or None
                                ),
                                order_role="EXIT",
                                created_at=exit_timestamp,
                            )
                        except Exception as error:
                            add_auto_trader_log(
                                "trade_book_order_link_error",
                                symbol=symbol,
                                message=(
                                    "Auto-trader SELL closed the "
                                    "trade, but its trade-book "
                                    "order link could not be saved."
                                ),
                                details={
                                    "trade_book_id": trade_book_id,
                                    "order_id": (
                                        trade_result.get("id")
                                    ),
                                    "error": clean_error_message(
                                        error
                                    ),
                                },
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
                            holding_seconds=(
                                calculate_holding_seconds(
                                    closed_book_entry.get(
                                        "entry_timestamp"
                                    ),
                                    exit_timestamp,
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

        daily_profit_ceiling_hit = (
            current_daily_pl
            >= AUTO_TRADER_DAILY_PROFIT_CEILING_DOLLARS
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

        cycle_result["daily_profit_ceiling"] = (
            AUTO_TRADER_DAILY_PROFIT_CEILING_DOLLARS
        )

        cycle_result["daily_profit_ceiling_hit"] = (
            daily_profit_ceiling_hit
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

            if daily_profit_ceiling_hit:
                cycle_result[
                    "entries_paused"
                ] = True

                cycle_result[
                    "entries_paused_reason"
                ] = (
                    "Daily paper-trading profit ceiling reached."
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
                    win_rate < 35.0
                    or average_return < -1.0
                ):
                    learning_score_min = tighten_score_minimum(
                        AUTO_TRADER_ENTRY_SCORE_MIN, 5.0,
                    )
                elif (
                    win_rate < 45.0
                    or average_return < 0
                ):
                    learning_score_min = tighten_score_minimum(
                        AUTO_TRADER_ENTRY_SCORE_MIN, 2.0,
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

                learning_score_min = tighten_score_minimum(
                    learning_score_min, 5.0,
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

            scanner_rank = safe_float(
                candidate.get("scanner_rank")
                or candidate.get("rank")
            )

            momentum_30_candidate = bool(
                candidate.get(
                    "momentum_30_candidate"
                )
            )

            selected_strategy_version = (
                "momentum_30_v1"
                if momentum_30_candidate
                else AUTO_TRADER_STRATEGY_VERSION
            )

            entry_quality = evaluate_entry_quality(
                candidate,
                score_min=learning_score_min,
                confidence_min=learning_confidence_min,
                max_scanner_rank=AUTO_TRADER_MAX_ENTRY_SCANNER_RANK,
                max_atr_percent=AUTO_TRADER_MAX_ENTRY_ATR_PERCENT,
            )
            preliminary_entry_pass = entry_quality["passed"]

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

                    learning_score_min = tighten_score_minimum(
                        learning_score_min, 5.0,
                    )

                    news_adjusted = (
                        learning_score_min
                        > previous_score_min
                    )

            # Every candidate must pass the FINAL,
            # learning, regime, and news-adjusted
            # requirements.
            entry_quality = evaluate_entry_quality(
                candidate,
                score_min=learning_score_min,
                confidence_min=learning_confidence_min,
                max_scanner_rank=AUTO_TRADER_MAX_ENTRY_SCANNER_RANK,
                max_atr_percent=AUTO_TRADER_MAX_ENTRY_ATR_PERCENT,
            )

            if not entry_quality["passed"]:
                failed_requirements = entry_quality["failed_requirements"]

                cycle_result[
                    "skipped_candidates"
                ].append({
                    "symbol": symbol,
                    "reason": (
                        "entry requirements not met"
                    ),
                    "entry_quality": entry_quality,
                    "failed_requirements": (
                        failed_requirements
                    ),
                    "signal": signal,
                    "score": score,
                    "confidence": confidence,
                    "strategy_version": (
                        selected_strategy_version
                    ),
                    "momentum_30_candidate": (
                        momentum_30_candidate
                    ),
                    "momentum_30_checks": (
                        candidate.get(
                            "momentum_30_checks",
                            {},
                        )
                    ),
                    "momentum_30_failed_checks": (
                        candidate.get(
                            "momentum_30_failed_checks",
                            [],
                        )
                    ),
                    "momentum_move_percent": (
                        safe_float(
                            candidate.get(
                                "momentum_move_percent"
                            )
                        )
                    ),
                    "momentum_trend": (
                        candidate.get("trend")
                    ),
                    "volume_ratio": (
                        safe_float(
                            candidate.get(
                                "volume_ratio"
                            )
                        )
                    ),
                    "scanner_rank": scanner_rank,
                    "maximum_scanner_rank": (
                        AUTO_TRADER_MAX_ENTRY_SCANNER_RANK
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
                        strategy_version=(
                            selected_strategy_version
                        ),
                        entry_context={
                            "entry_quality": entry_quality,
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
                            "strategy_version": (
                                selected_strategy_version
                            ),
                            "momentum_30_candidate": (
                                momentum_30_candidate
                            ),
                            "momentum_30_checks": (
                                candidate.get(
                                    "momentum_30_checks",
                                    {},
                                )
                            ),
                            "momentum_30_failed_checks": (
                                candidate.get(
                                    "momentum_30_failed_checks",
                                    [],
                                )
                            ),
                            "momentum_move_percent": (
                                safe_float(
                                    candidate.get(
                                        "momentum_move_percent"
                                    )
                                )
                            ),
                            "momentum_min_move_percent": (
                                safe_float(
                                    candidate.get(
                                        "momentum_min_move_percent"
                                    )
                                )
                            ),
                            "momentum_min_volume_ratio": (
                                safe_float(
                                    candidate.get(
                                        "momentum_min_volume_ratio"
                                    )
                                )
                            ),
                            "one_day_change": safe_float(
                                candidate.get("change")
                            ),
                            "negative_day_weak_volume_shadow": (
                                safe_float(candidate.get("change")) is not None
                                and safe_float(candidate.get("volume_ratio")) is not None
                                and safe_float(candidate.get("change")) < 0.0
                                and safe_float(candidate.get("volume_ratio")) < 0.60
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
                "strategy_version": (
                    selected_strategy_version
                ),
                "momentum_30_candidate": (
                    momentum_30_candidate
                ),
                "momentum_move_percent": (
                    safe_float(
                        candidate.get(
                            "momentum_move_percent"
                        )
                    )
                ),
                "momentum_trend": (
                    candidate.get("trend")
                ),
                "volume_ratio": (
                    safe_float(
                        candidate.get(
                            "volume_ratio"
                        )
                    )
                ),
                "scanner_rank": scanner_rank,
                "entry_success": (
                    bool(
                        entry_result.get("success")
                    )
                    if isinstance(
                        entry_result,
                        dict,
                    )
                    else False
                ),
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
                        "negative_day_weak_volume_shadow": (
                            safe_float(candidate.get("change")) is not None
                            and safe_float(candidate.get("volume_ratio")) is not None
                            and safe_float(candidate.get("change")) < 0.0
                            and safe_float(candidate.get("volume_ratio")) < 0.60
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
                observation_id = (
                    scanner_observation_ids.get(
                        symbol
                    )
                )

                if observation_id is not None:
                    try:
                        mark_scanner_observation_selected(
                            observation_id
                        )
                    except Exception as error:
                        print(
                            "Scanner observation selection "
                            f"error for {symbol}: "
                            f"{clean_error_message(error)}"
                        )

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

        _auto_trader_last_successful_cycle_at = (
            time.time()
        )

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


def health_alert_env_enabled(name: str) -> bool:
    return (
        os.getenv(
            name,
            "false",
        ).strip().lower()
        in {
            "1",
            "true",
            "yes",
            "on",
        }
    )


def send_health_alert_email(
    subject: str,
    message: str,
    attachments: list[dict[str, Any]]
    | None = None,
) -> bool:
    if not health_alert_env_enabled(
        "HEALTH_ALERT_EMAIL_ENABLED"
    ):
        return False

    api_key = os.getenv(
        "RESEND_API_KEY",
        "",
    ).strip()

    email_to = os.getenv(
        "HEALTH_ALERT_EMAIL_TO",
        "",
    ).strip()

    email_from = os.getenv(
        "HEALTH_ALERT_EMAIL_FROM",
        "",
    ).strip()

    if not (
        api_key
        and email_to
        and email_from
    ):
        print(
            "Health alert email enabled but "
            "Resend configuration is incomplete."
        )
        return False

    try:
        email_payload: dict[str, Any] = {
            "from": email_from,
            "to": [email_to],
            "subject": subject,
            "text": message,
        }

        resend_attachments: list[
            dict[str, str]
        ] = []

        for attachment in (
            attachments or []
        ):
            if not isinstance(
                attachment,
                dict,
            ):
                continue

            filename = str(
                attachment.get(
                    "filename"
                )
                or ""
            ).strip()

            content = attachment.get(
                "content"
            )

            if (
                not filename
                or not isinstance(
                    content,
                    (bytes, bytearray),
                )
            ):
                continue

            encoded_content = (
                base64.b64encode(
                    bytes(content)
                ).decode("ascii")
            )

            resend_attachments.append({
                "filename": filename,
                "content": encoded_content,
            })

        if resend_attachments:
            email_payload[
                "attachments"
            ] = resend_attachments

        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": (
                    f"Bearer {api_key}"
                ),
                "Content-Type": "application/json",
            },
            json=email_payload,
            timeout=30,
        )

        response.raise_for_status()
        return True

    except Exception as error:
        print(
            "Health alert email failed: "
            f"{clean_error_message(error)}"
        )
        return False


def send_health_alert_sms(
    message: str,
) -> bool:
    if not health_alert_env_enabled(
        "HEALTH_ALERT_SMS_ENABLED"
    ):
        return False

    account_sid = os.getenv(
        "TWILIO_ACCOUNT_SID",
        "",
    ).strip()

    auth_token = os.getenv(
        "TWILIO_AUTH_TOKEN",
        "",
    ).strip()

    sms_from = os.getenv(
        "TWILIO_FROM_NUMBER",
        "",
    ).strip()

    sms_to = os.getenv(
        "HEALTH_ALERT_SMS_TO",
        "",
    ).strip()

    if not (
        account_sid
        and auth_token
        and sms_from
        and sms_to
    ):
        print(
            "Health alert SMS enabled but "
            "Twilio configuration is incomplete."
        )
        return False

    try:
        response = requests.post(
            (
                "https://api.twilio.com/"
                "2010-04-01/Accounts/"
                f"{account_sid}/Messages.json"
            ),
            auth=(
                account_sid,
                auth_token,
            ),
            data={
                "From": sms_from,
                "To": sms_to,
                "Body": message,
            },
            timeout=15,
        )

        response.raise_for_status()
        return True

    except Exception as error:
        print(
            "Health alert SMS failed: "
            f"{clean_error_message(error)}"
        )
        return False


def send_auto_trader_health_notifications(
    subject: str,
    message: str,
) -> dict[str, Any]:
    email_enabled = health_alert_env_enabled(
        "HEALTH_ALERT_EMAIL_ENABLED"
    )
    sms_enabled = health_alert_env_enabled(
        "HEALTH_ALERT_SMS_ENABLED"
    )

    email_sent = send_health_alert_email(
        subject,
        message,
    )

    sms_sent = send_health_alert_sms(
        message,
    )

    return {
        "email": (
            "sent"
            if email_sent
            else (
                "failed"
                if email_enabled
                else "disabled"
            )
        ),
        "sms": (
            "sent"
            if sms_sent
            else (
                "failed"
                if sms_enabled
                else "disabled"
            )
        ),
    }


def maybe_send_daily_auto_trader_health_email() -> None:
    global _auto_trader_daily_health_email_date

    eastern_now = datetime.now(
        ZoneInfo("America/New_York")
    )

    today = eastern_now.date().isoformat()

    if eastern_now.hour != 8:
        return

    if (
        _auto_trader_daily_health_email_date
        == today
    ):
        return

    _auto_trader_daily_health_email_date = today

    now = time.time()

    successful_cycle_age = (
        now - _auto_trader_last_successful_cycle_at
        if _auto_trader_last_successful_cycle_at
        is not None
        else None
    )

    scan_age = (
        now - _auto_trader_last_scan_at
        if _auto_trader_last_scan_at is not None
        else None
    )

    # Allow for weekends when deciding whether the
    # most recent successful scanner activity is recent.
    recent_scan = (
        scan_age is not None
        and scan_age <= 72 * 60 * 60
    )

    recent_successful_cycle = (
        successful_cycle_age is not None
        and successful_cycle_age <= 72 * 60 * 60
    )

    scanner_good = (
        _auto_trader_enabled
        and not _auto_trader_health_alert_active
        and recent_scan
        and recent_successful_cycle
    )

    status = (
        "GOOD"
        if scanner_good
        else "CHECK NEEDED"
    )

    def format_age(
        seconds: float | None,
    ) -> str:
        if seconds is None:
            return "Never"

        minutes = max(
            0,
            int(seconds // 60),
        )

        if minutes < 60:
            return f"{minutes} minutes ago"

        hours = minutes // 60

        if hours < 48:
            return f"{hours} hours ago"

        days = hours // 24
        return f"{days} days ago"

    message = "\n".join(
        [
            "AI Paper Trader Daily Health Report",
            "",
            f"Scanner status: {status}",
            (
                "Auto trader enabled: "
                f"{_auto_trader_enabled}"
            ),
            (
                "Health alert active: "
                f"{_auto_trader_health_alert_active}"
            ),
            (
                "Last successful cycle: "
                f"{format_age(successful_cycle_age)}"
            ),
            (
                "Last successful scan: "
                f"{format_age(scan_age)}"
            ),
            "",
            (
                "This is your automatic "
                "8:00 AM Eastern health report."
            ),
        ]
    )

    send_health_alert_email(
        (
            "AI Paper Trader - "
            f"Morning Scanner Status: {status}"
        ),
        message,
    )


async def auto_trader_health_watchdog() -> None:
    """
    Watch the PAPER auto-trader independently from its trading loop.

    A health alert is raised when the market is open, automation is
    enabled, and the trading-cycle heartbeat has been stale for
    at least 30 minutes.
    """
    global _auto_trader_health_alert_active
    global _auto_trader_health_alerted_at

    market_open_observed_at: float | None = None

    while True:
        try:
            await asyncio.to_thread(
                maybe_send_daily_auto_trader_health_email
            )

            should_monitor = (
                _auto_trader_enabled
                and auto_trader_automation_allowed()
            )

            if should_monitor:
                try:
                    clock = await asyncio.to_thread(
                        fetch_alpaca_market_clock
                    )

                    market_is_open = bool(
                        clock.get("is_open")
                    )

                except Exception as clock_error:
                    error_message = clean_error_message(
                        clock_error
                    )

                    print(
                        "Auto-trader health watchdog "
                        "clock error: "
                        f"{error_message}"
                    )

                    add_auto_trader_log(
                        "health_watchdog_error",
                        message=error_message,
                    )

                    now = time.time()

                    cached_clock_age = (
                        now - _auto_trader_last_market_clock_at
                        if _auto_trader_last_market_clock_at
                        is not None
                        else None
                    )

                    cached_clock_available = (
                        _auto_trader_last_market_clock
                        is not None
                        and cached_clock_age is not None
                        and cached_clock_age
                        <= AUTO_TRADER_MARKET_CLOCK_CACHE_SECONDS
                    )

                    if cached_clock_available:
                        market_is_open = bool(
                            _auto_trader_last_market_clock.get(
                                "is_open"
                            )
                        )

                    else:
                        try:
                            calendar_entry = await asyncio.to_thread(
                                fetch_alpaca_market_calendar_today
                            )

                            market_is_open = (
                                market_is_open_from_calendar(
                                    calendar_entry
                                )
                            )

                        except Exception as calendar_error:
                            calendar_message = clean_error_message(
                                calendar_error
                            )

                            print(
                                "Auto-trader health watchdog "
                                "calendar error: "
                                f"{calendar_message}"
                            )

                            add_auto_trader_log(
                                "health_watchdog_error",
                                message=calendar_message,
                            )

                            continue

                if market_is_open:
                    now = time.time()

                    if market_open_observed_at is None:
                        market_open_observed_at = now

                    # Use the newest relevant timestamp so a
                    # restart, market open, or newly enabled trader
                    # receives a fresh grace period.
                    reference_candidates = [
                        timestamp
                        for timestamp in (
                            market_open_observed_at,
                            _auto_trader_enabled_at,
                            _auto_trader_last_successful_cycle_at,
                        )
                        if timestamp is not None
                    ]

                    cycle_reference_at = (
                        max(reference_candidates)
                        if reference_candidates
                        else now
                    )

                    cycle_age_seconds = (
                        now - cycle_reference_at
                    )

                    stalled = (
                        cycle_age_seconds
                        >= AUTO_TRADER_HEALTH_STALE_SECONDS
                    )

                    if (
                        stalled
                        and not _auto_trader_health_alert_active
                    ):
                        _auto_trader_health_alert_active = True
                        _auto_trader_health_alerted_at = now

                        alert_message = (
                            "AI Paper Trader ALERT: "
                            "No successful trading cycle has "
                            "completed for at least 30 minutes "
                            "while the market is open. "
                            "Check the trader and scanner."
                        )

                        add_auto_trader_log(
                            "trader_health_stalled",
                            message=alert_message,
                        )

                        await asyncio.to_thread(
                            send_auto_trader_health_notifications,
                            "AI Paper Trader - Health Alert",
                            alert_message,
                        )

                    elif (
                        not stalled
                        and _auto_trader_health_alert_active
                    ):
                        _auto_trader_health_alert_active = False
                        _auto_trader_health_alerted_at = None

                        recovery_message = (
                            "AI Paper Trader RECOVERED: "
                            "A successful trading cycle has "
                            "completed and the trader health "
                            "watchdog is back to normal."
                        )

                        add_auto_trader_log(
                            "trader_health_recovered",
                            message=recovery_message,
                        )

                        await asyncio.to_thread(
                            send_auto_trader_health_notifications,
                            "AI Paper Trader - Recovered",
                            recovery_message,
                        )

                else:
                    market_open_observed_at = None

        except Exception as error:
            error_message = clean_error_message(
                error
            )

            print(
                "Auto-trader health watchdog error: "
                f"{error_message}"
            )

            add_auto_trader_log(
                "health_watchdog_error",
                message=error_message,
            )

        await asyncio.sleep(
            AUTO_TRADER_HEALTH_CHECK_SECONDS
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
    now = time.time()

    cycle_age_seconds = (
        now - _auto_trader_last_cycle_at
        if _auto_trader_last_cycle_at is not None
        else None
    )

    successful_cycle_age_seconds = (
        now - _auto_trader_last_successful_cycle_at
        if _auto_trader_last_successful_cycle_at is not None
        else None
    )

    market_clock_cache_age_seconds = (
        now - _auto_trader_last_market_clock_at
        if _auto_trader_last_market_clock_at is not None
        else None
    )

    scan_age_seconds = (
        now - _auto_trader_last_scan_at
        if _auto_trader_last_scan_at is not None
        else None
    )

    latest_cycle_failed = (
        isinstance(_auto_trader_last_cycle_result, dict)
        and _auto_trader_last_cycle_result.get("success") is False
    )

    if not _auto_trader_enabled:
        health = "disabled"
    elif _auto_trader_health_alert_active:
        health = "stalled"
    elif latest_cycle_failed:
        health = "degraded"
    elif (
        _auto_trader_last_successful_cycle_at is None
        or _auto_trader_last_scan_at is None
    ):
        health = "waiting"
    else:
        health = "healthy"

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
        "last_successful_cycle_at": (
            _auto_trader_last_successful_cycle_at
        ),
        "last_cycle_result": (
            _auto_trader_last_cycle_result
        ),
        "health": health,
        "last_scan_at": (
            _auto_trader_last_scan_at
        ),
        "last_trade_at": (
            _auto_trader_last_trade_at
        ),
        "cycle_age_seconds": (
            round(cycle_age_seconds, 1)
            if cycle_age_seconds is not None
            else None
        ),
        "successful_cycle_age_seconds": (
            round(successful_cycle_age_seconds, 1)
            if successful_cycle_age_seconds is not None
            else None
        ),
        "scan_age_seconds": (
            round(scan_age_seconds, 1)
            if scan_age_seconds is not None
            else None
        ),
        "last_market_clock_at": (
            _auto_trader_last_market_clock_at
        ),
        "market_clock_cache_age_seconds": (
            round(market_clock_cache_age_seconds, 1)
            if market_clock_cache_age_seconds is not None
            else None
        ),
        "market_clock_cache_seconds": (
            AUTO_TRADER_MARKET_CLOCK_CACHE_SECONDS
        ),
        "health_alert_active": (
            _auto_trader_health_alert_active
        ),
        "health_alerted_at": (
            _auto_trader_health_alerted_at
        ),
        "health_stale_seconds": (
            AUTO_TRADER_HEALTH_STALE_SECONDS
        ),
        "settings": {
            "scan_seconds": (
                AUTO_TRADER_SCAN_SECONDS
            ),
            "entry_score_min": (
                AUTO_TRADER_ENTRY_SCORE_MIN
            ),
            "max_entry_scanner_rank": (
                AUTO_TRADER_MAX_ENTRY_SCANNER_RANK
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

@app.post("/auto-trader/health/test-notification")
async def test_auto_trader_health_notification(
    request: Request,
) -> dict[str, Any]:
    require_app_session(
        request
    )

    subject = (
        "AI Paper Trader - Test Alert"
    )

    message = (
        "AI Paper Trader TEST: "
        "Your trader health notification system "
        "is connected and working."
    )

    delivery = await asyncio.to_thread(
        send_auto_trader_health_notifications,
        subject,
        message,
    )

    add_auto_trader_log(
        "trader_health_test",
        message=(
            "A manual trader health notification "
            "test was requested."
        ),
    )

    return {
        "success": True,
        "paper": True,
        "message": (
            "Health notification test attempted. "
            "Check configured email/SMS channels."
        ),
        "delivery": delivery,
        "email_enabled": (
            health_alert_env_enabled(
                "HEALTH_ALERT_EMAIL_ENABLED"
            )
        ),
        "sms_enabled": (
            health_alert_env_enabled(
                "HEALTH_ALERT_SMS_ENABLED"
            )
        ),
    }


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



@app.get("/auto-trader/pnl-reconciliation")
def auto_trader_pnl_reconciliation(
    request: Request,
    date: str = Query(...),
) -> dict[str, Any]:
    require_app_session(
        request
    )

    try:
        target_date = datetime.strptime(
            date,
            "%Y-%m-%d",
        ).date()
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=(
                "date must use YYYY-MM-DD format"
            ),
        ) from error

    eastern = ZoneInfo(
        "America/New_York"
    )

    history_payload = auto_trader_history(
        request=request,
        limit=5000,
    )

    history_rows = (
        history_payload.get("trades")
        if isinstance(
            history_payload,
            dict,
        )
        else []
    )

    if not isinstance(
        history_rows,
        list,
    ):
        history_rows = []

    day_trades: list[
        dict[str, Any]
    ] = []

    for trade in history_rows:
        if not isinstance(
            trade,
            dict,
        ):
            continue

        timestamp = (
            trade.get("exit_timestamp")
            or trade.get("filled_at")
        )

        if not timestamp:
            continue

        try:
            parsed = datetime.fromisoformat(
                str(timestamp).replace(
                    "Z",
                    "+00:00",
                )
            )

            if parsed.tzinfo is None:
                parsed = parsed.replace(
                    tzinfo=timezone.utc
                )

            local_date = (
                parsed.astimezone(
                    eastern
                ).date()
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        if local_date == target_date:
            day_trades.append(
                trade
            )

    known_closed_trade_pl = 0.0
    complete_trades = 0
    incomplete_trades = 0
    unaccounted_closed_shares = 0.0

    for trade in day_trades:
        value = safe_float(
            trade.get(
                "realized_profit_loss"
            )
        )

        if value is not None:
            known_closed_trade_pl += value

        incomplete = (
            trade.get("pnl_complete")
            is False
            or str(
                trade.get("status")
                or ""
            ).upper()
            == "CLOSED_INCOMPLETE"
        )

        if incomplete:
            incomplete_trades += 1

            missing = safe_float(
                trade.get(
                    "unaccounted_closed_shares"
                )
            )

            if missing is not None:
                unaccounted_closed_shares += (
                    missing
                )
        else:
            complete_trades += 1

    portfolio_history = (
        fetch_alpaca_portfolio_history()
    )

    daily_equity: dict[
        str,
        float,
    ] = {}

    for point in portfolio_history:
        if not isinstance(
            point,
            dict,
        ):
            continue

        raw_timestamp = point.get(
            "timestamp"
        )

        equity = safe_float(
            point.get("equity")
        )

        if (
            raw_timestamp is None
            or equity is None
        ):
            continue

        try:
            if isinstance(
                raw_timestamp,
                (int, float),
            ):
                parsed = (
                    datetime.fromtimestamp(
                        raw_timestamp,
                        tz=timezone.utc,
                    )
                )
            else:
                parsed = (
                    datetime.fromisoformat(
                        str(
                            raw_timestamp
                        ).replace(
                            "Z",
                            "+00:00",
                        )
                    )
                )

                if parsed.tzinfo is None:
                    parsed = parsed.replace(
                        tzinfo=timezone.utc
                    )

            # Alpaca 1D portfolio-history timestamps
            # are UTC calendar-day labels. Converting
            # midnight UTC to Eastern would incorrectly
            # move the point to the prior calendar date.
            day_key = (
                parsed.astimezone(
                    timezone.utc
                ).date().isoformat()
            )

        except (
            TypeError,
            ValueError,
            OSError,
        ):
            continue

        daily_equity[day_key] = equity

    ordered_dates = sorted(
        daily_equity
    )

    target_key = (
        target_date.isoformat()
    )

    target_equity = daily_equity.get(
        target_key
    )

    prior_date = None
    prior_equity = None

    if target_key in ordered_dates:
        index = ordered_dates.index(
            target_key
        )

        if index > 0:
            prior_date = (
                ordered_dates[
                    index - 1
                ]
            )

            prior_equity = (
                daily_equity.get(
                    prior_date
                )
            )

    historical_equity_change = None

    if (
        target_equity is not None
        and prior_equity is not None
    ):
        historical_equity_change = (
            target_equity
            - prior_equity
        )

    eastern_today = (
        datetime.now(
            eastern
        ).date()
    )

    account_day_pl = None
    account_day_pl_percent = None
    live_equity = None
    live_last_equity = None

    if target_date == eastern_today:
        live_account = (
            build_alpaca_live_account_snapshot()
        )

        live_equity = safe_float(
            live_account.get(
                "equity"
            )
        )

        live_last_equity = safe_float(
            live_account.get(
                "starting_balance"
            )
        )

        account_day_pl = safe_float(
            live_account.get(
                "profit_loss"
            )
        )

        account_day_pl_percent = (
            safe_float(
                live_account.get(
                    "profit_loss_percent"
                )
            )
        )

    return {
        "date": target_key,
        "account_day_pl": (
            round(
                account_day_pl,
                2,
            )
            if account_day_pl
            is not None
            else None
        ),
        "account_day_pl_percent": (
            round(
                account_day_pl_percent,
                4,
            )
            if account_day_pl_percent
            is not None
            else None
        ),
        "live_equity": (
            round(
                live_equity,
                2,
            )
            if live_equity
            is not None
            else None
        ),
        "live_last_equity": (
            round(
                live_last_equity,
                2,
            )
            if live_last_equity
            is not None
            else None
        ),
        "portfolio_history_equity": (
            round(
                target_equity,
                2,
            )
            if target_equity
            is not None
            else None
        ),
        "prior_portfolio_history_date": (
            prior_date
        ),
        "prior_portfolio_history_equity": (
            round(
                prior_equity,
                2,
            )
            if prior_equity
            is not None
            else None
        ),
        "portfolio_history_equity_change": (
            round(
                historical_equity_change,
                2,
            )
            if historical_equity_change
            is not None
            else None
        ),
        "known_closed_trade_pl": round(
            known_closed_trade_pl,
            2,
        ),
        "closed_trades": len(
            day_trades
        ),
        "complete_trades": (
            complete_trades
        ),
        "incomplete_trades": (
            incomplete_trades
        ),
        "unaccounted_closed_shares": (
            round(
                unaccounted_closed_shares,
                6,
            )
        ),
        "journal_pl_complete": (
            incomplete_trades == 0
        ),
        "metrics_are_directly_comparable": (
            False
        ),
        "account_day_pl_definition": (
            "Live Alpaca account day P/L "
            "from equity minus last_equity. "
            "Available here only when the "
            "requested date is today."
        ),
        "portfolio_history_definition": (
            "Alpaca 1D portfolio-history "
            "equity points. Consecutive "
            "point differences are exposed "
            "separately and are not labeled "
            "as account day P/L."
        ),
        "closed_trade_pl_definition": (
            "Known canonical realized "
            "P/L measured from each "
            "trade's entry price to its "
            "matched exit fills."
        ),
        "note": (
            "Account Day P/L and "
            "closed-trade P/L measure "
            "different things and are "
            "not expected to match when "
            "positions span trading days. "
            "Incomplete canonical trades "
            "also make the known "
            "closed-trade total partial."
        ),
    }


@app.get("/auto-trader/excursions")
def auto_trader_excursions(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    require_app_session(
        request
    )

    rows = load_trade_excursions(
        limit=limit
    )

    return {
        "count": len(rows),
        "results": rows,
    }




# =========================================================
# Pro Ticker research routes
# =========================================================

def build_pro_ticker_learning_analysis(
    *,
    limit: int = 5000,
) -> dict[str, Any]:
    rows = load_pro_ticker_research(
        limit=limit,
    )

    total = len(rows)

    direction_counts = {
        "LONG": 0,
        "SHORT": 0,
        "UNKNOWN": 0,
    }

    setup_counts: dict[str, int] = {}
    feature_counts: dict[str, int] = {}

    actionable_level_count = 0
    outcome_count = 0
    scanner_seen_count = 0
    bot_action_count = 0
    skip_reason_count = 0

    move_values: list[float] = []

    feature_keys = (
        "vwap_present",
        "vwma_present",
        "relative_volume_present",
        "rsi_present",
        "breakout_present",
        "bull_flag_present",
        "higher_lows_present",
        "consolidation_present",
        "exhaustion_present",
        "parabolic_present",
    )

    examples: list[dict[str, Any]] = []

    for row in rows:
        direction = str(
            row.get("direction") or ""
        ).strip().upper()

        if direction not in (
            "LONG",
            "SHORT",
        ):
            direction = "UNKNOWN"

        direction_counts[direction] += 1

        setup_type = str(
            row.get("setup_type") or ""
        ).strip().lower()

        if setup_type:
            setup_counts[setup_type] = (
                setup_counts.get(
                    setup_type,
                    0,
                )
                + 1
            )

        long_level = row.get(
            "long_level"
        )
        short_level = row.get(
            "short_level"
        )

        if (
            long_level is not None
            or short_level is not None
        ):
            actionable_level_count += 1

        move_value = row.get(
            "reported_move_percent"
        )

        if move_value is not None:
            try:
                numeric_move = float(
                    move_value
                )

                if math.isfinite(
                    numeric_move
                ):
                    move_values.append(
                        numeric_move
                    )
                    outcome_count += 1

            except (
                TypeError,
                ValueError,
            ):
                pass

        scanner_seen = row.get(
            "our_scanner_seen"
        )

        if scanner_seen not in (
            None,
            0,
            False,
            "0",
            "",
        ):
            scanner_seen_count += 1

        if row.get(
            "our_bot_action"
        ):
            bot_action_count += 1

        if row.get(
            "our_skip_reason"
        ):
            skip_reason_count += 1

        raw_features = row.get(
            "raw_features"
        )

        if not isinstance(
            raw_features,
            dict,
        ):
            raw_features = {}

            raw_json = row.get(
                "raw_features_json"
            )

            if isinstance(
                raw_json,
                str,
            ):
                try:
                    parsed = json.loads(
                        raw_json
                    )

                    if isinstance(
                        parsed,
                        dict,
                    ):
                        raw_features = parsed

                except (
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                ):
                    pass

        for key in feature_keys:
            if bool(
                raw_features.get(key)
            ):
                feature_counts[key] = (
                    feature_counts.get(
                        key,
                        0,
                    )
                    + 1
                )

        examples.append(
            {
                "id": row.get("id"),
                "symbol": row.get(
                    "symbol"
                ),
                "published_date": row.get(
                    "published_date"
                ),
                "alert_date": row.get(
                    "alert_date"
                ),
                "alert_time": row.get(
                    "alert_time"
                ),
                "direction": (
                    row.get("direction")
                ),
                "long_level": long_level,
                "short_level": (
                    short_level
                ),
                "setup_type": (
                    row.get("setup_type")
                ),
                "reported_move_percent": (
                    move_value
                ),
                "our_scanner_seen": (
                    scanner_seen
                ),
                "our_scanner_score": (
                    row.get(
                        "our_scanner_score"
                    )
                ),
                "our_scanner_confidence": (
                    row.get(
                        "our_scanner_confidence"
                    )
                ),
                "our_scanner_rank": (
                    row.get(
                        "our_scanner_rank"
                    )
                ),
                "our_bot_action": (
                    row.get(
                        "our_bot_action"
                    )
                ),
                "our_skip_reason": (
                    row.get(
                        "our_skip_reason"
                    )
                ),
                "article_title": (
                    row.get(
                        "article_title"
                    )
                ),
            }
        )

    missing_alert_date = sum(
        1
        for row in rows
        if not row.get("alert_date")
    )

    missing_alert_time = sum(
        1
        for row in rows
        if not row.get("alert_time")
    )

    missing_direction = (
        direction_counts["UNKNOWN"]
    )

    missing_level = (
        total - actionable_level_count
    )

    sorted_features = dict(
        sorted(
            feature_counts.items(),
            key=lambda item: (
                -item[1],
                item[0],
            ),
        )
    )

    sorted_setups = dict(
        sorted(
            setup_counts.items(),
            key=lambda item: (
                -item[1],
                item[0],
            ),
        )
    )

    return {
        "paper": True,
        "research_only": True,
        "automatic_strategy_changes": False,
        "sample": {
            "articles": total,
            "directions": (
                direction_counts
            ),
            "articles_with_actionable_level": (
                actionable_level_count
            ),
            "articles_with_reported_outcome": (
                outcome_count
            ),
            "scanner_seen": (
                scanner_seen_count
            ),
            "bot_actions_recorded": (
                bot_action_count
            ),
            "skip_reasons_recorded": (
                skip_reason_count
            ),
        },
        "reported_outcomes": {
            "count": len(
                move_values
            ),
            "average_move_percent": (
                round(
                    sum(move_values)
                    / len(move_values),
                    4,
                )
                if move_values
                else None
            ),
            "minimum_move_percent": (
                round(
                    min(move_values),
                    4,
                )
                if move_values
                else None
            ),
            "maximum_move_percent": (
                round(
                    max(move_values),
                    4,
                )
                if move_values
                else None
            ),
            "warning": (
                "Reported move percentages are "
                "retrospective outcome data and "
                "must not be used as information "
                "available at entry."
            ),
        },
        "setup_counts": sorted_setups,
        "feature_counts": (
            sorted_features
        ),
        "missing_data": {
            "alert_date": (
                missing_alert_date
            ),
            "alert_time": (
                missing_alert_time
            ),
            "direction": (
                missing_direction
            ),
            "actionable_level": (
                missing_level
            ),
        },
        "decision_coverage": {
            "scanner_seen_percent": (
                round(
                    (
                        scanner_seen_count
                        / total
                        * 100.0
                    ),
                    2,
                )
                if total
                else 0.0
            ),
            "bot_action_percent": (
                round(
                    (
                        bot_action_count
                        / total
                        * 100.0
                    ),
                    2,
                )
                if total
                else 0.0
            ),
        },
        "ready_for_strategy_conclusions": (
            total >= 30
            and scanner_seen_count >= 20
            and bot_action_count >= 20
        ),
        "minimum_research_sample": 30,
        "examples": examples,
    }


@app.get(
    "/auto-trader/pro-ticker-learning"
)
def auto_trader_pro_ticker_learning(
    request: Request,
    limit: int = Query(
        default=1000,
        ge=1,
        le=5000,
    ),
) -> dict[str, Any]:
    require_app_session(
        request
    )

    return (
        build_pro_ticker_learning_analysis(
            limit=limit,
        )
    )


@app.get("/auto-trader/pro-ticker-research")
def auto_trader_pro_ticker_research(
    request: Request,
    symbol: str | None = Query(
        default=None
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
) -> dict[str, Any]:
    require_app_session(
        request
    )

    rows = load_pro_ticker_research(
        symbol=symbol,
        limit=limit,
    )

    return {
        "paper": True,
        "research_only": True,
        "count": len(rows),
        "results": rows,
    }


def build_pro_ticker_scan_email(
    result: dict[str, Any],
    *,
    label: str = "Hourly",
) -> tuple[str, str]:
    collected = result.get(
        "collected",
        [],
    )

    if not isinstance(
        collected,
        list,
    ):
        collected = []

    lines = [
        "AI Paper Trader - Pro Ticker Research",
        "",
        f"Scan type: {label}",
        (
            "Pages scanned: "
            f"{result.get('pages_scanned', 0)}"
        ),
        (
            "Recaps collected: "
            f"{result.get('collected_count', 0)}"
        ),
        (
            "Errors: "
            f"{result.get('error_count', 0)}"
        ),
        "",
    ]

    if collected:
        lines.append("Research collected:")
        lines.append("")

        for item in collected:
            symbol = (
                item.get("symbol")
                or "UNKNOWN"
            )

            title = (
                item.get("article_title")
                or "Untitled article"
            )

            move = item.get(
                "reported_move_percent"
            )

            direction = (
                item.get("direction")
                or "Unknown"
            )

            features = item.get(
                "raw_features",
                {},
            )

            if not isinstance(
                features,
                dict,
            ):
                features = {}

            learned = []

            feature_labels = {
                "breakout_present": "Breakout",
                "bull_flag_present": "Bull flag",
                "consolidation_present": "Consolidation",
                "higher_lows_present": "Higher lows",
                "relative_volume_present": "Relative volume",
                "rsi_present": "RSI",
                "vwap_present": "VWAP",
                "vwma_present": "VWMA",
                "exhaustion_present": "Exhaustion",
                "parabolic_present": "Parabolic move",
            }

            for key, label_text in (
                feature_labels.items()
            ):
                if features.get(key):
                    learned.append(
                        label_text
                    )

            lines.append(
                f"{symbol}"
            )
            lines.append(
                f"Title: {title}"
            )
            lines.append(
                f"Direction: {direction}"
            )

            if move is not None:
                lines.append(
                    "Reported move: "
                    f"{move}%"
                )

            if learned:
                lines.append(
                    "Setup characteristics: "
                    + ", ".join(learned)
                )

            lines.append("")

    else:
        lines.append(
            "No qualifying Pro Ticker "
            "signal recaps were found."
        )

    errors = result.get(
        "errors",
        [],
    )

    if isinstance(errors, list) and errors:
        lines.append("")
        lines.append("Errors:")

        for item in errors[:10]:
            if isinstance(item, dict):
                lines.append(
                    "- "
                    + str(
                        item.get(
                            "article_url",
                            "Unknown URL",
                        )
                    )
                    + ": "
                    + str(
                        item.get(
                            "error",
                            "Unknown error",
                        )
                    )
                )

    subject = (
        "AI Paper Trader - "
        f"Pro Ticker {label} Research Complete"
    )

    return (
        subject,
        "\n".join(lines),
    )


@app.post("/auto-trader/pro-ticker-research/daily-recap")
def auto_trader_pro_ticker_daily_recap(
    request: Request,
    send_email: bool = Query(
        default=False,
    ),
    run_research: bool = Query(
        default=False,
    ),
) -> dict[str, Any]:
    require_app_session(
        request
    )

    eastern_now = datetime.now(
        ZoneInfo("America/New_York")
    )

    eastern_date = (
        eastern_now.date().isoformat()
    )

    if run_research:
        research_result = (
            run_pro_ticker_fresh_scan(
                pages=40,
            )
        )
    else:
        research_result = {
            "pages_scanned": 0,
            "candidate_count": 0,
            "collected_count": 0,
            "error_count": 0,
            "collected": [],
            "errors": [],
        }

    subject, message = (
        build_daily_market_recap_email(
            research_result=research_result,
            eastern_date=eastern_date,
        )
    )

    email_status = "skipped"

    if send_email:
        email_status = (
            "sent"
            if send_health_alert_email(
                subject,
                message,
            )
            else "failed"
        )

    return {
        "paper": True,
        "research_only": True,
        "success": True,
        "email": email_status,
        "subject": subject,
        "message": message,
        "research": research_result,
    }


@app.post("/auto-trader/pro-ticker-research/fresh-scan")
def auto_trader_pro_ticker_fresh_scan(
    request: Request,
    pages: int = Query(
        default=50,
        ge=1,
        le=50,
    ),
    send_email: bool = Query(
        default=True,
    ),
) -> dict[str, Any]:
    require_app_session(
        request
    )

    try:
        result = run_pro_ticker_fresh_scan(
            pages=pages,
        )
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Pro Ticker fresh scan failed: "
                f"{error}"
            ),
        ) from error

    email_status = "skipped"

    if send_email:
        subject, message = (
            build_pro_ticker_scan_email(
                result,
                label="Hourly",
            )
        )

        email_status = (
            "sent"
            if send_health_alert_email(
                subject,
                message,
            )
            else "failed"
        )

    return {
        "paper": True,
        "research_only": True,
        "success": True,
        "email": email_status,
        **result,
    }


@app.post("/auto-trader/pro-ticker-research/backfill")
def auto_trader_pro_ticker_backfill(
    request: Request,
    pages_per_run: int = Query(
        default=10,
        ge=1,
        le=25,
    ),
) -> dict[str, Any]:
    require_app_session(
        request
    )

    try:
        result = (
            run_pro_ticker_historical_backfill(
                pages_per_run=pages_per_run,
            )
        )
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Pro Ticker historical backfill "
                f"failed: {error}"
            ),
        ) from error

    return {
        "paper": True,
        "research_only": True,
        "success": True,
        **result,
    }


@app.post("/auto-trader/pro-ticker-research/collect")
def auto_trader_collect_pro_ticker_research(
    request: Request,
    payload: dict[str, Any],
) -> dict[str, Any]:
    require_app_session(
        request
    )

    article_url = str(
        payload.get(
            "article_url",
            "",
        )
    ).strip()

    if not article_url:
        raise HTTPException(
            status_code=400,
            detail=(
                "article_url is required."
            ),
        )

    try:
        result = (
            collect_pro_ticker_article(
                article_url
            )
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Pro Ticker article "
                f"request failed: {error}"
            ),
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=(
                "Pro Ticker research "
                f"collection failed: {error}"
            ),
        ) from error

    return {
        "paper": True,
        "research_only": True,
        "success": True,
        "result": result,
    }


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
            force_refresh=refresh,
            request_func=alpaca_paper_request,
            market_data_request_func=alpaca_market_data_request,
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
            "scanned_universe": "Alpaca Momentum Candidates",
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
            "scanned_universe": "Alpaca Momentum Candidates",
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

@app.get("/auto-trader/feature-learning")
def auto_trader_feature_learning(
    request: Request,
    minimum_group_size: int = Query(
        default=5,
        ge=1,
        le=100,
    ),
    limit: int = Query(
        default=5000,
        ge=1,
        le=10000,
    ),
) -> dict[str, Any]:
    require_app_session(
        request
    )

    return calculate_feature_performance(
        minimum_group_size=minimum_group_size,
        limit=limit,
    )


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

@app.get("/auto-trader/broker-fills/trade-book-gap")
def auto_trader_broker_fill_trade_book_gap(
    request: Request,
) -> dict[str, Any]:
    """
    Compare reconstructed broker round trips against trade_book.

    Diagnostic only. No database writes are performed.
    """

    require_app_session(request)

    fills = load_broker_fills(
        limit=10000
    )

    fills = sorted(
        fills,
        key=lambda item: str(
            item.get("filled_at") or ""
        ),
    )

    open_lots: dict[
        str,
        list[dict[str, Any]],
    ] = {}

    reconstructed: list[
        dict[str, Any]
    ] = []

    unmatched_sells: list[
        dict[str, Any]
    ] = []

    for fill in fills:
        symbol = str(
            fill.get("symbol") or ""
        ).strip().upper()

        side = str(
            fill.get("side") or ""
        ).strip().upper()

        shares = safe_float(
            fill.get("shares")
        )

        price = safe_float(
            fill.get("price")
        )

        if (
            not symbol
            or shares is None
            or shares <= 0
            or price is None
            or price <= 0
        ):
            continue

        raw_order = fill.get(
            "raw_order"
        )

        if not isinstance(
            raw_order,
            dict,
        ):
            raw_order = {}

        client_order_id = str(
            raw_order.get(
                "client_order_id"
            )
            or ""
        ).strip()

        if side == "BUY":
            if not (
                client_order_id
                .lower()
                .startswith(
                    "auto-entry-"
                )
            ):
                continue

            open_lots.setdefault(
                symbol,
                [],
            ).append(
                {
                    "order_id": fill.get(
                        "order_id"
                    ),
                    "remaining_shares": shares,
                    "price": price,
                    "filled_at": fill.get(
                        "filled_at"
                    ),
                    "client_order_id": (
                        client_order_id
                    ),
                }
            )

            continue

        if side != "SELL":
            continue

        remaining_sell = shares

        lots = open_lots.get(
            symbol,
            [],
        )

        while (
            remaining_sell > 0.00000001
            and lots
        ):
            lot = lots[0]

            lot_remaining = (
                safe_float(
                    lot.get(
                        "remaining_shares"
                    )
                )
                or 0.0
            )

            if lot_remaining <= 0.00000001:
                lots.pop(0)
                continue

            matched_shares = min(
                remaining_sell,
                lot_remaining,
            )

            entry_price = float(
                lot["price"]
            )

            exit_price = float(
                price
            )

            realized_pl = (
                exit_price
                - entry_price
            ) * matched_shares

            realized_return = (
                (
                    exit_price
                    - entry_price
                )
                / entry_price
                * 100.0
            )

            reconstructed.append(
                {
                    "symbol": symbol,
                    "shares": round(
                        matched_shares,
                        8,
                    ),
                    "entry_order_id": (
                        lot.get(
                            "order_id"
                        )
                    ),
                    "exit_order_id": (
                        fill.get(
                            "order_id"
                        )
                    ),
                    "entry_price": (
                        entry_price
                    ),
                    "exit_price": (
                        exit_price
                    ),
                    "entry_timestamp": (
                        lot.get(
                            "filled_at"
                        )
                    ),
                    "exit_timestamp": (
                        fill.get(
                            "filled_at"
                        )
                    ),
                    "realized_profit_loss": (
                        round(
                            realized_pl,
                            4,
                        )
                    ),
                    "realized_return_percent": (
                        round(
                            realized_return,
                            6,
                        )
                    ),
                }
            )

            lot[
                "remaining_shares"
            ] = (
                lot_remaining
                - matched_shares
            )

            remaining_sell -= (
                matched_shares
            )

            if (
                lot[
                    "remaining_shares"
                ]
                <= 0.00000001
            ):
                lots.pop(0)

        if remaining_sell > 0.00000001:
            unmatched_sells.append(
                {
                    "order_id": fill.get(
                        "order_id"
                    ),
                    "symbol": symbol,
                    "shares": shares,
                    "unmatched_shares": round(
                        remaining_sell,
                        8,
                    ),
                    "price": price,
                    "filled_at": fill.get(
                        "filled_at"
                    ),
                }
            )

    trade_book_rows = load_trade_book(
        limit=5000
    )

    by_entry_order_id = {
        str(
            row.get(
                "entry_order_id"
            )
            or ""
        ).strip(): row
        for row in trade_book_rows
        if str(
            row.get(
                "entry_order_id"
            )
            or ""
        ).strip()
    }

    by_exit_order_id = {
        str(
            row.get(
                "exit_order_id"
            )
            or ""
        ).strip(): row
        for row in trade_book_rows
        if str(
            row.get(
                "exit_order_id"
            )
            or ""
        ).strip()
    }

    matched_existing: list[
        dict[str, Any]
    ] = []

    missing: list[
        dict[str, Any]
    ] = []

    conflicting: list[
        dict[str, Any]
    ] = []

    for trade in reconstructed:
        entry_id = str(
            trade.get(
                "entry_order_id"
            )
            or ""
        ).strip()

        exit_id = str(
            trade.get(
                "exit_order_id"
            )
            or ""
        ).strip()

        entry_row = (
            by_entry_order_id.get(
                entry_id
            )
        )

        exit_row = (
            by_exit_order_id.get(
                exit_id
            )
        )

        if (
            entry_row is not None
            and exit_row is not None
            and entry_row.get("id")
            == exit_row.get("id")
        ):
            matched_existing.append(
                {
                    **trade,
                    "trade_book_id": (
                        entry_row.get(
                            "id"
                        )
                    ),
                    "trade_book_status": (
                        entry_row.get(
                            "status"
                        )
                    ),
                }
            )
            continue

        if (
            entry_row is None
            and exit_row is None
        ):
            missing.append(
                trade
            )
            continue

        conflicting.append(
            {
                **trade,
                "entry_trade_book_id": (
                    entry_row.get("id")
                    if entry_row
                    else None
                ),
                "exit_trade_book_id": (
                    exit_row.get("id")
                    if exit_row
                    else None
                ),
                "entry_status": (
                    entry_row.get("status")
                    if entry_row
                    else None
                ),
                "exit_status": (
                    exit_row.get("status")
                    if exit_row
                    else None
                ),
            }
        )

    missing_pl = sum(
        float(
            trade.get(
                "realized_profit_loss"
            )
            or 0.0
        )
        for trade in missing
    )

    existing_pl = sum(
        float(
            trade.get(
                "realized_profit_loss"
            )
            or 0.0
        )
        for trade in matched_existing
    )

    return {
        "paper": True,
        "read_only": True,
        "source": (
            "broker_fills+trade_book"
        ),
        "summary": {
            "broker_fills": len(
                fills
            ),
            "reconstructed_matches": len(
                reconstructed
            ),
            "already_in_trade_book": len(
                matched_existing
            ),
            "missing_from_trade_book": len(
                missing
            ),
            "conflicting_partial_matches": len(
                conflicting
            ),
            "unmatched_sell_fills": len(
                unmatched_sells
            ),
            "existing_reconstructed_pl": round(
                existing_pl,
                2,
            ),
            "missing_reconstructed_pl": round(
                missing_pl,
                2,
            ),
        },
        "missing_trades": missing,
        "conflicting_trades": conflicting,
        "already_recorded": (
            matched_existing
        ),
        "unmatched_sells": (
            unmatched_sells
        ),
    }

@app.get("/auto-trader/broker-fills/canonical-trades")
def auto_trader_canonical_broker_trades(
    request: Request = None,
) -> dict[str, Any]:
    """
    Reconstruct one canonical bot trade per auto-entry BUY.

    Read-only. Does not modify trade_book.
    """

    if request is not None:
        require_app_session(request)

    fills = load_broker_fills(
        limit=10000
    )

    fills = sorted(
        fills,
        key=lambda item: (
            timestamp_sort_key(item.get("filled_at")),
            str(item.get("side", "")).upper() == "SELL",
        ),
    )

    trade_book_rows = load_trade_book(
        limit=5000
    )

    trade_book_by_entry_order_id = {
        str(
            row.get("entry_order_id") or ""
        ).strip(): row
        for row in trade_book_rows
        if str(
            row.get("entry_order_id") or ""
        ).strip()
    }

    trade_book_exit_order_ids: dict[
        int,
        set[str],
    ] = {}

    for row in trade_book_rows:
        trade_book_id = int(
            row["id"]
        )

        exit_ids: set[str] = set()

        legacy_exit_order_id = str(
            row.get("exit_order_id") or ""
        ).strip()

        if legacy_exit_order_id:
            exit_ids.add(
                legacy_exit_order_id
            )

        trade_book_exit_order_ids[
            trade_book_id
        ] = exit_ids

    try:
        all_order_links = (
            load_all_trade_book_order_links()
        )
    except Exception:
        all_order_links = []

    for link in all_order_links:
        if (
            str(
                link.get("order_role") or ""
            ).strip().upper()
            != "EXIT"
        ):
            continue

        linked_order_id = str(
            link.get("order_id") or ""
        ).strip()

        linked_trade_book_id = (
            safe_float(
                link.get("trade_book_id")
            )
        )

        if (
            not linked_order_id
            or linked_trade_book_id is None
            or linked_trade_book_id <= 0
        ):
            continue

        linked_trade_book_id = int(
            linked_trade_book_id
        )

        trade_book_exit_order_ids.setdefault(
            linked_trade_book_id,
            set(),
        ).add(
            linked_order_id
        )

    open_lots: dict[
        str,
        list[dict[str, Any]],
    ] = {}

    entries: dict[
        str,
        dict[str, Any],
    ] = {}

    unmatched_sells: list[
        dict[str, Any]
    ] = []

    for fill in fills:
        symbol = str(
            fill.get("symbol") or ""
        ).strip().upper()

        side = str(
            fill.get("side") or ""
        ).strip().upper()

        shares = safe_float(
            fill.get("shares")
        )

        price = safe_float(
            fill.get("price")
        )

        order_id = str(
            fill.get("order_id") or ""
        ).strip()

        if (
            not symbol
            or not order_id
            or shares is None
            or shares <= 0
            or price is None
            or price <= 0
        ):
            continue

        raw_order = fill.get(
            "raw_order"
        )

        if not isinstance(
            raw_order,
            dict,
        ):
            raw_order = {}

        client_order_id = str(
            raw_order.get(
                "client_order_id"
            )
            or ""
        ).strip()

        if side == "BUY":
            is_bot_entry = client_order_id.lower().startswith("auto-entry-")

            trade_book_entry = (
                trade_book_by_entry_order_id.get(
                    order_id
                )
            )

            broker_exit_order_ids: set[str] = set()

            bracket_legs = raw_order.get(
                "legs"
            )

            if isinstance(
                bracket_legs,
                list,
            ):
                for leg in bracket_legs:
                    if not isinstance(
                        leg,
                        dict,
                    ):
                        continue

                    leg_side = str(
                        leg.get("side")
                        or ""
                    ).strip().upper()

                    leg_order_id = str(
                        leg.get("id")
                        or ""
                    ).strip()

                    if (
                        leg_side == "SELL"
                        and leg_order_id
                    ):
                        broker_exit_order_ids.add(
                            leg_order_id
                        )

            linked_exit_order_ids: set[str] = set()

            if isinstance(
                trade_book_entry,
                dict,
            ):
                trade_book_id = int(
                    trade_book_entry["id"]
                )

                linked_exit_order_ids.update(
                    trade_book_exit_order_ids.get(
                        trade_book_id,
                        set(),
                    )
                )

            entry = {
                "symbol": symbol,
                "entry_order_id": order_id,
                "broker_exit_order_ids": (
                    broker_exit_order_ids
                ),
                "linked_exit_order_ids": (
                    linked_exit_order_ids
                ),
                "entry_client_order_id": (
                    client_order_id
                ),
                "is_bot_entry": is_bot_entry,
                "entry_timestamp": (
                    fill.get(
                        "filled_at"
                    )
                ),
                "entry_price": float(
                    price
                ),
                "entry_shares": float(
                    shares
                ),
                "remaining_shares": float(
                    shares
                ),
                "matched_shares": 0.0,
                "exit_value": 0.0,
                "realized_profit_loss": 0.0,
                "exit_order_ids": [],
                "exit_allocations": [],
                "last_exit_timestamp": None,
            }

            entries[
                order_id
            ] = entry

            open_lots.setdefault(
                symbol,
                [],
            ).append(
                entry
            )

            continue

        if side != "SELL":
            continue

        remaining_sell = float(
            shares
        )

        lots = open_lots.get(
            symbol,
            [],
        )

        while (
            remaining_sell > 0.00000001
            and lots
        ):
            broker_bracket_lot = next(
                (
                    candidate_lot
                    for candidate_lot in lots
                    if order_id
                    in (
                        candidate_lot.get(
                            "broker_exit_order_ids"
                        )
                        or set()
                    )
                ),
                None,
            )

            trade_book_linked_lot = next(
                (
                    candidate_lot
                    for candidate_lot in lots
                    if order_id
                    in (
                        candidate_lot.get(
                            "linked_exit_order_ids"
                        )
                        or set()
                    )
                ),
                None,
            )

            lot = (
                broker_bracket_lot
                if broker_bracket_lot is not None
                else (
                    trade_book_linked_lot
                    if trade_book_linked_lot is not None
                    else lots[0]
                )
            )

            lot_remaining = float(
                lot.get(
                    "remaining_shares"
                )
                or 0.0
            )

            if lot_remaining <= 0.00000001:
                lots.remove(lot)
                continue

            matched = min(
                remaining_sell,
                lot_remaining,
            )

            entry_price = float(
                lot["entry_price"]
            )

            exit_price = float(
                price
            )

            allocation_pl = (
                exit_price
                - entry_price
            ) * matched

            lot[
                "matched_shares"
            ] += matched

            lot[
                "remaining_shares"
            ] -= matched

            lot[
                "exit_value"
            ] += (
                exit_price
                * matched
            )

            lot[
                "realized_profit_loss"
            ] += allocation_pl

            lot[
                "last_exit_timestamp"
            ] = fill.get(
                "filled_at"
            )

            if (
                order_id
                not in lot[
                    "exit_order_ids"
                ]
            ):
                lot[
                    "exit_order_ids"
                ].append(
                    order_id
                )

            lot[
                "exit_allocations"
            ].append(
                {
                    "match_method": (
                        "broker_bracket_leg"
                        if broker_bracket_lot is not None
                        else (
                            "trade_book_order_link"
                            if trade_book_linked_lot is not None
                            else "fifo_fallback"
                        )
                    ),
                    "exit_order_id": (
                        order_id
                    ),
                    "shares": round(
                        matched,
                        8,
                    ),
                    "price": (
                        exit_price
                    ),
                    "timestamp": (
                        fill.get(
                            "filled_at"
                        )
                    ),
                }
            )

            remaining_sell -= (
                matched
            )

            if (
                lot[
                    "remaining_shares"
                ]
                <= 0.00000001
            ):
                lot[
                    "remaining_shares"
                ] = 0.0

                lots.remove(lot)

        if remaining_sell > 0.00000001:
            unmatched_sells.append(
                {
                    "order_id": (
                        order_id
                    ),
                    "symbol": symbol,
                    "shares": float(
                        shares
                    ),
                    "unmatched_shares": round(
                        remaining_sell,
                        8,
                    ),
                    "price": float(
                        price
                    ),
                    "filled_at": (
                        fill.get(
                            "filled_at"
                        )
                    ),
                }
            )

    canonical: list[
        dict[str, Any]
    ] = []

    for entry in entries.values():
        matched_shares = float(
            entry.get(
                "matched_shares"
            )
            or 0.0
        )

        entry_shares = float(
            entry.get(
                "entry_shares"
            )
            or 0.0
        )

        remaining_shares = max(
            0.0,
            float(
                entry.get(
                    "remaining_shares"
                )
                or 0.0
            ),
        )

        if matched_shares > 0:
            average_exit_price = (
                float(
                    entry.get(
                        "exit_value"
                    )
                    or 0.0
                )
                / matched_shares
            )

            realized_pl = float(
                entry.get(
                    "realized_profit_loss"
                )
                or 0.0
            )

            realized_return = (
                (
                    average_exit_price
                    - float(
                        entry[
                            "entry_price"
                        ]
                    )
                )
                / float(
                    entry[
                        "entry_price"
                    ]
                )
                * 100.0
            )

        else:
            average_exit_price = None
            realized_pl = None
            realized_return = None

        if remaining_shares <= 0.00000001:
            status = "CLOSED"
        else:
            status = "RESIDUAL"

        canonical.append(
            {
                "symbol": (
                    entry["symbol"]
                ),
                "status": status,
                "entry_order_id": (
                    entry[
                        "entry_order_id"
                    ]
                ),
                "entry_client_order_id": (
                    entry[
                        "entry_client_order_id"
                    ]
                ),
                "is_bot_entry": entry["is_bot_entry"],
                "entry_timestamp": (
                    entry[
                        "entry_timestamp"
                    ]
                ),
                "entry_price": round(
                    float(
                        entry[
                            "entry_price"
                        ]
                    ),
                    6,
                ),
                "entry_shares": round(
                    entry_shares,
                    8,
                ),
                "matched_shares": round(
                    matched_shares,
                    8,
                ),
                "remaining_shares": round(
                    remaining_shares,
                    8,
                ),
                "average_exit_price": (
                    round(
                        average_exit_price,
                        6,
                    )
                    if average_exit_price
                    is not None
                    else None
                ),
                "exit_timestamp": (
                    entry.get(
                        "last_exit_timestamp"
                    )
                ),
                "exit_order_ids": list(
                    entry.get(
                        "exit_order_ids"
                    )
                    or []
                ),
                "exit_allocations": list(
                    entry.get(
                        "exit_allocations"
                    )
                    or []
                ),
                "realized_profit_loss": (
                    round(realized_pl, 4) if realized_pl is not None else None
                ),
                "realized_return_percent": (
                    round(
                        realized_return,
                        6,
                    )
                    if realized_return
                    is not None
                    else None
                ),
            }
        )

    canonical.sort(
        key=lambda item: timestamp_sort_key(item.get("entry_timestamp")),
        reverse=True,
    )

    live_positions = fetch_alpaca_paper_positions()

    live_qty_by_symbol: dict[str, float] = {}

    for position in live_positions:
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

        qty = safe_float(
            position.get(
                "qty"
            )
        )

        if (
            not symbol
            or qty is None
            or qty <= 0
        ):
            continue

        live_qty_by_symbol[
            symbol
        ] = float(
            qty
        )

    residual_by_symbol: dict[
        str,
        list[dict[str, Any]],
    ] = {}

    for item in canonical:
        remaining = safe_float(
            item.get(
                "remaining_shares"
            )
        ) or 0.0

        if remaining <= 0.00000001:
            item[
                "broker_open_shares"
            ] = 0.0

            item[
                "unaccounted_closed_shares"
            ] = 0.0

            item[
                "pnl_complete"
            ] = True

            continue

        residual_by_symbol.setdefault(
            str(
                item.get(
                    "symbol"
                )
                or ""
            ),
            [],
        ).append(
            item
        )

    for symbol, items in residual_by_symbol.items():
        broker_remaining = float(
            live_qty_by_symbol.get(
                symbol,
                0.0,
            )
        )

        # FIFO selling leaves the newest lots open,
        # so allocate current broker shares newest first.
        items.sort(
            key=lambda item: timestamp_sort_key(item.get("entry_timestamp")),
            reverse=True,
        )

        for item in items:
            remaining = float(
                item.get(
                    "remaining_shares"
                )
                or 0.0
            )

            broker_open = min(
                remaining,
                broker_remaining,
            )

            broker_remaining -= (
                broker_open
            )

            historical_missing = max(
                0.0,
                remaining
                - broker_open,
            )

            item[
                "broker_open_shares"
            ] = round(
                broker_open,
                8,
            )

            item[
                "unaccounted_closed_shares"
            ] = round(
                historical_missing,
                8,
            )

            item[
                "pnl_complete"
            ] = (
                historical_missing
                <= 0.00000001
            )

            matched = float(
                item.get(
                    "matched_shares"
                )
                or 0.0
            )

            if (
                broker_open
                > 0.00000001
            ):
                if matched > 0.00000001:
                    item[
                        "status"
                    ] = "PARTIAL"
                else:
                    item[
                        "status"
                    ] = "OPEN"

            elif (
                historical_missing
                > 0.00000001
            ):
                item[
                    "status"
                ] = "CLOSED_INCOMPLETE"

            else:
                item[
                    "status"
                ] = "CLOSED"

    # Manual lots participate in matching and live-position reconciliation,
    # but never become bot trades or enter bot performance statistics.
    canonical = [item for item in canonical if item.pop("is_bot_entry")]

    closed = [
        item
        for item in canonical
        if item.get(
            "status"
        ) == "CLOSED"
    ]

    closed_incomplete = [
        item
        for item in canonical
        if item.get(
            "status"
        ) == "CLOSED_INCOMPLETE"
    ]

    partial = [
        item
        for item in canonical
        if item.get(
            "status"
        ) == "PARTIAL"
    ]

    opened = [
        item
        for item in canonical
        if item.get(
            "status"
        ) == "OPEN"
    ]

    realized_total = sum(
        float(
            item.get(
                "realized_profit_loss"
            )
            or 0.0
        )
        for item in canonical
    )

    return {
        "paper": True,
        "read_only": True,
        "source": "broker_fills_trade_book_link_then_fifo",
        "summary": {
            "ledger_limit_reached": len(fills) >= 10000,
            "ledger_last_fill_at": fills[-1].get("filled_at") if fills else None,
            "broker_fills": len(
                fills
            ),
            "bot_entry_orders": len(
                canonical
            ),
            "closed_trades": len(
                closed
            ),
            "closed_incomplete_trades": len(
                closed_incomplete
            ),
            "partial_trades": len(
                partial
            ),
            "open_trades": len(
                opened
            ),
            "live_position_symbols": len(
                live_qty_by_symbol
            ),
            "unmatched_sell_fills": len(
                unmatched_sells
            ),
            "realized_profit_loss": round(
                realized_total,
                2,
            ),
        },
        "trades": canonical,
        "unmatched_sells": (
            unmatched_sells
        ),
    }

@app.get("/auto-trader/broker-fills/pairing")
def auto_trader_broker_fill_pairing(
    request: Request,
) -> dict[str, Any]:
    """
    Reconstruct bot round trips from the persisted broker ledger.

    Diagnostic only. Does not modify trade_book.
    """

    require_app_session(request)

    fills = load_broker_fills(
        limit=10000
    )

    fills = sorted(
        fills,
        key=lambda item: str(
            item.get("filled_at") or ""
        ),
    )

    open_lots: dict[
        str,
        list[dict[str, Any]],
    ] = {}

    completed: list[
        dict[str, Any]
    ] = []

    unmatched_sells: list[
        dict[str, Any]
    ] = []

    ignored_buys: list[
        dict[str, Any]
    ] = []

    for fill in fills:
        symbol = str(
            fill.get("symbol") or ""
        ).strip().upper()

        side = str(
            fill.get("side") or ""
        ).strip().upper()

        shares = safe_float(
            fill.get("shares")
        )

        price = safe_float(
            fill.get("price")
        )

        if (
            not symbol
            or shares is None
            or shares <= 0
            or price is None
            or price <= 0
        ):
            continue

        raw_order = fill.get(
            "raw_order"
        )

        if not isinstance(
            raw_order,
            dict,
        ):
            raw_order = {}

        client_order_id = str(
            raw_order.get(
                "client_order_id"
            )
            or ""
        ).strip()

        if side == "BUY":
            is_auto_entry = (
                client_order_id
                .lower()
                .startswith(
                    "auto-entry-"
                )
            )

            if not is_auto_entry:
                ignored_buys.append(
                    {
                        "order_id": fill.get(
                            "order_id"
                        ),
                        "symbol": symbol,
                        "shares": shares,
                        "price": price,
                        "filled_at": fill.get(
                            "filled_at"
                        ),
                        "client_order_id": (
                            client_order_id
                        ),
                        "reason": (
                            "BUY was not identified "
                            "as an auto-entry order."
                        ),
                    }
                )
                continue

            open_lots.setdefault(
                symbol,
                [],
            ).append(
                {
                    "order_id": fill.get(
                        "order_id"
                    ),
                    "symbol": symbol,
                    "remaining_shares": (
                        shares
                    ),
                    "original_shares": (
                        shares
                    ),
                    "price": price,
                    "filled_at": fill.get(
                        "filled_at"
                    ),
                    "client_order_id": (
                        client_order_id
                    ),
                }
            )

            continue

        if side != "SELL":
            continue

        remaining_sell = shares

        symbol_lots = (
            open_lots.get(
                symbol,
                [],
            )
        )

        while (
            remaining_sell > 0
            and symbol_lots
        ):
            lot = symbol_lots[0]

            lot_remaining = (
                safe_float(
                    lot.get(
                        "remaining_shares"
                    )
                )
                or 0.0
            )

            if lot_remaining <= 0:
                symbol_lots.pop(0)
                continue

            matched_shares = min(
                remaining_sell,
                lot_remaining,
            )

            entry_price = float(
                lot["price"]
            )

            exit_price = float(
                price
            )

            realized_pl = (
                exit_price
                - entry_price
            ) * matched_shares

            realized_return = (
                (
                    exit_price
                    - entry_price
                )
                / entry_price
                * 100.0
            )

            completed.append(
                {
                    "symbol": symbol,
                    "shares": round(
                        matched_shares,
                        8,
                    ),
                    "entry_order_id": (
                        lot.get(
                            "order_id"
                        )
                    ),
                    "exit_order_id": (
                        fill.get(
                            "order_id"
                        )
                    ),
                    "entry_price": (
                        entry_price
                    ),
                    "exit_price": (
                        exit_price
                    ),
                    "entry_timestamp": (
                        lot.get(
                            "filled_at"
                        )
                    ),
                    "exit_timestamp": (
                        fill.get(
                            "filled_at"
                        )
                    ),
                    "realized_profit_loss": (
                        round(
                            realized_pl,
                            4,
                        )
                    ),
                    "realized_return_percent": (
                        round(
                            realized_return,
                            6,
                        )
                    ),
                    "entry_client_order_id": (
                        lot.get(
                            "client_order_id"
                        )
                    ),
                    "exit_client_order_id": (
                        client_order_id
                    ),
                    "exit_position_intent": (
                        raw_order.get(
                            "position_intent"
                        )
                    ),
                    "exit_order_class": (
                        raw_order.get(
                            "order_class"
                        )
                    ),
                    "exit_order_type": (
                        raw_order.get(
                            "order_type"
                        )
                        or raw_order.get(
                            "type"
                        )
                    ),
                }
            )

            lot[
                "remaining_shares"
            ] = (
                lot_remaining
                - matched_shares
            )

            remaining_sell -= (
                matched_shares
            )

            if (
                lot[
                    "remaining_shares"
                ]
                <= 0.00000001
            ):
                symbol_lots.pop(0)

        if remaining_sell > 0.00000001:
            unmatched_sells.append(
                {
                    "order_id": fill.get(
                        "order_id"
                    ),
                    "symbol": symbol,
                    "shares": shares,
                    "unmatched_shares": round(
                        remaining_sell,
                        8,
                    ),
                    "price": price,
                    "filled_at": fill.get(
                        "filled_at"
                    ),
                    "client_order_id": (
                        client_order_id
                    ),
                    "position_intent": (
                        raw_order.get(
                            "position_intent"
                        )
                    ),
                }
            )

    open_positions: list[
        dict[str, Any]
    ] = []

    for symbol, lots in open_lots.items():
        for lot in lots:
            remaining = safe_float(
                lot.get(
                    "remaining_shares"
                )
            )

            if (
                remaining is None
                or remaining
                <= 0.00000001
            ):
                continue

            open_positions.append(
                {
                    "symbol": symbol,
                    "entry_order_id": (
                        lot.get(
                            "order_id"
                        )
                    ),
                    "remaining_shares": (
                        remaining
                    ),
                    "entry_price": (
                        lot.get(
                            "price"
                        )
                    ),
                    "entry_timestamp": (
                        lot.get(
                            "filled_at"
                        )
                    ),
                    "client_order_id": (
                        lot.get(
                            "client_order_id"
                        )
                    ),
                }
            )

    total_realized = sum(
        float(
            trade.get(
                "realized_profit_loss"
            )
            or 0.0
        )
        for trade in completed
    )

    return {
        "paper": True,
        "read_only_trade_book": True,
        "method": (
            "FIFO using auto-entry BUY "
            "orders as bot entry lots"
        ),
        "summary": {
            "ledger_fills": len(
                fills
            ),
            "completed_matches": len(
                completed
            ),
            "open_bot_lots": len(
                open_positions
            ),
            "unmatched_sell_fills": len(
                unmatched_sells
            ),
            "ignored_non_auto_buys": len(
                ignored_buys
            ),
            "reconstructed_realized_pl": (
                round(
                    total_realized,
                    2,
                )
            ),
        },
        "completed_trades": completed,
        "open_positions": open_positions,
        "unmatched_sells": (
            unmatched_sells
        ),
        "ignored_buys": ignored_buys,
    }

@app.get("/auto-trader/broker-fills")
def auto_trader_broker_fills(
    request: Request,
    date: str | None = Query(
        default=None,
    ),
    limit: int = Query(
        default=5000,
        ge=1,
        le=10000,
    ),
) -> dict[str, Any]:
    """Read persisted Alpaca PAPER broker fills."""

    require_app_session(request)

    target_date = None

    if date is not None:
        try:
            target_date = datetime.strptime(
                date,
                "%Y-%m-%d",
            ).date()
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail=(
                    "date must use YYYY-MM-DD format."
                ),
            ) from error

    rows = load_broker_fills(
        limit=limit
    )

    eastern = ZoneInfo(
        "America/New_York"
    )

    results: list[
        dict[str, Any]
    ] = []

    for row in rows:
        item = dict(row)

        filled_at = str(
            item.get("filled_at") or ""
        ).strip()

        try:
            parsed = datetime.fromisoformat(
                filled_at.replace(
                    "Z",
                    "+00:00",
                )
            )

            eastern_time = (
                parsed.astimezone(
                    eastern
                )
            )

            eastern_date = (
                eastern_time.date()
            )

            item[
                "filled_at_eastern"
            ] = eastern_time.isoformat()

        except (
            TypeError,
            ValueError,
        ):
            eastern_date = None

            item[
                "filled_at_eastern"
            ] = None

        if (
            target_date is not None
            and eastern_date
            != target_date
        ):
            continue

        results.append(
            item
        )

    buy_count = sum(
        1
        for row in results
        if row.get("side") == "BUY"
    )

    sell_count = sum(
        1
        for row in results
        if row.get("side") == "SELL"
    )

    return {
        "paper": True,
        "source": "broker_fills",
        "date": (
            target_date.isoformat()
            if target_date is not None
            else None
        ),
        "count": len(results),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "fills": results,
    }


@app.get("/auto-trader/scheduler-state")
def auto_trader_scheduler_state(
    request: Request,
) -> dict[str, Any]:
    """
    Read-only view of persisted scheduler state.
    """

    require_app_session(request)

    return {
        "paper": True,
        "read_only": True,
        "weekly_report_key": get_scheduler_state(
            "trade_report_last_weekly_key"
        ),
        "monthly_report_key": get_scheduler_state(
            "trade_report_last_monthly_key"
        ),
        "broker_fill_backup_state": get_scheduler_state(
            "broker_fill_backup_state",
            {},
        ),
        "broker_fill_coverage_alert_state": get_scheduler_state(
            "broker_fill_coverage_alert_state",
            {},
        ),
    }


@app.get("/auto-trader/broker-order/{order_id}")
def auto_trader_broker_order(
    request: Request,
    order_id: str,
) -> dict[str, Any]:
    """
    Read-only diagnostic for one Alpaca PAPER order.
    """

    require_app_session(
        request
    )

    order = get_alpaca_paper_order(
        order_id
    )

    fields = (
        "id",
        "client_order_id",
        "symbol",
        "side",
        "type",
        "order_type",
        "order_class",
        "time_in_force",
        "status",
        "qty",
        "filled_qty",
        "filled_avg_price",
        "limit_price",
        "stop_price",
        "created_at",
        "submitted_at",
        "updated_at",
        "filled_at",
        "expired_at",
        "canceled_at",
        "failed_at",
        "replaced_at",
        "replaced_by",
        "replaces",
    )

    return {
        "success": True,
        "paper": True,
        "order": {
            field: order.get(field)
            for field in fields
        },
    }


@app.get("/auto-trader/broker-fills/backup-status")
def auto_trader_broker_fill_backup_status(
    request: Request,
) -> dict[str, Any]:
    """
    Read-only status for the automatic
    broker-fill recovery sync.
    """

    require_app_session(
        request
    )

    result = (
        _broker_fill_backup_last_result
        if isinstance(
            _broker_fill_backup_last_result,
            dict,
        )
        else {}
    )

    healthy = (
        _broker_fill_backup_last_success_at
        is not None
        and _broker_fill_backup_last_error
        is None
    )

    return {
        "paper": True,
        "read_only": True,
        "enabled": True,
        "interval_minutes": 15,
        "status": (
            "HEALTHY"
            if healthy
            else (
                "ERROR"
                if _broker_fill_backup_last_error
                else "WAITING"
            )
        ),
        "last_sync_key": (
            _broker_fill_backup_last_key
        ),
        "last_success_at": (
            _broker_fill_backup_last_success_at
        ),
        "last_error": (
            _broker_fill_backup_last_error
        ),
        "orders_examined": result.get(
            "orders_examined",
            result.get(
                "examined"
            ),
        ),
        "filled_orders_seen": result.get(
            "filled_orders_seen",
            result.get(
                "filled"
            ),
        ),
        "fills_saved_or_refreshed": result.get(
            "fills_saved_or_refreshed",
            result.get(
                "saved"
            ),
        ),
        "sync_errors": result.get(
            "errors",
            [],
        ),
    }


def audit_broker_fill_coverage(
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """
    Compare recent filled Alpaca PAPER orders
    against the persistent broker_fills ledger.

    Internal read-only helper.
    """

    safe_limit = max(
        1,
        min(
            int(limit),
            500,
        ),
    )

    raw_orders = (
        fetch_alpaca_paper_trade_history(
            limit=safe_limit
        )
    )

    filled_orders: list[
        dict[str, Any]
    ] = []

    for order in raw_orders:
        if not isinstance(
            order,
            dict,
        ):
            continue

        status = str(
            order.get("status")
            or ""
        ).strip().lower()

        if status != "filled":
            continue

        order_id = str(
            order.get("id")
            or ""
        ).strip()

        symbol = clean_symbol(
            order.get("symbol")
        )

        side = str(
            order.get("side")
            or ""
        ).strip().upper()

        filled_qty = safe_float(
            order.get("filled_qty")
        )

        filled_price = safe_float(
            order.get(
                "filled_avg_price"
            )
        )

        filled_at = str(
            order.get("filled_at")
            or ""
        ).strip()

        if (
            not order_id
            or not symbol
            or side not in {
                "BUY",
                "SELL",
            }
            or filled_qty is None
            or filled_qty <= 0
            or filled_price is None
            or filled_price <= 0
            or not filled_at
        ):
            continue

        ledger_row = get_broker_fill(
            order_id
        )

        present = isinstance(
            ledger_row,
            dict,
        )

        filled_orders.append(
            {
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "shares": filled_qty,
                "price": filled_price,
                "filled_at": filled_at,
                "present_in_broker_fills": (
                    present
                ),
                "ledger_source": (
                    ledger_row.get(
                        "source"
                    )
                    if present
                    else None
                ),
                "first_seen_at": (
                    ledger_row.get(
                        "first_seen_at"
                    )
                    if present
                    else None
                ),
                "last_seen_at": (
                    ledger_row.get(
                        "last_seen_at"
                    )
                    if present
                    else None
                ),
            }
        )

    present_count = sum(
        1
        for item in filled_orders
        if item[
            "present_in_broker_fills"
        ]
    )

    missing_count = (
        len(filled_orders)
        - present_count
    )

    coverage_percent = (
        (
            present_count
            / len(filled_orders)
        )
        * 100
        if filled_orders
        else 100.0
    )

    immediate_count = sum(
        1
        for item in filled_orders
        if item.get(
            "ledger_source"
        )
        == "alpaca_paper_immediate"
    )

    sync_count = sum(
        1
        for item in filled_orders
        if item.get(
            "ledger_source"
        )
        == "alpaca_paper"
    )

    return {
        "paper": True,
        "read_only": True,
        "orders_requested": safe_limit,
        "filled_orders_checked": (
            len(filled_orders)
        ),
        "present_in_broker_fills": (
            present_count
        ),
        "missing_from_broker_fills": (
            missing_count
        ),
        "coverage_percent": round(
            coverage_percent,
            2,
        ),
        "immediate_source_count": (
            immediate_count
        ),
        "sync_source_count": (
            sync_count
        ),
        "all_fills_persisted": (
            missing_count == 0
        ),
        "fills": filled_orders,
    }


def check_broker_fill_coverage_and_alert(
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """
    Audit recent Alpaca fills.

    If anything is missing:
    1. Run another recovery sync.
    2. Audit again.
    3. Email only if coverage is still incomplete.

    Alert state is stored in SQLite so Railway
    restarts do not resend the same incident.
    """

    safe_limit = max(
        1,
        min(
            int(limit),
            500,
        ),
    )

    before = audit_broker_fill_coverage(
        limit=safe_limit
    )

    missing_before = int(
        before.get(
            "missing_from_broker_fills",
            0,
        )
        or 0
    )

    recovery_sync: dict[str, Any] | None = None

    after = before

    if missing_before > 0:
        recovery_sync = (
            sync_alpaca_broker_fills(
                limit=500
            )
        )

        after = audit_broker_fill_coverage(
            limit=safe_limit
        )

    checked = int(
        after.get(
            "filled_orders_checked",
            0,
        )
        or 0
    )

    missing_rows = [
        row
        for row in (
            after.get("fills")
            or []
        )
        if isinstance(
            row,
            dict,
        )
        and not row.get(
            "present_in_broker_fills"
        )
    ]

    missing_after = len(
        missing_rows
    )

    missing_order_ids = sorted(
        str(
            row.get("order_id")
            or ""
        ).strip()
        for row in missing_rows
        if str(
            row.get("order_id")
            or ""
        ).strip()
    )

    fingerprint = "|".join(
        missing_order_ids
    )

    state = get_scheduler_state(
        "broker_fill_coverage_alert_state",
        {},
    )

    if not isinstance(
        state,
        dict,
    ):
        state = {}

    alert_active = bool(
        state.get("active")
    )

    previous_fingerprint = str(
        state.get("fingerprint")
        or ""
    )

    now_utc = datetime.now(
        timezone.utc
    ).isoformat()

    alert_email_sent = False
    recovery_email_sent = False
    alert_suppressed = False

    # ------------------------------------------------
    # Missing fills remain AFTER recovery.
    # ------------------------------------------------

    if (
        checked > 0
        and missing_after > 0
    ):
        same_incident = (
            alert_active
            and fingerprint
            == previous_fingerprint
        )

        if same_incident:
            alert_suppressed = True

        else:
            detail_lines: list[str] = []

            for row in missing_rows[:10]:
                detail_lines.append(
                    (
                        f"- {row.get('symbol') or 'UNKNOWN'} "
                        f"{row.get('side') or ''} "
                        f"{row.get('shares') or ''} "
                        f"@ {row.get('price') or ''} "
                        f"| order "
                        f"{row.get('order_id') or 'UNKNOWN'}"
                    )
                )

            if (
                len(missing_rows)
                > 10
            ):
                detail_lines.append(
                    (
                        f"- plus "
                        f"{len(missing_rows) - 10} "
                        "additional missing fills"
                    )
                )

            message = "\n".join(
                [
                    (
                        "AI Paper Trader detected "
                        "broker-fill ledger coverage "
                        "below 100%."
                    ),
                    "",
                    (
                        f"Recent filled orders checked: "
                        f"{checked}"
                    ),
                    (
                        f"Missing after automatic "
                        f"recovery: {missing_after}"
                    ),
                    (
                        f"Coverage: "
                        f"{after.get('coverage_percent')}%"
                    ),
                    "",
                    "Missing fills:",
                    *detail_lines,
                    "",
                    (
                        "Trading was not changed. "
                        "This alert concerns execution "
                        "record persistence only."
                    ),
                ]
            )

            alert_email_sent = (
                send_health_alert_email(
                    (
                        "[AI Paper Trader] "
                        "Broker-fill coverage alert"
                    ),
                    message,
                )
            )

            # Only mark the incident as alerted after
            # email delivery succeeds. A failed email
            # will therefore retry next cycle.
            if alert_email_sent:
                set_scheduler_state(
                    "broker_fill_coverage_alert_state",
                    {
                        "active": True,
                        "fingerprint": fingerprint,
                        "missing_order_ids": (
                            missing_order_ids
                        ),
                        "missing_count": (
                            missing_after
                        ),
                        "coverage_percent": (
                            after.get(
                                "coverage_percent"
                            )
                        ),
                        "alerted_at": now_utc,
                        "recovered_at": None,
                    },
                )

    # ------------------------------------------------
    # Coverage recovered after an active incident.
    #
    # Do NOT call zero checked orders a recovery.
    # ------------------------------------------------

    elif (
        checked > 0
        and missing_after == 0
        and alert_active
    ):
        recovery_email_sent = (
            send_health_alert_email(
                (
                    "[AI Paper Trader] "
                    "Broker-fill coverage recovered"
                ),
                "\n".join(
                    [
                        (
                            "Broker-fill ledger coverage "
                            "has returned to 100%."
                        ),
                        "",
                        (
                            f"Recent filled orders checked: "
                            f"{checked}"
                        ),
                        (
                            f"Coverage: "
                            f"{after.get('coverage_percent')}%"
                        ),
                        "",
                        (
                            "The previous broker-fill "
                            "persistence incident is now "
                            "resolved."
                        ),
                    ]
                ),
            )
        )

        # Keep incident active if the recovery email
        # fails so the notification can retry later.
        if recovery_email_sent:
            set_scheduler_state(
                "broker_fill_coverage_alert_state",
                {
                    "active": False,
                    "fingerprint": "",
                    "missing_order_ids": [],
                    "missing_count": 0,
                    "coverage_percent": (
                        after.get(
                            "coverage_percent"
                        )
                    ),
                    "alerted_at": (
                        state.get(
                            "alerted_at"
                        )
                    ),
                    "recovered_at": now_utc,
                },
            )

    return {
        "paper": True,
        "checked": checked,
        "missing_before_recovery": (
            missing_before
        ),
        "missing_after_recovery": (
            missing_after
        ),
        "coverage_percent": (
            after.get(
                "coverage_percent"
            )
        ),
        "all_fills_persisted": (
            missing_after == 0
        ),
        "recovery_sync_ran": (
            recovery_sync is not None
        ),
        "recovery_sync": recovery_sync,
        "alert_active_before_check": (
            alert_active
        ),
        "alert_email_sent": (
            alert_email_sent
        ),
        "alert_suppressed": (
            alert_suppressed
        ),
        "recovery_email_sent": (
            recovery_email_sent
        ),
        "missing_fills": missing_rows,
    }


@app.get("/auto-trader/broker-fills/audit")
def auto_trader_broker_fills_audit(
    request: Request,
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
    ),
) -> dict[str, Any]:
    """
    Compare recent filled Alpaca PAPER orders
    against the persistent broker_fills ledger.

    Read-only. Does not modify trading state
    or database contents.
    """

    require_app_session(
        request
    )

    return audit_broker_fill_coverage(
        limit=limit
    )


@app.post("/auto-trader/broker-fills/coverage-check")
def auto_trader_broker_fill_coverage_check(
    request: Request,
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
    ),
) -> dict[str, Any]:
    """
    Manually run the same broker-fill
    recovery + alert check used by the scheduler.
    """

    require_app_session(
        request
    )

    return check_broker_fill_coverage_and_alert(
        limit=limit
    )


@app.post("/auto-trader/broker-fills/sync")
def sync_auto_trader_broker_fills(
    request: Request,
    limit: int = Query(
        default=500,
        ge=1,
        le=5000,
    ),
) -> dict[str, Any]:
    """Persist recent Alpaca PAPER fills into SQLite."""

    require_app_session(request)

    return sync_alpaca_broker_fills(
        limit=limit
    )

@app.get("/auto-trader/reconciliation")
def auto_trader_reconciliation(
    request: Request,
    date: str = Query(...),
) -> dict[str, Any]:
    require_app_session(request)

    eastern = ZoneInfo("America/New_York")

    try:
        target_date = datetime.strptime(
            date,
            "%Y-%m-%d",
        ).date()
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail="date must use YYYY-MM-DD format",
        ) from error

    raw_orders = fetch_alpaca_paper_trade_history(
        limit=500
    )

    fills: list[dict[str, Any]] = []

    for order in raw_orders:
        trade = normalize_alpaca_order_for_history(
            order
        )

        if trade is None:
            continue

        timestamp = str(
            trade.get("timestamp") or ""
        ).strip()

        if not timestamp:
            continue

        try:
            fill_dt = datetime.fromisoformat(
                timestamp.replace(
                    "Z",
                    "+00:00",
                )
            )

            if fill_dt.tzinfo is None:
                fill_dt = fill_dt.replace(
                    tzinfo=timezone.utc
                )

            fill_eastern = (
                fill_dt.astimezone(eastern)
            )

        except Exception:
            continue

        if fill_eastern.date() != target_date:
            continue

        fills.append(
            {
                "order_id": str(
                    trade.get("id") or ""
                ),
                "symbol": clean_symbol(
                    trade.get("symbol")
                ),
                "side": str(
                    trade.get("side") or ""
                ).upper(),
                "shares": trade.get("shares"),
                "price": trade.get("price"),
                "filled_at": timestamp,
                "filled_at_eastern": (
                    fill_eastern.isoformat()
                ),
                "client_order_id": str(
                    order.get("client_order_id")
                    or ""
                ).strip(),
                "order_class": str(
                    order.get("order_class")
                    or ""
                ).strip(),
                "type": str(
                    order.get("type")
                    or ""
                ).strip(),
                "auto_entry": str(
                    order.get("client_order_id")
                    or ""
                ).strip().lower().startswith(
                    "auto-entry-"
                ),
            }
        )

    trade_book_rows = load_trade_book(
        limit=5000
    )

    entry_ids = {
        str(
            row.get("entry_order_id") or ""
        )
        for row in trade_book_rows
        if row.get("entry_order_id")
    }

    exit_ids = {
        str(
            row.get("exit_order_id") or ""
        )
        for row in trade_book_rows
        if row.get("exit_order_id")
    }

    known_ids = entry_ids | exit_ids

    matched: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    for fill in fills:
        order_id = fill["order_id"]
        client_order_id = str(
            fill.get("client_order_id")
            or ""
        ).strip()

        linked_trade = None

        if order_id or client_order_id:
            linked_trade = (
                load_trade_book_by_order_link(
                    order_id=(
                        order_id
                        or None
                    ),
                    client_order_id=(
                        client_order_id
                        or None
                    ),
                )
            )

        if linked_trade is not None:
            matched.append(fill)
        elif order_id and order_id in known_ids:
            matched.append(fill)
        else:
            missing.append(fill)

    buy_fills = [
        fill
        for fill in fills
        if fill["side"] == "BUY"
    ]

    sell_fills = [
        fill
        for fill in fills
        if fill["side"] == "SELL"
    ]

    missing_buys = [
        fill
        for fill in missing
        if fill["side"] == "BUY"
    ]

    missing_sells = [
        fill
        for fill in missing
        if fill["side"] == "SELL"
    ]

    return {
        "paper": True,
        "read_only": True,
        "date": target_date.isoformat(),
        "source": (
            "alpaca_filled_orders"
            "+sqlite_trade_book"
        ),
        "summary": {
            "alpaca_filled_orders": len(fills),
            "alpaca_buy_fills": len(buy_fills),
            "alpaca_sell_fills": len(sell_fills),
            "matched_to_trade_book": len(matched),
            "missing_from_trade_book": len(missing),
            "missing_buy_fills": len(missing_buys),
            "missing_sell_fills": len(missing_sells),
        },
        "missing_fills": missing,
        "matched_fills": matched,
    }

@app.get("/auto-trader/history")
def auto_trader_history(
    request: Request = None,
    limit: int = Query(
        default=200,
        ge=1,
        le=5000,
    ),
) -> dict[str, Any]:
    if request is not None:
        require_app_session(
            request
        )

    canonical_payload = (
        auto_trader_canonical_broker_trades(
            request=request
        )
    )

    canonical_trades = (
        canonical_payload.get(
            "trades",
            [],
        )
    )

    if not isinstance(
        canonical_trades,
        list,
    ):
        canonical_trades = []

    # Trade Journal/history represents positions that
    # are no longer open at Alpaca.
    closed_canonical = [
        trade
        for trade in canonical_trades
        if (
            isinstance(
                trade,
                dict,
            )
            and trade.get(
                "status"
            )
            in {
                "CLOSED",
                "CLOSED_INCOMPLETE",
            }
        )
    ]

    closed_canonical.sort(
        key=lambda trade: str(
            trade.get(
                "exit_timestamp"
            )
            or trade.get(
                "entry_timestamp"
            )
            or ""
        ),
        reverse=True,
    )

    report_summary = summarize_trades(
        canonical_trades, ledger_summary=canonical_payload.get("summary"),
    )
    daily_summaries = daily_realized_summaries(canonical_trades)
    closed_canonical = closed_canonical[:limit]

    legacy_trades = load_trade_book(
        limit=5000,
    )

    learning_outcomes = (
        load_learning_outcomes(
            limit=10000,
        )
    )

    entry_events = (
        load_trade_book_events(
            event="entry",
            limit=5000,
        )
    )

    legacy_by_entry_order_id: dict[
        str,
        dict[str, Any],
    ] = {}

    for trade in legacy_trades:
        entry_order_id = str(
            trade.get(
                "entry_order_id"
            )
            or ""
        ).strip()

        if entry_order_id:
            legacy_by_entry_order_id[
                entry_order_id
            ] = trade

    learning_by_trade_id: dict[
        int,
        dict[str, Any],
    ] = {}

    for outcome in learning_outcomes:
        trade_book_id = outcome.get(
            "trade_book_id"
        )

        if isinstance(
            trade_book_id,
            int,
        ):
            learning_by_trade_id[
                trade_book_id
            ] = outcome

    entry_by_trade_id: dict[
        int,
        dict[str, Any],
    ] = {}

    for event in entry_events:
        trade_book_id = event.get(
            "trade_book_id"
        )

        details = event.get(
            "details"
        )

        if (
            isinstance(
                trade_book_id,
                int,
            )
            and isinstance(
                details,
                dict,
            )
            and trade_book_id
            not in entry_by_trade_id
        ):
            entry_by_trade_id[
                trade_book_id
            ] = details

    history: list[
        dict[str, Any]
    ] = []

    wins = 0
    losses = 0
    breakeven = 0

    total_profit_loss = 0.0

    return_values: list[
        float
    ] = []

    complete_count = 0
    incomplete_count = 0

    for canonical in closed_canonical:
        entry_order_id = str(
            canonical.get(
                "entry_order_id"
            )
            or ""
        ).strip()

        exit_order_ids = [
            str(order_id).strip()
            for order_id
            in (
                canonical.get(
                    "exit_order_ids"
                )
                or []
            )
            if str(
                order_id
            ).strip()
        ]

        legacy_trade = (
            legacy_by_entry_order_id.get(
                entry_order_id
            )
        )

        legacy_trade_id = (
            legacy_trade.get("id")
            if isinstance(
                legacy_trade,
                dict,
            )
            else None
        )

        entry_details = (
            entry_by_trade_id.get(
                legacy_trade_id
            )
            if isinstance(
                legacy_trade_id,
                int,
            )
            else None
        )

        legacy_exit_order_id = str(
            (
                legacy_trade.get(
                    "exit_order_id"
                )
                if isinstance(
                    legacy_trade,
                    dict,
                )
                else ""
            )
            or ""
        ).strip()

        legacy_exit_consistent = (
            bool(
                legacy_exit_order_id
            )
            and legacy_exit_order_id
            in exit_order_ids
        )

        learning_outcome = (
            learning_by_trade_id.get(
                legacy_trade_id
            )
            if (
                isinstance(
                    legacy_trade_id,
                    int,
                )
                and legacy_exit_consistent
            )
            else None
        )

        realized_profit_loss = (
            canonical.get(
                "realized_profit_loss"
            )
        )

        realized_return_percent = (
            canonical.get(
                "realized_return_percent"
            )
        )

        pnl_complete = bool(
            canonical.get(
                "pnl_complete"
            )
        )

        if pnl_complete:
            complete_count += 1
        else:
            incomplete_count += 1

        if isinstance(
            realized_profit_loss,
            (int, float),
        ):
            profit_loss = float(
                realized_profit_loss
            )

            total_profit_loss += (
                profit_loss
            )

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

        average_exit_price = (
            canonical.get(
                "average_exit_price"
            )
        )

        history.append(
            {
                "id": (
                    legacy_trade_id
                    if isinstance(
                        legacy_trade_id,
                        int,
                    )
                    else (
                        "broker:"
                        + entry_order_id
                    )
                ),
                "symbol": canonical.get(
                    "symbol"
                ),
                "status": canonical.get(
                    "status"
                ),
                "shares": canonical.get(
                    "entry_shares"
                ),
                "entry_shares": canonical.get(
                    "entry_shares"
                ),
                "matched_shares": canonical.get(
                    "matched_shares"
                ),
                "remaining_shares": canonical.get(
                    "remaining_shares"
                ),
                "broker_open_shares": canonical.get(
                    "broker_open_shares"
                ),
                "unaccounted_closed_shares": (
                    canonical.get(
                        "unaccounted_closed_shares"
                    )
                ),
                "pnl_complete": pnl_complete,
                "exit_allocations": canonical.get("exit_allocations") or [],
                "entry_price": canonical.get(
                    "entry_price"
                ),
                "exit_price": (
                    average_exit_price
                ),
                "average_exit_price": (
                    average_exit_price
                ),
                "realized_profit_loss": (
                    realized_profit_loss
                ),
                "realized_return_percent": (
                    realized_return_percent
                ),
                "entry_timestamp": canonical.get(
                    "entry_timestamp"
                ),
                "exit_timestamp": canonical.get(
                    "exit_timestamp"
                ),
                "entry_reason": (
                    legacy_trade.get(
                        "entry_reason"
                    )
                    if isinstance(
                        legacy_trade,
                        dict,
                    )
                    else "auto_trader_entry"
                ),
                "exit_reason": (
                    legacy_trade.get(
                        "exit_reason"
                    )
                    if (
                        isinstance(
                            legacy_trade,
                            dict,
                        )
                        and legacy_exit_consistent
                    )
                    else "broker_sell_fill"
                ),
                "strategy": (
                    legacy_trade.get(
                        "strategy"
                    )
                    if isinstance(
                        legacy_trade,
                        dict,
                    )
                    else "auto_trader"
                ),
                "entry_order_id": (
                    entry_order_id
                ),
                "exit_order_id": (
                    exit_order_ids[-1]
                    if len(
                        exit_order_ids
                    ) == 1
                    else None
                ),
                "exit_order_ids": (
                    exit_order_ids
                ),
                "exit_allocations": (
                    canonical.get(
                        "exit_allocations"
                    )
                    or []
                ),
                "entry_diagnostics": (
                    entry_details
                    if isinstance(
                        entry_details,
                        dict,
                    )
                    else None
                ),
                "learning": (
                    {
                        "entry_score": (
                            learning_outcome.get(
                                "entry_score"
                            )
                        ),
                        "entry_confidence": (
                            learning_outcome.get(
                                "entry_confidence"
                            )
                        ),
                        "entry_signal": (
                            learning_outcome.get(
                                "entry_signal"
                            )
                        ),
                        "scanner_rank": (
                            learning_outcome.get(
                                "scanner_rank"
                            )
                        ),
                        "spread_percent": (
                            learning_outcome.get(
                                "spread_percent"
                            )
                        ),
                        "stop_loss_percent": (
                            learning_outcome.get(
                                "stop_loss_percent"
                            )
                        ),
                        "take_profit_percent": (
                            learning_outcome.get(
                                "take_profit_percent"
                            )
                        ),
                        "mfe_percent": (
                            learning_outcome.get(
                                "mfe_percent"
                            )
                        ),
                        "mae_percent": (
                            learning_outcome.get(
                                "mae_percent"
                            )
                        ),
                        "excursion_max_price": (
                            learning_outcome.get(
                                "excursion_max_price"
                            )
                        ),
                        "excursion_min_price": (
                            learning_outcome.get(
                                "excursion_min_price"
                            )
                        ),
                        "excursion_observation_count": (
                            learning_outcome.get(
                                "excursion_observation_count"
                            )
                        ),
                        "holding_seconds": (
                            learning_outcome.get(
                                "holding_seconds"
                            )
                            if learning_outcome.get(
                                "holding_seconds"
                            )
                            is not None
                            else calculate_holding_seconds(
                                canonical.get(
                                    "entry_timestamp"
                                ),
                                canonical.get(
                                    "exit_timestamp"
                                ),
                            )
                        ),
                        "won": (
                            learning_outcome.get(
                                "won"
                            )
                        ),
                        "exit_reason": (
                            learning_outcome.get(
                                "exit_reason"
                            )
                        ),
                        "metadata": (
                            learning_outcome.get(
                                "metadata"
                            )
                        ),
                    }
                    if isinstance(
                        learning_outcome,
                        dict,
                    )
                    else None
                ),
            }
        )

    completed = len(
        history
    )

    win_rate_percent = (
        (wins / completed)
        * 100.0
        if completed > 0
        else 0.0
    )

    average_return_percent = (
        sum(return_values)
        / len(return_values)
        if return_values
        else 0.0
    )

    learning_summary = (
        calculate_learning_summary()
    )

    canonical_summary = (
        canonical_payload.get(
            "summary",
            {}
        )
    )

    if not isinstance(
        canonical_summary,
        dict,
    ):
        canonical_summary = {}

    return {
        "paper": True,
        "read_only": True,
        "source": (
            "alpaca_broker_fills_canonical"
            "+trade_book_entry_diagnostics"
            "+validated_learning_outcomes"
        ),
        "count": completed,
        "summary": {
            **report_summary,
            "completed_trades": report_summary["completed_trades"],
            "open_trades": (
                canonical_summary.get(
                    "open_trades",
                    0,
                )
            ),
            "closed_incomplete_trades": (
                canonical_summary.get(
                    "closed_incomplete_trades",
                    0,
                )
            ),
            "unmatched_sell_fills": (
                canonical_summary.get(
                    "unmatched_sell_fills",
                    0,
                )
            ),
            "excursion_trade_count": (
                learning_summary.get(
                    "excursion_trade_count",
                    0,
                )
            ),
            "average_mfe_percent": (
                learning_summary.get(
                    "average_mfe_percent"
                )
            ),
            "average_mae_percent": (
                learning_summary.get(
                    "average_mae_percent"
                )
            ),
            "winner_average_mfe_percent": (
                learning_summary.get(
                    "winner_average_mfe_percent"
                )
            ),
            "loser_average_mfe_percent": (
                learning_summary.get(
                    "loser_average_mfe_percent"
                )
            ),
            "never_profitable_count": (
                learning_summary.get(
                    "never_profitable_count",
                    0,
                )
            ),
            "never_profitable_percent": (
                learning_summary.get(
                    "never_profitable_percent",
                    0.0,
                )
            ),
        },
        "trades": history,
        "returned_count": len(history),
        "total_count": report_summary["completed_trades"],
        "has_more": len(history) < report_summary["completed_trades"],
        "daily_realized": daily_summaries,
        "generated_at": time.time(),
    }



def _profitability_group_summary(
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Summarize one profitability bucket.
    """

    count = len(trades)

    if count == 0:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "win_rate_percent": None,
            "total_realized_pl": 0.0,
            "average_return_percent": None,
            "average_mfe_percent": None,
            "average_mae_percent": None,
            "average_holding_minutes": None,
        }

    wins = 0
    losses = 0
    breakeven = 0

    total_pl = 0.0

    returns: list[float] = []
    mfe_values: list[float] = []
    mae_values: list[float] = []
    holding_values: list[float] = []

    for trade in trades:
        pnl = safe_float(
            trade.get(
                "realized_profit_loss"
            )
        )

        return_pct = safe_float(
            trade.get(
                "realized_return_percent"
            )
        )

        learning = trade.get(
            "learning"
        )

        if not isinstance(
            learning,
            dict,
        ):
            learning = {}

        mfe = safe_float(
            learning.get(
                "mfe_percent"
            )
        )

        mae = safe_float(
            learning.get(
                "mae_percent"
            )
        )

        holding_seconds = safe_float(
            learning.get(
                "holding_seconds"
            )
        )

        if pnl is not None:
            total_pl += pnl

            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
            else:
                breakeven += 1

        if return_pct is not None:
            returns.append(
                return_pct
            )

        if mfe is not None:
            mfe_values.append(
                mfe
            )

        if mae is not None:
            mae_values.append(
                mae
            )

        if (
            holding_seconds is not None
            and holding_seconds >= 0
        ):
            holding_values.append(
                holding_seconds / 60.0
            )

    return {
        "trades": count,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate_percent": round(
            (
                wins
                / count
                * 100.0
            ),
            2,
        ),
        "total_realized_pl": round(
            total_pl,
            2,
        ),
        "average_return_percent": (
            round(
                sum(returns)
                / len(returns),
                4,
            )
            if returns
            else None
        ),
        "average_mfe_percent": (
            round(
                sum(mfe_values)
                / len(mfe_values),
                4,
            )
            if mfe_values
            else None
        ),
        "average_mae_percent": (
            round(
                sum(mae_values)
                / len(mae_values),
                4,
            )
            if mae_values
            else None
        ),
        "average_holding_minutes": (
            round(
                sum(holding_values)
                / len(holding_values),
                2,
            )
            if holding_values
            else None
        ),
    }


def build_profitability_analysis(
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Analyze complete canonical PAPER trades.

    This is diagnostics only. It does not alter
    trading settings or place orders.
    """

    complete_trades = [
        trade
        for trade in trades
        if (
            isinstance(
                trade,
                dict,
            )
            and trade.get(
                "pnl_complete"
            )
            is not False
            and trade.get(
                "status"
            )
            == "CLOSED"
        )
    ]

    score_bands: dict[
        str,
        list[dict[str, Any]],
    ] = {
        "75-79": [],
        "80-84": [],
        "85-89": [],
        "90-94": [],
        "95+": [],
        "unknown": [],
    }

    rank_bands: dict[
        str,
        list[dict[str, Any]],
    ] = {
        "1-5": [],
        "6-10": [],
        "11-20": [],
        "21+": [],
        "unknown": [],
    }

    holding_bands: dict[
        str,
        list[dict[str, Any]],
    ] = {
        "<5m": [],
        "5-15m": [],
        "15-30m": [],
        "30-60m": [],
        "1-4h": [],
        "4h+": [],
        "unknown": [],
    }

    exit_reason_groups: dict[
        str,
        list[dict[str, Any]],
    ] = {}

    strategy_groups: dict[
        str,
        list[dict[str, Any]],
    ] = {
        "rank20_v1": [],
        "momentum_30_v1": [],
        "legacy_or_unknown": [],
    }

    excursion_trades: list[
        dict[str, Any]
    ] = []

    for trade in complete_trades:
        learning = trade.get(
            "learning"
        )

        if not isinstance(
            learning,
            dict,
        ):
            learning = {}

        score = safe_float(
            learning.get(
                "entry_score"
            )
        )

        rank = safe_float(
            learning.get(
                "scanner_rank"
            )
        )

        holding_seconds = safe_float(
            learning.get(
                "holding_seconds"
            )
        )

        mfe = safe_float(
            learning.get(
                "mfe_percent"
            )
        )

        mae = safe_float(
            learning.get(
                "mae_percent"
            )
        )

        # ----------------------------
        # Entry score bands
        # ----------------------------

        if score is None:
            score_key = "unknown"
        elif score < 80:
            score_key = "75-79"
        elif score < 85:
            score_key = "80-84"
        elif score < 90:
            score_key = "85-89"
        elif score < 95:
            score_key = "90-94"
        else:
            score_key = "95+"

        score_bands[
            score_key
        ].append(
            trade
        )

        # ----------------------------
        # Scanner-rank bands
        # ----------------------------

        if rank is None:
            rank_key = "unknown"
        elif rank <= 5:
            rank_key = "1-5"
        elif rank <= 10:
            rank_key = "6-10"
        elif rank <= 20:
            rank_key = "11-20"
        else:
            rank_key = "21+"

        rank_bands[
            rank_key
        ].append(
            trade
        )

        # ----------------------------
        # Holding-time bands
        # ----------------------------

        if holding_seconds is None:
            holding_key = "unknown"
        elif holding_seconds < 300:
            holding_key = "<5m"
        elif holding_seconds < 900:
            holding_key = "5-15m"
        elif holding_seconds < 1800:
            holding_key = "15-30m"
        elif holding_seconds < 3600:
            holding_key = "30-60m"
        elif holding_seconds < 14400:
            holding_key = "1-4h"
        else:
            holding_key = "4h+"

        holding_bands[
            holding_key
        ].append(
            trade
        )

        # ----------------------------
        # Exit reason
        # ----------------------------

        exit_reason = str(
            trade.get(
                "exit_reason"
            )
            or "unknown"
        )

        exit_reason_groups.setdefault(
            exit_reason,
            [],
        ).append(
            trade
        )

        # ----------------------------
        # Strategy version
        # ----------------------------

        strategy = str(
            trade.get("strategy")
            or ""
        ).strip()

        if strategy not in {
            "rank20_v1",
            "momentum_30_v1",
        }:
            strategy = "legacy_or_unknown"

        strategy_groups[
            strategy
        ].append(
            trade
        )

        if (
            mfe is not None
            and mae is not None
        ):
            excursion_trades.append(
                trade
            )

    winners_with_excursion = [
        trade
        for trade in excursion_trades
        if (
            safe_float(
                trade.get(
                    "realized_profit_loss"
                )
            )
            or 0.0
        )
        > 0
    ]

    losers_with_excursion = [
        trade
        for trade in excursion_trades
        if (
            safe_float(
                trade.get(
                    "realized_profit_loss"
                )
            )
            or 0.0
        )
        < 0
    ]

    losers_profitable_first = []

    losers_never_worked = []

    for trade in losers_with_excursion:
        learning = trade.get(
            "learning"
        )

        if not isinstance(
            learning,
            dict,
        ):
            continue

        mfe = safe_float(
            learning.get(
                "mfe_percent"
            )
        )

        if mfe is None:
            continue

        if mfe >= 0.5:
            losers_profitable_first.append(
                trade
            )

        if mfe < 0.25:
            losers_never_worked.append(
                trade
            )

    score_summary = {
        key: _profitability_group_summary(
            rows
        )
        for key, rows
        in score_bands.items()
    }

    rank_summary = {
        key: _profitability_group_summary(
            rows
        )
        for key, rows
        in rank_bands.items()
    }

    holding_summary = {
        key: _profitability_group_summary(
            rows
        )
        for key, rows
        in holding_bands.items()
    }

    exit_reason_summary = {
        key: _profitability_group_summary(
            rows
        )
        for key, rows
        in sorted(
            exit_reason_groups.items()
        )
    }

    strategy_summary = {
        key: _profitability_group_summary(
            rows
        )
        for key, rows
        in strategy_groups.items()
    }

    observations: list[str] = []

    enough_excursion_data = (
        len(excursion_trades)
        >= 30
    )

    if not enough_excursion_data:
        observations.append(
            (
                "Excursion sample is still small. "
                "Do not automatically change entry "
                "or exit thresholds from this data yet."
            )
        )

    loser_excursion_summary = (
        _profitability_group_summary(
            losers_with_excursion
        )
    )

    winner_excursion_summary = (
        _profitability_group_summary(
            winners_with_excursion
        )
    )

    loser_avg_mfe = safe_float(
        loser_excursion_summary.get(
            "average_mfe_percent"
        )
    )

    winner_avg_mfe = safe_float(
        winner_excursion_summary.get(
            "average_mfe_percent"
        )
    )

    if (
        loser_avg_mfe is not None
        and winner_avg_mfe is not None
        and winner_avg_mfe
        > loser_avg_mfe
    ):
        observations.append(
            (
                "Current excursion data suggests "
                "winning trades move favorably much "
                "more than losing trades. Entry "
                "selection should be investigated "
                "before loosening stops."
            )
        )

    if losers_never_worked:
        observations.append(
            (
                f"{len(losers_never_worked)} losing "
                "trades had less than 0.25% favorable "
                "excursion, suggesting weak entries "
                "in the current sample."
            )
        )

    if losers_profitable_first:
        observations.append(
            (
                f"{len(losers_profitable_first)} losing "
                "trades first reached at least +0.50% "
                "MFE. These deserve exit/profit-lock "
                "review."
            )
        )

    return {
        "paper": True,
        "read_only": True,
        "shadow_mode": True,
        "automatic_strategy_changes": False,
        "sample": {
            "complete_trades": (
                len(complete_trades)
            ),
            "excursion_trades": (
                len(excursion_trades)
            ),
            "winners_with_excursion": (
                len(
                    winners_with_excursion
                )
            ),
            "losers_with_excursion": (
                len(
                    losers_with_excursion
                )
            ),
            "enough_excursion_data": (
                enough_excursion_data
            ),
            "minimum_excursion_sample": 30,
        },
        "overall": (
            _profitability_group_summary(
                complete_trades
            )
        ),
        "by_entry_score": (
            score_summary
        ),
        "by_scanner_rank": (
            rank_summary
        ),
        "by_holding_time": (
            holding_summary
        ),
        "by_exit_reason": (
            exit_reason_summary
        ),
        "by_strategy": (
            strategy_summary
        ),
        "excursion_analysis": {
            "winners": (
                winner_excursion_summary
            ),
            "losers": (
                loser_excursion_summary
            ),
            "losers_profitable_first_count": (
                len(
                    losers_profitable_first
                )
            ),
            "losers_never_worked_count": (
                len(
                    losers_never_worked
                )
            ),
            "profitable_first_mfe_threshold": (
                0.5
            ),
            "never_worked_mfe_threshold": (
                0.25
            ),
        },
        "observations": observations,
    }


@app.get(
    "/auto-trader/profitability-analysis"
)
def auto_trader_profitability_analysis(
    request: Request,
) -> dict[str, Any]:
    """
    Read-only PAPER-trading profitability
    diagnostics using canonical history.
    """

    require_app_session(
        request
    )

    payload = auto_trader_history(
        request=request,
        limit=5000,
    )

    trades = payload.get(
        "trades",
        [],
    )

    if not isinstance(
        trades,
        list,
    ):
        trades = []

    return build_profitability_analysis(
        [
            trade
            for trade in trades
            if isinstance(
                trade,
                dict,
            )
        ]
    )


def _trade_report_history(
    request: Request = None,
) -> list[dict[str, Any]]:
    """
    Reuse the existing persistent trade-history endpoint
    so PDF reports contain the same enriched trade data
    shown in the Trade Journal.
    """
    payload = auto_trader_history(
        request=request,
        limit=5000,
    )

    trades = payload.get(
        "trades",
        [],
    )

    if not isinstance(trades, list):
        return []

    return [
        trade
        for trade in trades
        if isinstance(trade, dict)
    ]


def _trade_report_local_date(
    timestamp: Any,
) -> str | None:
    """
    Convert a stored timestamp to the server's local
    calendar date for report grouping.
    """
    if not timestamp:
        return None

    try:
        parsed = datetime.fromisoformat(
            str(timestamp).replace(
                "Z",
                "+00:00",
            )
        )

        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(
                ZoneInfo(
                    "America/New_York"
                )
            )

        return parsed.strftime(
            "%Y-%m-%d"
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


@app.get("/auto-trader/report/daily.pdf")
def auto_trader_daily_report(
    request: Request,
    date: str = Query(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    ),
) -> Response:
    require_app_session(
        request
    )

    trades = _trade_report_history(
        request
    )

    selected = [
        trade
        for trade in trades
        if _trade_report_local_date(
            trade.get("exit_timestamp")
            or trade.get("entry_timestamp")
        ) == date
    ]

    if not selected:
        raise HTTPException(
            status_code=404,
            detail=(
                "No completed trades found "
                f"for {date}."
            ),
        )

    try:
        display_date = datetime.strptime(
            date,
            "%Y-%m-%d",
        ).strftime(
            "%B %d, %Y"
        )
    except ValueError:
        display_date = date

    pdf = build_trade_report_pdf(
        title="AI Paper Trader - Daily Report",
        subtitle=display_date,
        trades=selected,
    )

    filename = (
        f"ai-paper-trader-{date}.pdf"
    )

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            ),
        },
    )



@app.get("/auto-trader/report/weekly.pdf")
def auto_trader_weekly_report(
    request: Request,
    week: str = Query(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    ),
) -> Response:
    """
    Build a Monday-Friday canonical trade
    report for the requested trading week.

    The `week` parameter must be the Monday
    that starts the requested week.
    """

    require_app_session(
        request
    )

    try:
        week_start = datetime.strptime(
            week,
            "%Y-%m-%d",
        ).date()

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid week. Use YYYY-MM-DD."
            ),
        ) from error

    if week_start.weekday() != 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "week must be the Monday "
                "starting the requested week."
            ),
        )

    week_end = (
        week_start
        + timedelta(days=4)
    )

    trades = _trade_report_history(
        request
    )

    selected: list[
        dict[str, Any]
    ] = []

    for trade in trades:
        local_date = (
            _trade_report_local_date(
                trade.get(
                    "exit_timestamp"
                )
                or trade.get(
                    "entry_timestamp"
                )
            )
        )

        if not local_date:
            continue

        try:
            trade_date = (
                datetime.strptime(
                    local_date,
                    "%Y-%m-%d",
                ).date()
            )

        except ValueError:
            continue

        if (
            week_start
            <= trade_date
            <= week_end
        ):
            selected.append(
                trade
            )

    if not selected:
        raise HTTPException(
            status_code=404,
            detail=(
                "No completed trades found "
                f"for week starting {week}."
            ),
        )

    display_start = (
        week_start.strftime(
            "%B %d, %Y"
        )
    )

    display_end = (
        week_end.strftime(
            "%B %d, %Y"
        )
    )

    pdf = build_trade_report_pdf(
        title=(
            "AI Paper Trader - Weekly Report"
        ),
        subtitle=(
            f"{display_start} - {display_end}"
        ),
        trades=selected,
        compact=True,
    )

    filename = (
        "ai-paper-trader-week-"
        f"{week}.pdf"
    )

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                "attachment; "
                f'filename="{filename}"'
            ),
        },
    )


@app.get("/auto-trader/report/monthly.pdf")
def auto_trader_monthly_report(
    request: Request,
    month: str = Query(
        ...,
        pattern=r"^\d{4}-\d{2}$",
    ),
) -> Response:
    require_app_session(
        request
    )

    try:
        month_date = datetime.strptime(
            month,
            "%Y-%m",
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid month.",
        )

    trades = _trade_report_history(
        request
    )

    selected = []

    for trade in trades:
        local_date = _trade_report_local_date(
            trade.get("exit_timestamp")
            or trade.get("entry_timestamp")
        )

        if (
            local_date
            and local_date.startswith(
                month + "-"
            )
        ):
            selected.append(
                trade
            )

    if not selected:
        raise HTTPException(
            status_code=404,
            detail=(
                "No completed trades found "
                f"for {month}."
            ),
        )

    display_month = month_date.strftime(
        "%B %Y"
    )

    pdf = build_trade_report_pdf(
        title="AI Paper Trader - Monthly Report",
        subtitle=display_month,
        trades=selected,
        compact=True,
    )

    filename = (
        f"ai-paper-trader-{month}.pdf"
    )

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            ),
        },
    )


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
    global _auto_trader_enabled_at

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
    _auto_trader_enabled_at = time.time()

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
    global _auto_trader_enabled_at

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
    _auto_trader_enabled_at = None

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


@app.get("/jarvis/profile")
def get_jarvis_profile(
    request: Request,
) -> dict[str, Any]:
    require_app_session(request)

    return {
        "success": True,
        "profile": load_jarvis_profile(),
    }


@app.post("/jarvis/profile")
def update_jarvis_profile(
    data: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    require_app_session(request)

    allowed_fields = {
        "preferred_name",
        "address_as",
        "timezone",
        "language",
        "voice",
        "wake_phrase",
        "voice_activation",
        "automatic_greeting",
    }

    updates = {
        key: value
        for key, value in data.items()
        if key in allowed_fields
    }

    if not updates:
        raise HTTPException(
            status_code=400,
            detail="No valid Jarvis profile settings were provided.",
        )

    profile = save_jarvis_profile(
        updates
    )

    return {
        "success": True,
        "profile": profile,
    }

@app.get("/jarvis/status")
def jarvis_status(
    request: Request,
) -> dict[str, Any]:
    require_app_session(
        request
    )

    return {
        "success": True,
        "name": "Jarvis",
        "status": "online",
        "version": "1.0",
        "paper_trading": True,
        "chat_ready": bool(OPENAI_API_KEY),
        "trading_access": False,
    }



def call_jarvis_ai(
    user_message: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured."
        )

    message = str(user_message or "").strip()

    if not message:
        raise ValueError(
            "Jarvis requires a message."
        )

    normalized_history: list[dict[str, str]] = []

    if isinstance(conversation_history, list):
        for item in conversation_history[-12:]:
            if not isinstance(item, dict):
                continue

            role = str(
                item.get("role", "")
            ).strip().lower()

            content = str(
                item.get("content", "")
            ).strip()

            if role not in {
                "user",
                "assistant",
            }:
                continue

            if not content:
                continue

            normalized_history.append(
                {
                    "role": role,
                    "content": content[:4000],
                }
            )

    profile = load_jarvis_profile()

    profile_timezone = str(
        profile.get(
            "timezone",
            "America/New_York",
        )
    ).strip()

    try:
        local_timezone = ZoneInfo(
            profile_timezone
        )
    except Exception:
        profile_timezone = "America/New_York"
        local_timezone = ZoneInfo(
            profile_timezone
        )

    now_utc = datetime.now(
        timezone.utc
    )
    now_local = now_utc.astimezone(
        local_timezone
    )

    trader_status = get_auto_trader_status()

    try:
        account_data = build_alpaca_dashboard_account()
    except Exception as error:
        account_data = {
            "error": clean_error_message(error),
        }

    positions = account_data.get(
        "positions",
        [],
    )

    if not isinstance(positions, list):
        positions = []

    jarvis_context = {
        "user_profile": {
            "preferred_name": profile.get(
                "preferred_name",
                "",
            ),
            "address_as": profile.get(
                "address_as",
                "Sir",
            ),
            "timezone": profile_timezone,
            "language": profile.get(
                "language",
                "en-US",
            ),
            "voice": profile.get(
                "voice",
                "cedar",
            ),
            "wake_phrase": profile.get(
                "wake_phrase",
                "Hey Jarvis",
            ),
        },
        "current_time": {
            "timezone": profile_timezone,
            "local_iso": now_local.isoformat(),
            "utc_iso": now_utc.isoformat(),
            "local_date": now_local.strftime(
                "%A, %B %d, %Y"
            ),
            "local_time": now_local.strftime(
                "%I:%M:%S %p"
            ),
        },
        "automatic_trader": {
            "paper": trader_status.get("paper"),
            "enabled": trader_status.get("enabled"),
            "health": trader_status.get("health"),
            "cycle_running": trader_status.get(
                "cycle_running"
            ),
            "last_cycle_at": trader_status.get(
                "last_cycle_at"
            ),
            "last_successful_cycle_at": (
                trader_status.get(
                    "last_successful_cycle_at"
                )
            ),
            "last_scan_at": trader_status.get(
                "last_scan_at"
            ),
            "scan_age_seconds": trader_status.get(
                "scan_age_seconds"
            ),
            "last_trade_at": trader_status.get(
                "last_trade_at"
            ),
        },
        "account": {
            "status": account_data.get("status"),
            "source": account_data.get("source"),
            "paper": account_data.get("paper"),
            "equity": account_data.get("equity"),
            "cash": account_data.get("cash"),
            "buying_power": account_data.get(
                "buying_power"
            ),
            "profit_loss": account_data.get(
                "profit_loss"
            ),
            "profit_loss_percent": account_data.get(
                "profit_loss_percent"
            ),
            "total_profit_loss": account_data.get(
                "total_profit_loss"
            ),
            "total_return_percent": account_data.get(
                "total_return_percent"
            ),
            "unrealized_profit_loss": account_data.get(
                "unrealized_profit_loss"
            ),
            "realized_profit_loss": account_data.get(
                "realized_profit_loss"
            ),
            "open_position_count": len(positions),
            "positions": positions,
            "error": account_data.get("error"),
        },
    }

    context_text = json.dumps(
        jarvis_context,
        default=str,
        separators=(",", ":"),
    )

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": (
                f"Bearer {OPENAI_API_KEY}"
            ),
            "Content-Type": "application/json",
        },
        json={
            "model": JARVIS_MODEL,
            "instructions": (
                "You are Jarvis, the user's personal AI assistant "
                "inside an AI paper-trading research application. "
                "Be intelligent, composed, conversational, concise "
                "when appropriate, and detailed when useful. Speak "
                "naturally rather than sounding scripted or robotic. "
                "Use subtle dry wit occasionally when it fits, but "
                "never force jokes or slang. "

                "The supplied user_profile contains the user's saved "
                "preferences. If preferred_name is present, remember "
                "that this is the name the user wants you to use. "
                "The address_as field specifies how the user prefers "
                "to be addressed. Use it naturally, especially in "
                "greetings, acknowledgements, or short confirmations, "
                "but do not repeat it in every sentence. Never invent "
                "a name or personal detail that is not supplied. "

                "The supplied current_time object is authoritative for "
                "the current date and time available to you. Use its "
                "local date, local time, and timezone when the user "
                "asks what day, date, or time it is. Do not guess the "
                "user's physical location from their timezone. "

                "Respect the user's saved language preference when "
                "practical. Match the user's conversational language "
                "when they clearly choose another language. "

                "For simple questions, answer simply. For deeper "
                "analysis, explain the important details clearly and "
                "expand when useful. Avoid robotic headings, repetitive "
                "disclaimers, giant walls of text, and unnecessary "
                "restatement of the question. Use normal conversational "
                "paragraphs by default and lists only when they improve "
                "clarity. "

                "When discussing live trader data, use natural rounded "
                "values when exact precision is unnecessary. If "
                "something looks unusual in the supplied data, point "
                "it out plainly and explain why it matters. "

                "Treat all trading as PAPER trading. You have READ-ONLY "
                "access to the live paper-trading snapshot supplied "
                "with each message. Use that snapshot when answering "
                "questions about the account, positions, scanner, or "
                "automatic trader. Never claim access to data that is "
                "not in the supplied snapshot. Clearly distinguish "
                "known data from interpretation or uncertainty. Never "
                "claim guaranteed profits or certainty about future "
                "market performance. You cannot place, cancel, or "
                "modify trades and cannot change trader settings."
            ),
            "input": [
                {
                    "role": "developer",
                    "content": (
                        "CURRENT JARVIS CONTEXT:\n"
                        f"{context_text}"
                    ),
                },
                *normalized_history,
                {
                    "role": "user",
                    "content": message,
                },
            ],
        },
        timeout=60,
    )

    if not response.ok:
        raise RuntimeError(
            "Jarvis AI request failed with status "
            f"{response.status_code}."
        )

    payload = response.json()

    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue

        for content in item.get("content", []):
            if content.get("type") == "output_text":
                reply_text = str(
                    content.get("text", "")
                ).strip()

                if reply_text:
                    return reply_text

    raise RuntimeError(
        "Jarvis AI returned no text response."
    )

@app.post("/jarvis/chat")
def jarvis_chat(
    data: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    require_app_session(
        request
    )

    message = str(
        data.get("message", "")
    ).strip()

    if not message:
        return {
            "success": False,
            "error": "Enter a message for Jarvis.",
        }

    if len(message) > 8000:
        return {
            "success": False,
            "error": (
                "Jarvis messages are limited "
                "to 8,000 characters."
            ),
        }

    history = data.get(
        "history",
        [],
    )

    if not isinstance(history, list):
        history = []

    try:
        reply = call_jarvis_ai(
            message,
            conversation_history=history,
        )
    except Exception as error:
        return {
            "success": False,
            "error": clean_error_message(
                error
            ),
        }

    return {
        "success": True,
        "name": "Jarvis",
        "reply": reply,
        "paper_trading": True,
    }

@app.post("/jarvis/speech")
def jarvis_speech(
    data: dict[str, Any],
    request: Request,
) -> Response:
    require_app_session(
        request
    )

    text = str(
        data.get("text", "")
    ).strip()

    if not text:
        raise HTTPException(
            status_code=400,
            detail="Jarvis has nothing to say.",
        )

    if len(text) > 4000:
        raise HTTPException(
            status_code=400,
            detail=(
                "Jarvis speech is limited "
                "to 4,000 characters."
            ),
        )

    if not OPENAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "Jarvis speech is not configured."
            ),
        )

    profile = load_jarvis_profile()

    selected_voice = str(
        profile.get(
            "voice",
            "cedar",
        )
    ).strip() or "cedar"

    selected_language = str(
        profile.get(
            "language",
            "en-US",
        )
    ).strip() or "en-US"

    language_names = {
        "en-US": "American English",
        "en-GB": "British English",
        "ar": "Arabic",
        "es": "Spanish",
        "fr": "French",
        "de": "German",
    }

    language_name = language_names.get(
        selected_language,
        selected_language,
    )

    speech_instructions = (
        f"Speak in natural {language_name}. "
        "Use a sophisticated, composed, "
        "intelligent male-presenting delivery. "
        "Use precise diction, calm confidence, "
        "natural pauses, and restrained emotional "
        "expression. Sound natural and "
        "conversational, like an advanced personal "
        "AI assistant. Never sound rushed, overly "
        "theatrical, or robotic. Do not read "
        "markdown formatting or emoji names aloud."
    )

    try:
        response = requests.post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": (
                    f"Bearer {OPENAI_API_KEY}"
                ),
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini-tts",
                "voice": selected_voice,
                "input": text,
                "instructions": speech_instructions,
                "response_format": "mp3",
            },
            timeout=60,
        )

        if not response.ok:
            raise RuntimeError(
                "OpenAI speech request failed "
                f"with HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        if not response.content:
            raise RuntimeError(
                "OpenAI speech returned no audio."
            )

        return Response(
            content=response.content,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-store",
            },
        )

    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=clean_error_message(
                error
            ),
        ) from error
