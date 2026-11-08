"""Explicit owner-selected additions; never re-enable an existing paused source."""
from copy import deepcopy

LIVE_ADDITION = {
    'name': '9s4j',
    'wallet': '9s4ji7MRh6xx9PVx7D4by8YjpHMfPVtHfYuMUy8VaTTp',
    'source_url': 'https://dextrading.com/wallet/9s4ji7MRh6xx9PVx7D4by8YjpHMfPVtHfYuMUy8VaTTp/solana',
    'reviewed_on': '2026-11-08',
    'window': '30d',
    'reported_realized_usd': 121640,
    'reported_win_rate_percent': 97,
    'reported_closed_positions': 87,
    'reported_average_hold_seconds': 2880,
    'basis': 'DexTrading report, not independently verified. Owner explicitly selected live copying. Actual copied returns, supported swap decoding and execution remain subject to existing checks.',
}


def enroll(state, now):
    # The isolated trial uses the same simulator; never leak this source into it.
    # Wait for the established main RPC feed, preserving disabled/empty setups.
    if state.get('trial_started_at') or not state.get('started_at') or not state.get('public_rpc_enabled'):
        return
    wallet = LIVE_ADDITION['wallet']
    if any(row['wallet'] == wallet for row in state['traders']):
        return
    state['traders'].append({
        'name': LIVE_ADDITION['name'], 'wallet': wallet, 'active': True,
        'paper_only': False, 'auto_discovered': False,
        'selected_at': now, 'buy_resume_at': now, 'trade_provider': 'rpc',
        'qualification': 'owner_requested_live_source',
        'research_evidence': deepcopy(LIVE_ADDITION),
        'historical_realized_usd': None, 'win_rate_percent': None, 'sell_count': None,
    })
    state['cursors'][wallet] = {'at': now, 'provider': 'rpc'}
