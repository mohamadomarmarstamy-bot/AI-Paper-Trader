"""Conservative combined PAPER allocation and shared-broker review.

A clean internal ledger and matching broker quantity are not ownership proof.
No orders, fund releases, or database writes.
"""
from __future__ import annotations
from typing import Any
from pro_ticker_virtual_allocation_audit import audit_virtual_allocations
from pro_ticker_position_audit import audit_strategy_paper_positions


def review_paper_allocation_and_broker(
    reservations: list[dict[str, Any]],
    strategy_positions: list[dict[str, Any]],
    broker_positions: list[dict[str, Any]],
) -> dict[str, Any]:
    internal = audit_virtual_allocations(reservations, strategy_positions)
    if not internal.get("ledger_consistent"):
        return {
            "paper": True, "preview_only": True, "reconciled": False,
            "order_submitted": False, "database_updated": False,
            "budget_released": False, "reason": "internal_allocation_audit_failed",
            "allocation_audit": internal, "broker_audit": None,
        }
    try:
        broker = audit_strategy_paper_positions(strategy_positions, broker_positions)
    except (TypeError, ValueError, KeyError):
        broker = {"reason": "invalid_broker_snapshot", "reconciled": False}
    return {
        "paper": True, "preview_only": True, "reconciled": False,
        "order_submitted": False, "database_updated": False,
        "budget_released": False,
        "reason": "broker_review_requires_verified_strategy_order_ownership",
        "allocation_audit": internal, "broker_audit": broker,
    }
