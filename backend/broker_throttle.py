"""Shared per-process Trading API pacing and display-only snapshot caching."""
import copy
from datetime import timezone
from email.utils import parsedate_to_datetime
import math
import threading
import time


class BrokerRateLimited(RuntimeError):
    def __init__(self, retry_after):
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__(f"Alpaca request budget is cooling down; retry in {self.retry_after} seconds.")


class BrokerRequestGate:
    def __init__(self, requests_per_minute=100, *, clock=time.monotonic,
                 wall_clock=time.time, sleep=time.sleep):
        self.interval = 60.0 / requests_per_minute
        self.clock, self.wall_clock, self.sleep = clock, wall_clock, sleep
        self.lock = threading.Lock()
        self.next_request = 0.0
        self.cooldown_until = 0.0

    def retry_after(self):
        with self.lock:
            return max(0.0, self.cooldown_until - self.clock())

    def acquire(self, wait_budget=2.0):
        deadline = self.clock() + max(0.0, wait_budget)
        while True:
            with self.lock:
                now = self.clock()
                delay = max(self.next_request, self.cooldown_until) - now
                if delay <= 0:
                    self.next_request = now + self.interval
                    return
                if now + delay > deadline:
                    raise BrokerRateLimited(delay)
            self.sleep(delay)

    def observe(self, status_code, headers):
        headers = {str(k).lower(): v for k, v in headers.items()}
        exhausted = str(headers.get('x-ratelimit-remaining', '')).strip() == '0'
        if status_code != 429 and not exhausted:
            return
        delays = []
        retry = headers.get('retry-after')
        if retry is not None:
            try:
                delays.append(float(retry))
            except (ValueError, TypeError):
                try:
                    date = parsedate_to_datetime(str(retry))
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    delays.append(date.timestamp() - self.wall_clock())
                except (TypeError, ValueError, OverflowError):
                    pass
        try:
            delays.append(float(headers['x-ratelimit-reset']) - self.wall_clock())
        except (KeyError, TypeError, ValueError):
            pass
        delays = [d for d in delays if math.isfinite(d) and d > 0]
        delay = max(delays) + 1.0 if delays else 60.0
        with self.lock:
            self.cooldown_until = max(self.cooldown_until, self.clock() + delay)


class DisplaySnapshotCache:
    """Coalesce concurrent dashboard reads; never use for execution decisions."""
    def __init__(self, ttl=10.0, *, clock=time.monotonic):
        self.ttl, self.clock = ttl, clock
        self.lock = threading.Lock()
        self.value = None
        self.expires = 0.0

    def get(self, loader):
        with self.lock:
            if self.value is not None and self.clock() < self.expires:
                return copy.deepcopy(self.value)
            # Failures propagate; never silently substitute an expired snapshot.
            value = loader()
            self.value = copy.deepcopy(value)
            self.expires = self.clock() + self.ttl
            return value


def has_matching_stop(orders, symbol, shares):
    """Conservative skip check; all actual replacements still re-read the broker."""
    stops = {}
    terminal = {'filled', 'canceled', 'cancelled', 'expired', 'rejected', 'replaced', 'done_for_day'}
    for parent in orders:
        if not isinstance(parent, dict):
            continue
        legs = parent.get('legs')
        for order in [parent] + (legs if isinstance(legs, list) else []):
            if not isinstance(order, dict):
                continue
            if str(order.get('symbol') or parent.get('symbol') or '').upper() != symbol:
                continue
            if str(order.get('side', '')).lower() != 'sell':
                continue
            if str(order.get('status', '')).lower() in terminal:
                continue
            if str(order.get('type', '')).lower() not in {'stop', 'stop_limit', 'trailing_stop'}:
                continue
            try:
                qty = float(order.get('qty')) - float(order.get('filled_qty') or 0)
            except (ValueError, TypeError):
                return False
            if not math.isfinite(qty) or not order.get('id'):
                return False
            stops[order['id']] = qty
    return len(stops) == 1 and abs(next(iter(stops.values())) - shares) < 0.000001
