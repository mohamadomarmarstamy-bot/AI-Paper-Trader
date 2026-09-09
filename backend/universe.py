from __future__ import annotations

import time
from typing import Any, Callable


CACHE_SECONDS = 15 * 60

_universe_cache: dict[str, Any] = {
    "symbols": [],
    "updated_at": 0.0,
}


def load_momentum_universe(
    *,
    request_func: Callable[..., Any],
    market_data_request_func: Callable[..., Any] | None = None,
    force_refresh: bool = False,
) -> list[str]:
    """
    Load all active, tradable US equity symbols available through Alpaca.

    This is intentionally separate from the legacy S&P 500 universe.
    """
    current_time = time.time()

    cached_symbols = list(
        _universe_cache.get(
            "symbols",
            [],
        )
    )

    cached_time = float(
        _universe_cache.get(
            "updated_at",
            0.0,
        )
        or 0.0
    )

    cache_is_valid = (
        bool(cached_symbols)
        and current_time - cached_time
        < CACHE_SECONDS
    )

    if (
        cache_is_valid
        and not force_refresh
    ):
        return cached_symbols.copy()

    payload = request_func(
        "GET",
        "/v2/assets",
        params={
            "status": "active",
            "asset_class": "us_equity",
        },
        timeout=20.0,
    )

    if not isinstance(
        payload,
        list,
    ):
        raise RuntimeError(
            "Alpaca returned an invalid asset list."
        )

    symbols: list[str] = []
    seen: set[str] = set()

    allowed_exchanges = {
        "NASDAQ",
        "NYSE",
        "AMEX",
        "ARCA",
    }

    for asset in payload:
        if not isinstance(
            asset,
            dict,
        ):
            continue

        if not bool(
            asset.get(
                "tradable"
            )
        ):
            continue

        exchange = str(
            asset.get(
                "exchange",
                "",
            )
        ).strip().upper()

        if (
            exchange
            and exchange
            not in allowed_exchanges
        ):
            continue

        symbol = str(
            asset.get(
                "symbol",
                "",
            )
        ).strip().upper()

        if not symbol:
            continue

        if not symbol.isalpha():
            continue

        asset_name = str(
            asset.get(
                "name",
                "",
            )
        ).strip().lower()

        excluded_name_terms = (
            " warrant",
            " warrants",
            " right",
            " rights",
            " unit",
            " units",
            " preferred",
            " depositary preferred",
        )

        if any(
            term in asset_name
            for term in excluded_name_terms
        ):
            continue

        if symbol in seen:
            continue

        symbols.append(
            symbol
        )
        seen.add(
            symbol
        )

    if not symbols:
        raise RuntimeError(
            "No active tradable US equities "
            "were returned by Alpaca."
        )

    allowed_symbols = set(symbols)
    candidate_symbols: list[str] = []
    candidate_seen: set[str] = set()

    if market_data_request_func is not None:
        try:
            most_active_payload = market_data_request_func(
                "GET",
                "/v1beta1/screener/stocks/most-actives",
                params={
                    "by": "volume",
                    "top": 100,
                },
                timeout=15.0,
            )

            if isinstance(most_active_payload, dict):
                for item in most_active_payload.get(
                    "most_actives",
                    [],
                ):
                    if not isinstance(item, dict):
                        continue

                    symbol = str(
                        item.get(
                            "symbol",
                            "",
                        )
                    ).strip().upper()

                    if (
                        symbol in allowed_symbols
                        and symbol not in candidate_seen
                    ):
                        candidate_symbols.append(symbol)
                        candidate_seen.add(symbol)

        except Exception:
            pass

        try:
            movers_payload = market_data_request_func(
                "GET",
                "/v1beta1/screener/stocks/movers",
                params={
                    "top": 50,
                },
                timeout=15.0,
            )

            if isinstance(movers_payload, dict):
                for group_name in (
                    "gainers",
                    "losers",
                ):
                    for item in movers_payload.get(
                        group_name,
                        [],
                    ):
                        if not isinstance(item, dict):
                            continue

                        symbol = str(
                            item.get(
                                "symbol",
                                "",
                            )
                        ).strip().upper()

                        if (
                            symbol in allowed_symbols
                            and symbol not in candidate_seen
                        ):
                            candidate_symbols.append(symbol)
                            candidate_seen.add(symbol)

        except Exception:
            pass

    if candidate_symbols:
        symbols = candidate_symbols

    _universe_cache[
        "symbols"
    ] = symbols.copy()

    _universe_cache[
        "updated_at"
    ] = time.time()

    return symbols.copy()






