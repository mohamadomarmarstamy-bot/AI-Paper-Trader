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
    'lab-tp20-sl10': ('Take +20% / Stop −10%', 'instant', 'tp20'),
    'lab-squeeze': ('Squeeze Breakout', 'squeeze', 'squeeze'),
    'lab-rsi': ('RSI Recovery', 'rsi', 'tp15'),
    'lab-relative': ('Relative Strength', 'relative', 'relative'),
    'lab-drain': ('Liquidity Drain Exit', 'pressure', 'drain'),
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
NEW_BOTS = {'lab-tp20-sl10', 'lab-squeeze', 'lab-rsi', 'lab-relative', 'lab-drain'}
SOL = 'So11111111111111111111111111111111111111112'
BRACKETS = {'tp5': 5, 'tp15': 15, 'tp20': 20}
DESCRIPTIONS.update({
    'tp20': 'Sells fully at a quoted token-value gain of +20% or loss of −10%. Fees reduce net P/L; quote availability affects fills.',
    'tp15': 'Sells fully at a quoted token-value gain of +15% or loss of −10%. Fees reduce net P/L; quote availability affects fills.',
    'squeeze': 'Buys a completed candle above a ten-minute range no wider than 3%, with volume at least 1.5 times its median. Price must remain above the range.',
    'squeeze_exit': 'Uses the ladder stop and sells fully if fresh observed price returns inside the original range.',
    'rsi': 'Buys when 14-period Wilder RSI crosses above 35 after an RSI below 30 in the preceding five completed candles.',
    'relative': 'Requires positive five-minute price growth exceeding SOL by at least two percentage points, plus the Buy Pressure confirmation. Needs five minutes of matched observations.',
    'relative_exit': 'Starts with a −10% stop; sells fully when matched five-minute coin growth falls to or below SOL growth.',
    'drain': 'Uses Buy Pressure entries and the ladder stop; also sells fully if fresh same-pool liquidity falls 15% from entry.',
})


def version(bot):
    return 'strategy-lab-v2' if bot in NEW_BOTS else VERSION


def relative_metrics(row, benchmark, now):
    samples = [s for s in row.get('lab_samples', []) if finite(s.get('price')) and s['price'] > 0]
    if not samples or not benchmark:
        return None
    last = samples[-1]
    if not 0 <= now - last['at'] <= 90:
        return None
    earlier = [s for s in samples if 300 <= last['at'] - s['at'] <= 360]
    if not earlier:
        return None
    first = earlier[-1]
    def matched(at):
        values = [s for s in benchmark.get('samples', []) if 0 <= at - s['at'] <= 30
                  and finite(s.get('price')) and s['price'] > 0]
        return values[-1] if values else None
    start, end = matched(first['at']), matched(last['at'])
    if start is None or end is None:
        return None
    coin = 100 * (last['price'] / first['price'] - 1)
    sol = 100 * (end['price'] / start['price'] - 1)
    if not finite(coin) or not finite(sol):
        return None
    return {'coin_percent': coin, 'sol_percent': sol, 'excess_points': coin - sol, 'at': last['at']}



def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def observe(row, now):
    """Bounded observations, recorded only when a fresh provider row arrives."""
    samples = row.setdefault('lab_samples', [])
    if samples and samples[-1]['at'] >= now:
        return
    samples.append({'at': now, 'price': row.get('price_usd'), 'liquidity': row['liquidity_usd'],
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
    if entry == 'relative':
        metrics = relative_metrics(row, evidence.get('benchmark'), now)
        pressure = entry_signal('pressure', row, evidence, now)
        if metrics and metrics['coin_percent'] > 0 and metrics['excess_points'] >= 2 and pressure is not None:
            return metrics['at']
        return None
    chart = evidence.get('chart') or {}
    closed = chart.get('closed_at')
    if not finite(closed) or not 0 <= now - closed <= 90:
        return None
    if entry == 'volume' and chart.get('pattern') == 'volume_breakout' and (chart.get('volume_ratio') or 0) >= 2:
        return closed
    if entry == 'squeeze' and chart.get('squeeze_breakout') and finite(row.get('price_usd')) and row['price_usd'] > chart['squeeze_level']:
        return closed
    if entry == 'rsi' and chart.get('rsi_recovery'):
        return closed
    if entry == 'vwap' and chart.get('vwap_reclaim'):
        return closed
    if entry == 'trend' and chart.get('ema_cross_up') and chart.get('volume_rising'):
        return closed
    return None


def floor(exit_rule, peak, ladder_floor):
    if exit_rule in (*BRACKETS, 'relative', 'vwap', 'trend', 'stalled', 'breakeven'):
        if exit_rule == 'breakeven' and peak >= 15:
            return max(0, (100 + peak) * .9 - 100)
        return -10
    return ladder_floor(peak)


def exit_reason(position, gain, amount, now, chart, market, trade, benchmark=None):
    rule = position['policy']['exit']
    if rule in BRACKETS:
        target = BRACKETS[rule]
        if amount * 100 >= trade * (100 + target):
            return f'Take profit +{target}% · full exit'
        if amount * 100 <= trade * 90:
            return 'Stop loss −10% · full exit'
        return None
    if rule == 'drain' and finite(market.get('liquidity_usd')) and position['liquidity'] > 0 and market['liquidity_usd'] <= position['liquidity'] * .85:
        return 'Pool liquidity fell 15% · full exit'
    if rule == 'relative':
        metrics = relative_metrics(market, benchmark, now)
        if metrics and metrics['excess_points'] <= 0:
            return 'Relative strength versus SOL faded · full exit'
    if rule == 'squeeze':
        level = (position.get('entry_evidence', {}).get('chart') or {}).get('squeeze_level')
        if finite(level) and finite(market.get('price_usd')) and market['price_usd'] <= level:
            return 'Price returned inside squeeze range · full exit'
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
