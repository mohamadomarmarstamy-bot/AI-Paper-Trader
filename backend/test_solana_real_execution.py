import base64
import os
import struct
import unittest
from unittest.mock import patch
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.account import Account
from solders.litesvm import LiteSVM
from solders.transaction import Transaction,VersionedTransaction
from solders.system_program import transfer,TransferParams
from solders.instruction import Instruction,AccountMeta
import solana_real_execution as ex


def encoded(account):return {'owner':str(account.owner),'lamports':account.lamports,'data':[base64.b64encode(account.data).decode(),'base64']}


def token(owner,mint,amount):
    raw=bytearray(165);raw[:32]=bytes(mint);raw[32:64]=bytes(owner);raw[64:72]=struct.pack('<Q',amount);raw[108]=1
    return Account(2_100_000,bytes(raw),ex.pub(ex.TOKEN))


class AtomicGuardTests(unittest.TestCase):
    def setUp(self):
        self.svm=LiteSVM();self.owner=Keypair();self.mint=Pubkey.new_unique();self.account=ex.pub(ex.ata(str(self.owner.pubkey()),str(self.mint)))
        self.svm.airdrop(self.owner.pubkey(),1_000_000_000)
        raw=bytearray(82);raw[36:44]=struct.pack('<Q',1_000_000);raw[44]=6;raw[45]=1
        self.svm.set_account(self.mint,Account(2_100_000,bytes(raw),ex.pub(ex.TOKEN)))
        self.svm.set_account(self.account,token(self.owner.pubkey(),self.mint,100))

    def send(self,ixs):
        tx=Transaction.new_signed_with_payer(ixs,self.owner.pubkey(),[self.owner],self.svm.latest_blockhash())
        return self.svm.send_transaction(tx)

    def test_guard_accepts_exact_token_and_sol_floors_without_moving_assets(self):
        result=self.send(ex.guards(str(self.owner.pubkey()),str(self.mint),6,100,900_000_000))
        self.assertEqual(type(result).__name__,'TransactionMetadata')
        self.assertEqual(int.from_bytes(self.svm.get_account(self.account).data[64:72],'little'),100)

    def test_minimum_output_or_native_floor_rolls_back_prior_transfers(self):
        recipient=Pubkey.new_unique()
        spend=transfer(TransferParams(from_pubkey=self.owner.pubkey(),to_pubkey=recipient,lamports=200_000_000))
        result=self.send([spend,*ex.guards(str(self.owner.pubkey()),str(self.mint),6,101,0)])
        self.assertEqual(type(result).__name__,'FailedTransactionMetadata');self.assertIsNone(self.svm.get_account(recipient))
        result=self.send([spend,*ex.guards(str(self.owner.pubkey()),str(self.mint),6,100,900_000_000)])
        self.assertEqual(type(result).__name__,'FailedTransactionMetadata');self.assertIsNone(self.svm.get_account(recipient))

    def test_account_delegate_is_revoked_and_close_authority_takeover_reverts(self):
        stranger=Pubkey.new_unique();authority=[AccountMeta(self.account,False,True),AccountMeta(stranger,False,False),AccountMeta(self.owner.pubkey(),True,False)]
        approve=Instruction(ex.pub(ex.TOKEN),bytes([4])+struct.pack('<Q',50),authority)
        result=self.send([approve,*ex.guards(str(self.owner.pubkey()),str(self.mint),6,100,0)])
        self.assertEqual(type(result).__name__,'TransactionMetadata');self.assertEqual(self.svm.get_account(self.account).data[72:76],b'\0'*4)
        takeover=Instruction(ex.pub(ex.TOKEN),bytes([6,3,1])+bytes(stranger),[AccountMeta(self.account,False,True),AccountMeta(self.owner.pubkey(),True,False)])
        result=self.send([takeover,*ex.guards(str(self.owner.pubkey()),str(self.mint),6,100,0)])
        self.assertEqual(type(result).__name__,'FailedTransactionMetadata')


