"""Deterministic research-only ranking of normalized market snapshots.

No trading signals, model training, broker orders, or market-data fetches.
"""
from decimal import Decimal

def rank_research_candidates(snapshots, *, limit=20):
    """Rank distinct symbols by relative volume; preserve evidence provenance."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Invalid candidate limit")
    if not isinstance(snapshots, list) or len(snapshots) > 1000:
        raise ValueError("Invalid candidate batch")
    from pro_ticker_market_snapshot import normalize_market_snapshot
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    latest = {}
    for raw in snapshots:
        record = normalize_market_snapshot(raw, now=now)
        symbol = record["symbol"]
        previous = latest.get(symbol)
        if previous is None or record["observed_at"] > previous["observed_at"]:
            latest[symbol] = record
    ranked = sorted(latest.values(), key=lambda r: (-Decimal(r["relative_volume"]), r["symbol"]))
    return {
        "paper": True, "research_only": True, "order_execution_authorized": False,
        "evaluation": "relative_volume_ranking_not_ai_score",
        "count": len(latest), "has_more": len(latest) > limit,
        "candidates": ranked[:limit],
    }
