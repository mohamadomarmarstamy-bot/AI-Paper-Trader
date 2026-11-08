from contextlib import closing
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import database
import solana_accounting_repairs as repair
import solana_copy_performance as performance
import solana_wallet_review as review


def fixture():
    buy={'id':'buy:goon','wallet':repair.WALLET,'mint':repair.MINT,'source_wallet':'cap-wallet','status':'confirmed','side':'buy',
         'created_at':1793552000,'result':{'native_delta':-repair.COST,'token_delta':repair.QUANTITY,'fee_lamports':5000}}
    # Exact reviewed net wallet movements from the five-transaction repair.
    deltas=[50_000_000,-3_297_678,-45_489_912,99_671_373,-1_204_431]
    evidence=[{'signature':sig,'slot':464170579 if i<3 else 464170884,'block_time':1793552926 if i<3 else 1793552991,
               'native_delta_lamports':delta,'fee_lamports':5000,'token_deltas':{repair.MINT:-repair.QUANTITY} if i==1 else {}}
              for i,(sig,delta) in enumerate(zip(repair.SIGNATURES,deltas))]
    archive={'repair_id':repair.REPAIR_ID,'wallet':repair.WALLET,'mint':repair.MINT,'quantity_raw':repair.QUANTITY,
             'net_proceeds_lamports':repair.EXPECTED_PROCEEDS,'removed_cost_lamports':repair.COST,
             'realized_pnl_adjustment_lamports':repair.EXPECTED_PROCEEDS-repair.COST,'reviewed_at':1793554000,
             'original_adjustment':{'position_id':buy['id'],'mint':repair.MINT,'quantity_removed_raw':repair.QUANTITY,
                                    'unreconciled_cost_lamports':repair.COST,'proceeds_lamports':None},'evidence':evidence}
    state={'wallet':repair.WALLET,'positions':{},'accounting_repairs':[archive],'realized_lamports':-113155034}
    return buy,archive,state


