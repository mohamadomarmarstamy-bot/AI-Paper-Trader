"""Read-only PAPER reconciliation using a bound Alpaca PAPER GET transport.

Never releases reservations, posts fills, or sends trading requests.
"""
from __future__ import annotations

from typing import Any, Callable

from pro_ticker_alpaca_paper_reader import make_alpaca_paper_order_reader
from pro_ticker_paper_broker_fetch_audit import audit_fetched_paper_reservation


def audit_alpaca_paper_reservation(
    reservation_id: str, *, paper_key_id: str,
    paper_secret_key: str, http_get: Callable[..., Any],
) -> dict[str, Any]:
    """Fetch linked orders from PAPER endpoint, then cross-check persisted fills.

    This function controls the endpoint but cannot attest to the authenticity
    of an injected HTTP transport. Results remain review-only.
    """
    base = {
        "paper": True, "preview_only": True, "verified": False,
        "broker_origin_authenticated": False, "release_authorized": False,
        "budget_released": False, "database_updated": False,
    }
    try:
        reader = make_alpaca_paper_order_reader(
            key_id=paper_key_id, secret_key=paper_secret_key,
            http_get=http_get,
        )
    except (ValueError, TypeError):
        return {**base, "reason": "invalid_paper_broker_configuration"}
    result = audit_fetched_paper_reservation(
        reservation_id, fetch_paper_order=reader,
    )
    return {
        **result, "broker_origin_authenticated": False,
        "release_authorized": False, "budget_released": False,
        "database_updated": False,
    }
