"""Manual read-only comparison of exact-pool chart providers; never feeds the trader."""
import argparse
from collections import Counter
import json
import math
import os
import time

import jarvis_research as research

MAX_POOLS = 12
BIRDEYE = 'https://public-api.birdeye.so/defi/v3/ohlcv/pair'


def birdeye_chart(row, now, key, fetch=research.payload):
    data = fetch(BIRDEYE, headers={'Accept': 'application/json', 'X-API-KEY': key, 'x-chain': 'solana'},
                 params={'address': row['pair'], 'type': '1m', 'mode': 'count',
                         'count_limit': 30, 'time_to': int(now), 'padding': 'true',
                         'outlier': 'true', 'inversion': 'false'})
    try:
        if data['success'] is not True:
            raise ValueError('unsuccessful response')
        items = data['data']['items']
        if not isinstance(items, list) or any(c['type'] != '1m' for c in items):
            raise ValueError('invalid interval')
        bars = [[c['unix_time'], c['o'], c['h'], c['l'], c['c'], c['v_usd']] for c in items]
    except (KeyError, TypeError, ValueError):
        raise research.ChartUnavailable('invalid_chart') from None
    return research._chart_signal(bars, now)


def inspect(get_chart, row, now):
    try:
        signal = get_chart(row, now)
    except research.ChartUnavailable as error:
        return {'status': error.status}
    except Exception as error:
        response = getattr(error, 'response', None)
        status = getattr(response, 'status_code', None)
        return {'status': f'http_{status}' if isinstance(status, int) else 'provider_error'}
    rsi, volume = signal.get('rsi'), signal.get('volume_ratio')
    complete = all(type(x) in (int, float) and math.isfinite(x) for x in (rsi, volume))
    return {'status': 'complete' if complete else 'missing_indicators',
            'closed_at': signal['closed_at'], 'rsi': rsi, 'volume_ratio': volume}


def benchmark(rows, key, *, clock=time.time, sleep=time.sleep, primary=research.chart, alternate=birdeye_chart):
    seen = set()
    if not key or not isinstance(rows, list) or not 1 <= len(rows) <= MAX_POOLS:
        raise ValueError('An API key and one to twelve exact-pool rows are required.')
    for row in rows:
        if (not isinstance(row, dict) or not all(
                isinstance(row.get(field), str) and research.ADDRESS.fullmatch(row[field])
                for field in ('mint', 'pair')) or row['pair'] in seen):
            raise ValueError('Each row needs a valid mint and unique pool address.')
        seen.add(row['pair'])
    results = []
    for row in rows:
        if results:
            sleep(research.CHART_INTERVAL)
        now = clock()
        gecko = inspect(primary, row, now)
        bird = inspect(lambda r, t: alternate(r, t, key), row, now)
        results.append({'pair': row['pair'], 'as_of': now, 'geckoterminal': gecko, 'birdeye': bird,
                        'same_close_minute': gecko.get('closed_at') is not None
                        and gecko.get('closed_at') == bird.get('closed_at')})
        if any(x['status'] in ('http_401', 'http_403', 'http_429') for x in (gecko, bird)):
            break  # Stop rather than guessing credentials or bypassing the provider's cooldown.
    return {'requested': len(rows), 'sampled': len(results),
            'coverage': {provider: dict(Counter(row[provider]['status'] for row in results))
                         for provider in ('geckoterminal', 'birdeye')},
            'rows': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pools_json', help='JSON array of 1–12 public Solana {mint,pair} objects')
    args = parser.parse_args()
    key = os.environ.get('JARVIS_BIRDEYE_BENCHMARK_KEY', '').strip()
    if not key:
        parser.error('JARVIS_BIRDEYE_BENCHMARK_KEY is required; no requests were made.')
    try:
        with open(args.pools_json, encoding='utf-8') as source:
            rows = json.load(source)
        report = benchmark(rows, key)
    except (OSError, ValueError):
        parser.error('Supply a readable JSON array with one to twelve valid mint and unique pool addresses.')
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
