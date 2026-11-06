"""Local elapsed timings; no RPC payloads, credentials or transaction bytes."""
from contextlib import contextmanager
import time


@contextmanager
def measure(timings, name):
    started = time.perf_counter()
    try:
        yield
    finally:
        timings[name] = round(timings.get(name, 0) + (time.perf_counter() - started) * 1000, 3)
