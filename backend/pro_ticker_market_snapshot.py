"""Pure, fail-closed market snapshot normalization for future Pro Ticker research.

No network access, trading orders, database writes, or AI claims.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re

def _positive(value, name, *, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)) or len(str(value)) > 32:
        raise ValueError("Invalid " + name)
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Invalid " + name) from None
    if not number.is_finite() or (number < 0 if allow_zero else number <= 0):
        raise ValueError("Invalid " + name)
    return number

def normalize_market_snapshot(payload, *, now=None, max_age_seconds=120):
    """Validate an explicitly sourced, timestamped quote before research use.

    Staleness and future timestamps are rejected. The returned result is not
    broker-verified and cannot authorize an order.
    """
    if not isinstance(payload, dict):
        raise ValueError("Market snapshot must be an object")
    symbol = payload.get("symbol")
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol, flags=re.ASCII):
        raise ValueError("Invalid symbol")
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int) or not 1 <= max_age_seconds <= 600:
        raise ValueError("Invalid freshness window")
    observed = payload.get("observed_at")
    if not isinstance(observed, str):
        raise ValueError("Missing observation time")
    try:
        moment = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Invalid observation time") from None
    if moment.tzinfo is None:
        raise ValueError("Observation time must include timezone")
    moment = moment.astimezone(timezone.utc)
    clock = datetime.now(timezone.utc) if now is None else now
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Clock must include timezone")
    age = (clock.astimezone(timezone.utc) - moment).total_seconds()
    if age < 0 or age > max_age_seconds:
        raise ValueError("Stale or future market snapshot")
    price = _positive(payload.get("price"), "price")
    volume = _positive(payload.get("volume"), "volume", allow_zero=True)
    avg_volume = _positive(payload.get("average_volume"), "average volume")
    if volume != volume.to_integral_value():
        raise ValueError("Traded volume must be whole shares")
    source = payload.get("source")
    if not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", source):
        raise ValueError("Invalid source")
    movement=payload.get('price_change_percent')
    if movement is not None:
        if isinstance(movement,bool) or not isinstance(movement,(str,int,float)) or len(str(movement))>40:raise ValueError('Invalid movement')
        try:movement=Decimal(str(movement))
        except InvalidOperation:raise ValueError('Invalid movement') from None
        if not movement.is_finite() or movement < -100 or movement>1000000:raise ValueError('Invalid movement')
    return {
        "symbol": symbol, "price": str(price), "volume": int(volume),
        "average_volume": str(avg_volume),
        "price_change_percent": str(movement) if movement is not None else None,
        "research_score": None,
        "relative_volume": str((volume / avg_volume).quantize(Decimal("0.001"))),
        "observed_at": moment.isoformat(), "source": source,
        "market_data_verified": False, "order_execution_authorized": False,
        "research_only": True,
    }
