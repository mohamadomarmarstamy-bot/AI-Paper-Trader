import unittest
from solana_real_worker import trailing_profit_trigger
from test_solana_real_worker import RealWorkerTests
import solana_real_worker as live
from contextlib import closing
import database, json

class TrailingTests(unittest.TestCase):
    def test_untrimmed_holding_has_no_trail(self):
        p={'stages':0,'quantity_raw':100,'cost_lamports':1000}
        self.assertFalse(trailing_profit_trigger(p,2000,10))
        self.assertNotIn('trailing_peak_lamports_per_raw',p)
    def test_rising_peak_and_twenty_percent_drop(self):
        p={'stages':1,'quantity_raw':100,'cost_lamports':1000}
        self.assertFalse(trailing_profit_trigger(p,1990,10))
        self.assertFalse(trailing_profit_trigger(p,2490,10))
        self.assertFalse(trailing_profit_trigger(p,2000,10))
        self.assertTrue(trailing_profit_trigger(p,1990,10))
        self.assertEqual(p['trailing_floor_lamports'],1990)
    def test_half_sale_does_not_fake_drawdown(self):
        p={'stages':1,'quantity_raw':100,'cost_lamports':1000}
        trailing_profit_trigger(p,1990,10)
        p.update(quantity_raw=50,cost_lamports=500,stages=2)
        self.assertFalse(trailing_profit_trigger(p,990,10))
        self.assertTrue(trailing_profit_trigger(p,790,10))
    def test_cost_floor_still_applies(self):
        p={'stages':1,'quantity_raw':100,'cost_lamports':1000}
        self.assertTrue(trailing_profit_trigger(p,999,10))

class TrailingWorkerTests(RealWorkerTests):
    def test_current_quote_triggers_full_runner_exit(self):
        self.enter()
        state,_=live.load();p=next(iter(state['positions'].values()))
        p.update(stages=1,next_gain_percent=1000)
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE solana_real_run SET state_json=? WHERE id=1',(json.dumps(state),));conn.commit()
        self.chain.exit_value=400_105_000;live.tick(self.chain)
        self.assertFalse(live.orders(True))
        self.chain.exit_value=319_105_000;live.tick(self.chain)
        order=live.orders(True)[0]
        self.assertEqual(order['reason'],'profit_protection')
        self.assertEqual(order['quantity'],10_000_000)
