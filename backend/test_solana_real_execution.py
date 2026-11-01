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
        provider=ex.Provider();provider.network=lambda:None;provider.mint=lambda mint:{'decimals':6,'program':ex.TOKEN,'account_size':165}
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

    def test_network_requires_the_full_mainnet_genesis_hash(self):
        provider=ex.Provider()
        with patch.object(provider,'rpc',return_value=ex.MAINNET_GENESIS):provider.network()
        for value in (ex.MAINNET_GENESIS[:32],ex.MAINNET_GENESIS[:-1]+'e',str(Pubkey.new_unique())):
            with patch.object(provider,'rpc',return_value=value),self.assertRaises(ex.Unavailable):provider.network()

    def test_api_denial_is_reported_without_response_or_key_data(self):
        from types import SimpleNamespace
        with patch.object(ex.requests,'get',return_value=SimpleNamespace(status_code=403)):
            with self.assertRaisesRegex(ex.Unavailable,'key or plan') as error:ex.Provider().build(self.owner,ex.SOL,self.mint,100000000,self.policy)
        self.assertNotIn('synthetic-api-key',str(error.exception))

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


def mint_bytes(program=ex.TOKEN2022,extensions=True):
    raw=bytearray(82);raw[36:44]=struct.pack('<Q',1_000_000_000);raw[44]=6;raw[45]=1
    if program==ex.TOKEN2022 and extensions:
        # Inactive metadata pointer plus a minimal Borsh TokenMetadata record.
        metadata=bytes(64)+struct.pack('<IIII',0,0,0,0)
        raw.extend(bytes(83)+bytes([1])+struct.pack('<HH',18,64)+bytes(64)+struct.pack('<HH',19,len(metadata))+metadata)
    return bytes(raw)


def token2022(owner,mint,amount):
    raw=token(owner,mint,amount).data+bytes([2])+struct.pack('<HH',7,0)
    return Account(3_000_000,raw,ex.pub(ex.TOKEN2022))


class Token2022MintTests(unittest.TestCase):
    def mint(self,raw,program=ex.TOKEN2022):
        provider=ex.Provider()
        with patch.object(provider,'rpc',return_value={'value':encoded(Account(3_000_000,raw,ex.pub(program)))}):
            return provider.mint(str(Pubkey.new_unique()))

    def test_metadata_only_and_extensionless_mints_use_program_specific_rent_size(self):
        for extended in (False,True):
            self.assertEqual(self.mint(mint_bytes(extensions=extended)),{'decimals':6,'program':ex.TOKEN2022,'account_size':170})
        self.assertEqual(self.mint(mint_bytes(ex.TOKEN),ex.TOKEN),{'decimals':6,'program':ex.TOKEN,'account_size':165})

    def test_active_mint_and_freeze_authorities_have_distinct_errors(self):
        for offset,name in ((0,'mint authority'),(46,'freeze authority')):
            raw=bytearray(mint_bytes());raw[offset:offset+4]=struct.pack('<I',1)
            with self.subTest(authority=name),self.assertRaisesRegex(ex.Unavailable,name):self.mint(bytes(raw))

    def test_unknown_economic_or_malformed_extensions_fail_closed(self):
        prefix=mint_bytes(extensions=False)+bytes(83)+bytes([1])
        for kind in (1,3,4,6,9,10,12,14,20,24,25,26,27,65500):
            with self.subTest(kind=kind),self.assertRaisesRegex(ex.Unavailable,'Unsupported Token-2022 extension'):
                self.mint(prefix+struct.pack('<HH',kind,0))
        for tail in (b'\x12',struct.pack('<HH',18,65)+bytes(65),struct.pack('<HH',18,64)+bytes(63),struct.pack('<HH',7,0),struct.pack('<HH',19,1)+b'x',struct.pack('<HH',18,64)+bytes(64)+struct.pack('<HH',18,64)+bytes(64)):
            with self.subTest(tail=tail[:4]),self.assertRaises(ex.Unavailable):self.mint(prefix+tail)

    def test_token_accounts_require_correct_program_and_only_immutable_owner(self):
        owner=Pubkey.new_unique();mint=Pubkey.new_unique();account=token2022(owner,mint,123)
        self.assertEqual(ex.token_amount(encoded(account),str(owner),str(mint),ex.TOKEN2022),123)
        with self.assertRaises(ex.Unavailable):ex.token_amount(encoded(account),str(owner),str(mint))
        for offset,value in ((108,b'\x02'),(72,struct.pack('<I',1)),(129,struct.pack('<I',1))):
            raw=bytearray(account.data);raw[offset:offset+len(value)]=value
            with self.assertRaises(ex.Unavailable):ex.token_amount(encoded(Account(3_000_000,bytes(raw),ex.pub(ex.TOKEN2022))),str(owner),str(mint),ex.TOKEN2022)
        raw=account.data+struct.pack('<HH',2,8)+bytes(8)
        with self.assertRaises(ex.Unavailable):ex.token_amount(encoded(Account(3_000_000,raw,ex.pub(ex.TOKEN2022))),str(owner),str(mint),ex.TOKEN2022)


