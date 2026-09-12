from __future__ import annotations

from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import (
    ParagraphStyle,
    getSampleStyleSheet,
)
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def _money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"

    return f"${number:,.2f}"


def _percent(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"

    return f"{number:.2f}%"


def _clean_reason(value: Any) -> str:
    text = str(value or "").strip()

    if not text:
        return "No reason recorded."

    known = {
        "auto_trader_entry": (
            "Automatic trader entry after scanner "
            "and entry filters approved the setup."
        ),
        "scanner_exit": (
            "Scanner conditions weakened enough "
            "to trigger an automatic exit."
        ),
        "hard_max_loss_exit": (
            "Hard maximum-loss protection triggered."
        ),
        "defensive_portfolio_exit": (
            "Daily profit-giveback protection entered "
            "defensive mode and exited the position."
        ),
        "broker_sell_fill": (
            "Position closed by a broker-side sell "
            "or protective order fill."
        ),
        "take_profit": (
            "Take-profit protection triggered."
        ),
        "stop_loss": (
            "Stop-loss protection triggered."
        ),
    }

    if text in known:
        return known[text]

    return text.replace("_", " ").title()


def _build_buy_explanation(
    trade: dict[str, Any],
) -> str:
    learning = trade.get("learning") or {}
    diagnostics = (
        trade.get("entry_diagnostics") or {}
    )

    parts: list[str] = []

    signal = (
        learning.get("entry_signal")
        or diagnostics.get("signal")
    )

    score = (
        learning.get("entry_score")
        if learning.get("entry_score") is not None
        else diagnostics.get("score")
    )

    confidence = (
        learning.get("entry_confidence")
        if learning.get("entry_confidence") is not None
        else diagnostics.get("confidence")
    )

    scanner_rank = learning.get(
        "scanner_rank"
    )

    trend = diagnostics.get("trend")
    rsi = diagnostics.get("rsi")
    volume_ratio = diagnostics.get(
        "volume_ratio"
    )

    if signal:
        parts.append(
            f"Scanner signal: {str(signal).upper()}."
        )

    if score is not None:
        parts.append(
            f"Entry score: {score}."
        )

    if confidence is not None:
        parts.append(
            f"Confidence: {confidence}%."
        )

    if scanner_rank is not None:
        parts.append(
            f"Scanner rank: #{scanner_rank}."
        )

    if trend:
        parts.append(
            f"Trend: {trend}."
        )

    if rsi is not None:
        try:
            parts.append(
                f"RSI: {float(rsi):.1f}."
            )
        except (TypeError, ValueError):
            pass

    if volume_ratio is not None:
        try:
            parts.append(
                "Volume ratio: "
                f"{float(volume_ratio):.2f}x."
            )
        except (TypeError, ValueError):
            pass

    if parts:
        return " ".join(parts)

    return _clean_reason(
        trade.get("entry_reason")
    )


def _build_sell_explanation(
    trade: dict[str, Any],
) -> str:
    learning = trade.get("learning") or {}

    parts = [
        _clean_reason(
            learning.get("exit_reason")
            or trade.get("exit_reason")
        )
    ]

    mfe = learning.get("mfe_percent")
    mae = learning.get("mae_percent")

    if mfe is not None:
        parts.append(
            f"Best excursion: {_percent(mfe)}."
        )

    if mae is not None:
        parts.append(
            f"Worst excursion: {_percent(mae)}."
        )

    return " ".join(parts)


def _summary_table(
    trades: list[dict[str, Any]],
) -> Table:
    gains = 0.0
    losses = 0.0
    total = 0.0
    incomplete_count = 0

    for trade in trades:
        try:
            value = float(
                trade.get(
                    "realized_profit_loss"
                )
                or 0.0
            )
        except (TypeError, ValueError):
            value = 0.0

        total += value

        if value > 0:
            gains += value
        elif value < 0:
            losses += value

        if (
            trade.get("pnl_complete") is False
            or trade.get("status")
            == "CLOSED_INCOMPLETE"
        ):
            incomplete_count += 1

    total_label = (
        "Known realized P/L"
        if incomplete_count > 0
        else "Net realized P/L"
    )

    rows = [
        ["Trades", str(len(trades))],
        ["Realized gains", _money(gains)],
        ["Realized losses", _money(losses)],
        [total_label, _money(total)],
    ]

    if incomplete_count > 0:
        rows.append([
            "Incomplete trades",
            str(incomplete_count),
        ])

    table = Table(
        rows,
        colWidths=[
            2.1 * inch,
            2.1 * inch,
        ],
    )

    table.setStyle(
        TableStyle([
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.5,
                colors.grey,
            ),
            (
                "BACKGROUND",
                (0, 0),
                (0, -1),
                colors.whitesmoke,
            ),
            (
                "FONTNAME",
                (0, 0),
                (0, -1),
                "Helvetica-Bold",
            ),
            (
                "ALIGN",
                (1, 0),
                (1, -1),
                "RIGHT",
            ),
            (
                "PADDING",
                (0, 0),
                (-1, -1),
                7,
            ),
        ])
    )

    return table


