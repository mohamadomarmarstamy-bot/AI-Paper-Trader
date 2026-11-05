"""Cached display metadata only. Never supplies execution prices or identity."""
from threading import Lock
import math
import time
import unicodedata
import requests
from solana_meme_bot import mint

_cache = {}
_lock = Lock()


def clean(value, limit):
    if not isinstance(value, str):
        return ''
    return ' '.join(''.join(c for c in value if unicodedata.category(c) not in ('Cc', 'Cf')).split())[:limit]


def labels(addresses):
    if not 1 <= len(addresses) <= 30:
        raise ValueError('Request between one and thirty coin addresses.')
    addresses = list(dict.fromkeys(mint(a) for a in addresses))
    with _lock:
        now = time.monotonic()
        missing = [a for a in addresses if a not in _cache or _cache[a][0] <= now]
        if missing:
            found = {}
            try:
                response = requests.get('https://api.dexscreener.com/tokens/v1/solana/' + ','.join(missing), timeout=3)
                response.raise_for_status()
                if len(response.content) > 2_000_000:
                    raise ValueError('Metadata response too large.')
                rows = response.json()
                if not isinstance(rows, list) or len(rows) > 500:
                    raise ValueError('Invalid token metadata.')
                for row in rows:
                    if not isinstance(row, dict) or row.get('chainId') != 'solana':
                        continue
                    try:
                        liquidity = float((row.get('liquidity') or {}).get('usd') or 0)
                        if not math.isfinite(liquidity) or liquidity < 0:
                            liquidity = 0
                    except (TypeError, ValueError):
                        liquidity = 0
                    for side in ('baseToken', 'quoteToken'):
                        token = row.get(side)
                        if not isinstance(token, dict) or token.get('address') not in missing:
                            continue
                        address = token['address']
                        name, symbol = clean(token.get('name'), 100), clean(token.get('symbol'), 40)
                        if not name and not symbol:
                            continue
                        if address not in found or liquidity > found[address][0]:
                            found[address] = (liquidity, {'name': name, 'symbol': symbol})
            except (requests.RequestException, ValueError, TypeError):
                pass
            for address in missing:
                value = found.get(address, (0, None))[1]
                _cache[address] = (time.monotonic() + (3600 if value else 60), value)
            while len(_cache) > 2048:
                _cache.pop(next(a for a in _cache if a not in addresses))
        return {a: dict(_cache[a][1]) if _cache[a][1] else None for a in addresses}
