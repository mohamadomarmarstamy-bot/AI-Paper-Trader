"""Read-only reconciliation of unambiguous, finalized manual token-to-SOL swaps.

No price estimates, signing, activation or multi-transaction settlement guesses.
Receipts and the cost adjustment are committed together by the live worker.
"""
from contextlib import closing
import hashlib
import json
import time
import database
import solana_real_execution as ex
from solana_real_budget import SOL

COMPUTE = 'ComputeBudget111111111111111111111111111111'


def stored_orders():
    with closing(database.get_connection()) as conn:
        return [json.loads(r[0]) for r in conn.execute('SELECT details_json FROM solana_real_orders')]


def used_signatures(orders):
    signatures = set()
    for order in orders:
        if order.get('signature'):signatures.add(order['signature'])
        signatures.update(r['signature'] for r in (order.get('result') or {}).get('receipts', []))
    return signatures


def receipt(tx, signature, wallet, mint, account):
    """Accept a single recognized atomic swap, with only one changed wallet asset."""
    from solana_copy_rpc import PROGRAMS, decode58, parse
    try:
        meta = tx['meta']; message = tx['transaction']['message']; keys = message['accountKeys']
        if meta['err'] is not None or tx['transaction']['signatures'][0] != signature:raise ValueError()
        if keys[0]['pubkey'] != wallet or keys[0]['signer'] is not True:raise ValueError()
        changes = {}; wrapped_accounts = set()
        for field, sign in (('preTokenBalances', -1), ('postTokenBalances', 1)):
            for row in meta[field]:
                if row.get('owner') != wallet:continue
                token = row['mint']; key = keys[ex.uint(row['accountIndex'])]['pubkey']
                if token == mint and key != account:raise ValueError()
                if token == SOL:wrapped_accounts.add(key)
                changes[token] = changes.get(token, 0) + sign * ex.uint(row['uiTokenAmount']['amount'])
        # Wrapped SOL or a different output asset needs a separate settlement audit.
        if changes.get(mint, 0) >= 0 or any(v for k, v in changes.items() if k != mint):raise ValueError()
        swap_count = 0
        for ins in message['instructions']:
            program = ins['programId']
            if program == ex.JUPITER:
                if decode58(ins['data'])[:8] not in ex.ROUTES or wallet not in ins['accounts'] or account not in ins['accounts']:raise ValueError()
                swap_count += 1
            elif program in PROGRAMS:
                trades = parse(tx, wallet, signature)
                if len(trades) != 1 or trades[0]['base']['address'] != mint or trades[0]['base']['type_swap'] != 'from' or trades[0]['quote']['address'] != SOL:raise ValueError()
                swap_count += 1
            elif program == ex.SYSTEM:
                parsed = ins['parsed']; info = parsed['info']
                if parsed['type'] == 'transfer':
                    if info['source'] != wallet or info['destination'] not in wrapped_accounts:raise ValueError()
                elif parsed['type'] == 'createAccount':
                    if info['source'] != wallet or info['newAccount'] not in wrapped_accounts:raise ValueError()
                else:raise ValueError()
            elif program in (ex.TOKEN, ex.TOKEN2022):
                parsed = ins['parsed']
                if parsed['type'] == 'closeAccount':
                    if parsed['info']['destination'] != wallet:raise ValueError()
                elif parsed['type'] not in ('syncNative', 'initializeAccount', 'initializeAccount2', 'initializeAccount3'):raise ValueError()
            elif program not in (COMPUTE, ex.ASSOCIATED):raise ValueError()
        if swap_count != 1:raise ValueError()
        native = ex.uint(meta['postBalances'][0]) - ex.uint(meta['preBalances'][0])
        if native <= 0:raise ValueError()
        return {'signature': signature, 'slot': ex.uint(tx['slot']), 'block_time': ex.uint(tx['blockTime']),
                'token_delta': changes[mint], 'native_delta': native, 'fee_lamports': ex.uint(meta['fee'])}
    except (KeyError, TypeError, ValueError, IndexError, ex.Unavailable):
        raise ex.Unavailable('External transaction is not an unambiguous finalized token-to-SOL sale.') from None


