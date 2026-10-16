"""Strict read-only Alpaca PAPER order reader; no order submission.

Credentials and HTTP transport are supplied by the caller. Never log secrets.
"""
from __future__ import annotations

from typing import Any, Callable
from math import isfinite
from urllib.parse import quote

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
HttpGet = Callable[..., Any]


def make_alpaca_paper_order_reader(
    *, key_id: str, secret_key: str, http_get: HttpGet,
    timeout_seconds: float = 10.0,
) -> Callable[[str], dict[str, Any]]:
    """Bind GET-only order retrieval to the literal Alpaca PAPER hostname."""
    if not isinstance(key_id, str) or not key_id.strip():
        raise ValueError("PAPER API key required.")
    if not isinstance(secret_key, str) or not secret_key.strip():
        raise ValueError("PAPER API secret required.")
    if not callable(http_get):
        raise ValueError("HTTP GET transport required.")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
        raise ValueError("PAPER request timeout must be between 0 and 30 seconds.")

    def fetch_paper_order(order_id: str) -> dict[str, Any]:
        import uuid
        if not isinstance(order_id, str):
            raise ValueError("Invalid PAPER order ID.")
        try:
            if str(uuid.UUID(order_id)) != order_id:
                raise ValueError("Invalid PAPER order ID.")
        except (ValueError, AttributeError, TypeError) as error:
            raise ValueError("Invalid PAPER order ID.") from error
        if any(ord(char) < 33 or ord(char) == 127 for char in order_id):
            raise ValueError("PAPER order ID contains control characters.")
        # The request must remain pinned to the PAPER hostname even if the
        # injected HTTP client would otherwise follow external redirects.
        response = http_get(
            PAPER_BASE_URL + "/v2/orders/" + quote(order_id, safe=""),
            headers={
                "APCA-API-KEY-ID": key_id,
                "APCA-API-SECRET-KEY": secret_key,
            },
            timeout=timeout_seconds,
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise ValueError("PAPER broker redirect refused.")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("id") != order_id:
            raise ValueError("PAPER broker returned invalid or mismatched order.")
        return payload

    return fetch_paper_order
