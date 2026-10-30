"""Owner-configured filters for broad automatic PAPER stock entries."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from copy import deepcopy
from datetime import datetime
import math
import re
from threading import Lock
import time

DEFAULTS={'stock_min_price_usd':2.,'stock_max_price_usd':20.,
         'stock_min_float_millions':0.,'stock_max_float_millions':20.,
         'stock_min_volume_ratio':2.,'stock_min_average_volume':250000.,
         'stock_max_spread_percent':.5,'stock_min_day_change_percent':2.,
         'stock_min_gap_percent':0.,'stock_max_atr_percent':8.,'stock_news_max_age_hours':24.,
         'stock_min_time_volume_ratio':1.5,'stock_max_vwap_distance_percent':5.,
         'stock_slippage_bps_per_side':10.,'stock_fee_bps_per_side':0.}
RANGES={k:(0.,100. if k.endswith('_percent') else float('inf')) for k in DEFAULTS}
PREFERENCES={'stock_filters_enabled':True,'stock_require_recent_news':False,'stock_auto_adjust_enabled':True,
             'stock_intraday_enabled':True,'stock_require_entry_setup':True,'stock_auto_rollback_enabled':True,'stock_parallel_validation_enabled':True}
_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='stock-float')
_lock=Lock();_cache={};_pending={}


def number(value):
    if isinstance(value,bool):return None
    try:value=float(value)
    except (TypeError,ValueError,OverflowError):return None
    return value if math.isfinite(value) else None


def reported_float(symbol,*,clock=time.time,fetch=None):
    """Two bounded in-flight lookups; cache reported float, never shares outstanding."""
    if not isinstance(symbol,str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}',symbol):return {'float_shares':None}
    def download():
        if fetch is not None:return fetch(symbol)
        import yfinance as yf
        return yf.Ticker(symbol).get_info()
    now=clock()
    with _lock:
        for name,done in list(_pending.items()):
            if not done.done():continue
            try:
                value=number(done.result().get('floatShares'))
                if value is not None and value<=0:value=None
                _cache[name]={'float_shares':value,'fetched_at':now,'float_source':'Yahoo Finance reported float; filing date unavailable; cached up to 24 hours'}
            except Exception:_cache[name]={'float_shares':None,'fetched_at':now,'float_source':'provider_unavailable'}
            _pending.pop(name,None)
        while len(_cache)>500:_cache.pop(next(iter(_cache)))
        cached=_cache.get(symbol)
        if cached and 0<=now-cached['fetched_at']<(86400 if cached['float_shares'] is not None else 900):return deepcopy(cached)
        future=_pending.get(symbol)
        if future is None:
            if len(_pending)>=2:return {'float_shares':None,'float_source':'lookup_capacity'}
            future=_pending[symbol]=_pool.submit(download)
    try:
        info=future.result(timeout=1.5);value=number(info.get('floatShares'))
        if value is None or value<=0:value=None
        result={'float_shares':value,'fetched_at':clock(),'float_source':'Yahoo Finance reported float; filing date unavailable; cached up to 24 hours'}
    except TimeoutError:return {'float_shares':None,'float_source':'lookup_pending'}
    except Exception:result={'float_shares':None,'fetched_at':clock(),'float_source':'provider_unavailable'}
    with _lock:
        _pending.pop(symbol,None)
        if len(_cache)>=500:_cache.pop(next(iter(_cache)))
        _cache[symbol]=result
    return deepcopy(result)


def settings():
    import app_controls
    return app_controls.snapshot()


def features(candidate,quote,config,*,symbol,clock=time.time,float_fetch=None,intraday_fetch=None):
    result={'price':number(quote.get('ask')),'spread_percent':number(quote.get('spread_percent')),
            'volume_ratio':number(candidate.get('volume_ratio')),'average_volume':number(candidate.get('average_volume')),
            'day_change_percent':number(candidate.get('momentum_move_percent',candidate.get('one_day_change',candidate.get('change')))),
            'gap_percent':number(candidate.get('gap_percent')),'atr_percent':number(candidate.get('atr_percent')),
            'score':number(candidate.get('score')),'confidence':number(candidate.get('confidence')),
            'news_latest_at':number(candidate.get('stock_news_latest_at')),
            'float_shares':None,'captured_at':clock(),
            'volume_basis':'Scanner daily cumulative volume / daily 20-session average; not time-of-day adjusted RVOL'}
    price=result['price']
    plausible=price is not None and price>0 and price>=config['stock_min_price_usd'] and (not config['stock_max_price_usd'] or price<=config['stock_max_price_usd'])
    if config['stock_filters_enabled'] and plausible and (config['stock_min_float_millions'] or config['stock_max_float_millions']):
        result.update((float_fetch or reported_float)(symbol))
    if config['stock_filters_enabled'] and config['stock_intraday_enabled'] and plausible:
        intraday=(intraday_fetch or (lambda _: {'available':False,'reason':'intraday_adapter_missing'}))(symbol)
        result['intraday']=intraday
        result['time_volume_ratio']=number(intraday.get('time_volume_ratio'))
        result['entry_setup']=intraday.get('setup')
        vwap=number(intraday.get('vwap'))
        result['vwap_distance_percent']=(price/vwap-1)*100 if vwap and vwap>0 else None
    return result


def evaluate(values,config,*,now):
    if not config['stock_filters_enabled']:return []
    failed=[]
    def bound(field,key,lower):
        threshold=config[key]
        if not threshold:return
        value=number(values.get(field))
        if value is None or (field in ('price','float_millions','average_volume','atr_percent') and value<=0) or (field in ('volume_ratio','spread_percent') and value<0):
            failed.append(field+'_missing_or_invalid')
        elif (value<threshold if lower else value>threshold):failed.append(key)
    values={**values,'float_millions':number(values.get('float_shares'))/1e6 if number(values.get('float_shares')) is not None else None}
    for field,key,lower in [('price','stock_min_price_usd',True),('price','stock_max_price_usd',False),
        ('float_millions','stock_min_float_millions',True),('float_millions','stock_max_float_millions',False),
        ('volume_ratio','stock_min_volume_ratio',True),('average_volume','stock_min_average_volume',True),
        ('spread_percent','stock_max_spread_percent',False),('day_change_percent','stock_min_day_change_percent',True),
        ('gap_percent','stock_min_gap_percent',True),('atr_percent','stock_max_atr_percent',False)]:
        if key=='stock_min_volume_ratio' and config['stock_intraday_enabled']:continue
        bound(field,key,lower)
    if config['stock_intraday_enabled']:
        intraday=values.get('intraday') or {}
        at=number(intraday.get('as_of'))
        bar_at=number(intraday.get('latest_bar_at'))
        if not intraday.get('available') or at is None or not 0<=now-at<=90 or bar_at is None or not 300<=now-bar_at<=900:
            failed.append('intraday_evidence_missing_or_stale')
        bound('time_volume_ratio','stock_min_time_volume_ratio',True)
        bound('vwap_distance_percent','stock_max_vwap_distance_percent',False)
        if config['stock_require_entry_setup'] and values.get('entry_setup') not in ('breakout','vwap_pullback'):
            failed.append('completed_breakout_or_vwap_pullback_missing')
    if config['stock_require_recent_news'] and config['stock_news_max_age_hours']:
        at=number(values.get('news_latest_at'))
        if at is None or not 0<=now-at<=config['stock_news_max_age_hours']*3600:failed.append('recent_news_missing')
    return list(dict.fromkeys(failed))


def news_timestamp(context):
    dates=[]
    for article in (context or {}).get('articles',[]):
        try:
            stamp=datetime.fromisoformat(article['published_at'].replace('Z','+00:00'))
            if stamp.tzinfo is not None:dates.append(stamp.timestamp())
        except (ValueError,TypeError,KeyError):continue
    return max(dates) if dates else None
