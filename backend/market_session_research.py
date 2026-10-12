"""Read-only market-session classification for momentum research."""

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo


EASTERN = ZoneInfo("America/New_York")


def classify_market_session(
    observed_at: datetime,
    calendar_entry: dict[str, Any] | None,
    *,
    calendar_verified: bool = True,
) -> dict[str, Any]:
    """Classify the observed market session without placing orders."""

    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")

    eastern_now = observed_at.astimezone(EASTERN)

    result = {
        "market_session": "unknown",
        "regular_market_open": False,
        "market_calendar_verified": calendar_verified,
        "market_time_eastern": eastern_now.isoformat(),
        "research_only": True,
    }

    if not calendar_verified:
        return result

    # No trading calendar entry means the exchange is closed.
    if calendar_entry is None:
        result["market_session"] = "closed"
        return result

    if not isinstance(calendar_entry, dict):
        return result

    # Verify that the calendar entry belongs to the observed date.
    calendar_date = str(calendar_entry.get("date", "")).strip()

    if calendar_date != eastern_now.date().isoformat():
        return result

    try:
        open_value = time.fromisoformat(
            str(calendar_entry["open"])
        )
        close_value = time.fromisoformat(
            str(calendar_entry["close"])
        )
    except (KeyError, TypeError, ValueError):
        return result

    if (
        open_value.tzinfo is not None
        or close_value.tzinfo is not None
        or open_value >= close_value
    ):
        return result

    current_time = eastern_now.time().replace(tzinfo=None)

    premarket_start = time(4, 0)
    after_hours_end = time(20, 0)

    if open_value <= current_time < close_value:
        result["market_session"] = "regular_session"
        result["regular_market_open"] = True

    elif premarket_start <= current_time < open_value:
        result["market_session"] = "pre_market"

    elif close_value <= current_time < after_hours_end:
        result["market_session"] = "after_hours"

    else:
        result["market_session"] = "closed"

    return result
