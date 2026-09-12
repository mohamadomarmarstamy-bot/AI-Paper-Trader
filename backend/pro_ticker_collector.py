from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import requests

from database import upsert_pro_ticker_research


PRO_TICKER_HOSTS = {
    "protickersignals.com",
    "www.protickersignals.com",
}

PRO_TICKER_TIMEOUT_SECONDS = 20

USER_AGENT = (
    "AI-Paper-Trader-Research/1.0 "
    "(public article research collector)"
)


def _validate_pro_ticker_url(url: str) -> str:
    normalized = str(url).strip()

    if not normalized:
        raise ValueError(
            "Pro Ticker article URL cannot be empty."
        )

    parsed = urlparse(normalized)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError(
            "Pro Ticker URL must use HTTP or HTTPS."
        )

    host = parsed.hostname

    if host is None or host.lower() not in PRO_TICKER_HOSTS:
        raise ValueError(
            "Only public Pro Ticker Signals URLs are supported."
        )

    if not parsed.path.startswith("/latestnews/"):
        raise ValueError(
            "URL must point to a Pro Ticker latest-news article."
        )

    return normalized


def _fetch_article_html(
    article_url: str,
) -> str:
    response = requests.get(
        article_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,"
                "application/xhtml+xml"
            ),
        },
        timeout=PRO_TICKER_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    content_type = (
        response.headers.get(
            "Content-Type",
            "",
        ).lower()
    )

    if (
        "text/html" not in content_type
        and "application/xhtml+xml" not in content_type
    ):
        raise RuntimeError(
            "Pro Ticker response was not an HTML page."
        )

    return response.text


