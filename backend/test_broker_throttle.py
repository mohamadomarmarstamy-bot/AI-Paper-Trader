"""Offline tests: no application startup, credentials, or real broker requests."""
import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime
import math
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
import uuid

from broker_throttle import BrokerRateLimited, BrokerRequestGate, DisplaySnapshotCache, has_matching_stop


class Clock:
    now = 1000.0
    def read(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


def gate(clock):
    return BrokerRequestGate(clock=clock.read, wall_clock=clock.read, sleep=clock.sleep)


def stop(symbol='TEST', qty=2, **extra):
    return dict({'id': 'stop', 'symbol': symbol, 'qty': qty, 'filled_qty': '0',
                 'side': 'sell', 'type': 'stop', 'status': 'new'}, **extra)


def functions(**overrides):
    names = {'alpaca_paper_request', 'submit_alpaca_recovery_oco', 'reconcile_unprotected_positions', 'run_auto_trader_cycle'}
    source = ast.parse((Path(__file__).parent / 'main.py').read_text(encoding='utf-8'))
    nodes = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {'BrokerRateLimited': BrokerRateLimited, 'has_matching_stop': has_matching_stop,
          'ALPACA_PAPER_BASE_URL': 'https://paper-api.alpaca.markets',
          'ALPACA_READ_RETRY_ATTEMPTS': 3, 'ALPACA_READ_RETRY_DELAY_SECONDS': .75,
          'get_alpaca_headers': lambda: {}, 'extract_alpaca_error': lambda p: str(p),
          'clean_symbol': lambda s: str(s or '').strip().upper(), 'safe_float': lambda x: float(x) if x is not None else None,
          'clean_error_message': str, 'math': math, 'uuid': uuid,
          'AUTO_TRADER_STOP_LOSS_PERCENT': 2, 'AUTO_TRADER_TAKE_PROFIT_PERCENT': 4,
          'time': SimpleNamespace(sleep=lambda s: None), 'add_auto_trader_log': lambda *a, **kw: None}
    ns.update(overrides)
    exec(compile(ast.Module(body=ast.parse('from __future__ import annotations').body + nodes,
                            type_ignores=[]), 'main.py', 'exec'), ns)
    return ns


class PacingTests(unittest.TestCase):
    def test_every_attempt_is_paced(self):
        c = Clock(); g = gate(c)
        g.acquire(); g.acquire(); g.acquire()
        self.assertAlmostEqual(c.now, 1001.2)

    def test_429_sets_shared_cooldown_and_does_not_sleep_worker_for_a_minute(self):
        c = Clock(); g = gate(c)
        g.observe(429, {})
        with self.assertRaises(BrokerRateLimited) as error:
            g.acquire()
        self.assertEqual(error.exception.retry_after, 60)
        self.assertEqual(c.now, 1000)
        c.sleep(60); g.acquire()

    def test_honor_retry_after_and_reset_using_longer_delay(self):
        c = Clock(); g = gate(c)
        g.observe(429, {'Retry-After': '4', 'X-RateLimit-Reset': '1010'})
        self.assertEqual(g.retry_after(), 11)

    def test_retry_after_http_date(self):
        c = Clock(); g = gate(c)
        date = format_datetime(datetime.fromtimestamp(1020, timezone.utc), usegmt=True)
        g.observe(429, {'Retry-After': date})
        self.assertEqual(g.retry_after(), 21)

    def test_exhausted_success_prevents_next_call_until_reset(self):
        c = Clock(); g = gate(c)
        g.observe(200, {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1030'})
        with self.assertRaises(BrokerRateLimited): g.acquire()

    def test_malformed_headers_use_conservative_fallback(self):
        for headers in ({'Retry-After': 'nan'}, {'Retry-After': 'bad'}, {'X-RateLimit-Reset': '900'}):
            g = gate(Clock()); g.observe(429, headers)
            self.assertEqual(g.retry_after(), 60)

    def test_get_and_post_429_are_not_replayed(self):
        for method in ('GET', 'POST'):
            calls = []
            g = gate(Clock())
            response = SimpleNamespace(status_code=429, headers={'Retry-After': '60'}, ok=False, json=lambda: {})
            requests = SimpleNamespace(request=lambda **kw: calls.append(kw) or response,
                                       RequestException=ConnectionError)
            ns = functions(_broker_request_gate=g, requests=requests)
            with self.assertRaises(BrokerRateLimited): ns['alpaca_paper_request'](method, '/v2/orders')
            with self.assertRaises(BrokerRateLimited): ns['alpaca_paper_request']('GET', '/v2/positions')
            self.assertEqual(len(calls), 1)

    def test_post_timeout_is_not_replayed(self):
        calls = []
        def request(**kw):
            calls.append(kw); raise ConnectionError('ambiguous timeout')
        ns = functions(_broker_request_gate=gate(Clock()),
                       requests=SimpleNamespace(request=request, RequestException=ConnectionError))
        with self.assertRaises(ConnectionError): ns['alpaca_paper_request']('POST', '/v2/orders')
        self.assertEqual(len(calls), 1)


class SnapshotTests(unittest.TestCase):
    def test_shared_snapshot_expires_and_is_defensively_copied(self):
        c = Clock(); cache = DisplaySnapshotCache(clock=c.read); calls = []
        def load(): calls.append(1); return {'positions': [1]}
        first = cache.get(load); first['positions'].append(2)
        self.assertEqual(cache.get(load)['positions'], [1]); self.assertEqual(len(calls), 1)
        c.sleep(10); cache.get(load); self.assertEqual(len(calls), 2)

    def test_failed_refresh_never_returns_stale_values(self):
        c = Clock(); cache = DisplaySnapshotCache(clock=c.read)
        cache.get(lambda: {'equity': 100}); c.sleep(10)
        def fail(): raise RuntimeError('broker unavailable')
        with self.assertRaises(RuntimeError): cache.get(fail)

    def test_concurrent_tabs_share_one_loader(self):
        cache = DisplaySnapshotCache(); calls = []
        barrier = threading.Barrier(8)
        def load(): calls.append(1); return {'equity': 100}
        def read(_): barrier.wait(); return cache.get(load)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(read, range(8)))
        self.assertEqual(len(calls), 1); self.assertEqual(len(results), 8)


class ProtectionTests(unittest.TestCase):
    def test_empty_portfolio_needs_no_requests(self):
        ns = functions()
        ns['alpaca_paper_request'] = lambda *a, **kw: self.fail('no holdings')
        self.assertEqual(ns['reconcile_unprotected_positions']([]), [])

    def test_cycle_rate_limit_is_deferred_and_releases_running_flag(self):
        logs = []
        def limited(): raise BrokerRateLimited(60)
        ns = functions(_auto_trader_cycle_running=False, _auto_trader_enabled=True,
                       auto_trader_automation_allowed=lambda: True,
                       fetch_alpaca_market_clock=limited,
                       time=SimpleNamespace(time=lambda: 1000),
                       add_auto_trader_log=lambda name, **kw: logs.append(name))
        result = ns['run_auto_trader_cycle']()
        self.assertTrue(result['deferred'])
        self.assertFalse(result['success'])
        self.assertEqual(logs, ['cycle_deferred'])
        self.assertFalse(ns['_auto_trader_cycle_running'])

    def test_batch_counts_remaining_qty_and_ignores_terminal_legs(self):
        self.assertTrue(has_matching_stop([stop(qty=5, filled_qty=3)], 'TEST', 2))
        self.assertFalse(has_matching_stop([stop(status='canceled')], 'TEST', 2))
        self.assertFalse(has_matching_stop([stop(), stop(id='second')], 'TEST', 2))
        self.assertTrue(has_matching_stop([{'symbol': 'TEST', 'legs': [stop()]}], 'TEST', 2))

    def test_protected_portfolio_needs_only_one_request(self):
        ns = functions(); calls = []
        positions = [{'symbol': s, 'qty': 2, 'current_price': 100} for s in ('QQQ', 'INTC', 'TEST')]
        ns['alpaca_paper_request'] = lambda *a, **kw: calls.append(a) or [stop(p['symbol']) for p in positions]
        ns['submit_alpaca_recovery_oco'] = lambda **kw: self.fail('already protected; must not replace')
        result = ns['reconcile_unprotected_positions'](positions)
        self.assertEqual(len(calls), 1); self.assertTrue(all(r['result']['already_protected'] for r in result))

    def test_failed_batch_never_implies_orders_are_missing(self):
        ns = functions()
        def fail(*a, **kw): raise BrokerRateLimited(60)
        ns['alpaca_paper_request'] = fail
        ns['submit_alpaca_recovery_oco'] = lambda **kw: self.fail('must not submit')
        result = ns['reconcile_unprotected_positions']([{'symbol': 'QQQ', 'qty': 2}])
        self.assertTrue(result[0]['result']['deferred']); self.assertFalse(result[0]['result']['success'])

    def test_truncated_snapshot_falls_back_to_fresh_symbol_check(self):
        ns = functions(); calls = []
        ns['alpaca_paper_request'] = lambda *a, **kw: [stop()] * 500
        ns['submit_alpaca_recovery_oco'] = lambda **kw: calls.append(kw) or {'success': True, 'already_protected': True}
        ns['reconcile_unprotected_positions']([{'symbol': 'TEST', 'qty': 2, 'current_price': 100}])
        self.assertEqual(len(calls), 1)

    def test_recovery_still_refreshes_quantity_before_post(self):
        ns = functions(fetch_alpaca_open_orders_for_symbol=lambda s: [],
                       fetch_alpaca_paper_positions=lambda: [{'symbol': 'TEST', 'qty': 44}])
        posts = []
        ns['alpaca_paper_request'] = lambda *a, **kw: posts.append(kw['json_body']) or {'id': 'new'}
        result = ns['submit_alpaca_recovery_oco'](symbol='TEST', shares=537, current_price=3)
        self.assertTrue(result['success']); self.assertEqual(posts[0]['qty'], '44')

    def test_position_read_rate_limit_defers_without_post(self):
        def limited(): raise BrokerRateLimited(60)
        ns = functions(fetch_alpaca_open_orders_for_symbol=lambda s: [], fetch_alpaca_paper_positions=limited)
        ns['alpaca_paper_request'] = lambda *a, **kw: self.fail('quantity unknown; must not submit')
        result = ns['submit_alpaca_recovery_oco'](symbol='TEST', shares=2, current_price=100)
        self.assertTrue(result['deferred']); self.assertEqual(result['retry_after_seconds'], 60)


if __name__ == '__main__':
    unittest.main()
