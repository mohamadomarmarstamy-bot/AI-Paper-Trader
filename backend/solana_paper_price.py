"""Reject inconsistent PumpSwap provider observations; never manufacture a quote.

DEX last-trade prices can be distorted by lamport rounding on dust swaps. The
same response's reserve ratio is a broad consistency check, not an executable
quote or a constant-product pricing model (virtual reserves may differ).
"""
import math

SOL = 'So11111111111111111111111111111111111111112'
MAX_RESERVE_DIVERGENCE = 4


def positive(value):
    if isinstance(value, bool):
        raise ValueError('Invalid price evidence')
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Invalid price evidence')
    return value


def validate(row):
    if row.get('dexId') != 'pumpswap':
        return None
    # priceNative is SOL per base token on these SOL-quoted pools. Other pool
    # types need their own model; never apply a reserve formula to CLMM pools.
    if (row.get('quoteToken') or {}).get('address') != SOL:
        raise ValueError('PumpSwap cash quote cannot be validated')
    reserves = row.get('liquidity') or {}
    base = positive(reserves.get('base'))
    quote = positive(reserves.get('quote'))
    native = positive(row.get('priceNative'))
    ratio = native * base / quote
    if not 1 / MAX_RESERVE_DIVERGENCE <= ratio <= MAX_RESERVE_DIVERGENCE:
        raise ValueError('PumpSwap last price conflicts with pool balances')
    return {'basis': 'PumpSwap SOL last-price / reserve-ratio sanity check; not an executable quote',
            'native_price': native, 'reserve_price': quote / base, 'ratio': ratio}
