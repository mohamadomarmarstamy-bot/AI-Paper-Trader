"""Prospective paper-only candidate roster in a separate SQLite ledger.

No live worker, signer, mail worker or automatic promotion is invoked here.
"""
import asyncio
from contextlib import closing
import json
import logging
from pathlib import Path
import time

import database
import solana_copy_trader as paper

CANDIDATES = (
    ('Jijo', '4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk'),
    ('Tech', '5d3jQcuUvsuHyZkhdp78FFqc7WogrzZpTtec1X9VNkuE'),
    ('Kadenox', 'B32QbbdDAyhvUQzjcaM5j6ZVKwjCxAwGH5Xgvb9SJqnC'),
)


def exists():
    return Path(database.DATABASE_PATH).with_name('copy_wallet_trials.sqlite3').exists()


def start():
    # Read strategy once; subsequent trial writes cannot touch the source ledger.
    settings = paper.load()[0]['config'].copy()
    with database.paper_trial_context(), closing(database.get_connection()) as conn:
        paper.initialize(conn)
        state, revision = paper.load()
        if state.get('trial_started_at'):
            return status()  # Idempotent: never reset results or replay history.
        now = time.time()
        state['config'].update(settings)
        state['config'].update(enabled=True, auto_discover=False, paper_capital_usd=100000)
        state['cash_usd'] = 100000.
        state['public_rpc_enabled'] = True
        state['trial_started_at'] = now
        state['traders'] = [dict(name=name, wallet=wallet, active=True,
            paper_only=True, selected_at=now, buy_resume_at=now,
            qualification='owner_requested_paper_trial', trade_provider='rpc')
            for name, wallet in CANDIDATES]
        state['cursors'] = {wallet: {'at': now, 'provider': 'rpc'} for _, wallet in CANDIDATES}
        state['settings_revision'] += 1
        if conn.execute('UPDATE solana_copy_state SET state_json=?,revision=revision+1 WHERE id=1 AND revision=?',
                        (json.dumps(state, allow_nan=False), revision)).rowcount != 1:
            raise paper.bot.Conflict('Trial changed. Retry without resetting it.')
        conn.commit()
    return status()


def status():
    if not exists():
        return {'paper': True, 'started': False, 'live_execution_enabled': False,
                'candidates': [{'name': n, 'wallet': w} for n, w in CANDIDATES]}
    with database.paper_trial_context():
        state, _ = paper.load()
        result = paper.public()
        with closing(database.get_connection()) as conn:
            rows = [json.loads(r[0]) for r in conn.execute('SELECT details_json FROM solana_copy_closes')]
        result.update(started=bool(state.get('trial_started_at')), trial_started_at=state.get('trial_started_at'),
                      automatic_promotion=False, isolated_paper_trial=True)
        result['wallet_results'] = []
        for name, wallet in CANDIDATES:
            exits = [r for r in rows if r['wallet'] == wallet]
            lots = {}
            for row in exits:
                lots.setdefault(row['lot_id'], []).append(row)
            complete = [sum(r['pnl_usd'] for r in lot) for lot in lots.values()
                        if any(not r.get('partial_exit', False) for r in lot)]
            result['wallet_results'].append(dict(name=name, wallet=wallet,
                realized_pnl_usd=sum(r['pnl_usd'] for r in exits),
                exit_count=len(exits), completed_positions=len(complete),
                winning_completed_positions=sum(p > 0 for p in complete),
                open_positions=sum(p['wallet'] == wallet for p in state['positions'].values())))
        return result


class TrialProvider(paper.Birdeye):
    def discover(self, state, clock):
        pass  # Fixed approved roster; neither discovery pipeline can add wallets.


def tick():
    if not exists():
        return
    with database.paper_trial_context():
        paper.tick(provider=TrialProvider())


async def worker():
    while True:
        started = time.monotonic()
        try:
            await asyncio.to_thread(tick)
        except Exception:
            logging.getLogger(__name__).exception('Paper wallet trial cycle failed')
        await asyncio.sleep(max(1, 10 - (time.monotonic() - started)))
