"""Daily owner-requested live entry window. Never governs exits or PAPER."""
from datetime import datetime
from zoneinfo import ZoneInfo
import time

TIMEZONE = 'America/New_York'


def entry_allowed(at=None):
    local = datetime.fromtimestamp(time.time() if at is None else at, ZoneInfo(TIMEZONE))
    return local.hour < 14


def entry_status(at=None):
    return {'timezone': TIMEZONE, 'new_buys_allowed': entry_allowed(at),
            'pause_at': '14:00', 'resume_at': '00:00', 'exits_monitored': True,
            'basis': 'Daily live entry window only; manual pause and all readiness checks remain binding.'}
