"""Read-only PAPER strategy versus broker holdings audit.

Broker holdings are shared by strategies. Equality of quantities does NOT
establish order ownership or authorize any budget release.
"""
from __future__ import annotations

from math import isfinite
from typing import Any


def audit_strategy_paper_positions(
    strategy_positions: list[dict[str, Any]],
    broker_positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Fail closed if a PAPER broker snapshot is malformed or incomplete."""
    result = {
        "paper": True, "preview_only": True, "database_updated": False,
        "order_submitted": False, "broker_reconciled": False,
        "safe_to_release_budget": False, "results": [],
    }
    if not isinstance(strategy_positions, list) or not isinstance(broker_positions, list):
        return {**result, "reason": "snapshot_unavailable"}
    broker_by_symbol: dict[str, float] = {}
    for row in broker_positions:
        if not isinstance(row, dict):
            return {**result, "reason": "invalid_broker_position"}
        symbol = str(row.get("symbol") or "").strip().upper()
        try:
            qty = float(row["qty"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return {**result, "reason": "invalid_broker_quantity"}
        if not symbol or not isfinite(qty) or qty < 0 or symbol in broker_by_symbol:
            return {**result, "reason": "invalid_or_duplicate_broker_position"}
        broker_by_symbol[symbol] = qty
    seen: set[str] = set()
    rows = []
    for row in strategy_positions:
        if not isinstance(row, dict):
            return {**result, "reason": "invalid_strategy_position"}
        symbol = str(row.get("symbol") or "").strip().upper()
        shares = row.get("shares")
        if (not symbol or symbol in seen or isinstance(shares, bool)
                or not isinstance(shares, int) or shares < 0):
            return {**result, "reason": "invalid_or_duplicate_strategy_position"}
        seen.add(symbol)
        broker_qty = broker_by_symbol.get(symbol, 0.0)
        rows.append({
            "symbol": symbol, "strategy_shares": shares,
            "broker_account_shares": broker_qty,
            "broker_covers_strategy_shares": broker_qty >= shares,
            "reason": ("broker_shortfall" if broker_qty < shares
                       else "quantity_compatible_ownership_unverified"),
        })
    return {**result, "reason": "read_only_comparison", "results": rows}
