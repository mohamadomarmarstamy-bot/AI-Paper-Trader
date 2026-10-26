"""Reproducible descriptive research; no execution, model or risk mutations."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
import math
import re
import time

VERSION = 'pro_ticker_research_context_v1'
NY = ZoneInfo('America/New_York')


def _number(value, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Finite evidence number required.')
    if value < 0 or (not zero and value == 0):
        raise ValueError('Positive evidence number required.')
    return float(value)


def _stamp(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Timezone required.')
    return stamp


def _bars(payload, symbol, observed):
    if not isinstance(payload, dict) or payload.get('symbol') != symbol or payload.get('source') != 'yahoo_finance_daily':
        raise ValueError('Timestamped symbol-specific daily evidence required.')
    rows = payload.get('bars')
    if not isinstance(rows, list) or not 50 <= len(rows) <= 130:
        raise ValueError('50 to 130 completed daily bars required.')
    result = []
    previous = None
    for row in rows:
        stamp = _stamp(row['timestamp'])
        if stamp.astimezone(NY).date() >= observed.astimezone(NY).date() or previous and stamp <= previous:
            raise ValueError('Unordered, duplicate or incomplete daily bar.')
        values = {key: _number(row[key], zero=key == 'volume') for key in ('open', 'high', 'low', 'close', 'volume')}
        if not values['low'] <= min(values['open'], values['close']) <= max(values['open'], values['close']) <= values['high']:
            raise ValueError('Contradictory daily prices.')
        if values['volume'] != int(values['volume']):
            raise ValueError('Whole-share volume required.')
        previous = stamp
        result.append({'timestamp': stamp.isoformat(), **values})
    if not 0 <= (observed - previous).total_seconds() <= 7 * 86400:
        raise ValueError('Daily history is stale.')
    return result


def research_report(evidence):
    """Only the saved envelope is used, including its observation clock."""
    if not isinstance(evidence, dict) or evidence.get('version') != VERSION:
        raise ValueError('Unsupported evidence version.')
    symbol = evidence.get('symbol')
    if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}', symbol):
        raise ValueError('Canonical symbol required.')
    observed = _stamp(evidence['observed_at'])
    missing = ['independently_verified_catalyst', 'bid_ask_spread', 'corporate_action_adjustments']
    news = normalize_news(evidence.get('news'), symbol=symbol, observed=observed)
    if not news['articles']:
        missing.append('fresh_supported_news')
    positive, negative, contradictions = [], [], []
    metrics, sources = {}, []
    quote = evidence.get('reference')
    price = None
    try:
        if quote.get('source') != 'alpaca_latest_trade':
            raise ValueError('Quote source missing.')
        candidate = _number(quote['price'])
        age = (observed - _stamp(quote['market_timestamp'])).total_seconds()
        if not 0 <= age <= 90:
            raise ValueError('Stale/future reference.')
        price = candidate
        sources.append({'source': quote['source'], 'as_of': quote['market_timestamp'], 'age_seconds': age})
    except (ValueError, TypeError, KeyError, AttributeError):
        missing.append('fresh_reference')
    bars = None
    try:
        bars = _bars(evidence.get('history'), symbol, observed)
        sources.append({'source': 'yahoo_finance_daily', 'as_of': bars[-1]['timestamp'], 'completed_sessions': len(bars)})
        closes = [r['close'] for r in bars]
        sma20, sma50 = sum(closes[-20:]) / 20, sum(closes[-50:]) / 50
        avgvol = sum(r['volume'] for r in bars[-21:-1]) / 20
        # Yesterday's complete volume versus the preceding 20 complete sessions.
        rvol = bars[-1]['volume'] / avgvol if avgvol else None
        atr = sum(max(r['high'] - r['low'], abs(r['high'] - p['close']), abs(r['low'] - p['close'])) for p, r in zip(bars[-15:-1], bars[-14:])) / 14
        high = max(r['high'] for r in bars[-20:])
        metrics.update(sma20=sma20, sma50=sma50, momentum_5d_percent=(closes[-1]/closes[-6]-1)*100,
                       average_daily_volume=avgvol, average_daily_dollar_volume=sum(r['volume']*r['close'] for r in bars[-20:])/20,
                       completed_session_relative_volume=rvol, atr14=atr, atr14_percent=100*atr/closes[-1], prior_20_session_high=high)
        (positive if sma20 > sma50 else negative).append('Daily trend: SMA20 ' + ('above' if sma20 > sma50 else 'at or below') + ' SMA50.')
        if price is not None:
            metrics['distance_to_prior_high_percent'] = (price/high-1)*100
            (positive if price > high else negative).append('Reference ' + ('exceeds' if price > high else 'does not exceed') + ' the prior 20-session high.')
            if price > high and (rvol is None or rvol < 1.5):
                contradictions.append('Price exceeds prior high but the last completed session lacks 1.5x volume confirmation; current-session confirmation is unavailable.')
        if avgvol < 50000:
            negative.append('Average completed-session volume is below 50,000 shares.')
        if rvol is None:
            missing.append('relative_volume_denominator')
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        bars = None
        missing.append('fresh_completed_daily_history')
    regime = None
    try:
        market = _bars(evidence.get('market_history'), 'SPY', observed)
        closes = [r['close'] for r in market]
        regime = 'uptrend' if closes[-1] > sum(closes[-20:])/20 > sum(closes[-50:])/50 else 'downtrend' if closes[-1] < sum(closes[-20:])/20 < sum(closes[-50:])/50 else 'mixed'
        sources.append({'source': 'yahoo_finance_daily', 'symbol': 'SPY', 'as_of': market[-1]['timestamp']})
        if regime != 'uptrend':
            negative.append('SPY daily trend is ' + regime + '.')
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        missing.append('market_regime')
    digest = hashlib.sha256(json.dumps(evidence, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
    return {'version': VERSION, 'symbol': symbol, 'observed_at': evidence['observed_at'],
            'status': 'available' if bars and price is not None else 'unavailable', 'research_only': True,
            'order_execution_authorized': False, 'training_eligible': False,
            'evidence_sha256': digest, 'sources': sources, 'indicators': metrics,
            'market_regime': regime, 'supporting_reasons': positive, 'opposing_reasons': negative,
            'contradictions': contradictions, 'missing_information': missing, 'news': news,
            'score': None, 'score_reason': 'Descriptive context supplements the separately saved daily grade; no calibrated predictive score.',
            'limitations': ['Daily bars are unadjusted; splits can distort these metrics.',
                            'Volume is completed-session volume, not intraday or time-adjusted RVOL.',
                            'SPY trend is a market proxy, not a complete regime model.',
                            'News topics reflect attributed provider headlines; causation, sentiment and catalyst truth are not independently verified.',
                            'A historical high break does not establish a successful breakout.']}


def yahoo_daily_evidence(symbol, *, now):
    import yfinance as yf
    frame = yf.Ticker(symbol).history(period='6mo', interval='1d', auto_adjust=False,
                                      actions=False, prepost=False, timeout=3)
    if frame.empty or frame.index.tz is None:
        raise ValueError('Timestamped history unavailable.')
    completed = frame[[s.to_pydatetime().astimezone(NY).date() < datetime.fromtimestamp(now, NY).date() for s in frame.index]].tail(130)
    return {'source': 'yahoo_finance_daily', 'symbol': symbol, 'bars': [
        {'timestamp': stamp.isoformat(), **{k.lower(): float(row[k]) for k in ('Open','High','Low','Close','Volume')}}
        for stamp, row in completed.iterrows()]}


def capture_research(symbol, reference, *, fetch_history=yahoo_daily_evidence, clock=time.time, fetch_news=None):
    histories = {}
    for key, ticker in (('history', symbol), ('market_history', 'SPY')):
        try:
            histories[key] = fetch_history(ticker, now=clock())
            # Reject malformed/non-finite upstream data before saving it.
            json.dumps(histories[key], allow_nan=False)
        except Exception:
            histories[key] = None
    news = None
    try:
        news = (fetch_news or dedicated_news)(symbol)
        # Store only normalized bounded news, never full content or credentials.
        news = normalize_news(news, symbol=symbol, observed=datetime.fromtimestamp(clock(), timezone.utc))['articles']
    except Exception:
        news = None
    evidence = {'version': VERSION, 'symbol': symbol, 'observed_at': datetime.fromtimestamp(clock(), timezone.utc).isoformat(),
                'reference': reference, 'news': news, **histories}
    return {'evidence': evidence, 'report': research_report(evidence)}


def dedicated_news(symbol):
    from pro_ticker_runtime import dedicated_paper_context
    context=dedicated_paper_context()
    if context is None:raise ValueError('Dedicated research news unavailable.')
    context.validate()
    return context.gateway().read_news(symbol)['news']


def normalize_news(raw, *, symbol, observed):
    from urllib.parse import urlsplit
    articles=[];seen=set()
    if isinstance(raw,list) and len(raw)<=10:
        for item in raw:
            try:
                if (not isinstance(item,dict) or item.get('source')!='benzinga'
                        or not isinstance(item.get('symbols'),list) or len(item['symbols'])>1000 or symbol not in item['symbols']):continue
                created=_stamp(item['created_at']);updated=_stamp(item.get('updated_at',item['created_at']))
                if not 0<=(observed-created).total_seconds()<=72*3600 or not created<=updated<=observed:continue
                title=item['headline'];url=item['url'];parts=urlsplit(url)
                if (not isinstance(title,str) or not 1<=len(title)<=500 or parts.scheme!='https' or parts.username or parts.password
                        or not (parts.hostname=='benzinga.com' or parts.hostname and parts.hostname.endswith('.benzinga.com'))):continue
                if url in seen:continue
                seen.add(url)
                topics=[key for key,pattern in [('earnings',r'\b(earnings|quarterly results)\b'),
                        ('financing',r'\b(offering|financing)\b'),('clinical',r'\b(trial|FDA)\b'),
                        ('merger',r'\b(merger|acquisition)\b'),('split',r'\b(reverse split|stock split)\b')]
                        if re.search(pattern,title,re.I)]
                articles.append({'source':'benzinga','symbols':[symbol],'headline':title,'url':url,
                                 'created_at':created.isoformat(),'updated_at':updated.isoformat(),
                                 'reported_topics':topics})
            except (ValueError,TypeError,KeyError,AttributeError):continue
    return {'provider':'alpaca_news_benzinga','status':'available' if articles else 'unavailable',
            'articles':articles,'bounded_window':True,'independently_verified':False,
            'analysis_kind':'headline_topic_tags_not_sentiment_or_causal_proof'}
