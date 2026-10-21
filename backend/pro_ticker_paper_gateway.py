"""Dedicated, GET-only Alpaca PAPER evidence gateway. No environment fallbacks."""
from urllib.parse import quote
from pro_ticker_signal_proposals import _uuid

PAPER_URL='https://paper-api.alpaca.markets'
DATA_URL='https://data.alpaca.markets'


class PaperGatewayUnavailable(ValueError):
    pass


class AlpacaPaperEvidenceGateway:
    """Caller supplies separately approved dedicated credentials and GET client.

    No submit/cancel/replace method exists. Nothing in main or staging constructs
    this adapter by default. Secrets are never included in returned evidence.
    """
    paper=True
    base_url=PAPER_URL

    def __init__(self, *, account_id, key_id, secret_key, http_get):
        self.account_id=_uuid(account_id)
        for value in (key_id,secret_key):
            if not isinstance(value,str) or not value or value!=value.strip() or len(value)>512 or any(ord(c)<33 or ord(c)==127 for c in value):
                raise ValueError('Dedicated PAPER credentials required.')
        if not callable(http_get):raise ValueError('GET transport required.')
        self._headers={'APCA-API-KEY-ID':key_id,'APCA-API-SECRET-KEY':secret_key}
        self._get=http_get

    def _read(self, path, *, data=False, missing_allowed=False):
        try:
            response=self._get((DATA_URL if data else PAPER_URL)+path,
                               headers=self._headers,timeout=3.0,allow_redirects=False)
            if type(response.status_code) is not int:raise ValueError('Invalid HTTP status.')
            if missing_allowed and response.status_code==404:return None
            if response.status_code!=200:raise ValueError('Unexpected HTTP status.')
            return response.json()
        except Exception:
            raise PaperGatewayUnavailable('Dedicated PAPER evidence request failed.') from None

    def read_account(self):
        value=self._read('/v2/account')
        if not isinstance(value,dict) or value.get('id')!=self.account_id:
            raise PaperGatewayUnavailable('Dedicated PAPER account identity mismatch.')
        return value

    def read_snapshot(self,symbol):
        import re
        if not isinstance(symbol,str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}',symbol):raise ValueError('Canonical symbol required.')
        account=self.read_account()
        clock=self._read('/v2/clock')
        asset=self._read('/v2/assets/'+quote(symbol,safe=''))
        positions=self._read('/v2/positions')
        orders=self._read('/v2/orders?status=open&limit=500&nested=true')
        trade=self._read('/v2/stocks/'+quote(symbol,safe='')+'/trades/latest',data=True)
        if not isinstance(trade,dict) or trade.get('symbol') not in (None,symbol):raise PaperGatewayUnavailable('Market evidence symbol mismatch.')
        return {'account':account,'clock':clock,'asset':asset,'positions':positions,'orders':orders,'quote':trade.get('trade')}

    def find_order(self,client_order_id):
        if not isinstance(client_order_id,str) or not client_order_id.startswith('pt-'):raise ValueError('Reservation client order ID required.')
        _uuid(client_order_id[3:])
        return self._read('/v2/orders:by_client_order_id?client_order_id='+quote(client_order_id,safe=''),missing_allowed=True)
