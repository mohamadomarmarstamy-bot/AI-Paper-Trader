"""Optional Pro Ticker candidate source for PAPER scanner experiments."""

import os
from typing import Any


def pro_ticker_scanner_enabled() -> bool:
    """Require an explicit feature flag before including research symbols."""
    return (
        os.getenv("PRO_TICKER_SCANNER_ENABLED", "false")
        .strip()
        .lower()
        == "true"
    )


def load_pro_ticker_scanner_symbols() -> list[str]:
    """Return recent Pro Ticker symbols for scanner analysis only."""

    if not pro_ticker_scanner_enabled():
        return []

    from pro_ticker_shadow import get_pro_ticker_shadow_candidates

    research: dict[str, Any] = get_pro_ticker_shadow_candidates(
        limit=500,
        max_age_days=1,
    )

    symbols = []
    seen = set()

    for candidate in research.get("candidates", []):
        symbol = str(candidate.get("symbol") or "").strip().upper()

        if (
            symbol
            and symbol.isalnum()
            and symbol not in seen
        ):
            symbols.append(symbol)
            seen.add(symbol)

    return symbols
