"""PAPER intake sizing, one-shot dispatch, scanner ownership and recovery."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

import database
from pro_ticker_manual_signals import submit_manual_signal
from pro_ticker_manual_scanner import (ManualScannerContext, submit_entry,
    worker_tick, saved_entries, status, _row, _save, EntryBlocked, assert_dispatch,
    persist_entry_fill, persist_exit_fills)

NOW = datetime(2026, 10, 27, 14, tzinfo=timezone.utc).timestamp()
ACCOUNT = str(uuid.uuid4())


class ManualScannerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch.object(database, 'DATABASE_PATH', Path(temp.name)/'test.sqlite3')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = NOW
        self.orders = {}
        self.positions = []
        self.market_open = True
        self.account_id = ACCOUNT
        self.enabled = True
        self.scan_signal = 'HOLD'
        self.request_id = str(uuid.uuid4())
        self.symbol = 'TEST'
        self.review(self.request_id)
        self.context = ManualScannerContext(
            request=Mock(side_effect=self.broker),
            quote=Mock(side_effect=lambda symbol: {'symbol':symbol,'ask':'45.50','timestamp':self.stamp()}),
            risk=Mock(return_value={'approved':True}),
            submit=Mock(side_effect=self.buy), close=Mock(side_effect=self.sell),
            scan=Mock(side_effect=lambda symbol: {'symbol':symbol,'scanner_stale':False,
                'signal':self.scan_signal,'score':20 if self.scan_signal=='SELL' else 60,'confidence':90}),
            enabled=lambda:self.enabled)
        self.tick()

    def stamp(self):
        return datetime.fromtimestamp(self.now, timezone.utc).isoformat()

    def review(self, request_id, symbol='TEST'):
        # A missing technical grade is deliberately not a discretionary BUY gate.
        submit_manual_signal({'symbol':symbol,'request_id':request_id},
            quote_fetcher=lambda s: None, analyzer=lambda s: None, clock=lambda:self.now)

    def broker(self, method, path, **kwargs):
        self.assertEqual(method, 'GET')
        if path == '/v2/account':
            return {'id':self.account_id,'status':'ACTIVE','trading_blocked':False,'account_blocked':False}
        if path == '/v2/clock':
            return {'is_open':self.market_open,'next_close':datetime.fromtimestamp(NOW+6*3600,timezone.utc).isoformat()}
        if path == '/v2/positions':
            return list(self.positions)
        if path == '/v2/orders:by_client_order_id':
            return self.orders[kwargs['params']['client_order_id']]
        if path.startswith('/v2/orders/'):
            return next(o for o in self.orders.values() if o['id']==path.rsplit('/',1)[1])
        raise AssertionError(path)

    def buy(self, *, symbol, shares, reference_price, request_id, candidate):
        client = assert_dispatch(request_id, symbol, shares)
        parent = {'id':str(uuid.uuid4()),'client_order_id':client,'symbol':symbol,
                  'side':'buy','qty':str(shares),'filled_qty':str(shares),
                  'status':'filled','order_class':'bracket','type':'market','time_in_force':'gtc','filled_avg_price':'45.50','filled_at':self.stamp(),'legs':[
                      {'id':str(uuid.uuid4()),'symbol':symbol,'side':'sell','qty':str(shares),
                       'filled_qty':'0','status':'new','type':kind}
                      for kind in ('limit','stop')]}
        self.orders[client] = parent
        self.positions.append({'symbol':symbol,'qty':str(shares)})
        return {'success':True,'risk':{'approved':True}}

    def sell(self, *, symbol, shares, client_order_id, expected_total_shares, owned_order_ids, expected_entry_order_id):
        self.assertEqual(shares, expected_total_shares)
        self.assertEqual(len(owned_order_ids), 3)
        self.orders[client_order_id] = {'id':str(uuid.uuid4()),'client_order_id':client_order_id,
            'symbol':symbol,'side':'sell','qty':str(shares),'filled_qty':str(shares),'status':'filled','type':'market','time_in_force':'day','filled_avg_price':'46','filled_at':self.stamp()}
        self.positions = [p for p in self.positions if p['symbol']!=symbol]

    def submit(self, **kwargs):
        return submit_entry(self.request_id, self.symbol, context=self.context,
                            clock=lambda:self.now, **kwargs)

    def tick(self):
        return worker_tick(context=self.context, clock=lambda:self.now)

    def test_whole_share_floor_and_unavailable_grade_can_be_owner_requested(self):
        result = self.submit()
        self.assertEqual(result['shares'], 43)
        self.assertLessEqual(result['shares']*45.5, 2000)
        self.assertEqual(result['state'], 'open')
        self.assertTrue(result['order_submitted'])
        self.assertEqual(self.context.submit.call_count, 1)

    def test_status_explains_existing_order_blockers_without_broker_requests(self):
        self.submit()
        _save(self.request_id,state='attention',reason='Protective order evidence incomplete.')
        before=self.context.request.call_count;sent=self.context.submit.call_count
        result=status(enabled=True)
        self.assertEqual(result['entry_blockers'][0]['symbol'],self.symbol)
        self.assertEqual(result['entry_blockers'][0]['scope'],'account')
        self.assertEqual(self.context.request.call_count,before)
        self.assertEqual(self.context.submit.call_count,sent)

    def test_repeat_and_lost_confirmation_never_repeat_buy(self):
        original = self.context.submit.side_effect
        def lost(**kwargs):
            original(**kwargs)
            raise TimeoutError('synthetic lost response')
        self.context.submit.side_effect = lost
        first = self.submit()
        second = self.submit()
        self.assertEqual(first, second)
        self.assertEqual(self.context.submit.call_count, 1)

    def test_unknown_buy_response_remains_uncertain_without_resubmission(self):
        self.context.submit.side_effect = TimeoutError('synthetic')
        self.assertEqual(self.submit()['state'], 'uncertain')
        self.assertIsNone(self.submit()['order_submitted'])
        self.tick()
        self.assertEqual(self.context.submit.call_count, 1)
        self.context.close.assert_not_called()

    def test_closed_market_is_not_a_queued_buy(self):
        self.market_open = False
        self.assertEqual(self.submit()['state'], 'blocked')
        self.market_open = True
        self.assertEqual(self.submit()['state'], 'blocked')
        self.context.submit.assert_not_called()

    def test_expensive_stock_never_creates_fractional_shares(self):
        self.context.quote.side_effect = lambda s:{'symbol':s,'ask':'2000.01','timestamp':self.stamp()}
        self.assertEqual(self.submit()['state'], 'blocked')
        self.context.submit.assert_not_called()

    def test_stale_quote_blocks_entry(self):
        self.context.quote.side_effect = lambda s:{'symbol':s,'ask':'10','timestamp':datetime.fromtimestamp(self.now-31,timezone.utc).isoformat()}
        self.assertEqual(self.submit()['state'], 'blocked')
        self.context.submit.assert_not_called()

    def test_owner_selection_buys_without_scanner_coverage(self):
        self.context.scan.side_effect = RuntimeError('No scanner history')
        self.assertEqual(self.submit()['state'], 'open')
        self.context.scan.assert_not_called()
        self.assertEqual(self.context.submit.call_args.kwargs['candidate']['signal'],'OWNER_SUBMITTED')

    def test_d_grade_is_informational_and_does_not_veto_owner_buy(self):
        self.request_id = str(uuid.uuid4())
        review = submit_manual_signal({'symbol':self.symbol,'request_id':self.request_id},
            quote_fetcher=lambda s:None, clock=lambda:self.now,
            analyzer=lambda s:{'symbol':s,'signal':'SELL','score':-3,'confidence':80,
                'indicators':{'sma_20':10,'sma_50':11,'rsi':30,'five_day_momentum':-5,'volume_ratio':1},'reason':[]})
        self.assertEqual(review['signal']['entry_grade']['grade'],'D')
        self.assertEqual(self.submit()['state'],'open')
        self.context.submit.assert_called_once()

    def test_specific_risk_rejection_is_visible(self):
        self.context.risk.return_value={'approved':False,'error':'Available buying power is too low.'}
        self.assertEqual(self.submit()['reason'],'Available buying power is too low.')
        self.context.submit.assert_not_called()

    def test_worker_heartbeat_is_required_before_buy(self):
        self.now += 61
        self.assertEqual(self.submit()['state'], 'blocked')
        self.context.submit.assert_not_called()

    def test_disabled_or_live_transport_cannot_dispatch(self):
        self.enabled = False
        with self.assertRaises(EntryBlocked):self.submit()
        self.enabled = True
        self.context.paper_base_url = 'https://api.alpaca.markets'
        with self.assertRaises(EntryBlocked):self.submit()
        self.context.submit.assert_not_called()

    def test_existing_symbol_position_is_not_mixed_with_another_buy(self):
        self.positions = [{'symbol':'TEST','qty':'1'}]
        self.assertEqual(self.submit()['state'], 'blocked')
        self.context.submit.assert_not_called()

    def test_normal_risk_limit_can_reduce_the_whole_share_quantity(self):
        self.context.risk.side_effect = [{'approved':False,'max_allowed_shares':42}, {'approved':True}]
        self.assertEqual(self.submit()['shares'], 42)
        self.assertEqual(self.context.submit.call_args.kwargs['shares'], 42)

    def test_risk_rejection_and_expired_preflight_never_submit(self):
        def slow(**kwargs):
            self.now += 46
            return {'approved':True}
        self.context.risk.side_effect = slow
        self.assertEqual(self.submit()['state'], 'blocked')
        self.context.submit.assert_not_called()

    def test_account_change_invalidates_heartbeat(self):
        self.account_id = str(uuid.uuid4())
        self.assertEqual(self.submit()['state'], 'blocked')
        self.context.submit.assert_not_called()

    def test_old_review_cannot_become_a_delayed_buy(self):
        self.now += 121
        self.tick()
        with self.assertRaises(EntryBlocked):self.submit()
        self.context.submit.assert_not_called()

    def test_scanner_sell_closes_once_and_leaves_unrelated_holdings_alone(self):
        self.submit()
        self.positions.append({'symbol':'OLD','qty':'100'})
        self.tick()
        self.context.close.assert_not_called()
        self.scan_signal = 'SELL'
        self.tick()
        self.assertEqual(self.context.close.call_count, 1)
        self.tick()
        self.assertEqual(saved_entries()[self.request_id]['state'], 'closed')
        self.assertEqual(self.context.close.call_count, 1)
        self.assertEqual(self.positions, [{'symbol':'OLD','qty':'100'}])

    def test_uncertain_exit_never_sends_a_second_sell(self):
        self.submit()
        self.scan_signal = 'SELL'
        self.context.close.side_effect = TimeoutError('synthetic')
        self.tick()
        self.tick()
        self.assertEqual(self.context.close.call_count, 1)
        self.assertIsNotNone(_row(self.request_id)['exit_started_at'])

    def test_native_exit_fill_closes_without_an_additional_sell(self):
        result = self.submit()
        parent = self.orders['ptm-'+uuid.UUID(self.request_id).hex]
        parent['legs'][0].update(status='filled', filled_qty=str(result['shares']))
        self.tick()
        self.assertEqual(saved_entries()[self.request_id]['state'], 'closed')
        self.context.close.assert_not_called()

    def test_partial_entry_and_mixed_position_require_attention(self):
        self.submit()
        self.positions[0]['qty'] = '44'
        self.scan_signal = 'SELL'
        self.tick()
        self.assertEqual(saved_entries()[self.request_id]['state'], 'attention')
        self.context.close.assert_not_called()

    def test_foreign_parent_evidence_never_authorizes_sell(self):
        self.submit()
        parent = self.orders['ptm-'+uuid.UUID(self.request_id).hex]
        parent['symbol'] = 'OTHER'
        self.scan_signal = 'SELL'
        self.tick()
        self.context.close.assert_not_called()
        self.assertEqual(saved_entries()[self.request_id]['state'], 'attention')

    def test_exit_risk_failure_keeps_protection_intact(self):
        self.submit()
        self.scan_signal = 'SELL'
        self.context.risk.return_value = {'approved':False}
        self.tick()
        self.context.close.assert_not_called()
        self.assertIsNone(_row(self.request_id)['exit_started_at'])

    def test_active_worker_lease_prevents_concurrent_close(self):
        self.submit()
        self.scan_signal = 'SELL'
        _save(self.request_id, lease_owner='another-worker', lease_until=self.now+60)
        self.tick()
        self.context.close.assert_not_called()

    def test_disposable_schema_validation_preserves_entry_rows(self):
        from validate_paper_database_copy import validate_database_backup
        self.submit()
        before = saved_entries()
        result = validate_database_backup(database.DATABASE_PATH)
        self.assertTrue(result['valid'])
        self.assertTrue(result['existing_values_preserved'])
        self.assertEqual(saved_entries(), before)

    def test_status_requires_fresh_worker_and_never_enables_broad_buys(self):
        result = status(enabled=True, clock=lambda:self.now)
        self.assertTrue(result['scanner_ready'])
        self.assertFalse(result['broad_scanner_buying_enabled'])
        self.now += 61
        self.assertFalse(status(enabled=True,clock=lambda:self.now)['scanner_ready'])

    def journal_context(self):
        database.initialize_database()
        self.context.record_entry = persist_entry_fill
        self.context.record_exit = persist_exit_fills

    def test_lost_buy_response_posts_one_original_account_entry_journal(self):
        self.journal_context()
        original = self.context.submit.side_effect
        def lost(**kwargs):
            original(**kwargs)
            raise TimeoutError('synthetic')
        self.context.submit.side_effect = lost
        self.submit()
        self.submit()
        self.tick()
        with closing(database.get_connection()) as conn:
            rows = conn.execute('SELECT * FROM trade_book').fetchall()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['strategy'],'owner_manual_scanner_v1')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM broker_fills').fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trade_book_events WHERE event='entry'").fetchone()[0],1)

    def test_native_profit_fill_updates_trade_book_without_scanner_sell(self):
        self.journal_context()
        result = self.submit()
        parent = self.orders['ptm-'+uuid.UUID(self.request_id).hex]
        parent['legs'][0].update(status='filled',filled_qty=str(result['shares']),filled_avg_price='46',filled_at=self.stamp())
        self.tick()
        self.context.close.assert_not_called()
        book = database.load_trade_book_entry_by_order_id(parent['id'])
        self.assertEqual(book['status'],'CLOSED')
        self.assertEqual(book['realized_profit_loss'],21.5)
        self.assertEqual(book['strategy'],'owner_manual_scanner_v1')

    def test_scanner_exit_posts_exact_owned_fill_and_closes_journal(self):
        self.journal_context()
        self.submit()
        self.scan_signal = 'SELL'
        self.tick()
        self.tick()
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trade_book WHERE status='CLOSED'").fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM broker_fills').fetchone()[0],2)

    def test_partial_native_profit_then_scanner_exit_sells_only_remaining_shares(self):
        self.journal_context()
        self.submit()
        parent = self.orders['ptm-'+uuid.UUID(self.request_id).hex]
        parent['legs'][0].update(status='partially_filled',filled_qty='20',filled_avg_price='46',filled_at=self.stamp())
        parent['legs'][1]['qty'] = '23'
        self.positions[0]['qty'] = '23'
        self.scan_signal = 'SELL'
        self.tick()
        self.assertEqual(self.context.close.call_args.kwargs['shares'],23)
        self.tick()
        book = database.load_trade_book_entry_by_order_id(parent['id'])
        self.assertEqual(book['status'],'CLOSED')
        self.assertEqual(book['shares'],43)
        self.assertEqual(book['realized_profit_loss'],21.5)

    def test_preexisting_trade_book_records_are_preserved(self):
        self.journal_context()
        old_id = database.create_trade_book_entry(symbol='OLD',shares=2,entry_price=9,
            entry_timestamp=self.stamp(),created_at=self.stamp(),entry_order_id='old-order',strategy='original')
        with closing(database.get_connection()) as conn:
            before = tuple(conn.execute('SELECT * FROM trade_book WHERE id=?',(old_id,)).fetchone())
        self.submit()
        self.scan_signal = 'SELL'
        self.tick()
        self.tick()
        with closing(database.get_connection()) as conn:
            after = tuple(conn.execute('SELECT * FROM trade_book WHERE id=?',(old_id,)).fetchone())
        self.assertEqual(before,after)


    def partial_buy(self, **kwargs):
        self.buy(**kwargs)
        parent = self.orders['ptm-'+uuid.UUID(self.request_id).hex]
        parent.update(status='partially_filled',filled_qty='10')
        self.positions[0]['qty']='10'
        return {'success':True}

    def cancel_partial(self,symbol,owned_order_ids):
        parent = self.orders['ptm-'+uuid.UUID(self.request_id).hex]
        self.assertEqual(symbol,self.symbol)
        self.assertIn(parent['id'],owned_order_ids)
        parent['status']='canceled'
        for leg in parent['legs']:leg['status']='canceled'

    def test_partial_entry_is_canceled_and_only_its_verified_fill_is_flattened(self):
        self.journal_context()
        self.context.submit.side_effect=self.partial_buy
        self.context.cancel=Mock(side_effect=self.cancel_partial)
        result=self.submit()
        self.assertEqual(result['state'],'accepted')
        self.tick()
        self.assertEqual(self.context.close.call_args.kwargs['shares'],10)
        self.tick()
        self.assertEqual(saved_entries()[self.request_id]['state'],'closed')
        self.assertEqual(self.context.close.call_count,1)
        parent=self.orders['ptm-'+uuid.UUID(self.request_id).hex]
        book=database.load_trade_book_entry_by_order_id(parent['id'])
        self.assertEqual(book['shares'],10)
        self.assertEqual(book['status'],'CLOSED')
        self.assertEqual(book['realized_profit_loss'],5)

    def test_unconfirmed_partial_cancel_never_sends_a_guess_quantity_exit(self):
        self.context.submit.side_effect=self.partial_buy
        self.context.cancel=Mock()
        self.submit()
        self.tick()
        self.context.close.assert_not_called()
        self.assertEqual(saved_entries()[self.request_id]['state'],'attention')

    def test_partial_entry_filling_during_cancel_keeps_native_bracket(self):
        self.context.submit.side_effect=self.partial_buy
        def finish(symbol,owned_order_ids):
            parent=self.orders['ptm-'+uuid.UUID(self.request_id).hex]
            parent.update(status='filled',filled_qty=parent['qty'])
            self.positions[0]['qty']=parent['qty']
        self.context.cancel=Mock(side_effect=finish)
        self.submit()
        self.tick()
        self.assertEqual(saved_entries()[self.request_id]['state'],'open')
        self.context.close.assert_not_called()

    def test_buy_completion_does_not_overwrite_a_concurrent_worker_exit(self):
        original=self.context.submit.side_effect
        def concurrent(**kwargs):
            original(**kwargs)
            self.scan_signal='SELL'
            self.tick()
        self.context.submit.side_effect=concurrent
        self.submit()
        self.tick()
        self.assertEqual(self.context.close.call_count,1)
        self.assertEqual(saved_entries()[self.request_id]['state'],'closed')


    def test_partial_entry_missing_completion_time_still_gets_one_safety_exit(self):
        self.journal_context()
        def partial(**kwargs):
            self.partial_buy(**kwargs)
            self.orders['ptm-'+uuid.UUID(self.request_id).hex]['filled_at']=None
            return {'success':True}
        self.context.submit.side_effect=partial
        self.context.cancel=Mock(side_effect=self.cancel_partial)
        self.submit()
        self.tick()
        self.assertEqual(self.context.close.call_count,1)
        self.assertEqual(self.context.close.call_args.kwargs['shares'],10)
        self.tick()
        self.tick()
        self.assertEqual(self.context.close.call_count,1)
        self.assertEqual(saved_entries()[self.request_id]['state'],'attention')
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM trade_book').fetchone()[0],0)


    def test_entry_filling_during_cancel_is_flattened_if_bracket_was_canceled(self):
        self.journal_context()
        self.context.submit.side_effect=self.partial_buy
        def finish_without_protection(symbol,owned_order_ids):
            self.cancel_partial(symbol,owned_order_ids)
            parent=self.orders['ptm-'+uuid.UUID(self.request_id).hex]
            parent.update(status='filled',filled_qty=parent['qty'])
            self.positions[0]['qty']=parent['qty']
        self.context.cancel=Mock(side_effect=finish_without_protection)
        self.submit()
        self.tick()
        self.assertEqual(self.context.close.call_args.kwargs['shares'],43)
        self.tick()
        self.assertEqual(saved_entries()[self.request_id]['state'],'closed')
        self.assertEqual(self.context.close.call_count,1)