class Token2022AtomicGuardTests(AtomicGuardTests):
    def setUp(self):
        super().setUp();self.account=ex.pub(ex.ata(str(self.owner.pubkey()),str(self.mint),ex.TOKEN2022))
        self.svm.set_account(self.mint,Account(3_000_000,mint_bytes(),ex.pub(ex.TOKEN2022)))
        self.svm.set_account(self.account,token2022(self.owner.pubkey(),self.mint,100))

    def guards(self,amount=100):return ex.guards(str(self.owner.pubkey()),str(self.mint),6,amount,900_000_000,ex.TOKEN2022)

    def test_guard_accepts_exact_token_and_sol_floors_without_moving_assets(self):
        self.assertNotEqual(str(self.account),ex.ata(str(self.owner.pubkey()),str(self.mint)))
        result=self.send(self.guards());self.assertEqual(type(result).__name__,'TransactionMetadata',str(result))
        self.assertEqual(int.from_bytes(self.svm.get_account(self.account).data[64:72],'little'),100)

    def test_minimum_output_or_native_floor_rolls_back_prior_transfers(self):
        recipient=Pubkey.new_unique();spend=transfer(TransferParams(from_pubkey=self.owner.pubkey(),to_pubkey=recipient,lamports=200_000_000))
        for amount in (100,101):
            result=self.send([spend,*self.guards(amount)])
            self.assertEqual(type(result).__name__,'FailedTransactionMetadata');self.assertIsNone(self.svm.get_account(recipient))

    def test_token2022_ata_creation_initializes_immutable_owner_with_correct_rent(self):
        mint=Pubkey.new_unique();self.svm.set_account(mint,Account(3_000_000,mint_bytes(),ex.pub(ex.TOKEN2022)))
        account=ex.pub(ex.ata(str(self.owner.pubkey()),str(mint),ex.TOKEN2022))
        create=Instruction(ex.pub(ex.ASSOCIATED),bytes([1]),[
            AccountMeta(self.owner.pubkey(),True,True),AccountMeta(account,False,True),AccountMeta(self.owner.pubkey(),False,False),
            AccountMeta(mint,False,False),AccountMeta(ex.pub(ex.SYSTEM),False,False),AccountMeta(ex.pub(ex.TOKEN2022),False,False)])
        result=self.send([create,*ex.guards(str(self.owner.pubkey()),str(mint),6,0,900_000_000,ex.TOKEN2022)])
        self.assertEqual(type(result).__name__,'TransactionMetadata',str(result))
        created=self.svm.get_account(account);self.assertEqual(len(created.data),170)
        self.assertEqual(ex.token_amount(encoded(created),str(self.owner.pubkey()),str(mint),ex.TOKEN2022),0)
        self.assertEqual(created.data[165:],bytes([2])+struct.pack('<HH',7,0))

    def test_account_delegate_is_revoked_and_close_authority_takeover_reverts(self):
        stranger=Pubkey.new_unique()
        approve=Instruction(ex.pub(ex.TOKEN2022),bytes([4])+struct.pack('<Q',50),[AccountMeta(self.account,False,True),AccountMeta(stranger,False,False),AccountMeta(self.owner.pubkey(),True,False)])
        result=self.send([approve,*self.guards()]);self.assertEqual(type(result).__name__,'TransactionMetadata',str(result))
        self.assertEqual(self.svm.get_account(self.account).data[72:76],bytes(4))
        takeover=Instruction(ex.pub(ex.TOKEN2022),bytes([6,3,1])+bytes(stranger),[AccountMeta(self.account,False,True),AccountMeta(self.owner.pubkey(),True,False)])
        result=self.send([takeover,*self.guards()]);self.assertEqual(type(result).__name__,'FailedTransactionMetadata')


