"""Regression against the public finalized Phantom receipts from 2026-11-06.

The fixture contains public chain data only. No signer or API credentials.
"""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import json
import unittest
from unittest.mock import patch
import solana_external_sales as external
import solana_phantom_accounting as phantom
import solana_real_execution as ex
import solana_real_worker as live
import test_solana_external_sales as base

WALLET = '3tmujS2fwJiPHRXc6NskYz1vJHf1Eq9JNvx2547MKut6'
MINT = '9hbzrGcuBBjqFhDgHzjF9jGPjWHZV6nRwjDeS38ypump'
QUANTITY = 634216588977
FIXTURE = Path(__file__).with_name('fixtures') / 'phantom_cash_bundle.json'


class PhantomProvider:
    def __init__(self):
        self.txs = json.loads(FIXTURE.read_text()); self.extra = None; self.cash_extra = None; self.slot = 466369900
    def network(self):pass
    def finalized_token_balance(self,*args):return {'quantity':0,'slot':self.slot}
    def rpc(self,method,params):
        if method == 'getSignaturesForAddress':
            if params[0] == WALLET:
                rows = [{'signature':t['transaction']['signatures'][0],'slot':t['slot'],'err':None} for t in reversed(self.txs)]
                return ([self.extra] if self.extra else []) + rows
            if params[0] == '2bPGZ9duaRT8SEC3E8dZ4UZGQ6eVNSF7ntogvft885LD':
                rows = [self.txs[3],self.txs[1]]
                result = [{'signature':t['transaction']['signatures'][0],'slot':t['slot'],'err':None} for t in rows]
                if self.cash_extra:result.insert(1,self.cash_extra)
                return result
            return []
        return deepcopy(next(t for t in self.txs if t['transaction']['signatures'][0] == params[0]))


class PhantomTests(unittest.TestCase):
    def setUp(self):self.p = PhantomProvider()
    def audit(self,orders=None):
        return phantom.audit(self.p,WALLET,MINT,ex.TOKEN2022,QUANTITY,466355410,466369706,orders or [])

    def test_actual_cash_bundle_traces_all_five_receipts_and_net_sol(self):
        result = self.audit()
        self.assertEqual(result['token_delta'],-QUANTITY)
        self.assertEqual(result['native_delta'],428160364)
        self.assertEqual(len(result['receipts']),5)
        self.assertEqual(sum(r['native_delta'] for r in result['receipts']),428160364)
        self.assertEqual(result['fee_lamports'],145000)

    def test_mutated_bundles_remain_unresolved(self):
        original = deepcopy(self.p.txs)
        for case in ('incomplete','failed','wrong_signature','wrong_slot','different_cash','cash_not_drained',
                     'unrelated_funding','missing_refund','other_payout','broken_continuity','excess_fee','wrong_router',
                     'unsigned_sale','unsigned_fee','unrelated_account_close','cash_spent_and_replaced','same_slot_wrong_order'):
            with self.subTest(case=case):
                self.p = PhantomProvider(); rows = self.p.txs
                if case == 'incomplete':rows.pop(4)
                if case == 'failed':rows[1]['meta']['err'] = {'failed':True}
                if case == 'wrong_signature':
                    original_rpc = self.p.rpc
                    def rpc(method,params):
                        tx=original_rpc(method,params)
                        if method == 'getTransaction':tx['transaction']['signatures'][0] = 'wrong'
                        return tx
                    self.p.rpc = rpc
                if case == 'wrong_slot':rows[3]['slot'] = 466369705
                if case == 'different_cash':rows[3]['meta']['preTokenBalances'][1]['uiTokenAmount']['amount'] = '38752209'
                if case == 'cash_not_drained':rows[3]['meta']['postTokenBalances'][1]['uiTokenAmount']['amount'] = '1'
                if case == 'unrelated_funding':rows[0]['transaction']['message']['instructions'][1]['parsed']['info']['source'] = 'other'
                if case == 'missing_refund':rows[2]['meta']['innerInstructions'][0]['instructions'][0]['parsed']['info']['lamports'] = 49_000_000
                if case == 'other_payout':
                    for i in phantom.instructions(rows[3]):
                        if i.get('parsed',{}).get('type') == 'transfer' and i['parsed']['info'].get('destination') == WALLET:i['parsed']['info']['destination'] = 'other'
                if case == 'broken_continuity':rows[2]['meta']['preBalances'][phantom.key(rows[2],WALLET)] += 1
                if case == 'excess_fee':rows[4]['meta']['postBalances'][phantom.key(rows[4],WALLET)] -= 2_000_000
                if case == 'wrong_router':rows[3]['transaction']['message']['instructions'][0]['programId'] = 'unknown'
                if case == 'unsigned_sale':rows[1]['transaction']['message']['accountKeys'][0]['signer'] = False
                if case == 'unsigned_fee':rows[4]['transaction']['message']['accountKeys'][phantom.key(rows[4],WALLET)]['signer'] = False
                if case == 'unrelated_account_close':rows[2]['transaction']['message']['instructions'][1]['parsed']['info']['account'] = 'other'
                if case == 'cash_spent_and_replaced':self.p.cash_extra = {'signature':'intervening','slot':466369800,'err':None}
                if case == 'same_slot_wrong_order':rows[2]['transactionIndex'] = rows[1]['transactionIndex']
                with self.assertRaises(ex.Unavailable):self.audit()
        self.p.txs = original

    def test_same_receipts_cannot_be_used_for_another_position(self):
        with self.assertRaises(ex.Unavailable):self.audit([{'signature':self.p.txs[3]['transaction']['signatures'][0]}])

    def test_wrong_missing_quantity_rejected(self):
        with self.assertRaises(ex.Unavailable):phantom.audit(self.p,WALLET,MINT,ex.TOKEN2022,QUANTITY-1,466355410,466369706,[])


