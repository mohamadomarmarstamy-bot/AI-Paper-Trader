"""Validate broker evidence payload shape for PAPER-only read-only audits."""
from __future__ import annotations

from typing import Any


def validate_paper_snapshot_inputs(
    entry_order: Any, exit_orders: Any,
) -> str | None:
    """Return a failure reason, or None for structurally valid snapshots."""
    if not isinstance(entry_order, dict):
        return "entry_broker_snapshot_missing"
    if not isinstance(exit_orders, dict):
        return "invalid_exit_snapshot_mapping"
    for order_id, snapshot in exit_orders.items():
        if not isinstance(order_id, str) or not order_id.strip() or order_id != order_id.strip():
            return "invalid_exit_order_identifier"
        if not isinstance(snapshot, dict):
            return "invalid_exit_broker_snapshot"
        if snapshot.get("id") != order_id:
            return "exit_snapshot_order_id_mismatch"
    return None
