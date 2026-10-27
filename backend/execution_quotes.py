"""Timestamped PAPER display quotes. No Yahoo/daily-close fallback or orders."""
from collections import OrderedDict
from datetime import datetime, timezone
import math
import threading
import time
from broker_throttle import DisplaySnapshotCache

MAX_AGE_SECONDS=30
_caches=OrderedDict()
_lock=threading.Lock()


def number(value):
    if isinstance(value,bool): return None
    try: n=float(value)
    except (TypeError,ValueError,OverflowError): return None
    return n if math.isfinite(n) and n>0 else None


def age(value,now):
    try:
        stamp=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if stamp.tzinfo is None:return None
        seconds=now-stamp.timestamp()
        return seconds if math.isfinite(seconds) and seconds>=0 else None
    except (TypeError,ValueError,OverflowError): return None


def parse_snapshot(symbol,snapshot,*,feed='iex',now=None):
    now=time.time() if now is None else now
    snapshot=snapshot if isinstance(snapshot,dict) else {}
    quote=snapshot.get('latestQuote');quote=quote if isinstance(quote,dict) else {}
    trade=snapshot.get('latestTrade');trade=trade if isinstance(trade,dict) else {}
    bid,ask,last=number(quote.get('bp')),number(quote.get('ap')),number(trade.get('p'))
    qa,ta=age(quote.get('t'),now),age(trade.get('t'),now)
    quote_fresh=bid is not None and ask is not None and bid<=ask and qa is not None and qa<=MAX_AGE_SECONDS
    trade_fresh=last is not None and ta is not None and ta<=MAX_AGE_SECONDS
    midpoint=(bid/2+ask/2) if quote_fresh else None
    return dict(symbol=symbol,price=last if trade_fresh else midpoint,
        price_kind='last_trade' if trade_fresh else 'quote_midpoint' if quote_fresh else 'unavailable',
        bid=bid if quote_fresh else None,ask=ask if quote_fresh else None,
        last_trade=last if trade_fresh else None,quote_fresh=quote_fresh,trade_fresh=trade_fresh,
        quote_timestamp=quote.get('t'),trade_timestamp=trade.get('t'),
        quote_age_seconds=qa,trade_age_seconds=ta,source='alpaca_snapshot',feed=feed,
        received_at=datetime.fromtimestamp(now,timezone.utc).isoformat(),
        max_age_seconds=MAX_AGE_SECONDS,estimate_only=True,
        quote_error=None if quote_fresh else 'Fresh bid/ask unavailable; no delayed price was substituted.')


def read_quote(symbol,request_func,*,feed='iex'):
    if feed not in ('iex','sip'):raise ValueError('Configured quote feed must be iex or sip.')
    key=(feed,symbol)
    with _lock:
        cache=_caches.get(key)
        if cache is None:
            cache=DisplaySnapshotCache(ttl=2)
            _caches[key]=cache
            if len(_caches)>256:_caches.popitem(last=False)
        _caches.move_to_end(key)
    def load():
        payload=request_func('GET','/v2/stocks/snapshots',params={'symbols':symbol,'feed':feed},timeout=5.0)
        if not isinstance(payload,dict):raise ValueError('Invalid snapshot response.')
        return payload.get(symbol,{})
    # Recompute freshness even when a raw snapshot was served from the 2s cache.
    return parse_snapshot(symbol,cache.get(load),feed=feed)
