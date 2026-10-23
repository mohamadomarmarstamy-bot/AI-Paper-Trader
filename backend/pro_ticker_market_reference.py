"""Read-only provider reference diagnostics; never order eligibility or authority."""
import re
import time
from pro_ticker_manual_signals import quote_snapshot, REFERENCE_MAX_AGE_SECONDS
from pro_ticker_broker_admission import ADMISSION_REFERENCE_MAX_AGE_SECONDS


def market_reference_snapshot(symbol, *, context, clock=None):
    if not isinstance(symbol,str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}',symbol):
        raise ValueError('Canonical symbol required.')
    gateway=context.gateway()
    account=gateway.read_account()
    if not isinstance(account,dict) or account.get('id')!=context.expected_account_id:
        raise ValueError('Dedicated account identity mismatch.')
    raw=gateway.read_trade(symbol)
    if isinstance(raw,dict) and raw.get('symbol') not in (None,symbol):
        raise ValueError('Reference symbol mismatch.')
    snapshot=quote_snapshot(raw,now=(clock or time.time)())
    return {'paper':True,'account_verified':True,'reference_only':True,
            'order_execution_authorized':False,'symbol':symbol,'reference':snapshot,
            'allocation_reference_fresh':snapshot['fresh'] is True,
            'admission_reference_fresh':snapshot['fresh'] is True and snapshot['age_seconds']<=ADMISSION_REFERENCE_MAX_AGE_SECONDS,
            'allocation_max_age_seconds':REFERENCE_MAX_AGE_SECONDS,
            'admission_max_age_seconds':ADMISSION_REFERENCE_MAX_AGE_SECONDS,
            'feed_selection':'provider_default'}
