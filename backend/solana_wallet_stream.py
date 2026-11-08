"""Read-only confirmed-log hints. Only the normal HTTP ledger can admit trades."""
import asyncio
from collections import OrderedDict
import json
import logging
from threading import Lock
import time

from solana_rpc_endpoints import notifications_url

MAX_WALLETS = 16


class Wakeup:
    def __init__(self):
        self.lock = Lock()
        self.dirty = set()
        self.loop = self.event = None

    def bind(self):
        self.loop = asyncio.get_running_loop()
        self.event = asyncio.Event()

    def mark(self, wallets):
        with self.lock:
            self.dirty.update(list(wallets)[:MAX_WALLETS])
            self.dirty = set(sorted(self.dirty)[:MAX_WALLETS])
        if self.loop is not None:
            try:self.loop.call_soon_threadsafe(self.event.set)
            except RuntimeError:pass

    def pending(self):
        with self.lock:return set(self.dirty)

    def acknowledge(self, wallets):
        # Called before reads; a notification arriving during reads is retained.
        with self.lock:self.dirty.difference_update(wallets)

    def retain(self, wallets):
        with self.lock:self.dirty.intersection_update(wallets)

    async def wait(self, timeout):
        self.event.clear()
        if self.pending():return
        try:await asyncio.wait_for(self.event.wait(), timeout=max(.05, timeout))
        except asyncio.TimeoutError:pass


wakeup = Wakeup()
_status = {'connected': False, 'subscriptions': 0, 'last_message_at': None,
           'last_notification_at': None, 'reconnects': 0, 'error': None}


def public():
    return {**_status, 'polling_backup': True, 'commitment': 'confirmed',
            'basis': 'Notifications wake verified HTTP history reads; they never authorize a trade.'}


def target_wallets():
    import solana_copy_trader as paper
    import solana_real_worker as live
    from solana_live_source_policy import paper_only
    source = paper.load()[0]
    run = live.load()[0]
    held = {p['source_wallet'] for p in run['positions'].values()}
    active = {r['wallet'] for r in source['traders']
              if run['enabled'] and source['config']['enabled'] and r.get('active', True)
              and not paper_only(r['wallet'], source['traders'])}
    candidates = held | active
    # Non-RPC sources retain their original provider rather than being duplicated.
    return tuple(sorted((w for w in candidates if source['cursors'].get(w, {}).get('provider') == 'rpc'),
                        key=lambda w: (w not in held, w))[:MAX_WALLETS])


def notification_wallet(message, subscriptions):
    """Ignore failed, malformed, unrelated and unacknowledged notifications."""
    try:
        if message.get('method') != 'logsNotification':return None
        params = message['params']
        subscription = params['subscription']
        if type(subscription) is not int or subscription not in subscriptions:return None
        value = params['result']['value']
        if value['err'] is not None:return None
        from solana_copy_rpc import decode58, PROGRAMS
        signature = value['signature']
        if not isinstance(signature, str) or not 64 <= len(signature) <= 128 or len(decode58(signature)) != 64:
            return None
        logs = value['logs']
        if not isinstance(logs, list) or len(logs) > 2000:return None
        if not any(isinstance(line, str) and any(line.startswith(f'Program {program} invoke [')
                   for program in PROGRAMS) for line in logs):return None
        return subscriptions[subscription], signature
    except (KeyError, TypeError, ValueError):
        return None


def connect_socket(url):
    # Lazy import keeps non-streaming/offline workflows independent of transport.
    from websockets.asyncio.client import connect
    class NoRedirectConnect(connect):
        def process_redirect(self, exc):
            return exc  # Never forward a credential-bearing URL to another host.
    return NoRedirectConnect(url, open_timeout=8, close_timeout=3, ping_interval=20,
                             ping_timeout=20, max_size=256000, max_queue=16,
                             compression=None)


async def session(socket, wallets, *, loader=target_wallets, seen=None):
    requests = {i + 1: wallet for i, wallet in enumerate(wallets)}
    subscriptions = {}
    seen = OrderedDict() if seen is None else seen
    for ident, wallet in requests.items():
        await socket.send(json.dumps({'jsonrpc': '2.0', 'id': ident, 'method': 'logsSubscribe',
                          'params': [{'mentions': [wallet]}, {'commitment': 'confirmed'}]}))
    checked = time.monotonic()
    acknowledgement_deadline = checked + 10
    while True:
        if time.monotonic() - checked >= 10:
            if tuple(await asyncio.to_thread(loader)) != wallets:return
            checked = time.monotonic()
        if requests and time.monotonic() > acknowledgement_deadline:
            raise ValueError('Wallet subscription acknowledgement timed out.')
        try:raw = await asyncio.wait_for(socket.recv(), timeout=5)
        except asyncio.TimeoutError:continue
        message = json.loads(raw)
        if not isinstance(message, dict):raise ValueError('Unsupported stream response.')
        _status['last_message_at'] = time.time()
        ident = message.get('id')
        if type(ident) is int and ident in requests:
            subscription = message.get('result')
            if message.get('error') or type(subscription) is not int or subscription < 0 or subscription in subscriptions:
                raise ValueError('Wallet subscription was not accepted.')
            subscriptions[subscription] = requests.pop(ident)
            _status['subscriptions'] = len(subscriptions)
            if not requests:
                _status.update(connected=True, error=None)
                logging.getLogger(__name__).info('Wallet notifications connected',extra={'subscribed_wallet_count':len(subscriptions)})
                # Reconnects always reconcile HTTP history from durable cursors.
                wakeup.mark(wallets)
            continue
        notification = notification_wallet(message, subscriptions)
        if notification:
            wallet, signature = notification
            key = (wallet, signature)
            if key in seen:continue
            seen[key] = True
            while len(seen) > 2048:seen.popitem(last=False)
            _status['last_notification_at'] = time.time()
            wakeup.mark([wallet])


async def worker(*, loader=target_wallets, connector=connect_socket):
    backoff = 1
    seen = OrderedDict()
    try:
        while True:
            try:
                wallets = tuple(await asyncio.to_thread(loader))
                wakeup.retain(wallets)
                if not wallets:
                    _status.update(connected=False, subscriptions=0, error=None)
                    await asyncio.sleep(5)
                    continue
                url = notifications_url()
                async with connector(url) as socket:
                    _status['reconnects'] += 1
                    await session(socket, wallets, loader=loader, seen=seen)
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception:
                # Provider exceptions may embed API keys; never stringify them.
                if _status['error'] is None:
                    logging.getLogger(__name__).warning('Wallet notifications unavailable; HTTP polling remains active')
                _status.update(connected=False, subscriptions=0,
                               error='Live notifications unavailable; polling remains active.')
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)
            finally:
                _status.update(connected=False, subscriptions=0)
    finally:
        wakeup.retain(())
