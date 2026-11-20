"""Entry-time warning signs and separate legacy diagnostics for Jarvis paper."""
from contextlib import closing
import sqlite3

import jarvis_research as research
import solana_meme_learning as legacy

SCHEMA = """
CREATE TABLE IF NOT EXISTS legacy_outcome_summary(
    id INTEGER PRIMARY KEY CHECK (id=1), data TEXT NOT NULL
);
"""

WARNINGS = {
    'young_thin_pool': 'Pool younger than 2 hours with liquidity under $100,000',
    'low_liquidity': 'Pool liquidity under $100,000',
    'young_pool': 'Pool younger than 2 hours',
    'heavy_selling': 'More sells than buys in the prior 5 minutes',
    'failed_breakout': 'Failed breakout at entry',
}


def warning(rule, evidence, liquidity, age):
    if rule == 'young_thin_pool':
        return legacy.finite(age) and legacy.finite(liquidity) and age < 120 and liquidity < 100_000
    if rule == 'low_liquidity':
        return legacy.finite(liquidity) and liquidity < 100_000
    if rule == 'young_pool':
        return legacy.finite(age) and age < 120
    if rule == 'heavy_selling':
        market = evidence.get('market', {})
        buys, sells = market.get('buys_m5'), market.get('sells_m5')
        return legacy.finite(buys) and legacy.finite(sells) and sells > buys
    if rule == 'failed_breakout':
        return (evidence.get('chart') or {}).get('pattern') == rule
    return False


def split(rule, trades):
    matched, remaining = [], []
    for trade in trades:
        group = matched if warning(rule, trade.get('entry_evidence') or {}, trade.get('liquidity'), trade.get('age')) else remaining
        group.append(trade)
    return matched, remaining


def winner_loss_counts(rows):
    wins = sum(t['pnl'] > 0 for t in rows)
    losses = sum(t['pnl'] < 0 for t in rows)
    return {'wins': wins, 'losses': losses, 'flat': len(rows) - wins - losses}


def examples(trades, limit=5):
    """Keep a balanced view of recent examples, using exact-address identifiers."""
    output = {'winners': [], 'losers': []}
    for trade in reversed(trades):
        pnl = trade['pnl']
        if pnl == 0:
            continue
        side = 'winners' if pnl > 0 else 'losers'
        if len(output[side]) >= limit:
            continue
        evidence = trade.get('entry_evidence') or {}
        signs = [label for key, label in WARNINGS.items()
                 if warning(key, evidence, trade.get('liquidity'), trade.get('age'))]
        positive = [rule.replace('_', ' ') for rule in ('volume_breakout', 'breakout_retest',
                    'pullback_recovery', 'social_confirmed') if research.allows(rule, evidence)]
        output[side].append({'mint': trade['mint'], 'name': trade.get('symbol') or trade['mint'],
                             'bot': trade['bot'], 'pnl': pnl, 'warnings': signs, 'positive': positive})
    return output


def legacy_counts(path):
    """Read the prior Auto Trader archive without sharing its modeled P/L labels."""
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
        rows = legacy.recent(conn, limit=1000)
    trades = []
    for row in rows:
        pnl = row.get('pnl_usd')
        if not legacy.finite(pnl) or row.get('exit_source', 'automatic') != 'automatic':
            continue
        features = row.get('entry_features') or {}
        if legacy.finite(features.get('liquidity_usd')) and legacy.finite(features.get('age_minutes')):
            trades.append({'pnl': pnl, 'liquidity': features['liquidity_usd'], 'age': features['age_minutes'],
                           'entry_evidence': {'market': features}})
    counts = winner_loss_counts(trades)
    counts['warnings'] = [{'label': label, **winner_loss_counts(split(rule, trades)[0])}
                          for rule, label in WARNINGS.items() if rule != 'failed_breakout']
    return counts


def collection(trades):
    return {**winner_loss_counts(trades), 'examples': examples(trades),
            'warnings': [{'label': label, **winner_loss_counts(split(rule, trades)[0])}
                         for rule, label in WARNINGS.items()]}
