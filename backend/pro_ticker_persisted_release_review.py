"""Read-only eligibility evidence assembled from persisted PAPER strategy ledgers.

Persisted records alone are not broker-origin verification. This audit is
intentionally unable to authorize or release virtual funds.
"""
from __future__ import annotations

from typing import Any

from database import get_connection
from pro_ticker_budget_ledger import get_pro_ticker_reservation
from pro_ticker_release_eligibility import review_paper_release_eligibility


def review_persisted_paper_release(reservation_id: str) -> dict[str, Any]:
    reservation = get_pro_ticker_reservation(reservation_id)
    base = {
        "paper": True, "preview_only": True, "database_updated": False,
        "budget_released": False, "release_authorized": False,
        "eligible_for_independent_review": False,
    }
    if reservation is None:
        return {**base, "reason": "reservation_missing"}
    symbol = reservation["symbol"]
    entry_id = reservation.get("broker_order_id")
    if not entry_id:
        return {**base, "reason": "entry_order_unlinked"}
    with get_connection() as conn:
        # Read only; never infer a completed exit from absent broker positions.
        entry = conn.execute(
            "SELECT side, symbol, COALESCE(SUM(shares), 0) AS shares "
            "FROM pro_ticker_strategy_fill_events WHERE broker_order_id = ? "
            "GROUP BY side, symbol", (entry_id,),
        ).fetchall()
        if len(entry) != 1 or entry[0]["side"] != "buy" or entry[0]["symbol"] != symbol:
            return {**base, "reason": "persisted_entry_fill_missing_or_conflicting"}
        links = conn.execute(
            "SELECT broker_order_id, symbol FROM pro_ticker_paper_exit_links "
            "WHERE reservation_id = ?", (reservation_id,),
        ).fetchall()
        exits: list[dict[str, Any]] = []
        for link in links:
            fills = conn.execute(
                "SELECT symbol, side, COALESCE(SUM(shares), 0) AS shares "
                "FROM pro_ticker_strategy_fill_events WHERE broker_order_id = ? "
                "GROUP BY symbol, side", (link["broker_order_id"],),
            ).fetchall()
            if len(fills) != 1 or fills[0]["symbol"] != symbol or fills[0]["side"] != "sell":
                return {**base, "reason": "persisted_exit_fill_missing_or_conflicting"}
            exits.append({
                "broker_order_id": link["broker_order_id"], "symbol": symbol,
                "side": "sell", "accounted_shares": int(fills[0]["shares"]),
                "ownership_verified": True, "fill_verified": True,
            })
        # Strategy positions are aggregated by symbol, not reservation.
        # Never mistake other reservations' fills for this reservation's exit.
        other_reservations = conn.execute(
            "SELECT COUNT(*) FROM pro_ticker_paper_reservations "
            "WHERE symbol = ? AND id != ? AND status IN ('reserved','submitted','filled')",
            (symbol, reservation_id),
        ).fetchone()[0]
        if other_reservations:
            return {**base, "reason": "other_active_reservations_share_symbol"}
        unlinked_sells = conn.execute(
            "SELECT COUNT(*) FROM pro_ticker_strategy_fill_events "
            "WHERE symbol = ? AND side = 'sell' AND broker_order_id NOT IN "
            "(SELECT broker_order_id FROM pro_ticker_paper_exit_links WHERE reservation_id = ?)",
            (symbol, reservation_id),
        ).fetchone()[0]
        if unlinked_sells:
            return {**base, "reason": "unattributed_strategy_sell_fills"}
        position = conn.execute(
            "SELECT shares FROM pro_ticker_strategy_positions WHERE symbol = ?",
            (symbol,),
        ).fetchone()
    result = review_paper_release_eligibility(
        reservation=reservation, entry_order_id=entry_id,
        entry_filled_shares=int(entry[0]["shares"]), exit_fills=exits,
        remaining_strategy_shares=int(position["shares"]) if position else 0,
        broker_evidence_verified=False,
    )
    entry_shares = int(entry[0]["shares"])
    exit_shares = sum(fill["accounted_shares"] for fill in exits)
    remaining = int(position["shares"]) if position else 0
    # Diagnostics only; persisted records are not independent broker proof.
    if exit_shares > entry_shares:
        evidence_reason = "persisted_exit_shares_exceed_entry"
    elif entry_shares != exit_shares + remaining:
        evidence_reason = "persisted_share_balance_mismatch"
    elif remaining > 0:
        evidence_reason = "persisted_strategy_position_still_open"
    elif not exits:
        evidence_reason = "persisted_exit_fills_missing"
    else:
        evidence_reason = "persisted_records_not_independent_broker_proof"
    return {**result, "reason": evidence_reason,
            "persisted_entry_shares": entry_shares,
            "persisted_exit_shares": exit_shares,
            "persisted_remaining_shares": remaining,
            "persisted_exit_orders": len(exits)}
