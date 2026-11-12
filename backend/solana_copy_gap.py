"""Read-only gap evidence and forward simulator calibration from saved receipts.

No prices are fetched and no trading parameter, balance or prediction is edited.
Observed differences overlap; they are not a causal P/L waterfall.
"""
from collections import Counter, defaultdict
from decimal import Decimal
import json
import math

PENDING = {'preparing', 'signed', 'submitted', 'unknown'}
TERMINAL_MISS = {'failed', 'rejected', 'expired', 'canceled', 'abandoned'}
MATCH_SECONDS = 5
GROUPS = {
    'closed': 'Actual position closed', 'open': 'Actual buy · open or incomplete',
    'pending': 'Live order awaiting settlement', 'paused': 'Live entries paused',
    'late': 'Expired or late signal', 'price': 'Source entry price bound',
    'funding': 'Balance, sizing or accounting', 'token': 'Token age or mint restriction',
    'quote': 'Route, slippage or provider capacity', 'source': 'Source eligibility or feed',
    'failed_buy': 'Buy failed on chain', 'other': 'Other rejected or canceled entry',
    'unrecorded': 'No recorded live decision or order',
}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def integer(value, *, positive=False):
    return type(value) is int and (value > 0 if positive else value >= 0)


def percent(actual, predicted):
    return float((Decimal(actual)/Decimal(predicted)-1)*100)


def classification(reason):
    reason = (reason or '').lower()
    rules = (
        ('paused', ('paused', 'daily live entry pause', 'execution switch is off')),
        ('late', ('expired', 'fresh live entry window', 'already sold', 'seconds late')),
        ('price', ('source cost', 'source entry price', 'source-price')),
        ('funding', ('balance', 'funding', 'reserve', 'buy size', 'accounting', 'insufficient')),
        ('token', ('token age', 'under 2 hours', 'mint', 'freeze authority')),
        ('quote', ('quote', 'route', 'slippage', 'rate limit', 'jupiter', 'cooldown')),
        ('source', ('source', 'paper-only', 'follow', 'feed', 'copy rules', 'position per coin')),
    )
    return next((group for group, fragments in rules if any(f in reason for f in fragments)), 'other')


def receipt_time(order):
    """Prefer receipt block time; explicitly mark polling-based fallbacks."""
    stamp = (order.get('result') or {}).get('block_time')
    if finite(stamp) and stamp > 0: return stamp, 'block_time'
    key, start = ('source_to_finalized_ms', order.get('source_at')) if order.get('side') == 'buy' else ('trigger_to_finalized_ms', order.get('detected_at'))
    duration = (order.get('timing_ms') or {}).get(key)
    if finite(start) and start > 0 and finite(duration) and duration >= 0:
        return start+duration/1000, 'observed_finalization'
    return None, None


def identity(row, order, owner):
    return (order.get('wallet') == owner and order.get('mint') == row.get('mint')
            and order.get('source_wallet') == row.get('source_wallet'))


def fully_closed(buy, exits):
    quantity = (buy.get('result') or {}).get('token_delta')
    sold = [(e.get('result') or {}).get('token_delta') for e in exits]
    return (integer(quantity, positive=True) and bool(sold)
            and all(type(q) is int and q < 0 for q in sold) and -sum(sold) == quantity)


