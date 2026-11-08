from contextlib import closing
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch
import database
import solana_real_worker as live
import solana_manual_accounting as manual
import solana_token_age as age
from solana_real_execution import Unavailable


class AgeTests(unittest.TestCase):
    def setUp(self):
        age._proofs.clear()

    def rpc(self, stamp, balances=True):
        def read(method, args):
            if method == 'getSignaturesForAddress':
                return [{'signature': 'proof', 'blockTime': stamp, 'err': None}]
            return {'blockTime': stamp, 'meta': {'err': None, 'preTokenBalances': [],
                    'postTokenBalances': [{'mint': 'coin'}] if balances else []},
                    'transaction': {'signatures': ['proof'], 'message': {'instructions': []}}}
        return read

    def test_under_two_hours_rejected_and_exact_two_hours_admitted(self):
        with self.assertRaises(age.AgeUnavailable):age.require_age('coin', rpc=self.rpc(10000-7199), now=10000)
        self.assertEqual(age.require_age('coin', rpc=self.rpc(2800), now=10000)['existed_at'], 2800)

    def test_old_address_reference_without_mint_activity_is_not_age_proof(self):
        with self.assertRaises(age.AgeUnavailable):age.require_age('coin', rpc=self.rpc(1000, False), now=10000)

    def test_old_thirty_minute_cache_cannot_bypass_two_hour_minimum(self):
        age._proofs['coin'] = dict(mint='coin', checked_at=9999, existed_at=8200,
                                  minimum_age_seconds=1800)
        with self.assertRaises(age.AgeUnavailable):
            age.require_age('coin', rpc=self.rpc(8200), now=10000)

    def test_missing_time_rpc_failure_and_future_time_fail_closed(self):
        for read in (lambda *a: [], lambda *a: [{'blockTime': None, 'err': None}], self.rpc(11000)):
            with self.assertRaises(age.AgeUnavailable):age.require_age('coin', rpc=read, now=10000)
        with self.assertRaises(age.AgeUnavailable):age.require_age('coin', rpc=lambda *a: 1/0, now=10000)

    def test_cache_is_mint_specific_and_cannot_be_used_before_observation(self):
        age.require_age('coin', rpc=self.rpc(1000), now=10000)
        self.assertEqual(age.require_age('coin', rpc=lambda *a: 1/0, now=10001)['mint'], 'coin')
        with self.assertRaises(age.AgeUnavailable):age.require_age('other', rpc=lambda *a: [], now=10001)
        with self.assertRaises(age.AgeUnavailable):age.require_age('coin', rpc=lambda *a: [], now=9999)


class ReviewedProvider:
    def network(self):pass
    def rpc(self, method, args):
        sale=getattr(self, 'sale', manual.REVIEWED_SALES[0]);sig=args[0];delta=dict(sale['legs'])[sig]
        swap=sig==sale['legs'][0 if len(sale['legs']) == 2 else 1][0]
        return {'slot':464575808,'blockTime':sale['time_window'][0],'meta':{'err':None,'fee':10000,
            'preBalances':[1000000000], 'postBalances':[1000000000+delta],
            'preTokenBalances':[{'owner':sale['wallet'],'mint':sale['mint'],
              'uiTokenAmount':{'amount':str(sale['quantity'])}}] if swap else [], 'postTokenBalances':[]},
            'transaction':{'signatures':[sig],'message':{'accountKeys':[{'pubkey':sale['wallet'],'signer':True}]}}}


class AccountingTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'accounting.db');p.start();self.addCleanup(p.stop)
        sale=manual.REVIEWED_SALES[0];self.sale=sale
        state=live.initial();state['wallet']=sale['wallet'];state['realized_lamports']=-100
        adjustment={'position_id':sale['position_id'],'mint':sale['mint'],
                    'quantity_removed_raw':sale['quantity'],'unreconciled_cost_lamports':sale['cost']}
        state['balance_adjustments']=[adjustment]
        with closing(database.get_connection()) as conn:
            live.initialize(conn)
            conn.execute('UPDATE solana_real_run SET state_json=? WHERE id=1',(json.dumps(state),));conn.commit()

    def test_bundled_sale_booked_once_without_restarting(self):
        self.assertTrue(manual.reconcile_reviewed(ReviewedProvider()))
        state,_=live.load();self.assertFalse(state['enabled']);self.assertFalse(state['balance_adjustments'])
        self.assertEqual(state['realized_lamports'], -100+81032391-109030226)
        self.assertEqual(len(state['accounting_reviews'][0]['receipts']),5)
        self.assertFalse(manual.reconcile_reviewed(ReviewedProvider()))
        self.assertEqual(live.load()[0]['realized_lamports'],state['realized_lamports'])
        self.assertEqual(live.orders()[0]['reason'],'manual_external')

    def test_kimchi_cash_settlement_books_once(self):
        sale = manual.REVIEWED_SALES[1]
        state, _ = live.load()
        state['balance_adjustments'] = [{
            'position_id': sale['position_id'], 'mint': sale['mint'],
            'quantity_removed_raw': sale['quantity'],
            'unreconciled_cost_lamports': sale['cost']}]
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE solana_real_run SET state_json=? WHERE id=1', (json.dumps(state),))
            conn.commit()
        provider = ReviewedProvider()
        provider.sale = sale
        self.assertTrue(manual.reconcile_reviewed(provider))
        updated, _ = live.load()
        self.assertEqual(updated['realized_lamports'], -100 - 3745058)
        self.assertFalse(updated['enabled'])
        self.assertFalse(updated['balance_adjustments'])
        self.assertEqual(live.orders()[0]['signature'], sale['settlement_signature'])
        self.assertFalse(manual.reconcile_reviewed(provider))

    def test_wrong_receipt_never_clears_adjustment(self):
        provider=ReviewedProvider();original=provider.rpc
        def wrong(*args):
            tx=original(*args);tx['meta']['postBalances'][0]+=1;return tx
        provider.rpc=wrong
        with self.assertRaises(Unavailable):manual.reconcile_reviewed(provider)
        self.assertTrue(live.load()[0]['balance_adjustments']);self.assertFalse(live.orders())

    def test_unknown_adjustment_is_preserved(self):
        state,_=live.load();state['balance_adjustments'][0]['mint']='another-coin'
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE solana_real_run SET state_json=? WHERE id=1',(json.dumps(state),));conn.commit()
        self.assertFalse(manual.reconcile_reviewed(ReviewedProvider()))
        self.assertTrue(live.load()[0]['balance_adjustments'])


if __name__=='__main__':unittest.main()
