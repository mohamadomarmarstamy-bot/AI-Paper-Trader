"""Read-only audit of simulated Pro Ticker reservations and attributed positions.

Matching the shared broker holdings cannot prove strategy ownership.
This module never releases funds or places orders.
"""
from __future__ import annotations
from typing import Any


def audit_virtual_allocations(reservations: list[dict[str, Any]], positions: list[dict[str, Any]]) -> dict[str, Any]:
    result = {"paper": True, "preview_only": True, "reconciled": False,
              "database_updated": False, "budget_released": False, "issues": []}
    if not isinstance(reservations, list) or not isinstance(positions, list):
        return {**result, "reason": "invalid_snapshot"}
    held = {}
    for row in positions:
        if not isinstance(row, dict):
            return {**result, "reason": "invalid_position"}
        symbol, shares = row.get("symbol"), row.get("shares")
        if not isinstance(symbol, str) or not symbol or symbol in held or isinstance(shares, bool) or not isinstance(shares, int) or shares < 0:
            return {**result, "reason": "invalid_position"}
        held[symbol] = shares
    active = {}
    for row in reservations:
        if not isinstance(row, dict):
            return {**result, "reason": "invalid_reservation"}
        if row.get("status") not in {"reserved", "submitted", "filled"}:
            continue
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            return {**result, "reason": "invalid_reservation"}
        if symbol in active:
            result["issues"].append({"symbol": symbol, "reason": "duplicate_active_allocation"})
        active[symbol] = row
        shares = held.get(symbol, 0)
        expected = row.get("expected_shares")
        if row["status"] != "reserved" and (not isinstance(expected, int) or isinstance(expected, bool) or expected < 1):
            result["issues"].append({"symbol": symbol, "reason": "missing_expected_shares"})
        elif isinstance(expected, int) and not isinstance(expected, bool) and shares > expected:
            result["issues"].append({"symbol": symbol, "reason": "shares_exceed_reserved_order"})
        if row["status"] != "reserved" and not row.get("broker_order_id"):
            result["issues"].append({"symbol": symbol, "reason": "missing_order_link"})
        if row["status"] == "reserved" and shares:
            result["issues"].append({"symbol": symbol, "reason": "unsubmitted_position"})
    for symbol, shares in held.items():
        if shares and symbol not in active:
            result["issues"].append({"symbol": symbol, "reason": "position_without_allocation"})
    return {**result, "reason": "issues_found" if result["issues"] else "ledger_consistent_broker_unverified",
            "ledger_consistent": not result["issues"], "active_reservations": len(active)}
