import io
import time
from collections.abc import Iterable
from typing import Any, Callable

import pandas as pd
import requests


WIKIPEDIA_URL = (
    "https://en.wikipedia.org/wiki/"
    "List_of_S%26P_500_companies"
)

FALLBACK_CSV_URL = (
    "https://raw.githubusercontent.com/"
    "datasets/s-and-p-500-companies/"
    "main/data/constituents.csv"
)

CACHE_SECONDS = 24 * 60 * 60
REQUEST_TIMEOUT_SECONDS = 20
MINIMUM_EXPECTED_SYMBOLS = 400

_universe_cache: dict[str, Any] = {
    "symbols": [],
    "updated_at": 0.0,
}


def clean_symbol(symbol: Any) -> str:
    """
    Convert a ticker symbol into Yahoo Finance format.

    Examples:
        BRK.B -> BRK-B
        BF.B  -> BF-B
    """
    return str(symbol or "").strip().upper().replace(".", "-")


def normalize_symbols(values: Iterable[Any]) -> list[str]:
    """
    Clean, validate, and deduplicate ticker symbols.
    """
    symbols: list[str] = []
    seen: set[str] = set()

    for value in values:
        symbol = clean_symbol(value)

        if not symbol:
            continue

        if symbol in {"NAN", "NONE", "NULL"}:
            continue

        if symbol in seen:
            continue

        symbols.append(symbol)
        seen.add(symbol)

    return symbols


def request_headers() -> dict[str, str]:
    """
    Return browser-style HTTP headers.

    Some websites reject requests using Python's default
    request identity.
    """
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }


def download_text(url: str) -> str:
    """
    Download text from a URL and raise an error for bad responses.
    """
    response = requests.get(
        url,
        headers=request_headers(),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    if not response.text.strip():
        raise ValueError(
            f"The response from {url} was empty."
        )

    return response.text


def load_from_wikipedia() -> list[str]:
    """
    Load the current S&P 500 constituent table from Wikipedia.
    """
    html = download_text(WIKIPEDIA_URL)

    tables = pd.read_html(
        io.StringIO(html)
    )

    if not tables:
        return []

    for table in tables:
        if "Symbol" not in table.columns:
            continue

        symbols = normalize_symbols(
            table["Symbol"].tolist()
        )

        if len(symbols) >= MINIMUM_EXPECTED_SYMBOLS:
            print(
                f"Loaded {len(symbols)} symbols "
                "from Wikipedia."
            )
            return symbols

    return []


def load_from_fallback_csv() -> list[str]:
    """
    Load the S&P 500 universe from the fallback GitHub CSV.
    """
    csv_text = download_text(
        FALLBACK_CSV_URL
    )

    dataframe = pd.read_csv(
        io.StringIO(csv_text)
    )

    if "Symbol" not in dataframe.columns:
        return []

    symbols = normalize_symbols(
        dataframe["Symbol"].tolist()
    )

    if len(symbols) >= MINIMUM_EXPECTED_SYMBOLS:
        print(
            f"Loaded {len(symbols)} symbols "
            "from the fallback CSV."
        )
        return symbols

    return []


def load_market_universe(
    force_refresh: bool = False,
) -> list[str]:
    """
    Return the current S&P 500 ticker universe.

    Results are cached in memory for 24 hours. If a refresh fails,
    stale cached symbols are returned instead of an empty universe.
    """
    current_time = time.time()

    cached_symbols = list(
        _universe_cache.get("symbols", [])
    )

    cached_time = float(
        _universe_cache.get("updated_at", 0.0)
    )

    cache_is_valid = (
        bool(cached_symbols)
        and current_time - cached_time < CACHE_SECONDS
    )

    if cache_is_valid and not force_refresh:
        return cached_symbols.copy()

    loaders: list[
        tuple[str, Callable[[], list[str]]]
    ] = [
        ("Wikipedia", load_from_wikipedia),
        ("fallback CSV", load_from_fallback_csv),
    ]

    for source_name, loader in loaders:
        try:
            symbols = loader()

            if len(symbols) < MINIMUM_EXPECTED_SYMBOLS:
                print(
                    f"Market universe source {source_name} "
                    f"returned only {len(symbols)} symbols."
                )
                continue

            _universe_cache["symbols"] = symbols.copy()
            _universe_cache["updated_at"] = time.time()

            return symbols.copy()

        except Exception as error:
            print(
                f"Market universe source "
                f"{source_name} failed: {error}"
            )

    if cached_symbols:
        print(
            "All market universe sources failed. "
            "Using stale cached symbols."
        )
        return cached_symbols.copy()

    print(
        "Market universe error: all sources failed "
        "and no cached symbols are available."
    )

    return []


def clear_market_universe_cache() -> None:
    """
    Clear the in-memory market universe cache.
    """
    _universe_cache["symbols"] = []
    _universe_cache["updated_at"] = 0.0