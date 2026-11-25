"""Read-only exact-pool USD candle trial; no trading fallback or auto-enrollment."""
from datetime import datetime
import time

import jarvis_research as research

BASE='https://api-pro.dexpaprika.com/networks/solana/pools/'


def timestamp(value):
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.utcoffset() is None:raise ValueError('timezone required')
    return parsed.timestamp()


def usd_chart(row, now, key, fetch=research.payload):
    if not key:raise ValueError('An entitled private evaluation key is required')
    if not all(isinstance(row.get(k),str) and research.ADDRESS.fullmatch(row[k]) for k in ('pair','mint')):
        raise ValueError('Exact pool and token identifiers required')
    headers={'Authorization':key}
    url=BASE+row['pair']
    pool=fetch(url,params={},headers=headers)
    if pool.get('id')!=row['pair'] or pool.get('chain')!='solana':
        raise research.ChartUnavailable('identity_mismatch')
    base,quote=pool.get('base_token_id'),pool.get('quote_token_id')
    if row['mint'] not in (base,quote) or base==quote:
        raise research.ChartUnavailable('token_orientation_unknown')
    end=int(now)//60*60
    records=fetch(url+'/ohlcv',headers=headers,params={'start':end-30*60,'end':end,
        'interval':'1m','limit':30,'inversed':str(row['mint']==quote).lower()})
    if not isinstance(records,list):raise research.ChartUnavailable('invalid_chart')
    bars=[];seen=set()
    try:
        for item in records:
            at=timestamp(item['time_open']);closed=timestamp(item['time_close'])
            if closed-at!=60 or at%60 or at in seen:raise ValueError('invalid interval')
            seen.add(at)
            bars.append([at,*[float(item[k]) for k in ('open','high','low','close','volume')]])
    except (KeyError,TypeError,ValueError,OverflowError):
        raise research.ChartUnavailable('invalid_chart') from None
    signal=research._chart_signal(bars,now)
    # Price currency is documented USD. Pool-volume units/equivalence still
    # need confirmation, so candidate volume ratios must never drive trading.
    return {'status':'fresh_usd_candles','closed_at':signal['closed_at'],'rsi_usd':signal['rsi'],
        'price_currency':'USD','volume_equivalence':'unverified','trading_enabled':False}


def benchmark(rows,key,*,clock=time.time,fetch=research.payload,sleep=time.sleep):
    if not key:raise ValueError('Private entitled key required; no requests made')
    if not isinstance(rows,list) or not 1<=len(rows)<=6:raise ValueError('Choose 1–6 pools')
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or not all(isinstance(row.get(k),str) and research.ADDRESS.fullmatch(row[k]) for k in ('mint','pair')) or row['pair'] in seen:
            raise ValueError('Valid token and distinct pool addresses required')
        seen.add(row['pair'])
    results=[]
    for row in rows:
        if results:sleep(1)
        try:result=usd_chart(row,clock(),key,fetch)
        except research.ChartUnavailable as error:result={'status':error.status}
        except Exception as error:
            status=getattr(getattr(error,'response',None),'status_code',None)
            result={'status':'http_'+str(status) if isinstance(status,int) else 'provider_error'}
        results.append(result)
        if result['status'] in ('http_401','http_403','http_429'):break
    return {'sampled':len(results),'maximum_requests':2*len(rows),'results':results,'trading_enabled':False}


def main():
    import argparse
    import json
    import os
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pools_json',help='JSON array of 1–6 exact public mint/pair objects')
    args=parser.parse_args()
    key=os.environ.get('JARVIS_DEXPAPRIKA_TRIAL_KEY','').strip()
    if not key:parser.error('Set the private entitled trial key; no requests made')
    try:
        with open(args.pools_json,encoding='utf-8') as source:rows=json.load(source)
        result=benchmark(rows,key)
    except (OSError,ValueError):parser.error('Supply a valid JSON pool sample; no secrets are printed')
    print(json.dumps(result,allow_nan=False))


if __name__=='__main__':main()
