from __future__ import annotations

import time
from typing import Any, Callable


# The tradable Alpaca asset list changes relatively slowly.
ASSET_CACHE_SECONDS = 15 * 60

# Movers and most-actives are time-sensitive.
# Refresh these much more frequently so sudden movers are discovered.
HOT_UNIVERSE_CACHE_SECONDS = 60


_asset_cache: dict[str, Any] = {
    "symbols": [],
    "updated_at": 0.0,
}

_hot_universe_cache: dict[str, Any] = {
    "symbols": [],
    "movers": {},
    "updated_at": 0.0,
}


def _load_allowed_symbols(
    *,
    request_func: Callable[..., Any],
    force_refresh: bool = False,
) -> list[str]:
    """
    Load active, tradable US equities from Alpaca.

    This list is relatively stable, so it is cached separately
    from the fast-moving screener candidate universe.
    """
    current_time = time.time()

    cached_symbols = list(
        _asset_cache.get(
            "symbols",
            [],
        )
    )

    cached_time = float(
        _asset_cache.get(
            "updated_at",
            0.0,
        )
        or 0.0
    )

    cache_is_valid = (
        bool(cached_symbols)
        and current_time - cached_time
        < ASSET_CACHE_SECONDS
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

    _asset_cache[
        "symbols"
    ] = symbols.copy()

    _asset_cache[
        "updated_at"
    ] = time.time()

    return symbols.copy()


def load_momentum_universe(
    *,
    request_func: Callable[..., Any],
    market_data_request_func: Callable[..., Any] | None = None,
    force_refresh: bool = False,
) -> list[str]:
    """
    Build a fast-refreshing momentum universe.

    The broad tradable asset list is cached for 15 minutes,
    while Alpaca most-actives and movers refresh every minute.
    """
    allowed_symbols_list = _load_allowed_symbols(
        request_func=request_func,
        force_refresh=force_refresh,
    )

    allowed_symbols = set(
        allowed_symbols_list
    )

    current_time = time.time()

    cached_hot_symbols = list(
        _hot_universe_cache.get(
            "symbols",
            [],
        )
    )

    cached_hot_time = float(
        _hot_universe_cache.get(
            "updated_at",
            0.0,
        )
        or 0.0
    )

    hot_cache_is_valid = (
        bool(cached_hot_symbols)
        and current_time - cached_hot_time
        < HOT_UNIVERSE_CACHE_SECONDS
    )

    if (
        hot_cache_is_valid
        and not force_refresh
    ):
        return cached_hot_symbols.copy()

    candidate_symbols: list[str] = []
    candidate_seen: set[str] = set()
    mover_metadata: dict[str, dict[str, Any]] = {}

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

            if isinstance(
                most_active_payload,
                dict,
            ):
                for item in most_active_payload.get(
                    "most_actives",
                    [],
                ):
                    if not isinstance(
                        item,
                        dict,
                    ):
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
                        candidate_symbols.append(
                            symbol
                        )
                        candidate_seen.add(
                            symbol
                        )

        except Exception as error:
            print(
                "Alpaca most-actives request failed: "
                f"{error}"
            )

        try:
            movers_payload = market_data_request_func(
                "GET",
                "/v1beta1/screener/stocks/movers",
                params={
                    "top": 50,
                },
                timeout=15.0,
            )

            if isinstance(
                movers_payload,
                dict,
            ):
                for group_name in (
                    "gainers",
                    "losers",
                ):
                    for item in movers_payload.get(
                        group_name,
                        [],
                    ):
                        if not isinstance(
                            item,
                            dict,
                        ):
                            continue

                        symbol = str(
                            item.get(
                                "symbol",
                                "",
                            )
                        ).strip().upper()

                        if symbol in allowed_symbols:
                            mover_metadata[symbol] = {
                                "symbol": symbol,
                                "price": item.get("price"),
                                "change": item.get("change"),
                                "percent_change": item.get(
                                    "percent_change"
                                ),
                            }


                        if (
                            symbol in allowed_symbols
                            and symbol not in candidate_seen
                        ):
                            candidate_symbols.append(
                                symbol
                            )
                            candidate_seen.add(
                                symbol
                            )

        except Exception as error:
            print(
                "Alpaca movers request failed: "
                f"{error}"
            )

    if candidate_symbols:
        hot_symbols = candidate_symbols
    elif cached_hot_symbols:
        print(
            "No fresh Alpaca momentum candidates "
            "were returned; using the previous hot universe."
        )
        hot_symbols = cached_hot_symbols
    else:
        print(
            "No Alpaca momentum candidates were returned; "
            "using the allowed tradable universe."
        )
        hot_symbols = allowed_symbols_list

    if mover_metadata:
        _hot_universe_cache["movers"] = {
            symbol: data.copy()
            for symbol, data in mover_metadata.items()
        }


    _hot_universe_cache[
        "symbols"
    ] = hot_symbols.copy()

    _hot_universe_cache[
        "updated_at"
    ] = time.time()

    return hot_symbols.copy()


def get_momentum_mover_metadata(
    symbol: str,
) -> dict[str, Any]:
    normalized_symbol = str(symbol).strip().upper()

    movers = _hot_universe_cache.get(
        "movers",
        {},
    )

    if not isinstance(movers, dict):
        return {}

    metadata = movers.get(
        normalized_symbol,
        {},
    )

    if not isinstance(metadata, dict):
        return {}

    return metadata.copy()
