"""Frozen, prospective paper-lab rules. No network access or order submission."""
import math

BOTS = {
    'lab-pressure': ('Buy Pressure', 'pressure', 'step'),
    'lab-volume': ('Volume Breakout', 'volume', 'step'),
    'lab-liquidity': ('Liquidity Growth', 'liquidity', 'step'),
    'lab-five-minute': ('Five-Minute Exit', 'instant', 'time5'),
    'lab-vwap': ('VWAP Reclaim', 'vwap', 'vwap'),
    'lab-trend': ('Trend Crossover', 'trend', 'trend'),
    'lab-breakeven': ('Break-Even Runner', 'instant', 'breakeven'),
    'lab-stalled': ('Stalled Momentum', 'volume', 'stalled'),
    'lab-tp5-sl10': ('Take +5% / Stop −10%', 'instant', 'tp5'),
}
DESCRIPTIONS = {
    'pressure': 'Requires at least 50 five-minute transactions and buys at least twice sells on two scans 5–90 seconds apart.',
    'volume': 'Requires a completed one-minute breakout candle with volume at least twice its recent median.',
    'liquidity': 'Requires a 25% liquidity increase over five minutes, sustained for another minute. Warms up for six minutes.',
    'vwap': 'Requires a move above the estimated 20-candle VWAP held for two completed one-minute candles.',
    'trend': 'Requires a 5-period EMA cross above the 13-period EMA with rising candle volume.',
    'time5': 'Uses the ladder stop and sells the full position at the first available quote after five minutes.',
    'vwap_exit': 'Starts with a −10% stop; sells fully when observed price falls below the latest fresh VWAP.',
    'trend_exit': 'Starts with a −10% stop; sells fully when the fast EMA falls below the slow EMA.',
    'breakeven': 'Starts at −10%. After a 15% net gain, protects entry costs and trails 10% below the quoted peak.',
    'stalled': 'Starts with a −10% stop; sells fully after three minutes without a new quoted high.',
    'tp5': 'Sells fully at a quoted token-value gain of +5% or loss of −10%. Fees reduce net P/L. Triggers are sampled, not guaranteed fill prices.',
}
VERSION = 'strategy-lab-v1'


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def observe(row, now):
    """Bounded observations, recorded only when a fresh provider row arrives."""
    samples = row.setdefault('lab_samples', [])
    if samples and samples[-1]['at'] >= now:
        return
    samples.append({'at': now, 'liquidity': row['liquidity_usd'],
                    'buys': row['buys_m5'], 'sells': row['sells_m5']})
    row['lab_samples'] = [sample for sample in samples if now - sample['at'] <= 900][-100:]


def entry_signal(entry, row, evidence, now):
    if entry == 'instant':
        return row['first']
    if entry == 'pullback':
        return row['pullback']
    samples = row.get('lab_samples', [])
    if entry == 'pressure':
        def pressure(sample):
            return sample['buys'] + sample['sells'] >= 50 and sample['buys'] >= 2 * sample['sells']
        if len(samples) >= 2 and 5 <= samples[-1]['at'] - samples[-2]['at'] <= 90 and all(pressure(s) for s in samples[-2:]):
            return samples[-1]['at']
    elif entry == 'liquidity':
        baseline = [s for s in samples if 360 <= now - s['at'] <= 450]
        recent = [s for s in samples if 0 <= now - s['at'] <= 60]
        if baseline and recent and recent[0]['at'] <= now - 50:
            base = baseline[-1]
            span = [s for s in samples if s['at'] >= base['at']]
            if (base['liquidity'] > 0 and all(b['at'] - a['at'] <= 90 for a, b in zip(span, span[1:]))
                    and all(s['liquidity'] >= 1.25 * base['liquidity'] for s in recent)):
                return recent[-1]['at']
    chart = evidence.get('chart') or {}
    closed = chart.get('closed_at')
    if not finite(closed) or not 0 <= now - closed <= 90:
        return None
    if entry == 'volume' and chart.get('pattern') == 'volume_breakout' and (chart.get('volume_ratio') or 0) >= 2:
        return closed
    if entry == 'vwap' and chart.get('vwap_reclaim'):
        return closed
    if entry == 'trend' and chart.get('ema_cross_up') and chart.get('volume_rising'):
        return closed
    return None


def floor(exit_rule, peak, ladder_floor):
    if exit_rule in ('tp5', 'vwap', 'trend', 'stalled', 'breakeven'):
        if exit_rule == 'breakeven' and peak >= 15:
            return max(0, (100 + peak) * .9 - 100)
        return -10
    return ladder_floor(peak)


def exit_reason(position, gain, amount, now, chart, market, trade):
    rule = position['policy']['exit']
    if rule == 'tp5':
        if amount * 100 >= trade * 105:
            return 'Take profit +5% · full exit'
        if amount * 100 <= trade * 90:
            return 'Stop loss −10% · full exit'
        return None
    if rule == 'time5' and now - position['opened'] >= 300:
        return 'Five-minute time exit · full exit'
    if rule == 'stalled' and now - position.get('last_high_at', position['opened']) >= 180:
        return 'No new quoted high for three minutes · full exit'
    if rule == 'breakeven' and position['peak'] >= 15 and gain <= max(0, (100 + position['peak']) * .9 - 100):
        return 'Break-even / trailing protection · full exit'
    if rule == 'vwap' and finite(chart.get('vwap')) and finite(market.get('price_usd')) and market['price_usd'] < chart['vwap']:
        return 'Price fell below VWAP · full exit'
    if rule == 'trend' and finite(chart.get('ema_fast')) and finite(chart.get('ema_slow')) and chart['ema_fast'] < chart['ema_slow']:
        return 'EMA crossed down · full exit'
    return None
