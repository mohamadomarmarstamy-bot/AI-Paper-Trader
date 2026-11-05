"""Idempotent accounting for explicitly reviewed owner-confirmed manual sales.

Read finalized receipts only. Never signs, transfers, clears unknown adjustments,
or resumes trading. Bundled sale/refund/settlement legs are reviewed as a set;
an unrelated deposit must never be guessed to be sale proceeds.
"""
from contextlib import closing
import json
import time
import database
from solana_real_execution import Provider, Unavailable, uint

# Owner confirmed this manual sale on 2026-11-02. Exact finalized receipt legs
# were independently reviewed, including funding, account closure and payout.
REVIEWED_SALES = ({
    'wallet': '3tmujS2fwJiPHRXc6NskYz1vJHf1Eq9JNvx2547MKut6',
    'position_id': 'buy:9d6f8a67dc49a55d95728334d95c1c0cf0171538505c0ced84685e38210eda69',
    'mint': 'y9XdvdUzvQanC64SxwzqaNKfV76YLo3mRpNnvKgpump',
    'quantity': 833221406038, 'cost': 109030226,
    'time_window': (1793639780, 1793639798),
    'legs': (
        ('5493PoD9QzB1C5asmEQWwR5dKkR2cnNWGyEnkYrdfgHrdM5ma65BQZqJ2VRHgCrzaVLLd2JjByCSTUajMeyomSVi', 50000000),
        ('GrhHax9cUCkbgNQ8FoiD55GmpdHXmaeqvpWneRaMMMZJZvWkn6b9V5y1xrxh6nLNEze48xYZddt6DETRLdsbrZv', -3172680),
        ('4ZLGf6upLxYMSnwzcFVvGQh2JPgcKgptMBzRbUR69YHJhyCRjuYBNJbYNcyVFBZ8WYfuQ5tUFL12y6WNpTy8uLya', -45468480),
        ('4reVhPyw58BmNXZkoBimh2Pzy6FdHrTVR846cR7VRuuLc9o5TQVGbtdkCMQv2hRNoP1P7HfkiDDU9n2xwWa5T2b1', 79763552),
        ('ALhUF3psRsSXVyWKGesTN2Gf9YjNWUUFNn9NhRErt33RhdKtZmZKBv5gcLa1MYZDHGLV984Dzv1c7j3CpeW5Xgn', -90001),
    ),
}, {
    'wallet': '3tmujS2fwJiPHRXc6NskYz1vJHf1Eq9JNvx2547MKut6',
    'position_id': 'buy:1a7c1b3d9e099c817b22a1942069294cc0b280796c43989d90b671ece3a77c86',
    'mint': 'CSocnzcRpgSCr1naKcNTieWZUxb6rABe45j844m7pump',
    'quantity': 2960033644, 'cost': 87677360,
    'time_window': (1793842582, 1793851879),
    'settlement_signature': 'BzHfqgx3dqgAWfRAYNzTGuuDQbyUAh4kjCxuDtU2j9qoziS4TyXsq2zPPpaRGwjNGL3rKpQznf5cLz6hpmxvae6',
    'legs': (
        ('3ioyLYXQht9tC1DDur9RVzhLm5on8ZUdjuTQAoJNYjCzeFhvV3CgQ6snVrqdkWCWWoHucHR2tB3a3NLxGTf2xjb9', -80001),
        ('BzHfqgx3dqgAWfRAYNzTGuuDQbyUAh4kjCxuDtU2j9qoziS4TyXsq2zPPpaRGwjNGL3rKpQznf5cLz6hpmxvae6', 84012303),
    ),
},)


