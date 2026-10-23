"""Bounded read-only market research intake using a caller-owned quote provider.

Provider integration is deliberately injected. No credentials, network calls,
broker orders, database writes, or trading permissions are supplied here.
"""
import re
from decimal import Decimal
from datetime import datetime, timezone
from pro_ticker_market_snapshot import normalize_market_snapshot

def collect_research_snapshots(symbols, *, fetch_snapshot, now=None, limit=20):
    if not isinstance(symbols, list) or len(symbols) > 100:
        raise ValueError("Invalid symbol batch")
    if not callable(fetch_snapshot):
        raise ValueError("Read-only quote provider required")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Invalid candidate limit")
    clock = datetime.now(timezone.utc) if now is None else now
    if not isinstance(clock, datetime) or clock.tzinfo is None:
        raise ValueError("Timezone-aware clock required")
    unique = []
    seen = set()
    for symbol in symbols:
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol, re.ASCII):
            raise ValueError("Invalid symbol")
        if symbol not in seen:
            unique.append(symbol)
            seen.add(symbol)
    valid = []
    failures = []
    for symbol in unique:
        try:
            raw = fetch_snapshot(symbol)
            if not isinstance(raw, dict) or raw.get("symbol") != symbol:
                raise ValueError("Mismatched provider symbol")
            valid.append(normalize_market_snapshot(raw, now=clock))
        except Exception:
            failures.append({"symbol": symbol, "reason": "Quote unavailable or invalid"})
    # Ranking must use the same supplied clock, not an independently advanced one.
    ranked = sorted(valid, key=lambda row: (-Decimal(row["relative_volume"]), row["symbol"]))
    return {
        "paper": True, "research_only": True, "order_execution_authorized": False,
        "provider_configured": True, "requested": len(unique), "accepted": len(valid),
        "failed": len(failures), "failures": failures,
        "evaluation": "relative_volume_ranking_not_ai_score",
        "count": len(valid), "has_more": len(valid) > limit,
        "candidates": ranked[:limit],
    }
