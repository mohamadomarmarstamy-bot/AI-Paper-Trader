import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import reconcile_goon_manual_sale as repair


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'trader.db'
        self.adjustment={'position_id':'buy:fixture','mint':repair.MINT,'quantity_removed_raw':repair.QUANTITY,'unreconciled_cost_lamports':repair.COST,'proceeds_lamports':None}
        self.state={'wallet':repair.WALLET,'enabled':False,'positions':{},'realized_lamports':177947201,'balance_adjustments':[self.adjustment],'error':'review'}
        self.proof={'repair_id':repair.REPAIR_ID,'wallet':repair.WALLET,'mint':repair.MINT,'quantity_raw':repair.QUANTITY,'net_proceeds_lamports':repair.EXPECTED_PROCEEDS,'evidence':[{'signature':s} for s in repair.SIGNATURES]}
        with sqlite3.connect(self.path) as conn:
            conn.execute('CREATE TABLE solana_real_run(id INTEGER PRIMARY KEY, revision INTEGER,state_json TEXT,lease_until REAL)')
            conn.execute('INSERT INTO solana_real_run VALUES(1,4,?,0)',(json.dumps(self.state),))
            conn.execute('CREATE TABLE solana_real_orders(status TEXT)');conn.execute('INSERT INTO solana_real_orders VALUES(?)',('expired',))
            conn.execute('CREATE TABLE unrelated(value TEXT)');conn.execute("INSERT INTO unrelated VALUES('preserve')")
    def saved(self):
        with sqlite3.connect(self.path) as conn:return conn.execute('SELECT revision,state_json FROM solana_real_run').fetchone()
    def write(self):
        with sqlite3.connect(self.path) as conn:conn.execute('UPDATE solana_real_run SET state_json=?',(json.dumps(self.state),))
    def chain_fixture(self):
        amounts=[50_000_000,-3_297_678,-45_489_912,99_671_373,-1_204_431];txs={}
        for i,(sig,delta) in enumerate(zip(repair.SIGNATURES,amounts)):
            row={'owner':repair.WALLET,'mint':repair.MINT,'uiTokenAmount':{'amount':str(repair.QUANTITY)}}
            txs[sig]={'slot':464170579 if i<3 else 464170884,'blockTime':1793552926 if i<3 else 1793552991,
                     'transaction':{'signatures':[sig],'message':{'accountKeys':[{'pubkey':repair.WALLET,'signer':i!=3}]}},
                     'meta':{'err':None,'fee':5000,'preBalances':[1_000_000_000],'postBalances':[1_000_000_000+delta],
                             'preTokenBalances':[row] if i==1 else [],'postTokenBalances':[]}}
        history=[{'signature':sig,'slot':tx['slot']} for sig,tx in txs.items()]
        statuses=[{'confirmationStatus':'finalized','err':None} for _ in repair.SIGNATURES]
        def call(method,params):
            if method=='getGenesisHash':return repair.GENESIS
            if method=='getSignatureStatuses':return {'value':statuses}
            if method=='getTransaction':return txs[params[0]]
            if method=='getSignaturesForAddress':return history
            if method=='getTokenAccountsByOwner':return {'value':[]}
            raise AssertionError('Non-read-only or unexpected RPC '+method)
        return call,txs,history,statuses
    def test_chain_proof_accepts_recipient_settlement_without_owner_signature(self):
        call,_,_,_=self.chain_fixture();result=repair.verify_chain(call)
        self.assertEqual(result['net_proceeds_lamports'],99_679_352);self.assertEqual(len(result['evidence']),5)
    def test_chain_proof_rejects_nonfinalized_wrong_proceeds_omissions_and_unsigned_sale(self):
        for case in ('pending','native','extra','omitted','unsigned','quantity','other_token'):
            call,txs,history,statuses=self.chain_fixture();sale=txs[repair.SIGNATURES[1]]
            if case=='pending':statuses[0]['confirmationStatus']='confirmed'
            if case=='native':sale['meta']['postBalances'][0]+=1
            if case=='extra':history.append({'signature':'unreviewed','slot':464170580})
            if case=='omitted':history.pop(0)
            if case=='unsigned':sale['transaction']['message']['accountKeys'][0]['signer']=False
            if case=='quantity':sale['meta']['preTokenBalances'][0]['uiTokenAmount']['amount']='1'
            if case=='other_token':sale['meta']['preTokenBalances'].append({'owner':repair.WALLET,'mint':'other','uiTokenAmount':{'amount':'1'}})
            with self.subTest(case=case),self.assertRaises(ValueError):repair.verify_chain(call)
    def test_preview_changes_nothing(self):
        before=self.saved();result=repair.repair(self.path,self.proof)
        self.assertEqual(result['status'],'preview');self.assertEqual(result['realized_pnl_adjustment_lamports'],-113155034)
        self.assertEqual(self.saved(),before);self.assertEqual(list(self.path.parent.glob('*.sqlite3')),[])
    def test_apply_backs_up_preserves_audit_and_is_idempotent(self):
        before=self.saved();result=repair.repair(self.path,self.proof,True);revision,raw=self.saved();state=json.loads(raw)
        self.assertEqual(revision,5);self.assertFalse(state['enabled']);self.assertEqual(state['realized_lamports'],64792167)
        self.assertEqual(state['balance_adjustments'],[]);self.assertIsNone(state['error']);self.assertEqual(state['accounting_repairs'][0]['original_adjustment'],self.adjustment)
        with sqlite3.connect(result['backup']) as backup:self.assertEqual(backup.execute('SELECT revision,state_json FROM solana_real_run').fetchone(),before)
        with sqlite3.connect(self.path) as conn:self.assertEqual(conn.execute('SELECT value FROM unrelated').fetchone()[0],'preserve')
        self.assertEqual(repair.repair(self.path,self.proof,True)['status'],'already_applied');self.assertEqual(self.saved(),(revision,raw))
    def test_enabled_wrong_wallet_holdings_cost_pending_and_lease_block_writes(self):
        for changes in ({'enabled':True},{'wallet':'different'},{'positions':{'p':{}}},{'balance_adjustments':[]},{'balance_adjustments':[{**self.adjustment,'unreconciled_cost_lamports':1}]}):
            saved=copy.deepcopy(self.state);self.state.update(changes);self.write();before=self.saved()
            with self.assertRaises(ValueError):repair.repair(self.path,self.proof,True)
            self.assertEqual(self.saved(),before);self.state=saved;self.write()
        with sqlite3.connect(self.path) as conn:conn.execute("INSERT INTO solana_real_orders VALUES('unknown')")
        with self.assertRaises(ValueError):repair.repair(self.path,self.proof,True)
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM solana_real_orders WHERE status='unknown'");conn.execute('UPDATE solana_real_run SET lease_until=9999999999')
        with self.assertRaises(ValueError):repair.repair(self.path,self.proof,True)
    def test_rollback_retains_unresolved_entry(self):
        before=self.saved()
        with patch.object(repair.json,'dumps',side_effect=ValueError('synthetic serialization failure')):
            with self.assertRaises(ValueError):repair.repair(self.path,self.proof,True)
        self.assertEqual(self.saved(),before)
    def test_wrong_proceeds_or_quantity_cannot_apply(self):
        for field in ('net_proceeds_lamports','quantity_raw'):
            with self.assertRaises(ValueError):repair.repair(self.path,{**self.proof,field:1},True)


if __name__=='__main__':unittest.main()
