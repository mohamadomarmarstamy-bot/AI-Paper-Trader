from datetime import datetime, timezone
from typing import Any

from database import load_pro_ticker_research


def get_pro_ticker_shadow_candidates(
    limit: int = 500,
    max_age_days: int = 1,
) -> dict[str, Any]:
    """Find recent Pro Ticker research for PAPER shadow evaluation."""

    records = load_pro_ticker_research(limit=limit)

    today = datetime.now(timezone.utc).date()

    candidates = []
    rejected = []
    seen_symbols = set()

    for record in records:
        symbol = str(record.get("symbol") or "").strip().upper()
        published = str(record.get("published_date") or "").strip()

        if not symbol:
            rejected.append({
                "symbol": None,
                "reason": "missing_symbol",
            })
            continue

        try:
            published_date = datetime.strptime(
                published,
                "%A, %B %d, %Y",
            ).date()
        except (ValueError, TypeError):
            rejected.append({
                "symbol": symbol,
                "reason": "invalid_publication_date",
            })
            continue

        age_days = (today - published_date).days

        if age_days < 0 or age_days > max_age_days:
            rejected.append({
                "symbol": symbol,
                "reason": "outside_publication_window",
            })
            continue

        if symbol in seen_symbols:
            rejected.append({
                "symbol": symbol,
                "reason": "duplicate_symbol",
            })
            continue

        seen_symbols.add(symbol)

        candidates.append({
            "symbol": symbol,
            "published_date": published,
            "alert_date": record.get("alert_date"),
            "alert_time": record.get("alert_time"),
            "direction": record.get("direction"),
            "article_url": record.get("article_url"),
            "research_only": True,
            "eligible_for_automatic_orders": False,
        })

    return {
        "paper": True,
        "research_only": True,
        "automatic_orders": False,
        "candidate_count": len(candidates),
        "rejected_count": len(rejected),
        "candidates": candidates,
        "rejected": rejected,
    }
