"""Read-only, same-feed completed-bar evidence for automatic PAPER entries."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from statistics import mean
from threading import Lock
from zoneinfo import ZoneInfo
import re
import time

ET = ZoneInfo('America/New_York')
_cache = {}
_lock = Lock()


def calculate(bars, *, now):
    from stock_momentum import number
    local = now.astimezone(ET)
    minute = local.hour * 60 + local.minute
    cutoff = minute // 5 * 5
    empty = {'available': False, 'source': 'Alpaca IEX completed 5-minute bars',
             'as_of': now.timestamp(), 'reason': 'insufficient_regular_session_bars'}
    if local.weekday() >= 5 or not 585 <= minute < 960:
        return {**empty, 'reason': 'outside_supported_session_window'}
    sessions = {}
    seen = set()
    for bar in bars:
        try:
            stamp = datetime.fromisoformat(str(bar['t']).replace('Z', '+00:00'))
            if stamp.tzinfo is None: raise ValueError()
            stamp = stamp.astimezone(ET)
            slot = stamp.hour * 60 + stamp.minute
            fields = {k: number(bar.get(k)) for k in ('o', 'h', 'l', 'c', 'v', 'vw')}
            if any(v is None for v in fields.values()): raise ValueError()
            if min(fields[k] for k in ('o', 'h', 'l', 'c', 'vw')) <= 0 or fields['v'] < 0: raise ValueError()
            if fields['l'] > min(fields['o'], fields['c'], fields['vw']) or fields['h'] < max(fields['o'], fields['c'], fields['vw']): raise ValueError()
            if stamp.second or stamp.microsecond or slot % 5: raise ValueError()
            identity = stamp.isoformat()
            if identity in seen: raise ValueError()
            seen.add(identity)
        except (KeyError, ValueError, TypeError, OverflowError):
            return {**empty, 'reason': 'invalid_or_duplicate_bars'}
        if stamp.date() > local.date() or stamp.weekday() >= 5: continue
        if 570 <= slot < min(cutoff, 960):
            sessions.setdefault(stamp.date(), []).append((slot, fields))
    today = sorted(sessions.pop(local.date(), []))
    slots = max(0, (cutoff - 570) // 5)
    # Missing intervals cannot establish a setup. Historical volume comparisons
    # use only sessions with at least 80% interval coverage through this cutoff.
    if len(today) < 4 or len(today) < slots * .8 or today[-1][0] != cutoff - 5:
        return empty
    recent = today[-4:]
    if any(b[0] - a[0] != 5 for a, b in zip(recent, recent[1:])):
        return {**empty, 'reason': 'gaps_in_setup_bars'}
    history = [sum(b['v'] for _, b in sessions[day]) for day in sorted(sessions, reverse=True)
               if len(sessions[day]) >= slots * .8][:20]
    total = sum(b['v'] for _, b in today)
    vwap = sum(b['vw'] * b['v'] for _, b in today) / total if total > 0 else None
    last = recent[-1][1]
    prior_high = max(b['h'] for _, b in recent[:-1])
    breakout = last['c'] > prior_high and last['c'] > last['o']
    pullback = bool(vwap and recent[-2][1]['l'] <= vwap * 1.01 and last['c'] > vwap
                    and last['c'] > recent[-2][1]['c'] and last['c'] > last['o'])
    return {**empty, 'available': bool(vwap), 'reason': None if vwap else 'zero_session_volume',
            'vwap': vwap, 'last_close': last['c'], 'latest_bar_at':
            datetime.combine(local.date(), datetime.min.time(), ET).replace(hour=today[-1][0] // 60, minute=today[-1][0] % 60).timestamp(),
            'time_volume_ratio': total / mean(history) if len(history) >= 5 and mean(history) > 0 else None,
            'comparison_sessions': len(history), 'completed_session_volume': total,
            'setup': 'breakout' if breakout else 'vwap_pullback' if pullback else None,
            'volume_basis': 'IEX cumulative regular-session volume / mean at identical ET cutoff over up to 20 preceding sessions; minimum 5, 80% interval coverage'}


def fetch(symbol, request, *, clock=time.time):
    if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}', symbol):
        return {'available': False, 'reason': 'invalid_symbol'}
    stamp = clock()
    with _lock:
        cached = _cache.get(symbol)
        if cached and 0 <= stamp - cached[0] < 60: return deepcopy(cached[1])
    now = datetime.fromtimestamp(stamp, timezone.utc)
    end = now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0)
    try:
        params = {'timeframe': '5Min', 'start': (end - timedelta(days=35)).isoformat(),
                  'end': end.isoformat(), 'limit': 10000, 'adjustment': 'split', 'feed': 'iex', 'sort': 'asc'}
        bars = []
        for _ in range(3):
            payload = request('GET', f'/v2/stocks/{symbol}/bars', params=dict(params))
            if not isinstance(payload, dict) or not isinstance(payload.get('bars'), list): raise ValueError()
            bars.extend(payload['bars'])
            token = payload.get('next_page_token')
            if not token: break
            params['page_token'] = token
        else: raise ValueError('Incomplete pagination')
        result = calculate(bars, now=now)
    except Exception:
        result = {'available': False, 'reason': 'intraday_provider_unavailable_or_incomplete', 'as_of': stamp}
    with _lock:
        if len(_cache) >= 300: _cache.pop(next(iter(_cache)))
        _cache[symbol] = (stamp, result)
    return deepcopy(result)
