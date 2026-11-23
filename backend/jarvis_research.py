"""Read-only chart and public social research for Jarvis paper trading."""
import math
import os
import re
import statistics
from datetime import datetime

import solana_http

FRESH_SECONDS = 180
POST_LOOKBACK = 3600
X_INTERVAL = 1800
ADDRESS = re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,44}')
SCHEMA = """
CREATE TABLE IF NOT EXISTS research(
    mint TEXT PRIMARY KEY, pair TEXT NOT NULL, checked REAL NOT NULL, evidence TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_tasks(
    name TEXT PRIMARY KEY, due REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS research_mail(
    version INTEGER PRIMARY KEY, body TEXT NOT NULL, due REAL NOT NULL, sent REAL
);
"""


def payload(url, *, params, headers=None):
    response = solana_http.get(url, params=params, headers=headers, timeout=3, allow_redirects=False)
    response.raise_for_status()
    if response.status_code != 200 or len(response.content) > 400_000:
        raise ValueError('Unexpected research response.')
    return response.json()


def chart_signal(candles, now):
    """Tag patterns using completed, contiguous 1-minute candles."""
    bars = {}
    for bar in candles:
        try:
            start, op, high, low, close, volume = map(float, bar[:6])
            if (start % 60 == 0 and start + 60 <= now and all(map(math.isfinite, (start, op, high, low, close, volume)))
                    and 0 < low <= min(op, close) <= max(op, close) <= high and volume >= 0):
                bars[int(start)] = (op, high, low, close, volume)
        except (ValueError, TypeError):
            continue
    if not bars:
        return None
    latest = max(bars)
    times = range(latest - 19 * 60, latest + 1, 60)
    if now - latest > 180 or any(at not in bars for at in times):
        return None
    history = [bars[at] for at in times]
    op, high, low, close, volume = history[-1]
    level = max(bar[1] for bar in history[-12:-2])
    normal_volume = statistics.median(bar[4] for bar in history[-12:-2])
    previous_close = history[-2][3]
    # Compare the last two completed, clock-aligned five-minute closes.
    five_min_end = (latest + 60) // 300 * 300
    trending = bars[five_min_end - 60][3] > bars[five_min_end - 360][3]
    if high > level > close and high - max(op, close) >= (high - low) * 0.4:
        pattern = 'failed_breakout'
    elif (trending and previous_close > level and close >= level and low <= level * 1.02
          and low >= level * 0.98 and history[-2][4] >= normal_volume * 1.5 and normal_volume > 0):
        pattern = 'breakout_retest'
    elif close > level >= previous_close and volume >= normal_volume * 1.5 and normal_volume > 0:
        pattern = 'volume_breakout'
    elif trending and history[-2][3] < history[-3][3] and low >= min(bar[2] for bar in history[-12:-3]) and close > history[-2][1]:
        pattern = 'pullback_recovery'
    else:
        pattern = 'none'
    # Indicator variants use only completed contiguous candles. Keep the
    # existing pattern calculation unchanged for current strategies.
    def vwap(window):
        # The USD OHLCV feed reports quote-currency volume. Estimate base
        # units from each candle's typical price, rather than weighting
        # expensive candles twice. This is a candle-based VWAP estimate.
        total = sum(bar[4] for bar in window)
        units = sum(bar[4] / ((bar[1] + bar[2] + bar[3]) / 3) for bar in window)
        return total / units if units else None

    def ema(window, period):
        value = sum(bar[3] for bar in window[:period]) / period
        for bar in window[period:]:
            value += 2 / (period + 1) * (bar[3] - value)
        return value

    fast, slow = ema(history, 5), ema(history, 13)
    previous_fast, previous_slow = ema(history[:-1], 5), ema(history[:-1], 13)
    reclaim = False
    starts = range(latest - 21 * 60, latest + 1, 60)
    if all(at in bars for at in starts):
        expanded = [bars[at] for at in starts]
        levels = [vwap(expanded[i:i + 20]) for i in range(3)]
        reclaim = all(level is not None for level in levels) and (
            expanded[-3][3] <= levels[0] and expanded[-2][3] > levels[1] and expanded[-1][3] > levels[2])
    # Wilder RSI uses a 14-change seed, then smoothing, on completed candles.
    changes = [b[3] - a[3] for a, b in zip(history, history[1:])]
    average_gain = sum(max(0, change) for change in changes[:14]) / 14
    average_loss = sum(max(0, -change) for change in changes[:14]) / 14
    def rsi_value():
        if average_loss == 0:
            return 100.0 if average_gain else 50.0
        return 100 - 100 / (1 + average_gain / average_loss)
    rsi_values = [rsi_value()]
    for change in changes[14:]:
        average_gain = (average_gain * 13 + max(0, change)) / 14
        average_loss = (average_loss * 13 + max(0, -change)) / 14
        rsi_values.append(rsi_value())
    squeeze = history[-11:-1]
    squeeze_level = max(bar[1] for bar in squeeze)
    squeeze_low = min(bar[2] for bar in squeeze)
    squeeze_volume = statistics.median(bar[4] for bar in squeeze)
    squeeze_breakout = (squeeze_level <= squeeze_low * 1.03 and close > squeeze_level
                        and squeeze_volume > 0 and volume >= squeeze_volume * 1.5)
    return {'pattern': pattern, 'closed_at': latest + 60,
            'volume_ratio': round(volume / normal_volume, 2) if normal_volume else None,
            'uptrend_5m': trending, 'vwap': vwap(history), 'vwap_reclaim': reclaim,
            'ema_fast': fast, 'ema_slow': slow,
            'ema_cross_up': previous_fast <= previous_slow and fast > slow,
            'volume_rising': volume > history[-2][4], 'squeeze_breakout': squeeze_breakout,
            'squeeze_level': squeeze_level, 'rsi': rsi_values[-1],
            'rsi_recovery': rsi_values[-1] > 35 >= rsi_values[-2] and min(rsi_values[:-1]) < 30}