class ExecutionProviderTests(unittest.TestCase):
    def setUp(self):
        self.key=Keypair();self.owner=str(self.key.pubkey());self.mint=str(Pubkey.new_unique());self.policy={'slippage_bps':100,'max_price_impact_percent':5,'max_priority_fee_lamports':100000,'reserve_lamports':10000000}
        p=patch.dict(os.environ,{'REAL_COPY_SIGNER_SECRET':str(self.key),'JUPITER_API_KEY':'synthetic-api-key','REAL_COPY_EXECUTION_ALLOWED':'true'},clear=True);p.start();self.addCleanup(p.stop)

    def quote(self):
        import time
        def row(program,data,accounts):return {'programId':program,'data':base64.b64encode(data).decode(),'accounts':[{'pubkey':key,'isSigner':key==self.owner,'isWritable':True} for key in accounts]}
        sol_ata=ex.ata(self.owner,ex.SOL);out=ex.ata(self.owner,self.mint)
        return {'inputMint':ex.SOL,'outputMint':self.mint,'inAmount':'100000000','outAmount':'100','otherAmountThreshold':'99','swapMode':'ExactIn','slippageBps':100,'priceImpactPct':'0',
            'setupInstructions':[],
            'swapInstruction':row(ex.JUPITER,next(iter(ex.ROUTES))+b'opaque route data',[self.owner,out,sol_ata]),
            'cleanupInstruction':None,'_observed_at':time.time()}

    def provider(self):
        provider=ex.Provider();provider.network=lambda:None;provider.mint=lambda mint:6
        def accounts(keys):
            return [encoded(Account(1_000_000_000,b'',ex.pub(ex.SYSTEM))) if key==self.owner else None for key in keys]
        provider.accounts=accounts
        def rpc(method,params):
            if method=='getMinimumBalanceForRentExemption':return 2_100_000
            if method=='getLatestBlockhash':return {'value':{'blockhash':str(Pubkey.new_unique()),'lastValidBlockHeight':1000}}
            if method=='getFeeForMessage':return {'value':105000}
            if method=='simulateTransaction':
                tx=VersionedTransaction.from_bytes(base64.b64decode(params[0]));self.assertEqual(len(tx.signatures),1)
                self.assertEqual(params[1]['sigVerify'],False);return {'value':{'err':None}}
            raise AssertionError(method)
        provider.rpc=rpc
        return provider

    def test_local_compilation_adds_guards_and_signs_only_after_authorization(self):
        provider=self.provider();calls=[]
        result=provider.prepare(self.owner,'buy',self.mint,100000000,self.policy,lambda:calls.append('authorized'),quote=self.quote())
        tx=VersionedTransaction.from_bytes(base64.b64decode(result['wire']));tx.verify_and_hash_message()
        self.assertEqual(calls,['authorized']);self.assertEqual(str(tx.signatures[0]),result['signature'])
        self.assertEqual(result['min_tokens_after'],99);self.assertGreaterEqual(result['min_native_after'],10000000)
        self.assertEqual([bytes(ix.data)[0] for ix in tx.message.instructions[-4:]],[5,6,12,2])
        def denied():raise ex.Unavailable('Paused before signing')
        with self.assertRaisesRegex(ex.Unavailable,'Paused before signing'):provider.prepare(self.owner,'buy',self.mint,100000000,self.policy,denied,quote=self.quote())

    def test_unexpected_native_transfer_token_approval_or_route_cannot_be_signed(self):
        for mutation in ('recipient','approval','program'):
            q=self.quote()
            if mutation in ('recipient','approval'):
                q['setupInstructions']=[{'programId':ex.SYSTEM if mutation=='recipient' else ex.TOKEN,'data':base64.b64encode(struct.pack('<IQ',2,100000000) if mutation=='recipient' else bytes([4])).decode(),
                    'accounts':[{'pubkey':self.owner,'isSigner':True,'isWritable':True},{'pubkey':str(Pubkey.new_unique()),'isSigner':False,'isWritable':True}]}]
            if mutation=='program':q['swapInstruction']['programId']=ex.SYSTEM
            with self.subTest(mutation=mutation),self.assertRaises(ex.Unavailable):self.provider().prepare(self.owner,'buy',self.mint,100000000,self.policy,lambda:self.fail('Must not authorize'),quote=q)

    def test_locally_wrapped_sol_roundtrip_runs_on_solana_runtime(self):
        prepared=self.provider().prepare(self.owner,'buy',self.mint,100000000,self.policy,lambda:None,quote=self.quote())
        message=VersionedTransaction.from_bytes(base64.b64decode(prepared['wire'])).message
        svm=LiteSVM();svm.airdrop(self.key.pubkey(),1_000_000_000)
        for address,decimals in ((self.mint,6),(ex.SOL,9)):
            raw=bytearray(82);raw[36:44]=struct.pack('<Q',1_000_000);raw[44]=decimals;raw[45]=1
            svm.set_account(ex.pub(address),Account(2_100_000,bytes(raw),ex.pub(ex.TOKEN)))
        target=ex.pub(ex.ata(self.owner,self.mint));svm.set_account(target,token(self.key.pubkey(),ex.pub(self.mint),100))
        keys=message.account_keys;header=message.header;ixs=[]
        for ix in message.instructions:
            program=keys[ix.program_id_index]
            # Only omit the mocked AMM swap. Wrapping, cleanup and all four
            # local guards execute against the actual built-in programs.
            if str(program)==ex.JUPITER:continue
            accounts=[AccountMeta(keys[i],i<header.num_required_signatures,
                i<header.num_required_signatures-header.num_readonly_signed_accounts if i<header.num_required_signatures else i<len(keys)-header.num_readonly_unsigned_accounts) for i in ix.accounts]
            ixs.append(Instruction(program,bytes(ix.data),accounts))
        tx=Transaction.new_signed_with_payer(ixs,self.key.pubkey(),[self.key],svm.latest_blockhash())
        result=svm.send_transaction(tx)
        self.assertEqual(type(result).__name__,'TransactionMetadata')
        self.assertIsNone(svm.get_account(ex.pub(ex.ata(self.owner,ex.SOL))))
        self.assertEqual(int.from_bytes(svm.get_account(target).data[64:72],'little'),100)
        self.assertGreater(svm.get_balance(self.key.pubkey()),999_000_000)

    def test_signer_status_does_not_return_key_and_wrong_wallet_never_matches(self):
        flags=ex.configuration(self.owner);self.assertTrue(flags['signer_matches']);self.assertTrue(flags['release_allowed'])
        self.assertNotIn(str(self.key),str(flags));self.assertFalse(ex.configuration(str(Pubkey.new_unique()))['signer_matches'])
        with patch.dict(os.environ,{'REAL_COPY_SIGNER_SECRET':'invalid synthetic secret'}):
            self.assertFalse(ex.configuration(self.owner)['signer_configured'])
            with self.assertRaisesRegex(ex.Unavailable,'missing or invalid'):ex.signer()

    def test_release_switch_independently_blocks_preparation_and_broadcast(self):
        with patch.dict(os.environ,{'REAL_COPY_EXECUTION_ALLOWED':'false'}),patch.object(ex.Provider,'rpc',side_effect=AssertionError('No network')):
            with self.assertRaisesRegex(ex.Unavailable,'switch is off'):self.provider().prepare(self.owner,'buy',self.mint,100000000,self.policy,lambda:None,quote=self.quote())
            with self.assertRaisesRegex(ex.Unavailable,'switch is off'):ex.Provider().send({'wire':'not sent'})

    def test_reconciliation_requires_finalized_matching_bytes_and_actual_deltas(self):
        provider=self.provider();prepared=provider.prepare(self.owner,'buy',self.mint,100000000,self.policy,lambda:None,quote=self.quote())
        order={'wallet':self.owner,'mint':self.mint,'side':'buy','prepared':prepared}
        status={'confirmationStatus':'confirmed'}
        meta={'err':None,'fee':105000,'preBalances':[1000000000],'postBalances':[899895000],
              'preTokenBalances':[],'postTokenBalances':[{'owner':self.owner,'mint':self.mint,'uiTokenAmount':{'amount':'100'}}]}
        def rpc(method,params):
            if method=='getSignatureStatuses':return {'value':[status]}
            if method=='getTransaction':return {'transaction':[prepared['wire'],'base64'],'meta':meta,'slot':123,'blockTime':1000}
            raise AssertionError(method)
        provider.rpc=rpc;self.assertEqual(provider.reconcile(order),{'state':'pending'})
        status['confirmationStatus']='finalized';receipt=provider.reconcile(order)
        self.assertEqual(receipt['state'],'confirmed');self.assertEqual(receipt['token_delta'],100);self.assertEqual(receipt['native_delta'],-100105000)
        meta['err']={'InstructionError':[1,'Custom']};self.assertEqual(provider.reconcile(order),{'state':'failed','fee_lamports':105000})


if __name__=='__main__':unittest.main()