class PhantomLedgerTests(unittest.TestCase):
    save = base.ExternalSalesTests.save
    tx = base.ExternalSalesTests.tx
    # Reuse the isolated database fixture; only these integration tests need the
    # public wallet and exact actual receipts.
    def setUp(self):
        base.ExternalSalesTests.setUp(self)
        self.wallet = WALLET; self.mint = MINT; self.account = ex.ata(WALLET,MINT,ex.TOKEN2022)
        self.state['wallet'] = WALLET
        self.position.update(mint=MINT,quantity_raw=QUANTITY,cost_lamports=554716186,
                             opened_slot=466355410,balance_checked_slot=466355410,token_program=ex.TOKEN2022)
        self.entry.update(wallet=WALLET,mint=MINT,prepared={'token_program':ex.TOKEN2022},
                          result={'slot':466355410,'token_delta':QUANTITY,'native_delta':-554716186})
        with closing(base.database.get_connection()) as conn:
            conn.execute('UPDATE solana_real_orders SET details_json=? WHERE id=?',(json.dumps(self.entry),'buy'));conn.commit()
        self.save(lease=True); self.provider = PhantomProvider()

    def test_phantom_running_position_is_accounted_without_pause(self):
        self.assertEqual(live.reconcile_balances(self.state,self.provider,2000),set())
        live.commit_cycle(self.state,'worker')
        state,_ = live.load(); self.assertTrue(state['enabled']); self.assertFalse(state['positions'])
        self.assertEqual(state['realized_lamports'],100-126555822)
        self.assertEqual(len(state['accounting_reviews'][0]['receipts']),5)

    def test_existing_phantom_adjustment_repairs_while_remaining_paused(self):
        self.state['enabled'] = False; self.state['positions'] = {}
        self.state['balance_adjustments'] = [{'position_id':'buy','mint':MINT,'quantity_removed_raw':QUANTITY,
            'unreconciled_cost_lamports':554716186,'finalized_slot':466369706}]
        self.save()
        self.assertTrue(external.reconcile_pending(self.provider))
        state,_ = live.load(); self.assertFalse(state['enabled']); self.assertFalse(state['balance_adjustments'])
        self.assertEqual(state['realized_lamports'],100-126555822)
        self.assertFalse(external.reconcile_pending(self.provider))

    def test_incomplete_cash_conversion_holds_buys_then_clears_automatically(self):
        complete = deepcopy(self.provider.txs); self.provider.txs = self.provider.txs[:3]
        self.provider.slot = 466369707
        live.reconcile_balances(self.state,self.provider,2000);live.commit_cycle(self.state,'worker')
        self.state,_ = live.load();self.assertTrue(self.state['enabled']);self.assertTrue(live.accounting_pending(self.state))
        self.save(lease=True);self.provider.slot += 1
        live.reconcile_balances(self.state,self.provider,2016);live.commit_cycle(self.state,'worker')
        self.state,_ = live.load();self.assertTrue(self.state['enabled']);self.assertTrue(self.state['balance_adjustments'])
        self.provider.txs = complete
        self.assertTrue(external.reconcile_pending(self.provider))
        state,_ = live.load();self.assertTrue(state['enabled']);self.assertFalse(live.accounting_pending(state))
        self.assertEqual(state['realized_lamports'],100-126555822)