def chart(row, now, fetch=payload):
    data = fetch('https://api.geckoterminal.com/api/v2/networks/solana/pools/'
                 + row['pair'] + '/ohlcv/minute',
                 params={'aggregate': 1, 'limit': 30, 'token': row['mint'], 'currency': 'usd'})
    return chart_signal(data['data']['attributes']['ohlcv_list'], now)


def social_source(provider, row, now, fetch=payload):
    # Token names are untrusted and become search keywords only.
    mint = row['mint']
    name = ' '.join(re.findall(r'[A-Za-z0-9]+', row['symbol'][:80]))[:60].strip()
    if provider == 'x':
        secret = os.getenv('JARVIS_X_BEARER_TOKEN', '').strip()
        if not secret:
            return None
        keywords = f'({mint} OR ("{name}" Solana))' if name else mint
        posts = fetch('https://api.x.com/2/tweets/search/recent',
                      params={'query': keywords + ' -is:retweet', 'max_results': 10,
                              'tweet.fields': 'created_at,author_id'},
                      headers={'Authorization': 'Bearer ' + secret}).get('data', [])
        records = ((p.get('author_id'), p.get('text', ''), p.get('created_at')) for p in posts)
    else:
        data = fetch('https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts',
                     params={'q': mint, 'limit': 15, 'sort': 'latest'})
        records = ((p.get('author', {}).get('did'), p.get('record', {}).get('text', ''),
                    p.get('record', {}).get('createdAt')) for p in data.get('posts', []))
    verified = set()
    name_only = set()
    exact = re.compile(r'(?<![A-Za-z0-9])' + re.escape(mint) + r'(?![A-Za-z0-9])')
    for author, body, created in records:
        try:
            posted = datetime.fromisoformat(created.replace('Z', '+00:00')).timestamp()
        except (AttributeError, ValueError):
            continue
        if not author or not isinstance(body, str) or not now - POST_LOOKBACK <= posted <= now:
            continue
        if exact.search(body):
            verified.add(author)
        elif name and name.casefold() in body.casefold():
            name_only.add(author)
    return {'mint_authors': len(verified), 'name_only_authors': len(name_only - verified)}


RULES = ('baseline', 'volume_breakout', 'breakout_retest', 'pullback_recovery', 'no_failed_breakout',
         'social_confirmed', 'chart_and_social')


def allows(rule, evidence):
    if rule == 'baseline':
        return True
    pattern = (evidence.get('chart') or {}).get('pattern')
    social = any(source.get('mint_authors', 0) >= 2 for source in (evidence[key] for key in ('x', 'bluesky') if key in evidence))
    if rule == 'social_confirmed':
        return social
    if rule == 'chart_and_social':
        return social and pattern in ('breakout_retest', 'pullback_recovery')
    if rule == 'no_failed_breakout':
        return pattern is not None and pattern != 'failed_breakout'
    return pattern == rule


def recommendation(trades):
    stops = [t for t in trades if t.get('stop_gap_points') is not None]
    delayed = [t for t in stops if t.get('exit_quote_wait', 0) >= 90]
    if len(stops) >= 10 and len(delayed) >= 5 and len(delayed) / len(stops) >= 0.2:
        mean = statistics.mean(t['stop_gap_points'] for t in delayed)
        return (f'{len(delayed)} of {len(stops)} stop exits reached a quote at least 90 seconds late, '
                f'with an average shortfall of {mean:.1f} percentage points. '
                'Code proposal: benchmark extra quote capacity for overdue exits against the same recorded delays.')
    charted = [t for t in trades if (t.get('entry_evidence') or {}).get('chart')]
    if len(trades) >= 100 and len(charted) < len(trades) * 0.2:
        return (f'Only {len(charted)} of {len(trades)} reviewed trades had usable chart evidence at entry. '
                'Code proposal: benchmark a batched candle feed so more entries can be evaluated.')
    return None


def deliver(version, body):
    key = os.getenv('RESEND_API_KEY', '').strip()
    sender = os.getenv('HEALTH_ALERT_EMAIL_FROM', '').strip()
    recipient = os.getenv('HEALTH_ALERT_EMAIL_TO', '').strip()
    if not all((key, sender, recipient)):
        return False
    response = solana_http.post(
        'https://api.resend.com/emails',
        json={'from': sender, 'to': [recipient], 'subject': f'Jarvis paper code proposal · review {version}', 'text': body},
        headers={'Authorization': 'Bearer ' + key, 'Idempotency-Key': f'jarvis-research/{version}'},
        timeout=8, allow_redirects=False,
    )
    return response.status_code in (200, 201, 202)