def audit_atomic(provider, wallet, mint, program, removed, start_slot, end_slot, orders):
    """Bounded complete ATA history, excluding every already recorded signature."""
    if not 0 < start_slot < end_slot:raise ex.Unavailable('External-sale history has no verified starting slot.')
    account = ex.ata(wallet, mint, program); known = used_signatures(orders)
    before = None; candidates = []; seen = set(); complete = False
    for _ in range(2):
        options = {'commitment': 'finalized', 'limit': 50}
        if before:options['before'] = before
        page = provider.rpc('getSignaturesForAddress', [account, options])
        if not isinstance(page, list):raise ex.Unavailable('External-sale history unavailable.')
        for row in page:
            slot = ex.uint(row['slot']); sig = row['signature']
            if sig in seen:raise ex.Unavailable('External-sale history repeated a signature.')
            seen.add(sig)
            if slot <= start_slot:complete = True;break
            if slot > end_slot or row.get('err') is not None or sig in known:continue
            candidates.append((sig, slot))
        if complete or len(page) < 50:complete = True;break
        before = page[-1]['signature']
    if not complete or not 1 <= len(candidates) <= 12:raise ex.Unavailable('External-sale history requires review.')
    receipts = []
    for sig, slot in reversed(candidates):
        tx = provider.rpc('getTransaction', [sig, {'encoding': 'jsonParsed', 'commitment': 'finalized', 'maxSupportedTransactionVersion': 1}])
        # Unknown account-only activity is deliberately not discarded as harmless.
        r = receipt(tx, sig, wallet, mint, account)
        if r['slot'] != slot:raise ex.Unavailable('External-sale history slot mismatch.')
        receipts.append(r)
    if sum(-r['token_delta'] for r in receipts) != removed:raise ex.Unavailable('External-sale receipts do not match the missing quantity.')
    return {'state': 'confirmed', 'token_delta': -removed, 'native_delta': sum(r['native_delta'] for r in receipts),
            'fee_lamports': sum(r['fee_lamports'] for r in receipts), 'slot': max(r['slot'] for r in receipts),
            'receipts': receipts, 'basis': 'Finalized atomic manual token-to-SOL swaps; actual wallet SOL change including fees and rent.'}


def audit(provider, wallet, mint, program, removed, start_slot, end_slot, orders):
    try:
        return audit_atomic(provider,wallet,mint,program,removed,start_slot,end_slot,orders)
    except ex.Unavailable:
        from solana_phantom_accounting import audit as phantom_audit
        return phantom_audit(provider,wallet,mint,program,removed,start_slot,end_slot,orders)


def book(state, adjustment, result, now):
    signatures = [r['signature'] for r in result['receipts']]
    ident = 'external:' + hashlib.sha256('|'.join(signatures).encode()).hexdigest()
    order = {'id': ident, 'side': 'sell', 'mint': adjustment['mint'], 'wallet': state['wallet'],
             'position_id': adjustment['position_id'], 'reason': 'manual_external', 'status': 'confirmed',
             'created_at': result['receipts'][-1]['block_time'], 'signature': signatures[-1], 'result': result}
    review = {**adjustment, 'order_id': ident, 'proceeds_lamports': result['native_delta'],
              'reviewed_at': now, 'receipts': result['receipts'], 'automatic': True}
    state.setdefault('accounting_reviews', []).append(review)
    state['realized_lamports'] += result['native_delta'] - adjustment['unreconciled_cost_lamports']
    return order


def reconcile_pending(provider=None):
    """Repair old adjustments without restarting a paused copier or touching other funds."""
    import solana_real_worker as live
    state, revision = live.load()
    if not state.get('balance_adjustments') or live.orders(True):return False
    provider = provider or ex.Provider(); provider.network(); orders = stored_orders()
    for adjustment in state['balance_adjustments']:
        matching = [a for a in state['balance_adjustments'] if a['mint'] == adjustment['mint']]
        entry = next((o for o in orders if o['id'] == adjustment['position_id'] and o.get('side') == 'buy'
                      and o.get('status') == 'confirmed' and o.get('wallet') == state['wallet'] and o.get('mint') == adjustment['mint']), None)
        if len(matching) != 1 or not entry:continue
        start = adjustment.get('start_slot') or (entry.get('result') or {}).get('slot', 0)
        end = adjustment.get('finalized_slot', 0)
        try:
            result = audit(provider, state['wallet'], adjustment['mint'], (entry.get('prepared') or {}).get('token_program', ex.TOKEN),
                           adjustment['quantity_removed_raw'], start, end, orders)
        except ex.Unavailable:continue
        order = book(state, adjustment, result, time.time())
        state['balance_adjustments'].remove(adjustment)
        position = state['positions'].get(adjustment['position_id'])
        if position:
            position.pop('external_change', None)
            position['realized_lamports'] += result['native_delta'] - adjustment['unreconciled_cost_lamports']
        if not state['balance_adjustments']:state['error'] = None
        with closing(database.get_connection()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM solana_real_run WHERE id=1').fetchone()
            if row['revision'] != revision or row['lease_until'] > time.time():return False
            if conn.execute("SELECT 1 FROM solana_real_orders WHERE status IN ('preparing','signed','submitted','unknown')").fetchone():return False
            conn.execute('INSERT INTO solana_real_orders VALUES(?,?,?,?)', (order['id'], order['created_at'], 'confirmed', json.dumps(order, allow_nan=False)))
            conn.execute('UPDATE solana_real_run SET revision=revision+1,state_json=? WHERE id=1', (json.dumps(state, allow_nan=False),))
            conn.commit()
        return True
    return False