def audit(sale, provider):
    total = 0; removed = 0; fees = 0; receipts = []
    for signature, expected_delta in sale['legs']:
        tx = provider.rpc('getTransaction', [signature, {'encoding': 'jsonParsed',
                          'commitment': 'finalized', 'maxSupportedTransactionVersion': 1}])
        try:
            meta = tx['meta']; message = tx['transaction']['message']
            if meta['err'] is not None or tx['transaction']['signatures'][0] != signature:
                raise ValueError()
            if type(tx['blockTime']) is not int or not sale['time_window'][0] <= tx['blockTime'] <= sale['time_window'][1]:
                raise ValueError()
            idx = next(i for i, k in enumerate(message['accountKeys']) if k['pubkey'] == sale['wallet'])
            delta = uint(meta['postBalances'][idx]) - uint(meta['preBalances'][idx])
            if delta != expected_delta:
                raise ValueError()
            change = 0
            for key, sign in (('preTokenBalances', -1), ('postTokenBalances', 1)):
                for balance in meta.get(key, []):
                    if balance.get('owner') == sale['wallet'] and balance['mint'] == sale['mint']:
                        change += sign * uint(balance['uiTokenAmount']['amount'])
            if change > 0:
                raise ValueError()
            removed -= change; total += delta
            if idx == 0:fees += uint(meta['fee'])
            receipts.append({'signature': signature, 'native_delta': delta,
                             'slot': uint(tx['slot']), 'block_time': tx['blockTime']})
        except (KeyError, TypeError, ValueError, StopIteration):
            raise Unavailable('Manual-sale receipts did not match the reviewed wallet changes.') from None
    if removed != sale['quantity'] or total < 0:
        raise Unavailable('Manual-sale quantity or proceeds are not verified.')
    return {'state': 'confirmed', 'token_delta': -removed, 'native_delta': total,
            'fee_lamports': fees, 'receipts': receipts,
            'basis': 'Owner-confirmed manual sale; sum of finalized wallet changes across reviewed settlement legs.'}


def reconcile_reviewed(provider=None):
    import solana_real_worker as live
    state, revision = live.load()
    if not state.get('balance_adjustments') or live.orders(True):return False
    for sale in REVIEWED_SALES:
        matches = [a for a in state['balance_adjustments'] if a['position_id'] == sale['position_id']
                   and a['mint'] == sale['mint'] and a['quantity_removed_raw'] == sale['quantity']
                   and a['unreconciled_cost_lamports'] == sale['cost']]
        if state['wallet'] != sale['wallet'] or len(matches) != 1:continue
        provider = provider or Provider();provider.network()
        result = audit(sale, provider)
        ident = 'manual:' + sale['position_id']
        order = {'id': ident, 'side': 'sell', 'mint': sale['mint'], 'position_id': sale['position_id'],
                 'wallet': sale['wallet'], 'reason': 'manual_external', 'created_at': time.time(),
                 'status': 'confirmed', 'signature': sale.get('settlement_signature', sale['legs'][-2][0]), 'result': result}
        with closing(database.get_connection()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM solana_real_run WHERE id=1').fetchone()
            if row['revision'] != revision or row['lease_until'] > time.time():return False
            if conn.execute('SELECT 1 FROM solana_real_orders WHERE id=?', (ident,)).fetchone():return False
            if conn.execute("SELECT 1 FROM solana_real_orders WHERE status IN ('preparing','signed','submitted','unknown')").fetchone():return False
            signatures = {s for s, _ in sale['legs']}
            for existing in conn.execute('SELECT details_json FROM solana_real_orders'):
                previous = json.loads(existing[0])
                if previous.get('signature') in signatures:return False
            state['balance_adjustments'].remove(matches[0])
            state.setdefault('accounting_reviews', []).append({**matches[0], 'order_id': ident,
                'proceeds_lamports': result['native_delta'], 'reviewed_at': time.time(), 'receipts': result['receipts']})
            state['realized_lamports'] += result['native_delta'] - sale['cost']
            state['enabled'] = False
            if not state['balance_adjustments']:state['error'] = None
            conn.execute('INSERT INTO solana_real_orders VALUES(?,?,?,?)',
                         (ident, order['created_at'], 'confirmed', json.dumps(order, allow_nan=False)))
            conn.execute('UPDATE solana_real_run SET revision=revision+1,state_json=? WHERE id=1',
                         (json.dumps(state, allow_nan=False),))
            conn.commit()
        return True
    return False
