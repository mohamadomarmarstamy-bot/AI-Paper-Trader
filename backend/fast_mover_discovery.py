"""Bounded, read-only snapshot discovery. Ranking is not a buy authorization."""
from datetime import datetime, timezone
import math
from zoneinfo import ZoneInfo

POOL_LIMIT=600
HISTORY_LIMIT=350
ROTATION_SIZE=300
EXPLORATION_SLOTS=50
SNAPSHOT_BATCH=200
MAX_AGE=90
EASTERN=ZoneInfo('America/New_York')


def finite(value):
    if isinstance(value,bool): return None
    try: n=float(value)
    except (ValueError,TypeError,OverflowError): return None
    return n if math.isfinite(n) else None


def timestamp(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return parsed if parsed.tzinfo else None
    except (ValueError,TypeError): return None


def snapshot_metrics(snapshot, *, now, feed='iex'):
    if not isinstance(snapshot,dict): return {}
    trade=snapshot.get('latestTrade') or {}; quote=snapshot.get('latestQuote') or {}
    daily=snapshot.get('dailyBar') or {}; previous=snapshot.get('prevDailyBar') or {}
    if any(not isinstance(x,dict) for x in (trade,quote,daily,previous)): return {}
    price,prior,volume,vwap,bid,ask=map(finite,(trade.get('p'),previous.get('c'),daily.get('v'),daily.get('vw'),quote.get('bp'),quote.get('ap')))
    times=[timestamp(x.get('t')) for x in (trade,quote,daily,previous)]
    if any(t is None for t in times): return {}
    current=datetime.fromtimestamp(now,timezone.utc); day=current.astimezone(EASTERN).date()
    if any(not 0<=now-t.timestamp()<=MAX_AGE for t in times[:2]): return {}
    if times[2].astimezone(EASTERN).date()!=day or not 0<(day-times[3].astimezone(EASTERN).date()).days<=7: return {}
    if any(x is None or x<=0 for x in (price,prior,volume,vwap,bid,ask)) or bid>ask: return {}
    change=(price/prior-1)*100; dollars=volume*vwap; spread=(ask-bid)/((ask+bid)/2)*100
    if not all(math.isfinite(x) for x in (change,dollars,spread)): return {}
    # Low price is a discovery preference, not proof of market cap or float.
    preferred=.25<=price<=30 and change>=3 and dollars>=250000 and spread<=2 and price>=vwap
    move_points=min(max(change,0),30)
    if change>80: move_points*=.25
    score=move_points+min(math.log10(max(dollars,1)),8)*3+max(0,2-spread)*5
    return dict(price=price,percent_change=change,change=price-prior,
        session_dollar_volume=dollars,spread_percent=spread,above_session_vwap=price>=vwap,
        fast_mover_priority=preferred,fast_mover_score=round(score,3) if preferred else 0,
        snapshot_trade_at=times[0].isoformat(),snapshot_quote_at=times[1].isoformat(),
        captured_at=now,source='alpaca_snapshots',feed=feed,
        volume_scope='current_session_on_selected_feed',market_cap_verified=False,float_verified=False)


def rank_pool(pool, rotation, metadata):
    """Reserve exploration even when hot lists are full; never lose breadth."""
    exploration=[s for s in rotation if s in pool][:EXPLORATION_SLOTS]
    reserved=set(exploration)
    ranked=sorted((s for s in pool if s not in reserved),key=lambda s:(
        bool(metadata.get(s,{}).get('fast_mover_priority')),
        metadata.get(s,{}).get('fast_mover_score',0)),reverse=True)
    return ranked[:HISTORY_LIMIT-len(exploration)]+exploration