class ArchivedRepairTests(unittest.TestCase):
    def test_exact_goon_loss_is_attributed_once_without_changing_ledger(self):
        buy,archive,state=fixture();before=deepcopy(state)
        report=performance.report([{'wallet':'cap-wallet','name':'Cap'}],state,[buy])
        self.assertEqual(report['ledger_difference_lamports'],0);self.assertTrue(report['accounting_complete'])
        self.assertEqual(report['reconciliation']['orders_net_lamports'],0)
        self.assertEqual(report['reconciliation']['archived_adjustment_lamports'],-113155034)
        cap=report['wallets'][0]
        self.assertEqual(cap['net_realized_lamports'],-113155034);self.assertEqual(cap['manual_adjustment_count'],1)
        self.assertEqual(cap['completed_positions'],1);self.assertEqual(cap['winning_positions'],0)
        self.assertEqual(cap['confirmed_sales'],0);self.assertEqual(report['order_count'],1)
        self.assertEqual(state,before);self.assertEqual(archive['realized_pnl_adjustment_lamports'],-113155034)

    def test_failed_bot_fee_is_separate_and_manual_receipt_fees_not_subtracted_twice(self):
        buy,_,state=fixture();fee={'id':'failed','wallet':repair.WALLET,'mint':repair.MINT,'position_id':buy['id'],
                                 'source_wallet':'cap-wallet','side':'sell','status':'failed','result':{'fee_lamports':5000}}
        state['realized_lamports']-=5000
        report=performance.report([],state,[buy,fee])
        self.assertEqual(report['wallets'][0]['net_realized_lamports'],-113160034)
        self.assertEqual(report['failed_fees_lamports'],5000);self.assertTrue(report['accounting_complete'])

    def test_missing_malformed_or_tampered_archives_do_not_fill_gap(self):
        buy,archive,state=fixture()
        cases=[]
        for field,value in (('wallet','other'),('mint','other'),('removed_cost_lamports',repair.COST+1),
                            ('net_proceeds_lamports',repair.EXPECTED_PROCEEDS+1),('realized_pnl_adjustment_lamports',0),('reviewed_at',None)):
            changed=deepcopy(archive);changed[field]=value;cases.append(changed)
        changed=deepcopy(archive);changed['evidence'].pop();cases.append(changed)
        changed=deepcopy(archive);changed['evidence'][0]['native_delta_lamports']+=1;cases.append(changed)
        changed=deepcopy(archive);changed['evidence'][1]['token_deltas'][repair.MINT]+=1;cases.append(changed)
        changed=deepcopy(archive);changed['evidence'][1]['token_deltas']['other-token']=1;cases.append(changed)
        changed=deepcopy(archive);changed['evidence'][0]=None;cases.append(changed)
        cases.extend([None,{'repair_id':'unsupported'}])
        for changed in cases:
            with self.subTest(archive=changed):
                state['accounting_repairs']=[changed]
                report=performance.report([],state,[buy]);self.assertFalse(report['accounting_complete'])
                self.assertEqual(report['accounting_adjustments'],[]);self.assertTrue(report['accounting_issues'])
                self.assertEqual(report['ledger_difference_lamports'],-113155034)
        state['accounting_repairs']=[]
        self.assertFalse(performance.report([],state,[buy])['accounting_complete'])

    def test_source_is_resolved_from_exact_entry_and_wrong_entries_are_rejected(self):
        buy,archive,state=fixture()
        for field,value in (('id','other-buy'),('wallet','other'),('mint','other'),('source_wallet',None),('status','failed')):
            with self.subTest(field=field):
                altered={**buy,field:value};result=repair.audit([altered],state)
                self.assertEqual(result['adjustments'],[]);self.assertTrue(result['issues'])
        altered=deepcopy(buy);altered['result']['native_delta']-=1
        self.assertTrue(repair.audit([altered],state)['issues'])

    def test_duplicate_archive_and_overlapping_receipts_never_double_count(self):
        buy,archive,state=fixture();state['accounting_repairs'].append(deepcopy(archive))
        self.assertEqual(repair.audit([buy],state)['adjustments'],[])
        state['accounting_repairs']=[archive]
        overlapping={'id':'other','status':'confirmed','side':'buy','signature':repair.SIGNATURES[0]}
        self.assertTrue(repair.audit([buy,overlapping],state)['issues'])

    def test_existing_full_receipt_sell_represents_adjustment_without_second_allocation(self):
        buy,archive,state=fixture()
        sale={'id':'manual','side':'sell','status':'confirmed','wallet':repair.WALLET,'mint':repair.MINT,'position_id':buy['id'],
              'source_wallet':'cap-wallet','created_at':1793553000,'realized_pnl_lamports':-113155034,
              'result':{'native_delta':repair.EXPECTED_PROCEEDS,'token_delta':-repair.QUANTITY,
                        'receipts':[{'signature':sig} for sig in repair.SIGNATURES]}}
        report=performance.report([],state,[buy,sale])
        self.assertTrue(report['accounting_complete']);self.assertEqual(report['accounting_adjustments'],[])
        self.assertEqual(report['reconciliation']['already_represented_repairs'],1)
        self.assertEqual(report['wallets'][0]['net_realized_lamports'],-113155034)
        buy['result']['native_delta']-=1
        self.assertTrue(repair.audit([buy,sale],state)['issues'])

    def test_unresolved_matching_cost_or_open_holding_keeps_archive_unallocated(self):
        buy,archive,state=fixture()
        for changes in ({'positions':{buy['id']:{'source_wallet':'cap-wallet'}}}, {'balance_adjustments':[archive['original_adjustment']]}):
            self.assertTrue(repair.audit([buy],{**state,**changes})['issues'])

    def test_partial_sale_before_manual_repair_preserves_remaining_cost_rounding(self):
        buy,archive,state=fixture();buy['result']['token_delta']=repair.QUANTITY*2;buy['result']['native_delta']=-repair.COST*2
        trim={'id':'trim','position_id':buy['id'],'side':'sell','status':'confirmed','wallet':repair.WALLET,'mint':repair.MINT,
              'source_wallet':'cap-wallet','created_at':1793552500,'result':{'token_delta':-repair.QUANTITY,'native_delta':repair.COST+100},'realized_pnl_lamports':100}
        state['realized_lamports']+=100
        report=performance.report([],state,[buy,trim]);self.assertTrue(report['accounting_complete'])
        self.assertEqual(report['wallets'][0]['completed_positions'],1)
        self.assertEqual(report['wallets'][0]['net_realized_lamports'],-113154934)

    def test_older_wallet_review_also_counts_archived_adjustment_exactly(self):
        buy,_,state=fixture();approved=repair.audit([buy],state)['adjustments']
        result=review.real_results([], [buy],approved)[0]
        self.assertEqual(result['net_realized_lamports'],-113155034)
        self.assertEqual(result['net_realized_sol'],-.113155034)


class StoredAuditTests(unittest.TestCase):
    def test_report_and_wallet_review_read_archived_proof_without_database_writes(self):
        import solana_real_worker as live
        import solana_copy_trader as paper
        with tempfile.TemporaryDirectory() as folder,patch.object(database,'DATABASE_PATH',Path(folder)/'audit.db'):
            buy,archive,state=fixture();run=live.initial();run.update(state)
            with closing(database.get_connection()) as conn:
                live.initialize(conn);paper.initialize(conn)
                conn.execute('UPDATE solana_real_run SET state_json=? WHERE id=1',(json.dumps(run),))
                conn.execute('INSERT INTO solana_real_orders VALUES(?,?,?,?)',(buy['id'],buy['created_at'],buy['status'],json.dumps(buy)));conn.commit()
                before=conn.execute('SELECT revision,state_json FROM solana_real_run').fetchone()
            for read in (performance.public,review.public):
                result=read()
                self.assertEqual(result.get('ledger_difference_lamports',result.get('ledger_minus_source_pnl_sol')),0)
            with closing(database.get_connection()) as conn:
                after=conn.execute('SELECT revision,state_json FROM solana_real_run').fetchone()
                self.assertEqual(tuple(before),tuple(after));self.assertEqual(conn.execute('SELECT count(*) FROM solana_real_orders').fetchone()[0],1)
