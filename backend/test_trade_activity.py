import unittest
from trade_activity import build_activity, execution_rows


def fill(key, side, qty, price, time='2026-10-26T15:00:00Z', symbol='ABC'):
    return dict(order_id=key, symbol=symbol, side=side, shares=qty, price=price, filled_at=time)


def order(key, side, qty, price, **extra):
    return dict(id=key, symbol='ABC', side=side, filled_qty=qty, filled_avg_price=price,
                filled_at='2026-10-26T15:01:00Z', **extra)


class ActivityTests(unittest.TestCase):
    def test_nested_sell_leg_and_duplicate_are_actual_price(self):
        buy = order('b', 'buy', '10', '2', status='filled', legs=[order('s','sell','10','2.25',status='filled')])
        data = build_activity([fill('b','BUY',10,2)], [buy, buy['legs'][0]])
        self.assertEqual(len(data['buys']), 1)
        self.assertEqual(len(data['sells']), 1)
        self.assertEqual(data['sells'][0]['price'], 2.25)
        self.assertEqual(data['completed'][0]['profit_loss'], 2.5)

    def test_blocked_unfilled_and_planned_prices_never_become_notifications(self):
        data=build_activity([], [order('b','buy',0,None,status='rejected',limit_price='5'),
             dict(id='idea',symbol='ABC',side='buy',qty='100',price=5),
             order('nan','buy',2,'NaN'),order('bool','buy',True,2)])
        self.assertEqual(data['buys'], [])
        self.assertEqual(data['completed'], [])

    def test_partial_fill_canceled_order_is_real_execution_and_does_not_shrink(self):
        data=build_activity([fill('b','BUY',7,2)], [order('b','buy',3,2,status='canceled')])
        self.assertEqual(data['buys'][0]['shares'],7)
        data=build_activity([], [order('b','buy',3,2,status='canceled')])
        self.assertEqual(data['buys'][0]['shares'],3)
        self.assertEqual(data['completed'],[])

    def test_fifo_completed_only_weighted_prices_and_open_lots_excluded(self):
        fills=[fill('b1','BUY',10,2),fill('b2','BUY',10,4,'2026-10-26T15:01:00Z'),
               fill('s1','SELL',5,3,'2026-10-26T15:02:00Z'),fill('s2','SELL',10,5,'2026-10-26T15:03:00Z')]
        data=build_activity(fills,[])
        g=data['completed'][0]
        self.assertEqual(g['shares'],10)
        self.assertEqual(g['buy_price'],2)
        self.assertEqual(g['sell_price'],4)
        self.assertEqual(len(g['trades'][0]['exits']),2)
        fills.append(fill('s3','SELL',5,7,'2026-10-26T15:04:00Z'))
        g=build_activity(fills,[])['completed'][0]
        self.assertEqual(g['shares'],20)
        self.assertEqual(g['buy_price'],3)
        self.assertEqual(g['sell_price'],5)
        self.assertEqual(g['profit_loss'],40)

    def test_unmatched_older_sell_does_not_pair_future_buy(self):
        data=build_activity([fill('s','SELL',5,3),fill('b','BUY',5,2,'2026-10-26T15:01:00Z')],[])
        self.assertEqual(data['completed'],[])
        self.assertEqual(data['unmatched_sells'][0]['shares'],5)

    def test_timezone_offsets_and_missing_timestamps(self):
        rows=execution_rows([fill('b','BUY',1,2,'2026-10-26T10:00:00-04:00'),fill('s','SELL',1,3,'2026-10-26T14:01:00Z'),fill('bad','BUY',1,3,'bad')],[])
        self.assertEqual([r['order_id'] for r in rows],['b','s'])
        self.assertEqual(rows[0]['filled_at'],'2026-10-26T14:00:00+00:00')

    def test_unavailable_broker_saved_prices_and_history_limit_explicit(self):
        data=build_activity([fill('b','BUY',1,.1234)],[],broker_available=False,ledger_limit=1)
        self.assertFalse(data['broker_available'])
        self.assertTrue(data['ledger_limit_reached'])
        self.assertEqual(data['buys'][0]['price'],.1234)