def read_context(conn, owner):
    """One database snapshot, with no schema creation or network side effects."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    state = conn.execute('SELECT state_json FROM solana_copy_shadow_run WHERE wallet=?', (owner,)).fetchone() if 'solana_copy_shadow_run' in tables else None
    state = json.loads(state[0]) if state else {}
    rows = [json.loads(r[0]) for r in conn.execute('SELECT details_json FROM solana_copy_shadow_entries WHERE wallet=? ORDER BY created_at DESC', (owner,))] if 'solana_copy_shadow_entries' in tables else []
    ids = {r['id'] for r in rows}
    decisions = {r[0]: r[1] for r in conn.execute('SELECT source_id,reason FROM solana_real_decisions') if r[0] in ids} if 'solana_real_decisions' in tables else {}
    return {'rows': rows, 'decisions': decisions, 'started_at': state.get('started_at')}


def analyze(rows, orders, owner, decisions=None, started_at=None):
    from solana_copy_performance import stats
    decisions = decisions or {}
    # Deduplicate stored IDs before any count or fee aggregation.
    actual = {o['id']: o for o in orders if o.get('wallet') == owner}
    exits = defaultdict(list); failures = defaultdict(list); sell_attempts = defaultdict(list)
    for order in actual.values():
        if order.get('side') == 'sell':
            sell_attempts[order.get('position_id')].append(order)
            if order.get('status') == 'confirmed': exits[order.get('position_id')].append(order)
        if order.get('status') == 'failed': failures[order.get('position_id') or order['id']].append(order)
    buckets = {key: {'key': key, 'label': label, 'count': 0, 'estimated_closed_misses': 0,
                     'estimated_missed_net_lamports': 0} for key, label in GROUPS.items()}
    conditions = Counter(); closed_excluded = Counter(); price_excluded = Counter(); exit_excluded = Counter()
    samples = defaultdict(list); historical = defaultdict(list); history_counts = Counter()
    examples = []; pair_gaps = []; missed_values = []; exact_entries = 0; exit_candidates = 0; exact_exits = 0
    fallback_times = 0; matched_buys = 0; entry_time_pairs = 0; forward_failed_fees = 0; unknown_forward_fees = 0

    # These checks also work on older real history, before the shadow started.
    for order in actual.values():
        if order.get('status') not in ('confirmed', 'failed'): continue
        result = order.get('result') or {}; prepared = order.get('prepared') or {}
        fee = result.get('fee_lamports'); cap = prepared.get('fee_cap')
        history_counts['settled_orders'] += 1
        if integer(fee) and integer(cap): historical['unused_fee_allowance_lamports'].append(cap-fee)
        if order.get('side') != 'buy' or order.get('status') != 'confirmed': continue
        history_counts['confirmed_buys'] += 1
        quantity = result.get('token_delta'); minimum = prepared.get('minimum_output')
        amount = prepared.get('input_amount'); debit = result.get('native_delta')
        if integer(quantity, positive=True) and integer(minimum, positive=True):
            historical['tokens_above_minimum_percent'].append(percent(quantity, minimum))
        if type(debit) is int and debit < 0 and integer(amount, positive=True) and integer(fee):
            historical['other_native_debit_lamports'].append(-debit-amount-fee)

    for row in rows:
        ident = 'buy:'+row['id']; buy = actual.get(ident)
        if buy and not identity(row, buy, owner): buy = None
        sales = sorted((e for e in exits[ident] if identity(row, e, owner)), key=lambda e: (e.get('created_at', 0), e['id']))
        failed = [e for e in failures[ident] if identity(row, e, owner)]
        attempts = [e for e in sell_attempts[ident] if identity(row, e, owner)]
        confirmed = buy and buy.get('status') == 'confirmed'
        closed = bool(confirmed and fully_closed(buy, sales))
        reason = (buy.get('error') or buy.get('reason')) if buy else decisions.get(row['id'])
        if confirmed: group = 'closed' if closed else 'open'
        elif buy and buy.get('status') in PENDING: group = 'pending'
        elif buy and buy.get('status') == 'failed': group = 'failed_buy'
        elif not buy and (not reason or reason.startswith('Selected fresh')): group = 'unrecorded'
        else: group = classification(reason)
        bucket = buckets[group]; bucket['count'] += 1
        if row.get('status') == 'skipped': conditions['simulation_skipped'] += 1
        missed_known = (buy and buy.get('status') in TERMINAL_MISS or not buy and reason and not reason.startswith('Selected fresh'))
        if not confirmed and missed_known and row.get('status') == 'closed' and type(row.get('realized_lamports')) is int:
            estimate = row['realized_lamports']; missed_values.append(estimate)
            bucket['estimated_closed_misses'] += 1; bucket['estimated_missed_net_lamports'] += estimate
        failed_sells = [o for o in attempts if o.get('status') == 'failed']
        conditions['failed_sells'] += len(failed_sells)
        conditions['rejected_sells'] += sum(o.get('status') == 'rejected' for o in attempts)
        for order in failed:
            fee = (order.get('result') or {}).get('fee_lamports')
            if integer(fee): forward_failed_fees += fee
            else: unknown_forward_fees += 1
        if not confirmed: continue
        matched_buys += 1
        receipt = buy.get('result') or {}; prepared = buy.get('prepared') or {}
        same_size = (integer(row.get('input_lamports'), positive=True) and integer(prepared.get('input_amount'), positive=True)
                     and row['input_lamports'] == prepared['input_amount'])
        fee_complete = all(integer((o.get('result') or {}).get('fee_lamports')) for o in failed)
        pnl_complete = all(type(o.get('realized_pnl_lamports')) is int for o in sales)
        external = any(o.get('reason') == 'manual_external' for o in sales)
        if row.get('status') == 'closed':
            if not closed: closed_excluded['actual_open_or_incomplete'] += 1
            elif not same_size: closed_excluded['different_or_unknown_size'] += 1
            elif external: closed_excluded['manual_external_activity'] += 1
            elif not fee_complete or not pnl_complete: closed_excluded['unknown_cost_or_fee'] += 1
            elif type(row.get('realized_lamports')) is int:
                live_pnl = sum(o['realized_pnl_lamports'] for o in sales)-sum(o['result']['fee_lamports'] for o in failed)
                pair_gaps.append(live_pnl-row['realized_lamports'])
                if [e.get('reason') for e in row.get('exits', [])] != [e.get('reason') for e in sales]: conditions['different_exits'] += 1

        # Timing compares saved predictions to subsequent receipts, not a newly
        # fitted model. Legacy rows without a training cutoff are not scored.
        model = row.get('delay_model') or {}; cutoff = model.get('training_cutoff_at')
        duration = (buy.get('timing_ms') or {}).get('source_to_finalized_ms')
        if (finite(cutoff) and finite(row.get('source_at')) and cutoff <= row['source_at']
                and finite(model.get('seconds')) and model['seconds'] >= 0 and finite(duration) and duration >= 0):
            samples['entry_delay_error_seconds'].append(duration/1000-model['seconds'])
        else: price_excluded['unscored_timing_prediction'] += 1
        stamp, time_basis = receipt_time(buy); quote_at = row.get('entry_quote_at')
        if finite(stamp) and finite(quote_at):
            entry_time_pairs += 1
            samples['entry_time_gap_seconds'].append(stamp-quote_at)
            conditions['later_entry'] += stamp-quote_at > 1
        same_time = finite(stamp) and finite(quote_at) and abs(stamp-quote_at) <= MATCH_SECONDS
        quantity = receipt.get('token_delta'); debit = receipt.get('native_delta')
        if not same_size: price_excluded['different_or_unknown_size'] += 1
        elif not same_time: price_excluded['missing_time_or_over_5s_apart'] += 1
        elif not (integer(quantity, positive=True) and type(debit) is int and debit < 0
                  and integer(row.get('quantity_raw'), positive=True) and integer(row.get('entry_cost_lamports'), positive=True)):
            price_excluded['missing_receipt_or_prediction'] += 1
        else:
            exact_entries += 1; fallback_times += time_basis == 'observed_finalization'
            cost_error = float((Decimal(-debit)*row['quantity_raw']/(Decimal(quantity)*row['entry_cost_lamports'])-1)*100)
            samples['entry_cost_error_percent'].append(cost_error)
            samples['entry_output_error_percent'].append(percent(quantity, row['quantity_raw']))
            conditions['higher_entry_cost'] += cost_error > 0
            paid_fee = receipt.get('fee_lamports'); predicted_fee = row.get('estimated_fee_lamports')
            if integer(paid_fee) and integer(predicted_fee): samples['fee_error_lamports'].append(paid_fee-predicted_fee)
            if integer(paid_fee) and integer(row.get('estimated_rent_lamports')):
                samples['other_debit_error_lamports'].append(-debit-prepared['input_amount']-paid_fee-row['estimated_rent_lamports'])

        # Greedy nearest-time, exact-quantity/reason matching; each actual exit
        # can be scored at most once. No prorating across partial exits.
        available = {o['id']: o for o in sales if o.get('reason') != 'manual_external'}
        for predicted in sorted(row.get('exits', []), key=lambda e: e.get('quote_at', 0)):
            exit_candidates += 1; at = predicted.get('quote_at'); quantity = predicted.get('quantity_raw')
            candidates = []
            for order in available.values():
                receipt_quantity = (order.get('result') or {}).get('token_delta')
                stamp, basis = receipt_time(order)
                if (integer(quantity, positive=True) and receipt_quantity == -quantity
                        and predicted.get('reason') == order.get('reason') and finite(at) and finite(stamp)):
                    candidates.append((abs(stamp-at), order['id'], stamp, basis))
            nearest = min(candidates) if candidates else None
            if nearest is None or nearest[0] > MATCH_SECONDS:
                exit_excluded['no_exact_quantity_reason_time_match'] += 1; continue
            _, key, stamp, basis = nearest; order = available.pop(key); receipt = order.get('result') or {}
            value = predicted.get('net_lamports'); actual_value = receipt.get('native_delta')
            if not integer(value, positive=True) or type(actual_value) is not int:
                exit_excluded['missing_net_proceeds'] += 1; continue
            exact_exits += 1; fallback_times += basis == 'observed_finalization'
            samples['exit_net_error_percent'].append(percent(actual_value, value))
            paid_fee = receipt.get('fee_lamports'); predicted_fee = predicted.get('estimated_fee_lamports')
            if integer(paid_fee) and integer(predicted_fee): samples['fee_error_lamports'].append(paid_fee-predicted_fee)
        if len(examples) < 20:
            examples.append({'source_id': row['id'], 'mint': row.get('mint'), 'source_wallet': row.get('source_wallet'),
                             'actual_status': group, 'reason': reason, 'entry_size_matches': same_size, 'entry_time_matches': same_time})

    metrics = (
        ('entry_cost_error_percent', 'Actual all-in entry cost/token vs simulation', 'percent', matched_buys),
        ('entry_output_error_percent', 'Actual received tokens vs simulated minimum', 'percent', matched_buys),
        ('entry_delay_error_seconds', 'Actual observed entry delay minus saved prediction', 'seconds', matched_buys),
        ('entry_time_gap_seconds', 'Actual receipt time minus simulated entry quote time', 'seconds', matched_buys),
        ('exit_net_error_percent', 'Actual net exit proceeds vs simulation', 'percent', exit_candidates),
        ('fee_error_lamports', 'Actual paid fee minus estimated fee', 'lamports', matched_buys+exit_candidates),
        ('other_debit_error_lamports', 'Other entry debit minus assumed rent', 'lamports', matched_buys),
    )
    historical_metrics = (
        ('tokens_above_minimum_percent', 'Received tokens above stored live quote minimum', 'percent', history_counts['confirmed_buys']),
        ('unused_fee_allowance_lamports', 'Stored fee allowance minus actual paid fee', 'lamports', history_counts['settled_orders']),
        ('other_native_debit_lamports', 'Entry debit beyond swap input and paid fee', 'lamports', history_counts['confirmed_buys']),
    )
    return {
        'owner': owner or None, 'started_at': started_at, 'signals': len(rows), 'matched_buys': matched_buys,
        'entry_outcomes': [{**b, 'estimated_missed_net_lamports': b['estimated_missed_net_lamports'] if b['estimated_closed_misses'] else None} for b in buckets.values() if b['count']],
        'conditions': [
            {'label': 'Higher actual entry cost per token', 'count': conditions['higher_entry_cost'], 'basis': f'{exact_entries} same-size entries quoted within five seconds of receipt time'},
            {'label': 'Actual entry more than one second after simulated quote', 'count': conditions['later_entry'], 'basis': f'{entry_time_pairs} matched signals with recorded times; size may differ, no five-second exclusion'},
            {'label': 'Different completed exit sequences', 'count': conditions['different_exits'], 'basis': f'{len(pair_gaps)} same-input, fully closed pairs'},
            {'label': 'Failed on-chain sell attempts', 'count': conditions['failed_sells'], 'basis': 'Finalized failures; no assumed successful exit'},
            {'label': 'Rejected sell attempts', 'count': conditions['rejected_sells'], 'basis': 'Rejected before broadcast; no paid fee assumed'},
            {'label': 'Simulator skipped the signal', 'count': conditions['simulation_skipped'], 'basis': 'Includes strategy, pricing and data restrictions'},
        ],
        'closed_pair_count': len(pair_gaps), 'actual_minus_simulated_lamports': sum(pair_gaps) if pair_gaps else None,
        'closed_pair_exclusions': dict(closed_excluded),
        'hypothetical_missed_count': len(missed_values), 'hypothetical_missed_net_lamports': sum(missed_values) if missed_values else None,
        'hypothetical_missed_winners': sum(v > 0 for v in missed_values), 'hypothetical_missed_losers': sum(v < 0 for v in missed_values),
        'known_failed_fees_lamports': forward_failed_fees, 'unknown_failed_fees': unknown_forward_fees,
        'calibration': {'status': 'Matched forward samples available' if exact_entries or exact_exits else 'Waiting for comparable forward live receipts',
            'automatic_setting_changes': False, 'match_window_seconds': MATCH_SECONDS,
            'exact_entry_pairs': exact_entries, 'exact_exit_pairs': exact_exits, 'polling_time_matches': fallback_times,
            'entry_exclusions': dict(price_excluded), 'exit_exclusions': dict(exit_excluded),
            'metrics': [{'key': key, 'label': label, 'unit': unit, 'eligible': eligible,
                         'absolute_median': stats([abs(v) for v in samples[key]])['median'],
                         **stats(samples[key])} for key, label, unit, eligible in metrics],
            'historical_metrics': [{'key': key, 'label': label, 'unit': unit, 'eligible': eligible, **stats(historical[key])} for key, label, unit, eligible in historical_metrics]},
        'examples': examples,
        'basis': 'Forward outcomes use all stored comparison signals, not the latest page. Conditions can overlap and do not allocate the P/L gap to causes. The matched closed result is actual minus simulated net SOL for equal-input, fully closed positions with known fees and no manual external sales. It combines price, timing, cost and exit-rule differences. Hypothetical missed results include simulated winners and losers; they are not money lost. Original paper USD is never converted into SOL.',
        'calibration_basis': 'Entry prices require the same source signal, execution wallet, mint and input amount, with simulated quote and actual receipt time at most five seconds apart. Exit prices also require identical raw quantity and exit reason, with one-to-one receipt matching. Signed errors are actual minus predicted (relative for percentages). Observed-finalization fallback times include polling delay. Timing predictions without a saved past-only training cutoff are unscored. Other native debit includes account funding/refunds, not just rent. Historical minimum-output checks measure a floor, not an expected fill price. Failed outcomes remain visible outside successful-fill calibration. No settings are changed automatically; small matched samples do not prove profitability.',
    }