class Token2022PreparationTests(ExecutionProviderTests):
    def quote(self):
        q=super().quote();old=ex.ata(self.owner,self.mint);new=ex.ata(self.owner,self.mint,ex.TOKEN2022)
        for a in q['swapInstruction']['accounts']:
            if a['pubkey']==old:a['pubkey']=new
        return q

    def provider(self):
        p=super().provider();p.mint=lambda mint:{'decimals':6,'program':ex.TOKEN2022,'account_size':170}
        return p

    def test_locally_wrapped_sol_roundtrip_runs_on_solana_runtime(self):
        prepared=self.provider().prepare(self.owner,'buy',self.mint,100000000,self.policy,lambda:None,quote=self.quote())
        message=VersionedTransaction.from_bytes(base64.b64decode(prepared['wire'])).message
        svm=LiteSVM();svm.airdrop(self.key.pubkey(),1_000_000_000)
        svm.set_account(ex.pub(self.mint),Account(3_000_000,mint_bytes(),ex.pub(ex.TOKEN2022)))
        raw=bytearray(82);raw[44]=9;raw[45]=1
        svm.set_account(ex.pub(ex.SOL),Account(2_100_000,bytes(raw),ex.pub(ex.TOKEN)))
        target=ex.pub(ex.ata(self.owner,self.mint,ex.TOKEN2022));svm.set_account(target,token2022(self.key.pubkey(),ex.pub(self.mint),100))
        keys=message.account_keys;header=message.header;ixs=[]
        for ix in message.instructions:
            program=keys[ix.program_id_index]
            if str(program)==ex.JUPITER:continue
            accounts=[AccountMeta(keys[i],i<header.num_required_signatures,i<header.num_required_signatures-header.num_readonly_signed_accounts if i<header.num_required_signatures else i<len(keys)-header.num_readonly_unsigned_accounts) for i in ix.accounts]
            ixs.append(Instruction(program,bytes(ix.data),accounts))
        result=svm.send_transaction(Transaction.new_signed_with_payer(ixs,self.key.pubkey(),[self.key],svm.latest_blockhash()))
        self.assertEqual(type(result).__name__,'TransactionMetadata',str(result))
        self.assertIsNone(svm.get_account(ex.pub(ex.ata(self.owner,ex.SOL))))
        self.assertEqual(int.from_bytes(svm.get_account(target).data[64:72],'little'),100)

    def test_reconciliation_requires_finalized_matching_bytes_and_actual_deltas(self):
        provider=self.provider();prepared=provider.prepare(self.owner,'buy',self.mint,100000000,self.policy,lambda:None,quote=self.quote())
        order={'wallet':self.owner,'mint':self.mint,'side':'buy','prepared':prepared}
        entry={'owner':self.owner,'mint':self.mint,'programId':ex.TOKEN2022,'uiTokenAmount':{'amount':'100'}}
        meta={'err':None,'fee':105000,'preBalances':[1000000000],'postBalances':[899895000],'preTokenBalances':[],'postTokenBalances':[entry]}
        def rpc(method,params):
            if method=='getSignatureStatuses':return {'value':[{'confirmationStatus':'finalized'}]}
            if method=='getTransaction':return {'transaction':[prepared['wire'],'base64'],'meta':meta,'slot':123,'blockTime':1000}
            raise AssertionError(method)
        provider.rpc=rpc;self.assertEqual(provider.reconcile(order)['token_delta'],100)
        entry['programId']=ex.TOKEN
        with self.assertRaisesRegex(ex.Unavailable,'program'):provider.reconcile(order)

    def test_associated_creation_requires_the_actual_mint_program_and_canonical_address(self):
        q=self.quote();target=ex.ata(self.owner,self.mint,ex.TOKEN2022)
        keys=[self.owner,target,self.owner,self.mint,ex.SYSTEM,ex.TOKEN2022]
        row={'programId':ex.ASSOCIATED,'data':base64.b64encode(bytes([1])).decode(),
             'accounts':[{'pubkey':k,'isSigner':i==0,'isWritable':i in (0,1)} for i,k in enumerate(keys)]}
        q['setupInstructions']=[row]
        self.assertEqual(self.provider().prepare(self.owner,'buy',self.mint,100000000,self.policy,lambda:None,quote=q)['token_program'],ex.TOKEN2022)
        for index,value in ((1,ex.ata(self.owner,self.mint)),(5,ex.TOKEN)):
            old=row['accounts'][index]['pubkey'];row['accounts'][index]['pubkey']=value
            with self.assertRaisesRegex(ex.Unavailable,'associated'):self.provider().prepare(self.owner,'buy',self.mint,100000000,self.policy,lambda:self.fail('Must not sign'),quote=q)
            row['accounts'][index]['pubkey']=old

    def test_sell_uses_token2022_balance_and_guard_and_actual_account_rent(self):
        provider=self.provider();original=provider.accounts
        def accounts(keys):
            rows=original(keys)
            for i,k in enumerate(keys):
                if k==ex.ata(self.owner,self.mint,ex.TOKEN2022):rows[i]=encoded(token2022(self.key.pubkey(),ex.pub(self.mint),1000))
            return rows
        provider.accounts=accounts;q=self.quote();q.update(inputMint=self.mint,outputMint=ex.SOL,inAmount='500',otherAmountThreshold='10000000')
        result=provider.prepare(self.owner,'sell',self.mint,500,self.policy,lambda:None,quote=q)
        self.assertEqual(result['token_program'],ex.TOKEN2022);self.assertEqual(result['min_tokens_after'],500)
        calls=[];provider.rpc=lambda method,params:calls.append(params[0]) or params[0]*100
        self.assertEqual(provider.entry_rent(self.mint),33500);self.assertEqual(calls,[170,165])


if __name__=='__main__':unittest.main()
