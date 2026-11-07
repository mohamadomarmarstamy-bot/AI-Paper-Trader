from contextlib import closing
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3
import time
import unittest
from unittest.mock import patch
from solders.keypair import Keypair
from solders.pubkey import Pubkey
import database
import solana_real_execution as ex
import solana_real_worker as live
import solana_external_sales as external
ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def encode58(raw):
    n = int.from_bytes(raw, 'big'); out = ''
    while n:n, r = divmod(n, 58); out = ALPHABET[r] + out
    return '1' * (len(raw) - len(raw.lstrip(b'\0'))) + out


class Provider:
    def __init__(self, test):
        self.t = test; self.quantity = 400; self.txs = {'sale': test.tx()}; self.on_read = None
        self.page = [{'signature': 'sale', 'slot': 110, 'err': None}, {'signature': 'buy', 'slot': 100, 'err': None}]
    def network(self):pass
    def finalized_token_balance(self, *args):return {'quantity': self.quantity, 'slot': 120}
    def rpc(self, method, params):
        if self.on_read:self.on_read(); self.on_read = None
        if method == 'getSignaturesForAddress':
            self.t.assertIn(params[0], (self.t.account,self.t.wallet))
            self.t.assertEqual(params[1]['commitment'], 'finalized')
            return self.page
        self.t.assertEqual(method, 'getTransaction')
        self.t.assertEqual(params[1]['commitment'], 'finalized')
        return deepcopy(self.txs[params[0]])


class ExternalSalesTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        p = patch.object(database, 'DATABASE_PATH', Path(temp.name) / 'live.db'); p.start(); self.addCleanup(p.stop)
        self.wallet = str(Keypair().pubkey()); self.mint = str(Pubkey.new_unique())
        self.account = ex.ata(self.wallet, self.mint)
        self.state = live.initial(); self.state.update(enabled=True, wallet=self.wallet, realized_lamports=100)
        self.position = {'id': 'buy', 'mint': self.mint, 'quantity_raw': 1000, 'cost_lamports': 10000,
                         'opened_slot': 100, 'balance_checked_slot': 100, 'realized_lamports': 0,
                         'pending_reason': None, 'exit_value_lamports': 20000}
        self.state['positions']['buy'] = self.position
        self.entry = {'id': 'buy', 'wallet': self.wallet, 'mint': self.mint, 'side': 'buy', 'status': 'confirmed',
                      'signature': 'buy', 'created_at': 1000, 'prepared': {'token_program': ex.TOKEN},
                      'result': {'slot': 100, 'token_delta': 1000, 'native_delta': -10000}}
        with closing(database.get_connection()) as conn:
            live.initialize(conn)
            conn.execute('INSERT INTO solana_real_orders VALUES(?,?,?,?)', ('buy', 1000, 'confirmed', json.dumps(self.entry)))
            conn.commit()
        self.save(lease=True); self.provider = Provider(self)

    def save(self, lease=False):
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE solana_real_run SET state_json=?,lease_owner=?,lease_until=? WHERE id=1',
                         (json.dumps(self.state), 'worker' if lease else None, time.time() + 60 if lease else 0))
            conn.commit()

    def tx(self, signature='sale', before=1000, after=400, native=9000, slot=110):
        token = lambda n: {'accountIndex': 1, 'owner': self.wallet, 'mint': self.mint,
                           'uiTokenAmount': {'amount': str(n), 'decimals': 6}}
        return {'slot': slot, 'blockTime': 1100 + slot, 'meta': {'err': None, 'fee': 5,
            'preBalances': [100000, 2039280], 'postBalances': [100000 + native, 2039280],
            'preTokenBalances': [token(before)], 'postTokenBalances': [token(after)], 'innerInstructions': []},
            'transaction': {'signatures': [signature], 'message': {
                'accountKeys': [{'pubkey': self.wallet, 'signer': True}, {'pubkey': self.account, 'signer': False}],
                'instructions': [{'programId': ex.JUPITER, 'data': encode58(next(iter(ex.ROUTES))),
                                  'accounts': [self.wallet, self.account]}]}}}

    def audit(self):
        return external.audit(self.provider, self.wallet, self.mint, ex.TOKEN, 600, 100, 120, [self.entry])

    def test_partial_sale_is_atomic_attributed_and_keeps_running(self):
        self.assertEqual(live.reconcile_balances(self.state, self.provider, 2000), set())
        self.assertTrue(self.state['enabled']); self.assertFalse(self.state['balance_adjustments'])
        self.assertEqual((self.position['quantity_raw'], self.position['cost_lamports']), (400, 4000))
        self.assertEqual(self.state['realized_lamports'], 3100)
        live.commit_cycle(self.state, 'worker')
        saved, _ = live.load(); self.assertNotIn('_external_orders', saved)
        self.assertEqual(live.public()['orders'][0]['realized_pnl_lamports'], 3000)
        self.assertEqual(live.reconcile_balances(saved, self.provider, 2001), set())
        self.assertEqual(saved['realized_lamports'], 3100); self.assertEqual(len(live.orders()), 2)

    def test_full_sale_removes_only_bot_position(self):
        self.provider.quantity = 0; self.provider.txs['sale'] = self.tx(after=0)
        live.reconcile_balances(self.state, self.provider, 2000)
        self.assertFalse(self.state['positions']); self.assertTrue(self.state['enabled'])
        self.assertEqual(self.state['realized_lamports'], -900)

    def test_multiple_manual_sales_are_summed_once(self):
        self.provider.txs = {'first': self.tx('first', 1000, 700, 4500, 109), 'sale': self.tx('sale', 700, 400, 4500)}
        self.provider.page.insert(1, {'signature': 'first', 'slot': 109, 'err': None})
        self.assertEqual(len(self.audit()['receipts']), 2)
        live.reconcile_balances(self.state, self.provider, 2000); live.commit_cycle(self.state, 'worker')
        self.assertEqual(live.load()[0]['realized_lamports'], 3100)
        self.assertEqual(len(external.used_signatures(live.orders())), 3)

    def test_owner_pause_during_audit_survives_commit(self):
        self.provider.on_read = lambda: live.pause(live.load()[1])
        live.reconcile_balances(self.state, self.provider, 2000); live.commit_cycle(self.state, 'worker')
        self.assertFalse(live.load()[0]['enabled'])
        self.assertEqual(live.load()[0]['realized_lamports'], 3100)

    def test_already_paused_stays_paused(self):
        self.state['enabled'] = False
        live.reconcile_balances(self.state, self.provider, 2000)
        self.assertFalse(self.state['enabled'])

    def test_receipt_insert_failure_rolls_back_accounting_and_retry_is_safe(self):
        live.reconcile_balances(self.state, self.provider, 2000)
        with closing(database.get_connection()) as conn:
            conn.execute("CREATE TRIGGER fail_external BEFORE INSERT ON solana_real_orders BEGIN SELECT RAISE(ABORT,'synthetic'); END"); conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):live.commit_cycle(self.state, 'worker')
        self.assertEqual(live.load()[0]['realized_lamports'], 100)
        with closing(database.get_connection()) as conn:conn.execute('DROP TRIGGER fail_external'); conn.commit()
        live.commit_cycle(self.state, 'worker')
        self.assertEqual(live.load()[0]['realized_lamports'], 3100); self.assertEqual(len(live.orders()), 2)

    def test_transfer_with_sol_deposit_is_not_a_sale(self):
        self.provider.txs['sale']['transaction']['message']['instructions'] = [
            {'programId': ex.TOKEN, 'parsed': {'type': 'transfer', 'info': {}}}]
        with self.assertRaises(ex.Unavailable):self.audit()
        live.reconcile_balances(self.state, self.provider, 2000)
        self.assertTrue(self.state['enabled']); self.assertTrue(live.accounting_pending(self.state)); self.assertEqual(self.state['realized_lamports'], 100)
        self.assertNotIn('_external_orders', self.state)

    def test_ambiguous_receipts_never_book(self):
        original = deepcopy(self.provider.txs['sale'])
        def mutate(case, tx):
            if case == 'failed':tx['meta']['err'] = {'InstructionError': [0, 'failed']}
            if case == 'missing':return None
            if case == 'wrong_signature':tx['transaction']['signatures'] = ['other']
            if case == 'wrong_wallet':tx['transaction']['message']['accountKeys'][0]['pubkey'] = 'other'
            if case == 'unsigned':tx['transaction']['message']['accountKeys'][0]['signer'] = False
            if case == 'wrong_slot':tx['slot'] = 111
            if case == 'no_sol':tx['meta']['postBalances'][0] = tx['meta']['preBalances'][0]
            if case == 'wrong_quantity':tx['meta']['postTokenBalances'][0]['uiTokenAmount']['amount'] = '399'
            if case == 'token_output':tx['meta']['postTokenBalances'].append({'accountIndex': 1, 'owner': self.wallet, 'mint': 'stable', 'uiTokenAmount': {'amount': '7'}})
            if case == 'extra_sol_transfer':tx['transaction']['message']['instructions'].append({'programId': ex.SYSTEM, 'parsed': {'type': 'transfer', 'info': {'source': self.wallet, 'destination': 'other'}}})
            if case == 'unknown_route':tx['transaction']['message']['instructions'][0]['programId'] = 'unknown'
            if case == 'multi_swap':tx['transaction']['message']['instructions'] *= 2
            if case == 'wrong_account':tx['transaction']['message']['accountKeys'][1]['pubkey'] = 'other'
            return tx
        for case in ('failed', 'missing', 'wrong_signature', 'wrong_wallet', 'unsigned', 'wrong_slot', 'no_sol', 'wrong_quantity', 'token_output', 'extra_sol_transfer', 'unknown_route', 'multi_swap', 'wrong_account'):
            with self.subTest(case=case):
                self.provider.txs['sale'] = mutate(case, deepcopy(original))
                with self.assertRaises(ex.Unavailable):self.audit()

    def test_incomplete_history_or_duplicate_or_already_booked_signature_rejected(self):
        for page in ([{'signature': str(i), 'slot': 110, 'err': None} for i in range(50)],
                     [self.provider.page[0]] * 2):
            self.provider.page = page
            with self.assertRaises(ex.Unavailable):self.audit()
        self.provider.page = [{'signature': 'buy', 'slot': 110, 'err': None}]
        with self.assertRaises(ex.Unavailable):self.audit()

    def test_stable_observation_updates_checkpoint_without_history_reads(self):
        self.provider.quantity = 1000
        self.provider.rpc = lambda *a: self.fail('unchanged balance needs no history RPC')
        live.reconcile_balances(self.state, self.provider, 2000)
        self.assertEqual(self.position['balance_checked_slot'], 120)

    def adjustment(self):
        return {'position_id': 'buy', 'mint': self.mint, 'quantity_removed_raw': 600,
                'unreconciled_cost_lamports': 6000, 'finalized_slot': 120}

    def test_old_adjustment_is_repaired_once_without_restart(self):
        self.state['enabled'] = False; self.state['balance_adjustments'] = [self.adjustment()]
        self.position.update(quantity_raw=400, cost_lamports=4000, external_change=True)
        self.save()
        self.assertTrue(external.reconcile_pending(self.provider))
        saved, _ = live.load(); self.assertFalse(saved['enabled']); self.assertFalse(saved['balance_adjustments'])
        self.assertNotIn('external_change', saved['positions']['buy']); self.assertEqual(saved['realized_lamports'], 3100)
        self.assertFalse(external.reconcile_pending(self.provider))

    def test_lease_pending_and_revision_race_prevent_old_repair(self):
        self.state['balance_adjustments'] = [self.adjustment()]; self.save(lease=True)
        self.assertFalse(external.reconcile_pending(self.provider))
        self.save(); self.provider.on_read = lambda: live.pause(live.load()[1])
        self.assertFalse(external.reconcile_pending(self.provider))
        self.assertEqual(live.load()[0]['realized_lamports'], 100)
        pending = {'id': 'unknown', 'status': 'unknown'}
        with closing(database.get_connection()) as conn:
            conn.execute('INSERT INTO solana_real_orders VALUES(?,?,?,?)', ('unknown', 1, 'unknown', json.dumps(pending))); conn.commit()
        self.provider.rpc = lambda *a: self.fail('pending order must block audit')
        self.assertFalse(external.reconcile_pending(self.provider))

    def test_bot_sell_advances_balance_checkpoint(self):
        self.position.update(protection={}, stages=0)
        live.post_receipt(self.state, {'side': 'sell', 'position_id': 'buy', 'reason': 'manual'},
                          {'state': 'confirmed', 'token_delta': -100, 'native_delta': 2000, 'slot': 115}, 2000)
        self.assertEqual(self.position['balance_checked_slot'], 115)


if __name__ == '__main__':unittest.main()
