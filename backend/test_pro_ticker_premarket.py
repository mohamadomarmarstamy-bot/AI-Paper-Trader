"""Synthetic premarket orders, calendar boundaries, ownership and native OCO recovery."""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import Mock
import uuid

import database
import test_pro_ticker_manual_scanner as fixtures
from pro_ticker_manual_scanner import _row,_save, saved_entries,status
from pro_ticker_premarket import session,tick_price


class PremarketTests(unittest.TestCase):
    buy=fixtures.ManualScannerTests.buy
    sell=fixtures.ManualScannerTests.sell
    stamp=fixtures.ManualScannerTests.stamp
    review=fixtures.ManualScannerTests.review
    submit=fixtures.ManualScannerTests.submit
    tick=fixtures.ManualScannerTests.tick
    journal_context=fixtures.ManualScannerTests.journal_context

    def setUp(self):
        self.posts=[]
        self.deleted=[]
        self.calendar_open=True
        self.immediate_fill=True
        self.lost_buy=False
        self.lost_exit=False
        self.fill_exit=True
        self.lost_protection=False
        self.fail_cancel=False
        self.pre_enabled=True
        fixtures.ManualScannerTests.setUp(self)
        self.now=datetime(2026,10,27,12,30,tzinfo=timezone.utc).timestamp()
        self.request_id=str(uuid.uuid4())
        self.review(self.request_id)
        self.context.premarket_enabled=lambda:self.pre_enabled
        self.context.quote=Mock(side_effect=lambda symbol:{'symbol':symbol,'bid':'45.49','ask':'45.50','timestamp':self.stamp()})
        self.context.cancel=Mock(side_effect=self.cancel)
        self.market_open=False
        self.tick()

    def broker(self,method,path,**kwargs):
        if method=='GET' and path=='/v2/clock':
            day=datetime.fromtimestamp(self.now,timezone.utc).date().isoformat()
            return {'is_open':self.market_open,'timestamp':self.stamp(),
                'next_open':day+'T09:30:00-04:00','next_close':day+'T16:00:00-04:00'}
        if method=='GET' and path=='/v2/calendar':
            return [{'date':kwargs['params']['start'],'open':'09:30','close':'16:00'}] if self.calendar_open else []
        if method=='GET' and path=='/v2/orders':
            result=[]
            active=('new','accepted','partially_filled','held','pending_new')
            for order in self.orders.values():
                if order['symbol']!=self.symbol:continue
                if order['status'] in active:result.append(order)
                else:result.extend(leg for leg in order.get('legs') or [] if leg['status'] in active)
            return result
        if method=='POST' and path=='/v2/orders':
            body=dict(kwargs['json_body']);self.posts.append(body)
            order={**body,'id':str(uuid.uuid4()),'filled_qty':'0','status':'new',
                'order_class':body.get('order_class','simple'),'filled_avg_price':None,'filled_at':None,'legs':[]}
            if body.get('order_class')=='oco':
                order['limit_price']=body['take_profit']['limit_price']
                order['legs']=[{'id':str(uuid.uuid4()),'symbol':body['symbol'],'side':'sell',
                    'qty':body['qty'],'filled_qty':'0','status':'new','type':'stop',
                    'stop_price':body['stop_loss']['stop_price']}]
            elif body['side']=='buy' and self.immediate_fill:
                order.update(status='filled',filled_qty=body['qty'],filled_avg_price=body['limit_price'],filled_at=self.stamp())
                self.positions.append({'symbol':body['symbol'],'qty':body['qty']})
            elif body['side']=='sell' and self.fill_exit:
                order.update(status='filled',filled_qty=body['qty'],filled_avg_price=body['limit_price'],filled_at=self.stamp())
                self.positions=[p for p in self.positions if p['symbol']!=body['symbol']]
            self.orders[body['client_order_id']]=order
            if (body.get('order_class')=='oco' and self.lost_protection
                    or body['side']=='buy' and self.lost_buy
                    or body['side']=='sell' and body.get('order_class')!='oco' and self.lost_exit):
                raise TimeoutError('Synthetic lost response')
            return order
        if method=='DELETE' and path.startswith('/v2/orders/'):
            self.deleted.append(path.rsplit('/',1)[1])
            if self.fail_cancel:
                raise TimeoutError('Synthetic unconfirmed cancellation')
            for order in self.orders.values():
                if order['id']==self.deleted[-1]:
                    order['status']='canceled'
                    for leg in order.get('legs') or []:leg['status']='canceled'
                for leg in order.get('legs') or []:
                    if leg['id']==self.deleted[-1]:leg['status']='canceled'
            return None
        return fixtures.ManualScannerTests.broker(self,method,path,**kwargs)

    def cancel(self,symbol,owned_order_ids):
        orders=self.broker('GET','/v2/orders')
        if any(order['id'] not in owned_order_ids for order in orders):
            raise ValueError('Unrelated order blocks cancellation.')
        for order in orders:
            self.broker('DELETE','/v2/orders/'+order['id'])

    def parent(self):
        return self.orders['ptm-'+self.request_id.replace('-','')]

    def protection(self):
        return self.orders['ptm-protect-'+self.request_id.replace('-','')]

    def test_buy_is_whole_share_day_limit_with_explicit_extended_flag(self):
        result=self.submit()
        self.assertEqual(result['shares'],43)
        self.assertEqual(result['entry_session'],'premarket')
        self.assertEqual(result['entry_limit'],'45.50')
        self.assertEqual(self.posts[0]['type'],'limit')
        self.assertEqual(self.posts[0]['time_in_force'],'day')
        self.assertTrue(self.posts[0]['extended_hours'])
        self.assertNotIn('order_class',self.posts[0])
        self.context.submit.assert_not_called()
        self.assertEqual(result['protection_mode'],'app_premarket_native_regular')

    def test_lost_buy_and_protection_responses_never_replay_posts(self):
        self.lost_buy=True;self.lost_protection=True
        self.submit();self.submit();self.tick();self.tick()
        self.assertEqual(len(self.posts),2)
        self.assertEqual([p['side'] for p in self.posts],['buy','sell'])
        self.assertEqual(self.posts[1]['order_class'],'oco')
        self.assertNotIn('extended_hours',self.posts[1])
        self.assertEqual(saved_entries()[self.request_id]['state'],'open')

    def test_quote_is_required_even_when_broker_clock_reports_premarket(self):
        self.context.quote.side_effect=lambda symbol:{'symbol':symbol,'bid':'45.49','ask':'45.50','timestamp':'2026-10-26T20:00:00Z'}
        self.assertEqual(self.submit()['state'],'blocked')
        self.assertEqual(self.posts,[])

    def test_disabled_premarket_never_changes_regular_market_policy(self):
        self.pre_enabled=False
        self.assertEqual(self.submit()['state'],'blocked')
        self.assertEqual(self.posts,[])

    def test_holiday_and_weekend_are_not_premarket_sessions(self):
        self.calendar_open=False
        self.assertEqual(self.submit()['state'],'blocked')
        self.now=datetime(2026,10,31,12,30,tzinfo=timezone.utc).timestamp()
        with self.assertRaises(ValueError):session(self.context,lambda:self.now)
        self.assertEqual(self.posts,[])

    def test_before_4am_and_after_hours_do_not_queue_new_buys(self):
        for hour in (7,22):
            self.now=datetime(2026,10,27,hour,30,tzinfo=timezone.utc).timestamp()
            with self.assertRaises(ValueError):session(self.context,lambda:self.now)
        self.assertEqual(self.posts,[])

    def test_early_premarket_requires_real_fresh_data_and_calendar(self):
        self.now=datetime(2026,10,27,8,0,tzinfo=timezone.utc).timestamp()
        self.assertEqual(session(self.context,lambda:self.now)[0],'premarket')

    def test_entry_cutoff_before_regular_open_blocks_late_pending_buy(self):
        self.now=datetime(2026,10,27,13,29,tzinfo=timezone.utc).timestamp()
        with self.assertRaises(ValueError):session(self.context,lambda:self.now,entry=True)
        self.assertEqual(session(self.context,lambda:self.now)[0],'premarket')

    def test_calendar_disagreement_is_not_treated_as_trading_day(self):
        original=self.context.request.side_effect
        def mismatch(method,path,**kwargs):
            result=original(method,path,**kwargs)
            if path=='/v2/calendar':result[0]['open']='10:00'
            return result
        self.context.request.side_effect=mismatch
        self.assertEqual(self.submit()['state'],'blocked')
        self.assertEqual(self.posts,[])

    def test_stale_broker_clock_prevents_premarket_dispatch(self):
        original=self.context.request.side_effect
        def stale(method,path,**kwargs):
            result=original(method,path,**kwargs)
            if path=='/v2/clock':result['timestamp']='2026-10-26T20:00:00Z'
            return result
        self.context.request.side_effect=stale
        self.assertEqual(self.submit()['state'],'blocked')
        self.assertEqual(self.posts,[])

    def test_subdollar_limits_use_legal_ticks_and_size_from_limit(self):
        self.context.quote.side_effect=lambda symbol:{'symbol':symbol,'bid':'.9000','ask':'.90005','timestamp':self.stamp()}
        result=self.submit()
        self.assertEqual(result['entry_limit'],'0.9001')
        self.assertLessEqual(Decimal(result['entry_limit'])*result['shares'],Decimal(2000))
        self.assertEqual(tick_price('45.501',buy=True),Decimal('45.51'))

    def test_unfilled_entry_expires_without_a_replacement_buy(self):
        self.immediate_fill=False
        result=self.submit();self.tick()
        self.assertEqual(result['state'],'accepted')
        self.assertEqual(self.deleted,[])
        self.now+=61;self.tick();self.tick()
        self.assertEqual(self.parent()['status'],'canceled')
        self.assertEqual(saved_entries()[self.request_id]['state'],'closed')
        self.assertEqual(len(self.posts),1)

    def test_partial_entry_is_canceled_before_protecting_exact_filled_quantity(self):
        self.journal_context();self.immediate_fill=False
        self.submit()
        self.parent().update(status='partially_filled',filled_qty='10',filled_avg_price='45.50',filled_at=self.stamp())
        self.positions=[{'symbol':self.symbol,'qty':'10'}]
        self.tick()
        self.assertEqual(self.protection()['qty'],'10')
        self.assertEqual(self.parent()['status'],'canceled')
        book=database.load_trade_book_entry_by_order_id(self.parent()['id'])
        self.assertEqual(book['shares'],10)

    def test_unconfirmed_partial_cancel_does_not_add_protection_or_sell(self):
        self.immediate_fill=False;self.submit()
        self.parent().update(status='partially_filled',filled_qty='10')
        self.positions=[{'symbol':self.symbol,'qty':'10'}];self.fail_cancel=True
        self.tick()
        self.assertEqual(len(self.posts),1)
        self.assertEqual(saved_entries()[self.request_id]['state'],'attention')

    def test_scanner_sell_uses_owned_limit_exit_and_one_post_after_lost_ack(self):
        self.journal_context();self.submit();self.tick()
        self.positions.append({'symbol':'OLD','qty':'100'})
        self.scan_signal='SELL';self.lost_exit=True
        self.tick();self.tick();self.tick()
        self.assertEqual(len(self.posts),3)
        exit_order=self.posts[-1]
        self.assertEqual(exit_order['type'],'limit')
        self.assertTrue(exit_order['extended_hours'])
        self.assertEqual(exit_order['qty'],'43')
        self.assertEqual(self.positions,[{'symbol':'OLD','qty':'100'}])
        self.assertEqual(saved_entries()[self.request_id]['state'],'closed')
        self.assertEqual(database.load_trade_book_entry_by_order_id(self.parent()['id'])['status'],'CLOSED')
        self.context.close.assert_not_called()

    def test_native_regular_protection_fill_closes_journal_without_another_exit(self):
        self.journal_context();self.submit();self.tick()
        self.market_open=True
        self.protection().update(status='filled',filled_qty='43',filled_avg_price='47.32',filled_at=self.stamp())
        self.protection()['legs'][0]['status']='canceled'
        self.positions=[];self.tick()
        self.assertEqual(saved_entries()[self.request_id]['state'],'closed')
        self.assertEqual(len(self.posts),2)

    def test_premarket_software_stop_can_exit_without_scanner_sell(self):
        self.submit();self.tick()
        self.context.quote.side_effect=lambda symbol:{'symbol':symbol,'bid':'44','ask':'44.01','timestamp':self.stamp()}
        self.tick();self.tick()
        self.assertEqual(saved_entries()[self.request_id]['state'],'closed')
        self.assertEqual(len(self.posts),3)
        self.assertEqual(self.scan_signal,'HOLD')

    def test_foreign_orders_or_mixed_holdings_prevent_owned_cancellation(self):
        self.submit();self.tick();self.scan_signal='SELL'
        self.positions[0]['qty']='44'
        self.tick()
        self.context.cancel.assert_not_called()
        self.assertEqual(len(self.posts),2)
        self.assertEqual(saved_entries()[self.request_id]['state'],'attention')

    def test_unknown_protective_response_is_read_only_recovered(self):
        self.submit()
        original=self.context.request.side_effect
        def unknown(method,path,**kwargs):
            if method=='POST' and kwargs.get('json_body',{}).get('order_class')=='oco':
                self.posts.append(kwargs['json_body'])
                raise TimeoutError('Synthetic unknown acknowledgement')
            return original(method,path,**kwargs)
        self.context.request.side_effect=unknown
        self.tick();self.tick();self.tick()
        self.assertEqual(len(self.posts),2)
        self.assertEqual(saved_entries()[self.request_id]['state'],'attention')

    def test_status_distinguishes_premarket_from_regular_only_mode(self):
        result=status(enabled=True,premarket_enabled=True,clock=lambda:self.now)
        self.assertTrue(result['premarket_enabled'])
        self.assertFalse(result['regular_session_only'])
        self.assertFalse(result['broad_scanner_buying_enabled'])

    def test_regular_only_existing_row_keeps_its_native_bracket_contract(self):
        self.market_open=True
        result=self.submit()
        self.assertEqual(result['entry_session'],'regular')
        self.context.submit.assert_called_once()
        self.assertEqual(len(self.posts),0)


    def test_existing_regular_entry_can_exit_on_premarket_scanner_sell(self):
        self.market_open=True;self.submit()
        self.parent()['legs'][0]['limit_price']='47.32'
        self.parent()['legs'][1]['stop_price']='44.59'
        self.market_open=False;self.scan_signal='SELL'
        self.tick();self.tick()
        self.assertEqual(len(self.posts),1)
        self.assertEqual(self.posts[0]['side'],'sell')
        self.assertEqual(self.posts[0]['type'],'limit')
        self.assertEqual(saved_entries()[self.request_id]['state'],'closed')
        self.assertEqual(len(self.deleted),2)

    def test_foreign_order_is_detected_before_any_owned_order_is_canceled(self):
        self.submit();self.tick();self.scan_signal='SELL'
        self.orders['foreign']={'id':str(uuid.uuid4()),'symbol':self.symbol,
            'status':'new','side':'buy','qty':'1','filled_qty':'0','legs':[]}
        self.tick();self.tick()
        self.assertEqual(self.deleted,[])
        self.assertEqual(len(self.posts),2)
        self.assertEqual(self.protection()['status'],'new')

    def test_pending_limit_exit_does_not_count_as_a_fill_or_replay(self):
        self.journal_context();self.submit();self.tick()
        self.fill_exit=False;self.scan_signal='SELL';self.tick();self.tick();self.tick()
        self.assertEqual(saved_entries()[self.request_id]['state'],'closing')
        self.assertEqual(len(self.posts),3)
        self.assertEqual(database.load_trade_book_entry_by_order_id(self.parent()['id'])['status'],'OPEN')

    def test_dst_session_uses_eastern_calendar_and_broker_next_open(self):
        self.now=datetime(2026,11,2,13,30,tzinfo=timezone.utc).timestamp()
        original=self.context.request.side_effect
        def winter(method,path,**kwargs):
            result=original(method,path,**kwargs)
            if path=='/v2/clock':
                result['next_open']='2026-11-02T09:30:00-05:00'
                result['next_close']='2026-11-02T16:00:00-05:00'
            return result
        self.context.request.side_effect=winter
        self.assertEqual(session(self.context,lambda:self.now)[0],'premarket')

    def test_native_protection_mismatched_identity_never_authorizes_new_exit(self):
        self.submit();self.tick();self.scan_signal='SELL'
        self.protection()['client_order_id']='foreign'
        self.tick()
        self.context.cancel.assert_not_called()
        self.assertEqual(len(self.posts),2)

    def test_entry_dispatch_rechecks_quote_after_slow_risk_preflight(self):
        def slow(**kwargs):
            self.now+=40
            return {'approved':True}
        self.context.risk.side_effect=slow
        self.assertEqual(self.submit()['state'],'blocked')
        self.assertEqual(self.posts,[])

    def test_invalid_protection_settings_block_before_buy(self):
        self.context.stop_loss_percent=100
        self.assertEqual(self.submit()['state'],'blocked')
        self.assertEqual(self.posts,[])
