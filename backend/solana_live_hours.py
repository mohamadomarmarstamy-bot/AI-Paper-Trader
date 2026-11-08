"""Daily owner-requested live entry window. Never governs exits or PAPER."""
from datetime import datetime
from zoneinfo import ZoneInfo
import time

TIMEZONE = 'America/New_York'


# Owner-selected current live roster. This changes hours only; source eligibility,
# manual pause, mint age and execution checks continue to apply independently.
ALL_DAY_SOURCE_NAMES = {
    '4DdrfiDHpmx55i4SPssxVzS9ZaKLb8qr45NKY9Er9nNh': 'Mr. Frog',
    'ApRnQN2HkbCn7W2WWiT2FEKvuKJp9LugRyAE1a9Hdz1': 'Solkrow',
}
ALL_DAY_SOURCES = frozenset(ALL_DAY_SOURCE_NAMES)


def entry_allowed(at=None, source_wallet=None):
    if source_wallet in ALL_DAY_SOURCES:
        return True
    local = datetime.fromtimestamp(time.time() if at is None else at, ZoneInfo(TIMEZONE))
    return local.hour < 14


def entry_status(at=None):
    return {'timezone': TIMEZONE, 'new_buys_allowed': entry_allowed(at),
            'all_day_source_names': sorted(ALL_DAY_SOURCE_NAMES.values()),
            'all_day_sources': sorted(ALL_DAY_SOURCES), 'pause_at': '14:00', 'resume_at': '00:00', 'exits_monitored': True,
            'basis': 'Daily live entry window only; manual pause and all readiness checks remain binding.'}
