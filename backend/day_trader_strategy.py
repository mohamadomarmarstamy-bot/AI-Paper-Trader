"""Frozen intraday hypotheses and conservative forward bar simulations.

No broker execution. Rules are intentionally fixed, not fitted to saved outcomes.
"""
from datetime import datetime, timezone
import math
from statistics import mean

VERSION = 'intraday_momentum_v1'
POLICY = {'notional_cap_usd': 2000, 'risk_cap_usd': 20,
          'reward_risk': 2, 'maximum_hold_minutes': 60,
          'cost_bps_per_side': [10, 30, 60], 'fee_per_share_per_side': .005}


def number(value):
    if isinstance(value, bool):
        raise ValueError('Finite number required.')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Finite number required.')
    return result


def stamp(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone required.')
    return result.timestamp()


def bars(raw, *, observed_at):
    if not isinstance(raw, list) or len(raw) > 20000:
        raise ValueError('Bounded minute history required.')
    result = []
    previous = None
    for item in raw:
        t = stamp(item['t'])
        values = {k: number(item[k]) for k in ('o', 'h', 'l', 'c', 'v')}
        if (t % 60 or previous is not None and t <= previous
                or min(values[k] for k in ('o', 'h', 'l', 'c')) <= 0
                or values['v'] < 0
                or not values['l'] <= min(values['o'], values['c']) <= max(values['o'], values['c']) <= values['h']):
            raise ValueError('Ordered, unique valid OHLCV minute bars required.')
        previous = t
        if t + 60 <= observed_at:
            result.append({'t': t, **values})
    return result


def contiguous(rows):
    return all(b['t'] - a['t'] == 60 for a, b in zip(rows, rows[1:]))


def session_vwap(rows):
    volume = sum(b['v'] for b in rows)
    if volume <= 0:
        raise ValueError('Positive observed volume required.')
    return sum((b['h'] + b['l'] + b['c']) / 3 * b['v'] for b in rows) / volume


def evaluate(raw, quote, *, session_open, session_close, observed_at, opening_volumes):
    """All input evidence must exist at decision time; partial bars are excluded."""
    rows = [b for b in bars(raw, observed_at=observed_at) if session_open <= b['t'] < session_close]
    base = {'version': VERSION, 'policy': POLICY, 'observed_at': observed_at,
            'session_open': session_open, 'session_close': session_close,
            'feed': quote.get('feed'), 'research_only': True,
            'order_execution_authorized': False, 'setups': []}
    def unavailable(reason):
        return {**base, 'status': 'unavailable', 'reason': reason}
    if not session_open + 6*60 <= observed_at < session_close - 30*60:
        return unavailable('Outside the completed-opening-range / new-entry window.')
    if (not rows or rows[0]['t'] != session_open or not contiguous(rows)
            or not 0 <= observed_at - rows[-1]['t'] - 60 <= 120):
        return unavailable('Complete, recent, gap-free regular-session minute bars required.')
    if quote.get('quote_fresh') is not True or not 0 <= observed_at - stamp(quote.get('quote_timestamp')) <= 30:
        return unavailable('Fresh same-feed bid/ask required.')
    ask, bid = number(quote['ask']), number(quote['bid'])
    if not 0 < bid <= ask:
        return unavailable('Valid bid/ask required.')
    vwap = session_vwap(rows)
    opening = rows[:5]
    high, low = max(b['h'] for b in opening), min(b['l'] for b in opening)
    volumes = [number(v) for v in opening_volumes]
    rvol = sum(b['v'] for b in opening) / mean(volumes[-14:]) if len(volumes) >= 14 and min(volumes[-14:]) > 0 else None
    base.update(status='evaluated', reason='Fixed research rules; no profitability claim.',
                vwap=vwap, opening_high=high, opening_low=low, opening_rvol=rvol,
                quote_timestamp=quote['quote_timestamp'], reference_ask=ask,
                spread_percent=(ask/bid-1)*100, bar_as_of=rows[-1]['t']+60)
    # A fresh cross, not a late chase of a breakout that happened hours ago.
    orb = rows[-2]['c'] <= high < rows[-1]['c'] and opening[-1]['c'] > opening[0]['o']
    pullback = (len(rows) >= 21 and vwap > session_vwap(rows[:-3])
                and min(b['l'] for b in rows[-3:]) <= vwap * 1.002
                and rows[-1]['c'] > max(rows[-2]['h'], vwap)
                and rows[-1]['v'] >= mean(b['v'] for b in rows[-21:-1]))
    for name, signal, stop in [('opening_range_breakout', orb, low),
                               ('vwap_pullback', pullback, min(b['l'] for b in rows[-3:]))]:
        reason = None
        if rvol is None:
            reason = '14 prior same-feed opening-volume sessions unavailable.'
        elif rvol < 1.5:
            reason = 'Opening relative volume below experimental 1.5 threshold.'
        elif not signal or rows[-1]['c'] <= vwap:
            reason = 'No confirmed completed-bar setup.'
        elif ask > rows[-1]['c'] * 1.005:
            reason = 'Quote has moved over 0.5% beyond the signal close; no chase.'
        risk = ask - stop
        if reason is None and not .002 <= risk / ask <= .05:
            reason = 'Technical stop distance outside experimental 0.2–5% range.'
        # Sizing includes the base cost stress and modeled fees, not just price distance.
        stressed_entry = ask * 1.001
        risk_per_share = stressed_entry - stop * .999 + .01
        shares = min(math.floor(2000/stressed_entry), math.floor(20/risk_per_share)) if risk_per_share > 0 else 0
        if reason is None and shares < 1:
            reason = 'No whole share fits the experimental notional/risk caps.'
        base['setups'].append({'strategy': name, 'status': 'no_setup' if reason else 'candidate',
            'reason': reason or 'Fresh setup above session VWAP with elevated opening activity.',
            'entry_ask': ask if reason is None else None, 'stop': stop if reason is None else None,
            'target': ask + 2*risk if reason is None else None, 'shares': shares if reason is None else 0,
            'planned_risk_usd': shares*risk_per_share if reason is None else None,
            'expires_at': min(math.floor(observed_at/60)*60 + 3600, session_close - 300)})
    return base


def simulate(plan, raw, *, frozen_at, observed_at):
    """A modeled quote-to-bar outcome, never a fill or guaranteed stop price."""
    if plan['status'] != 'candidate':
        return {'status': 'not_applicable'}
    start = (math.floor(frozen_at/60) + 1)*60
    end = plan['expires_at']
    rows = [b for b in bars(raw, observed_at=observed_at) if start <= b['t'] < end]
    if observed_at < end + 60:
        return {'status': 'pending', 'evaluate_after': end+60}
    expected_last = math.ceil(end/60)*60 - 60
    if not rows or rows[0]['t'] != start or rows[-1]['t'] != expected_last or not contiguous(rows):
        return {'status': 'inconclusive', 'reason': 'Minute path incomplete; no invented exit or zero return.'}
    exit_price, reason = rows[-1]['c'], 'time_exit'
    for b in rows:
        if b['o'] <= plan['stop']:
            exit_price, reason = b['o'], 'gap_below_stop'
            break
        if b['l'] <= plan['stop']:
            exit_price, reason = plan['stop'], 'stop_first_if_ambiguous'
            break
        if b['h'] >= plan['target']:
            # No favorable gap improvement is assumed for target fills.
            exit_price, reason = plan['target'], 'target'
            break
    scenarios = []
    for bps in POLICY['cost_bps_per_side']:
        entry = plan['entry_ask'] * (1 + bps/10000)
        fee = plan['shares'] * .01
        def pnl(price):
            return round(plan['shares']*(price*(1-bps/10000)-entry)-fee, 4)
        scenarios.append({'bps_per_side': bps, 'modeled_pnl_usd': pnl(exit_price),
                          'matched_hold_pnl_usd': pnl(rows[-1]['c'])})
    return {'status': 'modeled', 'exit_reason': reason, 'scenarios': scenarios,
            'basis': 'forward OHLC simulation; not broker fills or executable bid history',
            'blind_interval_seconds': start-frozen_at}
