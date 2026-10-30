from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import database
import solana_copy_rpc as rpc
import solana_copy_trader as copy
import solana_copy_setup as setup
from test_solana_copy_trader import Market,WALLET,TOKEN,SOL


def encode58(data):
    from solana_meme_bot import ALPHABET
    value=int.from_bytes(data,'big');text=''
    while value:value,r=divmod(value,58);text=ALPHABET[r]+text
    return text


def tx(at,side='buy',signature='2'*88):
    program=next(iter(rpc.PROGRAMS))
    code,layout=next((code,layout) for code,layout in rpc.PROGRAMS[program]['instructions'].items() if layout['name']==side)
    accounts=[SOL]*17;accounts[layout['user']]=WALLET;accounts[layout['mint']]=TOKEN
    def balance(amount):return {'owner':WALLET,'mint':TOKEN,'uiTokenAmount':{'amount':str(amount),'decimals':6}}
    return {'blockTime':at,'transaction':{'signatures':[signature],'message':{
        'accountKeys':[{'pubkey':WALLET,'signer':True}],
        'instructions':[{'programId':program,'data':encode58(bytes.fromhex(code)+b'\x01'*16),'accounts':accounts}]}},
        'meta':{'err':None,'preTokenBalances':[balance(0 if side=='buy' else 1000000)],
                'postTokenBalances':[balance(1000000 if side=='buy' else 0)],'innerInstructions':[]}}


class RpcTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'test.sqlite3');p.start();self.addCleanup(p.stop)
        self.now=1800000000.
        for p in [patch.object(copy.time,'time',side_effect=lambda:self.now),patch.object(copy,'configured',return_value=False),
                  patch.object(copy,'regular_metrics',return_value={'equity_usd':100000,'net_usd':0})]:
            p.start();self.addCleanup(p.stop)

    def enroll(self):
        copy.configure({'enabled':True},0);state,_=copy.load()
        state['traders']=[{'wallet':WALLET,'selected_at':self.now,'active':True}]
        state['cursors']={WALLET:{'at':self.now,'provider':'birdeye'}}
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE solana_copy_state SET state_json=? WHERE id=1',(json.dumps(state),));conn.commit()
        setup.enable_public_rpc(state['settings_revision'])

    def test_decoder_rejects_transfers_failed_unsigned_other_owner_and_ambiguous_swaps(self):
        valid=tx(self.now);self.assertEqual(len(rpc.parse(valid,WALLET,'2'*88)),1)
        self.assertEqual(rpc.parse(tx(self.now,'sell'),WALLET,'2'*88)[0]['base']['type_swap'],'from')
        for mutation in ['program','failed','unsigned','owner','direction','multiple','signature']:
            row=deepcopy(valid);message=row['transaction']['message']
            if mutation=='program':message['instructions'][0]['programId']=SOL
            if mutation=='failed':row['meta']['err']={'InstructionError':[0,'Custom']}
            if mutation=='unsigned':message['accountKeys'][0]['signer']=False
            if mutation=='owner':row['meta']['postTokenBalances'][0]['owner']=TOKEN
            if mutation=='direction':row['meta']['postTokenBalances'][0]['uiTokenAmount']['amount']='0'
            if mutation=='multiple':message['instructions']*=2
            if mutation=='signature':row['transaction']['signatures']=['3'*88]
            self.assertEqual(rpc.parse(row,WALLET,'2'*88),[],mutation)

    def test_rpc_buy_duplicate_and_source_sell_complete_without_any_api_key(self):
        self.enroll();self.now+=10;rows=[{'signature':'2'*88,'blockTime':self.now-1,'err':None,'confirmationStatus':'confirmed'}]
        current=tx(self.now-1)
        def request(method,params):
            if method=='getSignaturesForAddress':return rows
            self.assertEqual(method,'getTransaction');return current
        with (patch.object(rpc,'request',side_effect=request),patch.object(copy.Birdeye,'meme',side_effect=AssertionError('No metadata key required')),
              patch.object(copy.Birdeye,'discover',side_effect=copy.DataUnavailable('Birdeye HTTP 401'))):
            result=copy.tick(market=Market(),clock=lambda:self.now)
            self.assertEqual(len(result['positions']),1);self.assertEqual(result['health'],'running')
            self.assertIsNone(result['error']);self.assertIn('401',result['discovery_error'])
            cash=result['cash_usd'];self.now+=10
            result=copy.tick(market=Market(),clock=lambda:self.now)
            self.assertEqual(len(result['positions']),1);self.assertEqual(result['cash_usd'],cash)
            self.now+=10;rows[0].update(signature='3'*88,blockTime=self.now-1)
            current=tx(self.now-1,'sell','3'*88)
            result=copy.tick(market=Market(),clock=lambda:self.now)
            self.assertEqual(result['positions'],[]);self.assertEqual(result['closed_count'],1)
            self.assertAlmostEqual(result['cash_usd']-100000,result['realized_pnl_usd'])

    def test_window_is_frozen_buffered_and_null_transaction_retries_without_advancing(self):
        cursor={'provider':'rpc','at':self.now-10}
        rows=[{'signature':str(i)*88,'blockTime':self.now-i,'err':None,'confirmationStatus':'finalized'} for i in range(2,7)]
        with patch.object(rpc,'request',side_effect=lambda method,params:rows if method=='getSignaturesForAddress' else tx(self.now-2,signature=params[0])):
            first,next_cursor=rpc.trades(WALLET,cursor,self.now)
            self.assertEqual(len(first),4);self.assertIn('window',next_cursor)
            last,finished=rpc.trades(WALLET,next_cursor,self.now+10)
            self.assertEqual(len(last),1);self.assertNotIn('window',finished)
            self.assertEqual(finished['at'],self.now)
        with patch.object(rpc,'request',side_effect=[rows,None]):
            with self.assertRaises(copy.DataUnavailable):rpc.trades(WALLET,cursor,self.now)
        self.assertEqual(cursor,{'provider':'rpc','at':self.now-10})

    def test_source_change_is_idempotent_and_does_not_erase_cursor_or_open_lots(self):
        self.enroll();state,_=copy.load();cursor=deepcopy(state['cursors'][WALLET]);self.now+=10
        setup.enable_public_rpc(state['settings_revision'])
        self.assertEqual(copy.load()[0]['cursors'][WALLET],cursor)
        state,rev=copy.load();state['cursors'][WALLET]['provider']='solscan';state['positions']={'lot':{'wallet':WALLET}}
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE solana_copy_state SET state_json=? WHERE id=1',(json.dumps(state),));conn.commit()
        with self.assertRaises(ValueError):setup.enable_public_rpc(state['settings_revision'])
        self.assertEqual(copy.load()[0]['positions'],state['positions'])

    def test_rpc_client_never_accepts_execution_methods(self):
        with patch.object(rpc.requests,'post') as post:
            with self.assertRaises(ValueError):rpc.request('sendTransaction',['anything'])
            post.assert_not_called()


if __name__=='__main__':unittest.main()
