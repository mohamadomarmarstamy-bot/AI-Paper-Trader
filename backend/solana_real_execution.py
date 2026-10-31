"""Bounded Jupiter V2 swaps with locally constructed atomic balance guards.

No network call or signing occurs on import. The execution worker must supply an
authorization callback immediately before signing; the release switch defaults off.
"""
import base64
from decimal import Decimal
import hashlib
import json
import os
import struct
import time
from urllib.parse import urlsplit
import requests
from solana_real_budget import SOL

TOKEN='TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN2022='TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
ASSOCIATED='ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL'
SYSTEM='11111111111111111111111111111111'
JUPITER='JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4'
LOOKUP='AddressLookupTab1e1111111111111111111111111'
ROUTES={hashlib.sha256(('global:'+n).encode()).digest()[:8] for n in ('route','route_v2','shared_accounts_route','shared_accounts_route_v2')}


class Unavailable(Exception):pass


def release_allowed():return os.getenv('REAL_COPY_EXECUTION_ALLOWED','').lower()=='true'


def signer():
    """Never expose the value or exception text in responses, logs or reprs."""
    from solders.keypair import Keypair
    value=os.getenv('REAL_COPY_SIGNER_SECRET','')
    try:
        if value.startswith('['):
            data=json.loads(value)
            if not isinstance(data,list) or len(data)!=64 or any(type(n) is not int or not 0<=n<=255 for n in data):raise ValueError()
            return Keypair.from_bytes(bytes(data))
        return Keypair.from_base58_string(value)
    except Exception:raise Unavailable('Trading signer is missing or invalid.') from None


def configuration(address):
    key_present=bool(os.getenv('REAL_COPY_SIGNER_SECRET'));key_address=None
    if key_present:
        try:key_address=str(signer().pubkey())
        except Exception:pass
    return {'release_allowed':release_allowed(),'signer_configured':bool(key_address),
            'signer_matches':bool(address and key_address==address),'signer_address':key_address,
            'jupiter_configured':bool(os.getenv('JUPITER_API_KEY')),
            'network':'Solana mainnet','executor_implemented':True}


def pub(value):
    from solders.pubkey import Pubkey
    return Pubkey.from_string(value)


def ata(owner,mint):
    from solders.token.associated import get_associated_token_address
    return str(get_associated_token_address(pub(owner),pub(mint)))


def uint(value):
    if isinstance(value,str) and value.isascii() and value.isdigit() and len(value)<=20:value=int(value)
    if type(value) is not int or not 0<=value<2**64:raise Unavailable('Invalid integer in execution data.')
    return value


def raw_account(account):
    if not account:return None
    value=account['data']
    if not isinstance(value,list) or len(value)!=2 or value[1]!='base64':raise Unavailable('Unsupported account encoding.')
    return base64.b64decode(value[0],validate=True)


def token_amount(account,owner,mint):
    if account is None:return 0
    raw=raw_account(account)
    if account.get('owner')!=TOKEN or len(raw)!=165 or raw[:32]!=bytes(pub(mint)) or raw[32:64]!=bytes(pub(owner)) or raw[108]!=1:
        raise Unavailable('Only initialized legacy SPL accounts owned by the trading wallet are supported.')
    if raw[72:76]!=b'\0'*4 or raw[129:133]!=b'\0'*4:raise Unavailable('Token account has an existing delegate or close authority.')
    return int.from_bytes(raw[64:72],'little')


def guards(owner,mint,decimals,min_tokens,min_sol):
    """SPL self-transfer validates balance/mint/authority before its no-op branch.

    Revoke + clear close authority must succeed as owner first. System transfer
    to self similarly requires enough native lamports. All run atomically after
    the swap; an unmet floor rolls back every swap instruction.
    """
    from solders.instruction import AccountMeta,Instruction
    from solders.system_program import transfer,TransferParams
    account=pub(ata(owner,mint));owner=pub(owner)
    auth=[AccountMeta(account,False,True),AccountMeta(owner,True,False)]
    return [Instruction(pub(TOKEN),bytes([5]),auth),
            Instruction(pub(TOKEN),bytes([6,3,0]),auth),
            Instruction(pub(TOKEN),bytes([12])+struct.pack('<Q',uint(min_tokens))+bytes([decimals]),
                [AccountMeta(account,False,True),AccountMeta(pub(mint),False,False),AccountMeta(account,False,True),AccountMeta(owner,True,False)]),
            transfer(TransferParams(from_pubkey=owner,to_pubkey=owner,lamports=uint(min_sol)))]


