"""Explicit dedicated PAPER composition. Never fall back to original credentials."""
import os
from pro_ticker_signal_proposals import _uuid
from pro_ticker_submission import PaperSubmissionContext


def dedicated_paper_context(*,environ=None,http_get=None,http_post=None):
    env=os.environ if environ is None else environ
    if env.get('PRO_TICKER_DEDICATED_ACCOUNT_CONFIRMED')!='true':return None
    try:
        account=_uuid(env.get('PRO_TICKER_PAPER_ACCOUNT_ID'))
        original=_uuid(env.get('PRO_TICKER_ORIGINAL_ACCOUNT_ID'))
        key=env.get('PRO_TICKER_PAPER_KEY_ID');secret=env.get('PRO_TICKER_PAPER_SECRET_KEY')
        if account==original or not isinstance(key,str) or not isinstance(secret,str) or not key or not secret:return None
        if any(value!=value.strip() or len(value)>512 or any(ord(c)<33 or ord(c)==127 for c in value) for value in (key,secret)):return None
        if key in {env.get('ALPACA_API_KEY'),env.get('APCA_API_KEY_ID')}:return None
    except ValueError:return None
    def gateway():
        import requests
        from pro_ticker_paper_gateway import AlpacaPaperOrderGateway
        return AlpacaPaperOrderGateway(account_id=account,key_id=key,secret_key=secret,http_get=http_get or requests.get,http_post=http_post or requests.post)
    return PaperSubmissionContext(account,gateway,True,env.get('PRO_TICKER_OWNER_PAPER_SUBMISSION_ENABLED')=='true')


def dedicated_release_reader():
    context=dedicated_paper_context()
    if context is None:raise ValueError('Dedicated PAPER release account is not configured.')
    gateway=context.gateway();gateway.read_account()
    return gateway.read_order
