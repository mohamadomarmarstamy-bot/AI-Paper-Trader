"""Trace Phantom's funded sale -> CASH -> SOL settlement without price guesses.

Only the complete five-receipt pattern is accepted. Sponsor funding must be
refunded in full; CASH must start/end at zero with an exact matching conversion;
main-wallet SOL balances must form a continuous chain across all five receipts.
"""
import solana_real_execution as ex
from solana_copy_rpc import decode58
from solana_real_budget import SOL

CASH = 'CASHx9KJUStyftLFWGvEVf59SGeG9sh5FfcnZMVPCASH'
SETTLE = 'DF1ow4tspfHX9JwWJsAb9epbkA8hmpSEAtxXy1V27QBH'
SEND = 'Send9wszHjEiS3hwKcPeSLsPRu5Gb62iCrJEcG4Mq3b'
COMPUTE = 'ComputeBudget111111111111111111111111111111'


def key(tx, address):
    return next(i for i,k in enumerate(tx['transaction']['message']['accountKeys']) if k['pubkey'] == address)


def native(tx, address):
    i = key(tx, address)
    return ex.uint(tx['meta']['postBalances'][i]) - ex.uint(tx['meta']['preBalances'][i])


def balance(tx, field, address, mint):
    return sum(ex.uint(b['uiTokenAmount']['amount']) for b in tx['meta'][field] if b.get('owner') == address and b['mint'] == mint)


def changes(tx, address):
    mints = {b['mint'] for f in ('preTokenBalances','postTokenBalances') for b in tx['meta'][f] if b.get('owner') == address}
    return {mint: balance(tx,'postTokenBalances',address,mint) - balance(tx,'preTokenBalances',address,mint) for mint in mints}


def instructions(tx):
    top = tx['transaction']['message']['instructions']
    return top + [i for g in (tx['meta'].get('innerInstructions') or []) for i in g['instructions']]


def transfers(tx):
    return [i['parsed']['info'] for i in instructions(tx) if i['programId'] == ex.SYSTEM and i.get('parsed',{}).get('type') == 'transfer']


def signer(tx, address):
    return tx['transaction']['message']['accountKeys'][key(tx,address)]['signer'] is True


def require(condition):
    if not condition:raise ValueError()


def cash_history(provider, txs):
    """The CASH lot cannot have been spent/replaced between these receipts."""
    sale, settlement = txs[1], txs[3]
    cash = next(b for b in sale['meta']['postTokenBalances'] if b['mint'] == CASH
                and ex.uint(b['uiTokenAmount']['amount']) > 0
                and balance(sale,'preTokenBalances',b['owner'],CASH) == 0)
    address = sale['transaction']['message']['accountKeys'][ex.uint(cash['accountIndex'])]['pubkey']
    first = sale['transaction']['signatures'][0]; last = settlement['transaction']['signatures'][0]
    before = None; found = False
    for _ in range(2):
        options = {'commitment':'finalized','limit':50}
        if before:options['before'] = before
        page = provider.rpc('getSignaturesForAddress',[address,options]); require(isinstance(page,list))
        for row in page:
            if not found:
                if row['signature'] == last:found = True
                continue
            require(row['signature'] == first)
            return
        if len(page) < 50:break
        before = page[-1]['signature']
    raise ValueError()


