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

    def read_order(self,order_id):
        order_id=_uuid(order_id)
        value=self._read('/v2/orders/'+order_id+'?nested=true')
        if not isinstance(value,dict) or value.get('id')!=order_id:raise PaperGatewayUnavailable('PAPER order identity mismatch.')
        return value

    def read_clock(self):return self._read('/v2/clock')

    def read_positions(self):return self._read('/v2/positions')

    def read_trade(self,symbol):
        import re
        if not isinstance(symbol,str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}',symbol):raise ValueError('Canonical symbol required.')
        value=self._read('/v2/stocks/'+quote(symbol,safe='')+'/trades/latest',data=True)
        if not isinstance(value,dict) or value.get('symbol') not in (None,symbol):raise PaperGatewayUnavailable('Market evidence symbol mismatch.')
        return value.get('trade')

    def find_order(self,client_order_id):
        if not isinstance(client_order_id,str) or not client_order_id.startswith('pt-'):raise ValueError('Reservation client order ID required.')
        _uuid(client_order_id[3:])
        return self._read('/v2/orders:by_client_order_id?client_order_id='+quote(client_order_id,safe=''),missing_allowed=True)

    def read_fill_activities(self,order_id):
        order_id=_uuid(order_id);rows=[];token=None;seen=set()
        for _ in range(5):
            path='/v2/account/activities/FILL?order_id='+order_id+'&direction=asc&page_size=100'
            if token:path+='&page_token='+quote(token,safe='')
            page=self._read(path)
            if not isinstance(page,list) or len(page)>100:raise PaperGatewayUnavailable('Invalid PAPER execution page.')
            rows.extend(page)
            if len(page)<100:return rows
            token=page[-1].get('id') if isinstance(page[-1],dict) else None
            import re
            if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9:_\-]{1,180}',token) or token in seen:raise PaperGatewayUnavailable('Invalid PAPER execution pagination.')
            seen.add(token)
        raise PaperGatewayUnavailable('PAPER execution history exceeds the bounded page window.')


class AlpacaPaperOrderGateway(AlpacaPaperEvidenceGateway):
    """Separate explicitly constructed PAPER write transport; no retry/fallback."""
    def __init__(self, *, http_post, **kwargs):
        super().__init__(**kwargs)
        if not callable(http_post):raise ValueError('Dedicated POST transport required.')
        self._post=http_post

    def submit_bracket(self, payload):
        # Accept only the narrow server-generated whole-share long bracket.
        from decimal import Decimal
        expected={'symbol','qty','side','type','limit_price','time_in_force','extended_hours','client_order_id','order_class','stop_loss','take_profit'}
        import re
        if not isinstance(payload,dict) or set(payload)!=expected or payload['side']!='buy' or payload['type']!='limit' or payload['order_class']!='bracket' or payload['time_in_force']!='gtc' or payload['extended_hours'] is not False:
            raise ValueError('PAPER long limit bracket required.')
        if not isinstance(payload['symbol'],str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}',payload['symbol']):raise ValueError('Invalid symbol.')
        if not isinstance(payload['qty'],str) or not re.fullmatch(r'[1-9][0-9]{0,8}',payload['qty']):raise ValueError('Whole shares required.')
        if not isinstance(payload['client_order_id'],str) or not payload['client_order_id'].startswith('pt-'):raise ValueError('Owned client ID required.')
        _uuid(payload['client_order_id'][3:])
        try:
            if set(payload['stop_loss'])!={'stop_price'} or set(payload['take_profit'])!={'limit_price'}:raise ValueError()
            prices=[Decimal(str(payload['stop_loss']['stop_price'])),Decimal(str(payload['limit_price'])),Decimal(str(payload['take_profit']['limit_price']))]
            if not all(p.is_finite() and 0<p<Decimal('1000000000') and p==p.quantize(Decimal('.01') if p>=1 else Decimal('.0001')) for p in prices) or not prices[0]<prices[1]<prices[2]:raise ValueError()
        except Exception:raise ValueError('Valid bracket prices required.') from None
        # Check dedicated account identity immediately before the only POST.
        self.read_account()
        try:
            response=self._post(PAPER_URL+'/v2/orders',headers=self._headers,json=payload,timeout=3.0,allow_redirects=False)
            if type(response.status_code) is not int or response.status_code not in (200,201):raise ValueError()
            return response.json()
        except Exception:raise PaperGatewayUnavailable('PAPER submission unconfirmed; recover by saved client ID.') from None
