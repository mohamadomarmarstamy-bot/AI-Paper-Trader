"""Read-only audit of Pro Ticker shares against the shared Alpaca PAPER account.

The shared broker may hold shares belonging to the original scanner, so
broker shares >= Pro Ticker shares is necessary, NOT proof of ownership.
"""
from __future__ import annotations

import math
from typing import Any


def audit_strategy_paper_positions(
    strategy_positions: list[dict[str, Any]],
    broker_positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Fail closed for malformed, missing, fractional or short broker snapshots."""
    findings: list[dict[str, Any]] = []
    if not isinstance(strategy_positions, list) or not isinstance(broker_positions, list):
        return {"paper": True, "preview_only": True, "reconciled": False,
                "reason": "invalid_snapshot", "findings": []}
    broker_by_symbol: dict[str, int] = {}
    try:
        for row in broker_positions:
            if not isinstance(row, dict):
                raise ValueError("invalid broker position")
            symbol = str(row.get("symbol") or "").strip().upper()
            raw_qty = row.get("qty")
            if not symbol or isinstance(raw_qty, bool):
                raise ValueError("invalid broker position")
            qty = float(raw_qty)
            if not math.isfinite(qty) or qty < 0 or not qty.is_integer():
                raise ValueError("unsupported fractional or short broker position")
            if symbol in broker_by_symbol:
                raise ValueError("duplicate broker symbol")
            broker_by_symbol[symbol] = int(qty)
        seen: set[str] = set()
        for row in strategy_positions:
            if not isinstance(row, dict):
                raise ValueError("invalid strategy position")
            symbol = str(row.get("symbol") or "").strip().upper()
            shares = row.get("shares")
            if (
                not symbol or symbol in seen or isinstance(shares, bool)
                or not isinstance(shares, int) or shares < 0
            ):
                raise ValueError("invalid strategy position")
            seen.add(symbol)
            broker_shares = broker_by_symbol.get(symbol, 0)
            findings.append({
                "symbol": symbol, "strategy_shares": shares,
                "broker_shares": broker_shares,
                "broker_covers_strategy_shares": broker_shares >= shares,
                "unattributed_broker_shares": max(0, broker_shares - shares),
                "shortfall_shares": max(0, shares - broker_shares),
            })
    except (ValueError, TypeError, OverflowError):
        return {"paper": True, "preview_only": True, "reconciled": False,
                "reason": "invalid_snapshot", "findings": []}
    shortfalls = any(item["shortfall_shares"] > 0 for item in findings)
    return {
        "paper": True, "preview_only": True,
        # Even a matching snapshot cannot establish strategy order ownership.
        "reconciled": False, "broker_snapshot_consistent": not shortfalls,
        "reason": "broker_position_shortfall" if shortfalls else "order_ownership_reconciliation_required",
        "findings": findings,
    }
