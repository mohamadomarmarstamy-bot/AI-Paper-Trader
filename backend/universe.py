from __future__ import annotations

import time
import copy
import threading
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



def record_explosive_movers_for_research(
    movers_payload: dict[str, Any],
    allowed_symbols: set[str],
) -> int:
    """Record Alpaca gainers of 25%+ without affecting scanning."""

    from datetime import datetime, timezone
    import math

    recorded_count = 0

    gainers = movers_payload.get("gainers", [])

    if not isinstance(gainers, list):
        return 0

    for item in gainers:
        if not isinstance(item, dict):
            continue

        try:
            symbol = str(item.get("symbol", "")).strip().upper()
            price = float(item.get("price"))
            percent_change = float(item.get("percent_change"))

            if (
                not symbol
                or not math.isfinite(price)
                or not math.isfinite(percent_change)
                or price <= 0
                or percent_change < 25.0
            ):
                continue

            from database import record_explosive_momentum_observation

            record_explosive_momentum_observation(
                symbol=symbol,
                observed_at=datetime.now(timezone.utc).isoformat(),
                reference_price=price,
                percent_change=percent_change,
                scanner_eligible=symbol in allowed_symbols,
            )

            recorded_count += 1

        except Exception as error:
            print(
                "Explosive momentum research recording failed: "
                f"{error}"
            )

    return recorded_count

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


_rotation_cursor = 0
_universe_lock = threading.RLock()


def load_momentum_universe(*, request_func, market_data_request_func=None, force_refresh=False):
    with _universe_lock:
        return _refresh_momentum_universe(request_func=request_func,
            market_data_request_func=market_data_request_func,force_refresh=force_refresh)


def _refresh_momentum_universe(*, request_func, market_data_request_func, force_refresh):
    from fast_mover_discovery import (POOL_LIMIT,ROTATION_SIZE,SNAPSHOT_BATCH,rank_pool,snapshot_metrics)
    global _rotation_cursor
    # A UI force-refresh must not re-download the slow-changing asset master.
    allowed_list=_load_allowed_symbols(request_func=request_func,force_refresh=False)
    allowed=set(allowed_list);now=time.time()
    if not force_refresh and _hot_universe_cache.get('symbols') and 0<=now-_hot_universe_cache.get('updated_at',0)<HOT_UNIVERSE_CACHE_SECONDS:
        return [s for s in _hot_universe_cache['symbols'] if s in allowed]
    rotation=[allowed_list[(_rotation_cursor+i)%len(allowed_list)] for i in range(min(ROTATION_SIZE,len(allowed_list)))]
    _rotation_cursor=(_rotation_cursor+len(rotation))%len(allowed_list)
    hot=[];metadata={};failures=[];snapshot_count=0
    def add(items):
        for item in items if isinstance(items,list) else []:
            if not isinstance(item,dict): continue
            symbol=str(item.get('symbol','')).strip().upper()
            if symbol in allowed and symbol not in hot: hot.append(symbol)
    if market_data_request_func is not None:
        try:
            payload=market_data_request_func('GET','/v1beta1/screener/stocks/movers',params={'top':50},timeout=8.0)
            if not isinstance(payload,dict): raise ValueError('Invalid movers')
            add(payload.get('gainers',[]));add(payload.get('losers',[]))
            record_explosive_movers_for_research(payload,allowed)
        except Exception: failures.append('movers_unavailable')
        for by in ('volume','trades'):
            try:
                payload=market_data_request_func('GET','/v1beta1/screener/stocks/most-actives',params={'by':by,'top':100},timeout=8.0)
                if not isinstance(payload,dict): raise ValueError('Invalid actives')
                add(payload.get('most_actives',[]))
            except Exception: failures.append('most_active_'+by+'_unavailable')
    pool=list(dict.fromkeys(hot+rotation))[:POOL_LIMIT]
    if market_data_request_func is not None:
        for start in range(0,len(pool),SNAPSHOT_BATCH):
            batch=pool[start:start+SNAPSHOT_BATCH]
            try:
                snapshots=market_data_request_func('GET','/v2/stocks/snapshots',params={'symbols':','.join(batch),'feed':'iex'},timeout=8.0)
                if not isinstance(snapshots,dict): raise ValueError('Invalid snapshots')
                for symbol in batch:
                    metrics=snapshot_metrics(snapshots.get(symbol),now=time.time(),feed='iex')
                    if metrics: metadata[symbol]={'symbol':symbol,**metrics};snapshot_count+=1
            except Exception: failures.append('snapshot_batch_unavailable')
    symbols=rank_pool(pool,rotation,metadata)
    finished=time.time()
    _hot_universe_cache.update(symbols=symbols.copy(),movers=metadata,updated_at=finished,
        coverage={'profile':'fast_movers_v1','tradable_universe':len(allowed_list),'screened_symbols':len(pool),
          'hot_symbols':len(hot),'rotating_symbols':len(rotation),'fresh_snapshots':snapshot_count,
          'priority_movers':sum(m.get('fast_mover_priority') is True for m in metadata.values()),
          'history_candidates':len(symbols),'snapshot_feed':'iex','partial':bool(failures) or snapshot_count<len(pool),
          'missing_or_stale_snapshots':len(pool)-snapshot_count,
          'limitations':failures,'completed_at':finished,
          'market_cap_verified':False,'float_verified':False})
    return symbols.copy()


