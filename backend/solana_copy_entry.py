"""Receipt-derived SOL entry reference and a worst-case executable buy bound."""
from decimal import Decimal, InvalidOperation

SOL = 'So11111111111111111111111111111111111111112'
MAX_PREMIUM_PERCENT = 5


def source_entry(transaction, wallet, mint):
    """Use net SOL + wrapped SOL spent, including source fees and account rent.

    This conservative all-in reference can be slightly above the swap price.
    It never uses a chart quote or an inferred USD conversion.
    """
    try:
        meta = transaction['meta']
        keys = transaction['transaction']['message']['accountKeys']
        index = next(i for i, k in enumerate(keys) if k['pubkey'] == wallet)
        native = meta['preBalances'][index] - meta['postBalances'][index]
        amounts = {mint: 0, SOL: 0}
        others = {}
        decimals = set()
        for field, sign in (('preTokenBalances', -1), ('postTokenBalances', 1)):
            for row in meta[field]:
                if row.get('owner') == wallet and row['mint'] not in amounts:
                    others[row['mint']]=others.get(row['mint'],0)+sign*int(row['uiTokenAmount']['amount'])
                if row.get('owner') != wallet or row['mint'] not in amounts:
                    continue
                amount = row['uiTokenAmount']
                raw = int(amount['amount'])
                if raw < 0 or str(raw) != amount['amount']:
                    return None
                if row['mint'] == SOL and amount['decimals'] != 9:
                    return None
                amounts[row['mint']] += sign * raw
                if row['mint'] == mint:
                    decimals.add(amount['decimals'])
        spent = native - amounts[SOL]
        if spent <= 0 or amounts[mint] <= 0 or len(decimals) != 1 or any(others.values()):
            return None
        precision = decimals.pop()
        if type(precision) is not int or not 0 <= precision <= 18:
            return None
        return {'mint': mint, 'quote_mint': SOL, 'quote_amount_raw': str(spent),
                'token_amount_raw': str(amounts[mint]), 'token_decimals': precision,
                'basis': 'Source receipt net SOL and wrapped SOL cost, including fees and rent.'}
    except (KeyError, TypeError, ValueError, IndexError, StopIteration):
        return None


def check_quote(reference, mint, decimals, amount, minimum, premium=MAX_PREMIUM_PERCENT):
    """Bound SOL per raw token at the quote's minimum output, before signing."""
    from solana_real_execution import Unavailable
    try:
        if not isinstance(reference, dict) or reference['mint'] != mint or reference['quote_mint'] != SOL:
            raise ValueError()
        source_cost = int(reference['quote_amount_raw'])
        source_tokens = int(reference['token_amount_raw'])
        limit = Decimal(str(premium))
        if (type(reference['token_decimals']) is not int or reference['token_decimals'] != decimals
                or source_cost <= 0 or source_tokens <= 0 or amount <= 0 or minimum <= 0
                or not limit.is_finite() or not 0 <= limit <= 100):
            raise ValueError()
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise Unavailable('Buy skipped: verified source entry price is unavailable.') from None
    numerator,denominator=limit.as_integer_ratio()
    if amount * source_tokens * 100 * denominator > source_cost * minimum * (100 * denominator + numerator):
        raise Unavailable(f'Buy skipped: executable entry exceeds source cost by more than {limit}% (including slippage).')
