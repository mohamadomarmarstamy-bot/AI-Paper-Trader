"""Read-only comparison of Pro Ticker research and cached scanner results."""

from typing import Any

from pro_ticker_shadow import get_pro_ticker_shadow_candidates
from scanner import get_fresh_scanner_candidates


def compare_pro_ticker_with_scanner(
    *,
    limit: int = 500,
    max_age_days: int = 1,
) -> dict[str, Any]:
    """Compare research candidates with fresh cached scanner results."""

    research = get_pro_ticker_shadow_candidates(
        limit=limit,
        max_age_days=max_age_days,
    )

    scanner_candidates = get_fresh_scanner_candidates()

    if not scanner_candidates:
        return {
            "paper": True,
            "research_only": True,
            "automatic_orders": False,
            "scanner_cache_available": False,
            "reason": "no_fresh_scanner_candidates",
            "pro_ticker_candidate_count": research["candidate_count"],
            "results": [],
        }

    scanner_by_symbol = {
        str(candidate.get("symbol") or "").strip().upper(): candidate
        for candidate in scanner_candidates
        if isinstance(candidate, dict)
        and candidate.get("symbol")
    }

    results = []

    for candidate in research["candidates"]:
        symbol = candidate["symbol"]
        scanner_match = scanner_by_symbol.get(symbol)

        results.append({
            "symbol": symbol,
            "pro_ticker_direction": candidate.get("direction"),
            "pro_ticker_published_date": candidate.get("published_date"),
            "scanner_match": scanner_match is not None,
            "scanner_rank": (
                scanner_match.get("scanner_rank")
                if scanner_match else None
            ),
            "scanner_score": (
                scanner_match.get("score")
                if scanner_match else None
            ),
            "scanner_signal": (
                scanner_match.get("signal")
                if scanner_match else None
            ),
            "research_only": True,
            "eligible_for_automatic_orders": False,
        })

    return {
        "paper": True,
        "research_only": True,
        "automatic_orders": False,
        "scanner_cache_available": True,
        "scanner_candidate_count": len(scanner_candidates),
        "pro_ticker_candidate_count": research["candidate_count"],
        "matched_count": sum(
            result["scanner_match"] for result in results
        ),
        "results": results,
    }