def get_momentum_universe_status():
    result=copy.deepcopy(_hot_universe_cache.get('coverage',{}))
    result['age_seconds']=max(0,time.time()-result['completed_at']) if result.get('completed_at') else None
    return result


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

    captured=metadata.get('captured_at')
    if not isinstance(captured,(int,float)) or not 0<=time.time()-captured<=90:
        return {}
    from fast_mover_discovery import timestamp,MAX_AGE
    for key in ('snapshot_trade_at','snapshot_quote_at'):
        stamp=timestamp(metadata.get(key))
        if stamp is None or not 0<=time.time()-stamp.timestamp()<=MAX_AGE: return {}
    return metadata.copy()


def refresh_selected_momentum_snapshots(symbols, market_data_request_func):
    """Refresh selected live evidence after slow daily analysis, never reuse stale boosts."""
    from fast_mover_discovery import SNAPSHOT_BATCH,snapshot_metrics
    if market_data_request_func is None: return
    valid={};failed=False
    for start in range(0,len(symbols),SNAPSHOT_BATCH):
        batch=symbols[start:start+SNAPSHOT_BATCH]
        try:
            payload=market_data_request_func('GET','/v2/stocks/snapshots',params={'symbols':','.join(batch),'feed':'iex'},timeout=8.0)
            if not isinstance(payload,dict): raise ValueError('Invalid snapshots')
            for symbol in batch:
                metrics=snapshot_metrics(payload.get(symbol),now=time.time(),feed='iex')
                if metrics: valid[symbol]={'symbol':symbol,**metrics}
        except Exception: failed=True
    # Clear selected stale readings on partial failures, not just overwrite successes.
    metadata=dict(_hot_universe_cache.get('movers',{}))
    for symbol in symbols: metadata.pop(symbol,None)
    metadata.update(valid);_hot_universe_cache['movers']=metadata
    coverage=copy.deepcopy(_hot_universe_cache.get('coverage',{}))
    coverage.update(fresh_selected_snapshots=len(valid),live_refreshed_at=time.time(),selected_unavailable_snapshots=len(symbols)-len(valid))
    if len(valid)<len(symbols): coverage['partial']=True
    if failed:
        coverage['partial']=True
        coverage['limitations']=list(coverage.get('limitations',[]))+['selected_snapshots_unavailable']
    _hot_universe_cache['coverage']=coverage
