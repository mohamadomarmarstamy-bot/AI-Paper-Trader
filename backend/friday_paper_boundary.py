"""Local broker-contract checks. This module has no transport or credentials."""
from decimal import Decimal, InvalidOperation
from hashlib import sha256


def preview(probe, side):
    if side not in ('buy','sell') or probe['phase'] not in ('pre_market','after_hours'):
        raise ValueError('Unsupported phase intent')
    if side == 'sell' and (probe['state'] != 'measured' or probe['bid'] is None):
        raise ValueError('Owned modeled exit evidence required')
    price = Decimal(str(probe['ask'] if side == 'buy' else probe['bid']))
    if not price.is_finite() or price <= 0:
        raise ValueError('Valid limit required')
    key = '|'.join((probe['intent'],probe['symbol'],side))
    return {'route_enabled':False,'client_order_id':'friday-'+sha256(key.encode()).hexdigest()[:32],
            'symbol':probe['symbol'],'side':side,'type':'limit','time_in_force':'day',
            'extended_hours':True,'qty':1,'reference_limit':str(price)}


def reconcile_fixture(entry, exit_, reports):
    """Read-only reconciliation of cumulative fills for a pair of owned intent IDs."""
    if entry['side'] != 'buy' or exit_['side'] != 'sell' or entry['symbol'] != exit_['symbol']:
        raise ValueError('Matching owned intents required')
    filled = {}
    for intent in (entry,exit_):
        key = intent['client_order_id']
        report = reports.get(key)
        if report is None:
            return {'verified':False,'reason':'order_evidence_missing'}
        if any(report.get(k) != intent[k] for k in ('client_order_id','symbol','side')):
            return {'verified':False,'reason':'ownership_mismatch'}
        try:
            quantity = Decimal(str(report['filled_qty']))
            requested = Decimal(str(report['qty']))
        except (InvalidOperation,KeyError,TypeError):
            return {'verified':False,'reason':'invalid_quantity'}
        if not quantity.is_finite() or not requested.is_finite() or requested != intent['qty'] or not 0 <= quantity <= requested:
            return {'verified':False,'reason':'invalid_quantity'}
        filled[intent['side']] = quantity
    remaining = filled['buy'] - filled['sell']
    if remaining < 0:
        return {'verified':False,'reason':'exit_exceeds_owned_fills'}
    return {'verified':True,'owned_quantity':str(remaining),'route_enabled':False}