class Provider:
    def rpc(self,method,params):
        if method not in ('getGenesisHash','getBalance','getAccountInfo','getMultipleAccounts','getLatestBlockhash','getMinimumBalanceForRentExemption','getFeeForMessage','simulateTransaction','sendTransaction','getSignatureStatuses','getTransaction','getBlockHeight'):
            raise Unavailable('Unsupported execution RPC method.')
        url=os.getenv('SOLANA_REAL_RPC_URL','https://api.mainnet-beta.solana.com')
        parsed=urlsplit(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:raise Unavailable('An HTTPS Solana RPC URL is required.')
        try:
            response=requests.post(url,json={'jsonrpc':'2.0','id':1,'method':method,'params':params},timeout=8,allow_redirects=False)
            if response.status_code!=200 or len(response.content)>4_000_000:raise ValueError()
            data=response.json()
            if not isinstance(data,dict) or data.get('error') or 'result' not in data:raise ValueError()
            return data['result']
        except Exception:raise Unavailable('Solana execution RPC unavailable; transaction state must be reconciled.') from None

    def network(self):
        value=self.rpc('getGenesisHash',[])
        if not isinstance(value,str) or len(bytes(pub(value)))!=32 or not value.startswith('5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp'):
            raise Unavailable('The configured RPC is not Solana mainnet.')

    def balance(self,owner):return uint(self.rpc('getBalance',[owner,{'commitment':'confirmed'}])['value'])

    def accounts(self,addresses):
        if not 1<=len(addresses)<=100:raise Unavailable('Bounded account read required.')
        data=self.rpc('getMultipleAccounts',[addresses,{'encoding':'base64','commitment':'confirmed'}])['value']
        if not isinstance(data,list) or len(data)!=len(addresses):raise Unavailable('Incomplete account read.')
        return data

    def mint(self,address):
        info=self.rpc('getAccountInfo',[address,{'encoding':'jsonParsed','commitment':'confirmed'}])['value']
        try:
            data=info['data']['parsed'];m=data['info'];decimals=m['decimals']
            if info['owner']!=TOKEN or data['type']!='mint' or m['isInitialized'] is not True or type(decimals) is not int or not 0<=decimals<=18:
                raise ValueError()
            if m['mintAuthority'] is not None or m['freezeAuthority'] is not None:raise ValueError()
            return decimals
        except Exception:raise Unavailable('Unsupported token mint, or active mint/freeze authority.') from None

    def build(self,owner,input_mint,output_mint,amount,policy):
        api_key=os.getenv('JUPITER_API_KEY','')
        if not api_key:raise Unavailable('Jupiter API key is not configured.')
        try:
            start=time.monotonic()
            response=requests.get('https://api.jup.ag/swap/v2/build',params={
                'inputMint':input_mint,'outputMint':output_mint,'amount':str(uint(amount)),'taker':owner,
                'slippageBps':policy['slippage_bps'],'maxAccounts':48,'wrapAndUnwrapSol':'false',
                'platformFeeBps':0,'transactionVersion':'0'},headers={'x-api-key':api_key},timeout=8,allow_redirects=False)
            if response.status_code!=200 or len(response.content)>500_000 or time.monotonic()-start>12:raise ValueError()
            data=response.json()
            if data['inputMint']!=input_mint or data['outputMint']!=output_mint or uint(data['inAmount'])!=amount or data['swapMode']!='ExactIn':raise ValueError()
            out=uint(data['outAmount']);floor=uint(data['otherAmountThreshold'])
            impact=Decimal(str(data['priceImpactPct']))
            if not impact.is_finite() or abs(impact)*100>policy['max_price_impact_percent']:raise ValueError()
            if data['slippageBps']!=policy['slippage_bps'] or not 0<floor<=out or floor<out*(10000-policy['slippage_bps'])//10000:raise ValueError()
            if data.get('otherInstructions') or data.get('tipInstruction'):raise ValueError()
            data['_observed_at']=time.time()
            return data
        except Exception:raise Unavailable('No supported Jupiter route within the configured slippage and price-impact limits.') from None

    def prepare(self,owner,side,mint,amount,policy,authorize,quote=None):
        if not release_allowed():raise Unavailable('Server execution switch is off.')
        from solders.compute_budget import set_compute_unit_limit,set_compute_unit_price
        from solders.instruction import Instruction,AccountMeta
        from solders.message import MessageV0,to_bytes_versioned
        from solders.transaction import VersionedTransaction
        from solders.address_lookup_table_account import AddressLookupTableAccount
        from solders.hash import Hash
        from solders.system_program import transfer,TransferParams
        self.network();decimals=self.mint(mint)
        input_mint,output_mint=(SOL,mint) if side=='buy' else (mint,SOL)
        quote=quote or self.build(owner,input_mint,output_mint,amount,policy)
        if not 0<=time.time()-quote['_observed_at']<=20:raise Unavailable('Swap quote expired before preparation.')
        # Revalidate a caller-supplied quote before it can influence guards.
        if quote['inputMint']!=input_mint or quote['outputMint']!=output_mint or uint(quote['inAmount'])!=amount:raise Unavailable('Quote does not match the order.')
        minimum=uint(quote['otherAmountThreshold']);token_ata=ata(owner,mint);sol_ata=ata(owner,SOL)
        before=self.accounts([owner,token_ata,sol_ata]);native=uint(before[0]['lamports'])
        if before[0]['owner']!=SYSTEM or raw_account(before[0])!=b'':raise Unavailable('A system-owned trading wallet is required.')
        owned=token_amount(before[1],owner,mint)
        if token_amount(before[2],owner,SOL)!=0:raise Unavailable('Existing wrapped SOL must be unwrapped before trading.')
        if side=='buy' and owned:raise Unavailable('This wallet already owns the coin; a repeated buy is skipped.')
        if side=='sell' and amount>owned:raise Unavailable('Confirmed token balance is smaller than the planned exit.')
        rent=uint(self.rpc('getMinimumBalanceForRentExemption',[165]))
        fee_cap=5000+policy['max_priority_fee_lamports']
        max_cost=amount+fee_cap+2*rent if side=='buy' else fee_cap
        reserve=policy['reserve_lamports']
        if side=='buy' and native<max_cost+reserve:raise Unavailable('Insufficient SOL after reserve, rent and fee allowance.')
        if side=='sell' and native<fee_cap:raise Unavailable('Not enough native SOL to pay for this exit.')
        min_tokens=minimum if side=='buy' else owned-amount
        min_native=max(reserve,native-max_cost) if side=='buy' else max(0,native+minimum-fee_cap)
        ixs=[set_compute_unit_limit(1_400_000),set_compute_unit_price(policy['max_priority_fee_lamports']*1_000_000//1_400_000)]
        # Manage the canonical WSOL account ourselves, so upstream changes to
        # temporary-account wrapping cannot introduce extra signers or transfers.
        ixs.append(Instruction(pub(ASSOCIATED),bytes([1]),[
            AccountMeta(pub(owner),True,True),AccountMeta(pub(sol_ata),False,True),AccountMeta(pub(owner),False,False),
            AccountMeta(pub(SOL),False,False),AccountMeta(pub(SYSTEM),False,False),AccountMeta(pub(TOKEN),False,False)]))
        if side=='buy':
            ixs.append(transfer(TransferParams(from_pubkey=pub(owner),to_pubkey=pub(sol_ata),lamports=amount)))
            ixs.append(Instruction(pub(TOKEN),bytes([17]),[AccountMeta(pub(sol_ata),False,True)]))
        def decode(row,kind):
            data=base64.b64decode(row['data'],validate=True);program=row['programId'];accounts=row['accounts'];keys=[a['pubkey'] for a in accounts]
            if len(data)>2000 or len(accounts)>100 or any(type(a['isSigner']) is not bool or type(a['isWritable']) is not bool or a['isSigner'] and a['pubkey']!=owner for a in accounts):raise Unavailable('Unexpected instruction authority.')
            if kind=='swap':
                if program!=JUPITER or data[:8] not in ROUTES or not {owner,token_ata,sol_ata}<=set(keys):raise Unavailable('Unsupported Jupiter swap instruction.')
            elif program==SYSTEM:
                raise Unavailable('Unexpected upstream native SOL transfer; wrapping is managed locally.')
            elif program==ASSOCIATED:
                if kind!='setup' or data not in (b'',b'\0',b'\1') or len(keys)<6 or keys[0]!=owner or keys[2]!=owner or keys[3] not in (SOL,mint) or keys[1]!=ata(owner,keys[3]) or keys[4:6]!=[SYSTEM,TOKEN]:raise Unavailable('Unexpected associated account creation.')
            elif program==TOKEN:
                if data==bytes([17]) and kind=='setup' and keys==[sol_ata]:pass
                elif data==bytes([9]) and kind=='cleanup' and keys==[sol_ata,owner,owner]:pass
                else:raise Unavailable('Unsupported token operation in swap setup.')
            else:raise Unavailable('Unexpected top-level swap program.')
            return Instruction(pub(program),data,[AccountMeta(pub(a['pubkey']),a['isSigner'],a['isWritable']) for a in accounts])
        try:
            for row in quote.get('setupInstructions') or []:ixs.append(decode(row,'setup'))
            ixs.append(decode(quote['swapInstruction'],'swap'))
            if quote.get('cleanupInstruction'):raise Unavailable('Unexpected upstream cleanup; native cleanup is managed locally.')
        except Unavailable:raise
        except Exception:raise Unavailable('Malformed swap instruction set.') from None
        ixs.append(Instruction(pub(TOKEN),bytes([9]),[AccountMeta(pub(sol_ata),False,True),AccountMeta(pub(owner),False,True),AccountMeta(pub(owner),True,False)]))
        ixs.extend(guards(owner,mint,decimals,min_tokens,min_native))
        # Other pre-existing wallet token accounts cannot be used as route funds.
        writable=list(dict.fromkeys(str(a.pubkey) for ix in ixs for a in ix.accounts if a.is_writable))
        for address,account in zip(writable,self.accounts(writable)):
            if account and account.get('owner') in (TOKEN,TOKEN2022) and address not in (token_ata,sol_ata):
                raw=raw_account(account)
                if len(raw)>=64 and raw[32:64]==bytes(pub(owner)):raise Unavailable('Swap touches an unrelated wallet token account.')
        tables=[]
        lookup_map=quote.get('addressesByLookupTableAddress') or {}
        if not isinstance(lookup_map,dict) or len(lookup_map)>8:raise Unavailable('Unsupported address tables.')
        for address in lookup_map:
            account=self.accounts([address])[0];raw=raw_account(account)
            if not account or account['owner']!=LOOKUP or len(raw)<56 or (len(raw)-56)%32 or int.from_bytes(raw[4:12],'little')!=2**64-1:raise Unavailable('Address table is unavailable or deactivating.')
            tables.append(AddressLookupTableAccount(pub(address),[pub_from_bytes(raw[i:i+32]) for i in range(56,len(raw),32)]))
        block=self.rpc('getLatestBlockhash',[{'commitment':'confirmed'}])['value']
        msg=MessageV0.try_compile(pub(owner),ixs,tables,Hash.from_string(block['blockhash']))
        if msg.header.num_required_signatures!=1 or str(msg.account_keys[0])!=owner:raise Unavailable('Unexpected compiled signer set.')
        wire=bytes(VersionedTransaction.populate(msg,[empty_signature()]))
        if len(wire)>1232:raise Unavailable('Swap exceeds the supported transaction size.')
        fee=self.rpc('getFeeForMessage',[base64.b64encode(to_bytes_versioned(msg)).decode(),{'commitment':'confirmed'}])['value']
        if fee is None or uint(fee)>fee_cap:raise Unavailable('Transaction fee exceeds the configured cap.')
        simulated=self.rpc('simulateTransaction',[base64.b64encode(wire).decode(),{'encoding':'base64','sigVerify':False,'commitment':'confirmed'}])['value']
        if simulated.get('err') is not None:raise Unavailable('Swap simulation or atomic balance guard failed.')
        if not 0<=time.time()-quote['_observed_at']<=30:raise Unavailable('Swap preparation exceeded its quote window.')
        authorize()
        key=signer()
        if str(key.pubkey())!=owner:raise Unavailable('Trading signer does not match the linked wallet.')
        tx=VersionedTransaction(msg,[key]);tx.verify_and_hash_message()
        return {'wire':base64.b64encode(bytes(tx)).decode(),'signature':str(tx.signatures[0]),
                'last_valid_block_height':uint(block['lastValidBlockHeight']),'prepared_at':time.time(),
                'input_amount':amount,'minimum_output':minimum,'decimals':decimals,'max_debit_lamports':max_cost,
                'native_before':native,'token_before':owned,'fee_cap':fee_cap,'min_native_after':min_native,
                'min_tokens_after':min_tokens,'quote_observed_at':quote['_observed_at']}

    def send(self,prepared):
        if not release_allowed():raise Unavailable('Server execution switch is off; broadcast is blocked.')
        signature=self.rpc('sendTransaction',[prepared['wire'],{'encoding':'base64','skipPreflight':False,'preflightCommitment':'confirmed','maxRetries':0}])
        if signature!=prepared['signature']:raise Unavailable('Broadcast signature mismatch; reconciliation required.')

    def reconcile(self,order):
        prepared=order['prepared'];signature=prepared['signature']
        statuses=self.rpc('getSignatureStatuses',[[signature],{'searchTransactionHistory':True}])['value']
        if not isinstance(statuses,list) or len(statuses)!=1:raise Unavailable('Signature status unavailable.')
        status=statuses[0]
        if status is None:
            height=uint(self.rpc('getBlockHeight',[{'commitment':'finalized'}]))
            return {'state':'expired_unseen' if height>prepared['last_valid_block_height'] else 'pending'}
        if status.get('confirmationStatus')!='finalized':return {'state':'pending'}
        transaction=self.rpc('getTransaction',[signature,{'encoding':'base64','commitment':'finalized','maxSupportedTransactionVersion':0}])
        if transaction is None:return {'state':'pending'}
        if transaction['transaction'][0]!=prepared['wire']:raise Unavailable('Confirmed transaction bytes do not match the stored signature.')
        meta=transaction['meta'];fee=uint(meta['fee'])
        if meta['err'] is not None:return {'state':'failed','fee_lamports':fee}
        from solders.transaction import VersionedTransaction
        tx=VersionedTransaction.from_bytes(base64.b64decode(prepared['wire'],validate=True));tx.verify_and_hash_message()
        owner=order['wallet'];mint=order['mint']
        if str(tx.message.account_keys[0])!=owner:raise Unavailable('Confirmed fee payer mismatch.')
        def tokens(name):
            return sum(uint(r['uiTokenAmount']['amount']) for r in meta[name] if r.get('owner')==owner and r['mint']==mint)
        delta=tokens('postTokenBalances')-tokens('preTokenBalances')
        native_delta=uint(meta['postBalances'][0])-uint(meta['preBalances'][0])
        if order['side']=='buy' and (delta<prepared['minimum_output'] or native_delta>=0 or -native_delta>prepared['max_debit_lamports']):raise Unavailable('Confirmed buy requires manual reconciliation.')
        if order['side']=='sell' and (delta!=-prepared['input_amount'] or native_delta<prepared['minimum_output']-prepared['fee_cap']):raise Unavailable('Confirmed exit requires manual reconciliation.')
        return {'state':'confirmed','token_delta':delta,'native_delta':native_delta,'fee_lamports':fee,'slot':transaction['slot'],'block_time':transaction.get('blockTime')}


def pub_from_bytes(value):
    from solders.pubkey import Pubkey
    return Pubkey(value)


def empty_signature():
    from solders.signature import Signature
    return Signature.default()