def _html_to_text(
    html: str,
) -> str:
    from lxml import html as lxml_html

    document = lxml_html.fromstring(
        html
    )

    content_nodes = document.xpath(
        "//*[@data-slot-name='content_body']"
    )

    if content_nodes:
        document = content_nodes[0]

    for bad in document.xpath(
        ".//script|.//style|.//noscript"
    ):
        bad.drop_tree()

    text = document.text_content()

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    # Add space after sentence punctuation when
    # HTML joins the next sentence directly.
    text = re.sub(
        r"([.!?])(?=[A-Z$])",
        r"\1 ",
        text,
    )

    # Separate prose from ticker symbols.
    text = re.sub(
        r"([A-Za-z])(\$[A-Z]{1,6})\b",
        r"\1 \2",
        text,
    )

    # Separate prose from dollar prices.
    text = re.sub(
        r"([A-Za-z])(\$[0-9])",
        r"\1 \2",
        text,
    )

    # Repair common technical-term joins.
    text = re.sub(
        r"\b(VWAP|VWMA|RSI)(?=[a-z])",
        r"\1 ",
        text,
    )

    # A couple of source-specific prose joins.
    text = re.sub(
        r"\bpre-marketmoves\b",
        "pre-market moves",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\bThiswas\b",
        "This was",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return text


def _extract_published_date(
    html: str,
) -> str | None:
    from lxml import html as lxml_html

    document = lxml_html.fromstring(
        html
    )

    candidates = document.xpath(
        "//*[@title]/@title"
    )

    date_pattern = re.compile(
        r"^(?:Monday|Tuesday|Wednesday|Thursday|"
        r"Friday|Saturday|Sunday),\s+"
        r"(?:January|February|March|April|May|June|"
        r"July|August|September|October|November|December)"
        r"\s+\d{1,2},\s+\d{4}$",
        flags=re.IGNORECASE,
    )

    for candidate in candidates:
        value = str(candidate).strip()

        if date_pattern.match(value):
            return value

    return None


def _extract_alert_date(
    text: str,
) -> str | None:
    pattern = re.compile(
        r"\b(?:On\s+)?"
        r"((?:Monday|Tuesday|Wednesday|Thursday|"
        r"Friday|Saturday|Sunday),\s+)?"
        r"((?:January|February|March|April|May|June|"
        r"July|August|September|October|November|December)"
        r"\s+\d{1,2},\s+\d{4})"
        r",?\s+Pro\s+Ticker\s+alerted\b",
        flags=re.IGNORECASE,
    )

    match = pattern.search(text)

    if not match:
        return None

    weekday = (
        match.group(1) or ""
    ).strip()

    date_part = match.group(2).strip()

    if weekday:
        return (
            f"{weekday} {date_part}"
        )

    return date_part


def _extract_title(
    html: str,
) -> str | None:
    from lxml import html as lxml_html

    document = lxml_html.fromstring(
        html
    )

    headings = document.xpath(
        "//h1//text()"
    )

    title = " ".join(
        part.strip()
        for part in headings
        if part.strip()
    ).strip()

    if title:
        return title

    title_parts = document.xpath(
        "//title//text()"
    )

    title = " ".join(
        part.strip()
        for part in title_parts
        if part.strip()
    ).strip()

    return title or None


def _extract_symbol(
    title: str | None,
    text: str,
) -> str | None:
    candidates = " ".join(
        part
        for part in (
            title or "",
            text[:3000],
        )
        if part
    )

    match = re.search(
        r"\$([A-Z]{1,6})\b",
        candidates,
        flags=re.IGNORECASE,
    )

    if match:
        return match.group(1).upper()

    return None


def _extract_number_after_phrase(
    text: str,
    phrases: list[str],
) -> float | None:
    for phrase in phrases:
        patterns = [
            (
                rf"\$\s*"
                rf"([0-9]+(?:\.[0-9]+)?)"
                rf"\s*{phrase}"
            ),
            (
                rf"{phrase}"
                rf"\s*(?:at|of|:|-)?\s*"
                rf"\$?\s*"
                rf"([0-9]+(?:\.[0-9]+)?)"
            ),
        ]

        for pattern in patterns:
            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if not match:
                continue

            try:
                return float(
                    match.group(1)
                )
            except ValueError:
                continue

    return None


def _extract_reported_move(
    title: str | None,
    text: str,
) -> float | None:
    source = " ".join(
        [
            title or "",
            text,
        ]
    )

    patterns = [
        r"([0-9]+(?:\.[0-9]+)?)%\s+LONG MOVE",
        r"LONG MOVE.{0,20}?([0-9]+(?:\.[0-9]+)?)%",
        r"([0-9]+(?:\.[0-9]+)?)%\s+MOVE",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            source,
            flags=re.IGNORECASE,
        )

        if match:
            try:
                return float(
                    match.group(1)
                )
            except ValueError:
                continue

    return None


def _extract_feature_context(
    text: str,
    keyword: str,
    *,
    radius: int = 180,
) -> str | None:
    lowered = text.lower()
    position = lowered.find(
        keyword.lower()
    )

    if position < 0:
        return None

    start = max(
        0,
        position - radius,
    )
    end = min(
        len(text),
        position + len(keyword) + radius,
    )

    context = text[
        start:end
    ].strip()

    return context or None


def _contains_any(
    text: str,
    terms: list[str],
) -> bool:
    lowered = text.lower()

    return any(
        term.lower() in lowered
        for term in terms
    )


def parse_pro_ticker_article(
    *,
    article_url: str,
    html: str,
) -> dict[str, Any]:
    normalized_url = _validate_pro_ticker_url(
        article_url
    )

    title = _extract_title(
        html
    )

    text = _html_to_text(
        html
    )

    published_date = (
        _extract_published_date(
            html
        )
    )

    alert_date = (
        _extract_alert_date(
            text
        )
    )

    symbol = _extract_symbol(
        title,
        text,
    )

    long_level = _extract_number_after_phrase(
        text,
        [
            r"LONG SIGNAL",
            r"long signal",
            r"long level",
        ],
    )

    short_level = _extract_number_after_phrase(
        text,
        [
            r"SHORT SIGNAL",
            r"short signal",
            r"short level",
        ],
    )

    reported_high = _extract_number_after_phrase(
        text,
        [
            r"HIGH OF DAY",
            r"high of day",
            r"\bHOD\b",
        ],
    )

    reported_move_percent = (
        _extract_reported_move(
            title,
            text,
        )
    )

    raw_features = {
        "vwap_present": (
            "vwap" in text.lower()
        ),
        "vwma_present": (
            "vwma" in text.lower()
        ),
        "relative_volume_present": (
            "relative volume"
            in text.lower()
        ),
        "rsi_present": (
            re.search(
                r"\bRSI\b",
                text,
                flags=re.IGNORECASE,
            )
            is not None
        ),
        "breakout_present": (
            "breakout"
            in text.lower()
        ),
        "bull_flag_present": (
            _contains_any(
                text,
                [
                    "bull flag",
                    "bull-flag",
                ],
            )
        ),
        "higher_lows_present": (
            _contains_any(
                text,
                [
                    "higher low",
                    "higher lows",
                ],
            )
        ),
        "consolidation_present": (
            _contains_any(
                text,
                [
                    "consolidation",
                    "compressed",
                    "compression",
                    "base",
                ],
            )
        ),
        "exhaustion_present": (
            "exhaustion"
            in text.lower()
        ),
        "parabolic_present": (
            "parabolic"
            in text.lower()
        ),
    }

    return {
        "article_url": normalized_url,
        "symbol": symbol,
        "article_title": title,
        "published_date": published_date,
        "alert_date": alert_date,
        "alert_time": None,
        "direction": (
            "LONG"
            if long_level is not None
            else (
                "SHORT"
                if short_level is not None
                else None
            )
        ),
        "long_level": long_level,
        "short_level": short_level,
        "reported_high": reported_high,
        "reported_move_percent": (
            reported_move_percent
        ),
        "setup_type": (
            "breakout"
            if raw_features[
                "breakout_present"
            ]
            else None
        ),
        "relative_volume": (
            _extract_feature_context(
                text,
                "relative volume",
            )
        ),
        "vwap_context": (
            _extract_feature_context(
                text,
                "VWAP",
            )
        ),
        "vwma_context": (
            _extract_feature_context(
                text,
                "VWMA",
            )
        ),
        "rsi_context": (
            _extract_feature_context(
                text,
                "RSI",
            )
        ),
        "volume_context": (
            _extract_feature_context(
                text,
                "volume",
            )
        ),
        "consolidation_context": (
            _extract_feature_context(
                text,
                "consolidation",
            )
        ),
        "higher_lows": (
            raw_features[
                "higher_lows_present"
            ]
        ),
        "breakout_context": (
            _extract_feature_context(
                text,
                "breakout",
            )
        ),
        "continuation_context": (
            _extract_feature_context(
                text,
                "continuation",
            )
        ),
        "exhaustion_context": (
            _extract_feature_context(
                text,
                "exhaustion",
            )
        ),
        "article_summary": (
            text[:1500]
            if text
            else None
        ),
        "raw_features": {
            **raw_features,
            "collector_version": 2,
            "text_length": len(text),
        },
    }


def collect_pro_ticker_article(
    article_url: str,
) -> dict[str, Any]:
    normalized_url = (
        _validate_pro_ticker_url(
            article_url
        )
    )

    html = _fetch_article_html(
        normalized_url
    )

    parsed = parse_pro_ticker_article(
        article_url=normalized_url,
        html=html,
    )

    return upsert_pro_ticker_research(
        **parsed
    )
