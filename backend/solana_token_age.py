"""Read-only proof a token mint existed at least 30 minutes before entry.

Pool age and a wallet's first purchase are not token creation timestamps.
An older successful transaction with actual mint balances proves a conservative
lower bound; missing/pruned/busy history never grants permission to buy.
"""
from collections import OrderedDict
from threading import Lock
import time

MINIMUM_SECONDS = 1800
_proofs = OrderedDict()
_lock = Lock()


class AgeUnavailable(ValueError):
    pass


def require_age(mint, *, rpc=None, now=None):
    now = time.time() if now is None else now
    with _lock:
        proof = _proofs.get(mint)
        if proof and 0 <= now - proof['checked_at'] <= 3600:
            return dict(proof)
    if rpc is None:
        from solana_copy_rpc import request as rpc
    try:
        rows = rpc('getSignaturesForAddress', [mint, {'limit': 1000, 'commitment': 'finalized'}])
        if not isinstance(rows, list):
            raise ValueError()
        candidates = [r for r in rows if r.get('err') is None
                      and type(r.get('blockTime')) is int
                      and 0 < r['blockTime'] <= now - MINIMUM_SECONDS]
        for row in sorted(candidates, key=lambda r: r['blockTime'])[:3]:
            tx = rpc('getTransaction', [row['signature'], {'encoding': 'jsonParsed',
                     'commitment': 'finalized', 'maxSupportedTransactionVersion': 1}])
            if not isinstance(tx, dict) or not tx.get('meta') or tx['meta'].get('err') is not None:
                continue
            stamp = tx.get('blockTime')
            if type(stamp) is not int or not 0 < stamp <= now - MINIMUM_SECONDS:
                continue
            if tx['transaction']['signatures'][0] != row['signature']:
                continue
            balances = tx['meta'].get('preTokenBalances', []) + tx['meta'].get('postTokenBalances', [])
            initialized = any(ix.get('parsed', {}).get('type') in ('initializeMint', 'initializeMint2')
                              and ix['parsed'].get('info', {}).get('mint') == mint
                              and ix.get('program') in ('spl-token', 'spl-token-2022')
                              for ix in tx['transaction']['message']['instructions'])
            if not initialized and not any(b.get('mint') == mint for b in balances):
                continue
            proof = {'mint': mint, 'existed_at': stamp, 'signature': row['signature'],
                     'checked_at': now, 'minimum_age_seconds': MINIMUM_SECONDS,
                     'basis': 'Finalized on-chain mint existence; conservative age lower bound.'}
            with _lock:
                _proofs[mint] = proof
                while len(_proofs) > 1024:
                    _proofs.popitem(last=False)
            return dict(proof)
    except Exception:
        raise AgeUnavailable('Buy skipped: token age could not be verified as at least 30 minutes.') from None
    raise AgeUnavailable('Buy skipped: token is under 30 minutes old or older mint history could not be verified.')