def group(txs, wallet, mint, account):
    funding, sale, refund, settlement, fee = txs
    # No missing or unrelated main-wallet transaction can be hidden in this bundle.
    for left, right in zip(txs, txs[1:]):
        require(left['meta']['postBalances'][key(left,wallet)] == right['meta']['preBalances'][key(right,wallet)])
    require(all(not any(v for k,v in changes(t,wallet).items() if k != mint) for t in txs))
    require(all(not changes(t,wallet).get(mint,0) for t in (funding,refund,settlement,fee)))
    removed = -changes(sale,wallet).get(mint,0); require(removed > 0)
    require(sale['transaction']['message']['accountKeys'][0]['pubkey'] == wallet and signer(sale,wallet))
    require(all(sale['transaction']['message']['accountKeys'][ex.uint(b['accountIndex'])]['pubkey'] == account
                for f in ('preTokenBalances','postTokenBalances') for b in sale['meta'][f] if b.get('owner') == wallet and b['mint'] == mint))
    routes = [i for i in sale['transaction']['message']['instructions'] if i['programId'] == ex.JUPITER]
    require(len(routes) == 1 and decode58(routes[0]['data'])[:8] in ex.ROUTES and wallet in routes[0]['accounts'] and account in routes[0]['accounts'])
    require(all(i['programId'] in (ex.JUPITER,ex.ASSOCIATED,ex.TOKEN,ex.TOKEN2022,COMPUTE) for i in sale['transaction']['message']['instructions']))
    cash_rows = []
    for b in sale['meta']['postTokenBalances']:
        if b['mint'] != CASH or b.get('owner') == wallet:continue
        owner = b['owner']; after = ex.uint(b['uiTokenAmount']['amount']); before = balance(sale,'preTokenBalances',owner,CASH)
        if before == 0 and after > 0:cash_rows.append((owner,after,sale['transaction']['message']['accountKeys'][ex.uint(b['accountIndex'])]['pubkey']))
    require(len(cash_rows) == 1); cash_wallet, cash_amount, cash_account = cash_rows[0]
    require(cash_account in routes[0]['accounts'])
    require(any(i['programId'] == ex.TOKEN2022 and i.get('parsed',{}).get('type') == 'transferChecked'
                and i['parsed']['info'].get('authority') == wallet and i['parsed']['info'].get('destination') == cash_account
                and i['parsed']['info'].get('mint') == CASH and ex.uint(i['parsed']['info']['tokenAmount']['amount']) == cash_amount
                for i in instructions(sale)))
    # Sponsor advance and exact repayment, never a guessed unrelated deposit.
    funded = transfers(funding); require(len(funded) == 1)
    advance = funded[0]; sponsor = advance['source']; principal = ex.uint(advance['lamports'])
    require(principal > 0 and advance['destination'] == wallet and sponsor not in (wallet,cash_wallet))
    require(native(funding,wallet) == principal and not signer(funding,wallet))
    require(all(i['programId'] in (COMPUTE,ex.SYSTEM) for i in funding['transaction']['message']['instructions']))
    require(sum(ex.uint(t['lamports']) for t in transfers(refund) if t['source'] == wallet and t['destination'] == sponsor) == principal)
    require(signer(refund,wallet) and signer(refund,sponsor))
    require(any(i['programId'] == SEND for i in refund['transaction']['message']['instructions']))
    require(all(i['programId'] in (COMPUTE,ex.TOKEN,ex.TOKEN2022,ex.SYSTEM,SEND) for i in refund['transaction']['message']['instructions']))
    closes = [i['parsed']['info'] for i in refund['transaction']['message']['instructions']
              if i['programId'] in (ex.TOKEN,ex.TOKEN2022) and i.get('parsed',{}).get('type') == 'closeAccount']
    require(closes and all(c['owner'] == wallet and c['destination'] == wallet for c in closes))
    allowed_close = {account} | {sale['transaction']['message']['accountKeys'][ex.uint(b['accountIndex'])]['pubkey']
                    for b in sale['meta']['postTokenBalances'] if b.get('owner') == wallet and ex.uint(b['uiTokenAmount']['amount']) == 0}
    require(all(c['account'] in allowed_close for c in closes))
    require(all(t['source'] == wallet and (t['destination'] == sponsor or ex.uint(t['lamports']) <= 1_000_000) for t in transfers(refund)))
    # Exact CASH lot is consumed by the cash-wallet signer and paid to the bot wallet.
    require(signer(settlement,cash_wallet) and not signer(settlement,wallet))
    require(balance(settlement,'preTokenBalances',cash_wallet,CASH) == cash_amount and balance(settlement,'postTokenBalances',cash_wallet,CASH) == 0)
    require(all(not v for k,v in changes(settlement,cash_wallet).items() if k != CASH))
    top = settlement['transaction']['message']['instructions']; require(len(top) == 1 and top[0]['programId'] == SETTLE)
    require(wallet in top[0]['accounts'] and cash_account in top[0]['accounts'])
    require(any(i['programId'] == ex.TOKEN2022 and i.get('parsed',{}).get('type') == 'transferChecked'
                and i['parsed']['info'].get('authority') == cash_wallet and i['parsed']['info'].get('source') == cash_account
                and i['parsed']['info'].get('mint') == CASH and ex.uint(i['parsed']['info']['tokenAmount']['amount']) == cash_amount
                for i in instructions(settlement)))
    payout = [t for t in transfers(settlement) if t['destination'] == wallet]
    require(len(payout) == 1 and payout[0]['source'] == cash_wallet and ex.uint(payout[0]['lamports']) == native(settlement,wallet) > 0)
    # The separate fee receipt must bind both wallets and drain the gas advance.
    require(signer(fee,wallet) and signer(fee,cash_wallet) and fee['meta']['postBalances'][key(fee,cash_wallet)] == 0)
    require(any(i['programId'] == SEND for i in fee['transaction']['message']['instructions']))
    require(all(i['programId'] in (COMPUTE,ex.SYSTEM,SEND) for i in fee['transaction']['message']['instructions']))
    require(-1_000_000 <= native(fee,wallet) < 0)
    require(all(t['source'] in (wallet,cash_wallet) and t['destination'] not in (wallet,cash_wallet) for t in transfers(fee)))
    require(settlement['meta']['postBalances'][key(settlement,cash_wallet)] == fee['meta']['preBalances'][key(fee,cash_wallet)])
    require(0 <= fee['blockTime'] - funding['blockTime'] <= 3600)
    return removed, cash_wallet, cash_amount


