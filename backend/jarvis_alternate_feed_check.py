"""Bounded, read-only Dexploit freshness trial. Never feeds trading signals."""
from datetime import datetime, timezone
import math
import time

import jarvis_research as research

URL='https://api.dexploit.dev/api/v1/candles'


def inspect(row, now, key, fetch=research.payload):
    if not key or not all(isinstance(row.get(k),str) and research.ADDRESS.fullmatch(row[k]) for k in ('mint','pair')):
        raise ValueError('A private API key and valid exact-pool identifiers are required')
    end=int(now)//60*60
    iso=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat()
    data=fetch(URL,headers={'X-API-Key':key},params={'pair_address':row['pair'],'timeframe':'1m',
        'start':iso(end-30*60),'end':iso(end-1),'limit':31})
    if not isinstance(data,dict) or data.get('success') is not True or not isinstance(data.get('data'),list):
        return {'status':'invalid_response'}
    bars=[]
    try:
        for item in data['data']:
            if item['pair_address']!=row['pair'] or item['token_address']!=row['mint'] or item['timeframe']!='1m':
                return {'status':'identity_mismatch'}
            if item['base_mint']!='So11111111111111111111111111111111111111112':
                return {'status':'unsupported_price_currency'}
            # This provider labels SOL-denominated prices and lamport volume.
            # Never substitute them for the trader's USD-denominated indicators.
            if item['trade_count']<=0:continue
            parsed=datetime.fromisoformat(item['timestamp'].replace('Z','+00:00'))
            if parsed.utcoffset() is None:return {'status':'invalid_timestamp'}
            at=parsed.timestamp()
            values=[float(item[k]) for k in ('open','high','low','close')]+[float(item['volume_sol'])/1e9]
            if not all(math.isfinite(v) for v in values):return {'status':'invalid_candle'}
            bars.append([at,*values])
    except (KeyError,ValueError,TypeError,OverflowError):
        return {'status':'invalid_candle'}
    try:
        value=research._chart_signal(bars,now)
        return {'status':'fresh_contiguous','closed_at':value['closed_at'],'price_currency':'SOL',
            'volume_currency':'SOL','trading_compatible':False}
    except research.ChartUnavailable as error:
        return {'status':error.status,'latest_close':error.latest_candle,'trading_compatible':False}


def benchmark(rows, key, *, fetch=research.payload, clock=time.time, sleep=time.sleep):
    if not key:raise ValueError('Configure the private Dexploit trial key first; no requests made')
    if not isinstance(rows,list) or not 1<=len(rows)<=12:raise ValueError('Choose 1–12 exact-pool samples')
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or not all(isinstance(row.get(k),str) and research.ADDRESS.fullmatch(row[k]) for k in ('mint','pair')) or row['pair'] in seen:
            raise ValueError('Valid mint and distinct pool addresses required; no requests made')
        seen.add(row['pair'])
    results=[]
    for row in rows:
        if results:sleep(1)
        try:result=inspect(row,clock(),key,fetch)
        except Exception as error:
            status=getattr(getattr(error,'response',None),'status_code',None)
            result={'status':'http_'+str(status) if isinstance(status,int) else 'provider_error'}
        results.append(result)
        if result['status'] in ('http_401','http_403','http_429'):break
    return {'sampled':len(results),'results':results,'trading_enabled':False}