def build_trade_report_pdf(
    *,
    title: str,
    subtitle: str,
    trades: list[dict[str, Any]],
) -> bytes:
    buffer = BytesIO()

    document = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title=title,
        author="AI Paper Trader",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=18,
        leading=22,
        spaceAfter=6,
    )

    subtitle_style = ParagraphStyle(
        "ReportSubtitle",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=9,
        textColor=colors.grey,
        spaceAfter=14,
    )

    trade_title_style = ParagraphStyle(
        "TradeTitle",
        parent=styles["Heading3"],
        fontSize=11,
        leading=14,
        spaceBefore=10,
        spaceAfter=5,
    )

    body_style = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontSize=8.5,
        leading=11,
        spaceAfter=4,
    )

    story: list[Any] = [
        Paragraph(title, title_style),
        Paragraph(subtitle, subtitle_style),
        _summary_table(trades),
        Spacer(1, 0.18 * inch),
    ]

    for trade in trades:
        symbol = str(
            trade.get("symbol") or "Unknown"
        )

        pnl = _money(
            trade.get(
                "realized_profit_loss"
            )
        )

        return_percent = _percent(
            trade.get(
                "realized_return_percent"
            )
        )

        story.append(
            Paragraph(
                f"{symbol} - {pnl} ({return_percent})",
                trade_title_style,
            )
        )

        row_data = [
            [
                "Shares",
                "Buy",
                "Sell",
                "Entry time",
                "Exit time",
            ],
            [
                str(
                    trade.get("shares")
                    or "-"
                ),
                _money(
                    trade.get("entry_price")
                ),
                _money(
                    trade.get("exit_price")
                ),
                str(
                    trade.get(
                        "entry_timestamp"
                    )
                    or "-"
                ),
                str(
                    trade.get(
                        "exit_timestamp"
                    )
                    or "-"
                ),
            ],
        ]

        trade_table = Table(
            row_data,
            repeatRows=1,
            colWidths=[
                0.65 * inch,
                0.85 * inch,
                0.85 * inch,
                1.75 * inch,
                1.75 * inch,
            ],
        )

        trade_table.setStyle(
            TableStyle([
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.whitesmoke,
                ),
                (
                    "FONTNAME",
                    (0, 0),
                    (-1, 0),
                    "Helvetica-Bold",
                ),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.35,
                    colors.lightgrey,
                ),
                (
                    "FONTSIZE",
                    (0, 0),
                    (-1, -1),
                    7.5,
                ),
                (
                    "PADDING",
                    (0, 0),
                    (-1, -1),
                    5,
                ),
            ])
        )

        story.append(trade_table)
        story.append(Spacer(1, 5))

        story.append(
            Paragraph(
                "<b>Why the AI bought:</b> "
                + _build_buy_explanation(
                    trade
                ),
                body_style,
            )
        )

        story.append(
            Paragraph(
                "<b>Why the AI sold:</b> "
                + _build_sell_explanation(
                    trade
                ),
                body_style,
            )
        )

    story.append(
        Spacer(1, 0.18 * inch)
    )

    story.append(
        Paragraph(
            "Paper-trading report only. "
            "Not an official brokerage statement "
            "or tax document.",
            subtitle_style,
        )
    )

    document.build(story)

    return buffer.getvalue()