def audit(provider, wallet, mint, program, removed, start_slot, end_slot, orders):
    from solana_external_sales import used_signatures
    try:
        require(start_slot > 0)
        known = used_signatures(orders); rows = []; before = None; complete = False; seen = set()
        for _ in range(2):
            options = {'commitment':'finalized','limit':50}
            if before:options['before'] = before
            page = provider.rpc('getSignaturesForAddress',[wallet,options]); require(isinstance(page,list))
            for row in page:
                require(row['signature'] not in seen); seen.add(row['signature'])
                if ex.uint(row['slot']) <= start_slot:complete = True;break
                if row.get('err') is None:rows.append(row)
            if complete or len(page) < 50:complete = True;break
            before = page[-1]['signature']
        require(complete and 5 <= len(rows) <= 20)
        txs = []
        for row in reversed(rows):
            # Known executions are separators; they cannot be consumed as manual receipts.
            if row['signature'] in known:txs.append(None);continue
            tx = provider.rpc('getTransaction',[row['signature'],{'encoding':'jsonParsed','commitment':'finalized','maxSupportedTransactionVersion':1}])
            require(tx['meta']['err'] is None and tx['slot'] == row['slot'] and tx['transaction']['signatures'][0] == row['signature'])
            txs.append(tx)
        found = []; total_removed = 0; index = 0
        while index < len(txs):
            chunk = txs[index:index+5]
            if len(chunk) == 5 and all(t is not None for t in chunk):
                try:q, cash_wallet, cash_amount = group(chunk,wallet,mint,ex.ata(wallet,mint,program))
                except (KeyError,ValueError,TypeError,IndexError,StopIteration,ex.Unavailable):pass
                else:
                    cash_history(provider,chunk)
                    for tx in chunk:
                        require(not found or (tx['slot'],tx['transactionIndex']) > (found[-1]['slot'],found[-1]['transactionIndex']))
                        found.append({'signature':tx['transaction']['signatures'][0], 'slot':tx['slot'], 'transactionIndex':tx['transactionIndex'],
                            'block_time':tx['blockTime'], 'native_delta':native(tx,wallet), 'token_delta':changes(tx,wallet).get(mint,0),
                            'fee_lamports':ex.uint(tx['meta']['fee']) if key(tx,wallet) == 0 else 0})
                    total_removed += q; index += 5;continue
            tx = txs[index]
            require(tx is None or not changes(tx,wallet).get(mint,0))
            index += 1
        require(found and total_removed == removed)
        proceeds = sum(r['native_delta'] for r in found); require(proceeds >= 0)
        return {'state':'confirmed','token_delta':-removed,'native_delta':proceeds,'fee_lamports':sum(r['fee_lamports'] for r in found),
            'slot':found[-1]['slot'],'receipts':found,
            'basis':'Finalized linked Phantom sale/CASH/SOL receipts, including matched sponsor advance/refund and wallet fees.'}
    except (KeyError,ValueError,TypeError,IndexError,StopIteration):
        raise ex.Unavailable('Phantom CASH settlement is incomplete or ambiguous; accounting remains pending.') from None
