import unittest
from solana_real_worker import schedule_failed_sell_retry, exit_attempt_ready

class FailedSellRetryTests(unittest.TestCase):
    def test_rejected_and_finalized_failures_get_two_second_retries(self):
        for status in ('rejected','failed'):
            p={'last_exit_attempt_at':99};s={'positions':{'p':p}}
            o={'side':'sell','position_id':'p','status':status}
            schedule_failed_sell_retry(s,o,100)
            self.assertFalse(exit_attempt_ready(p,'stop_loss',101))
            self.assertTrue(exit_attempt_ready(p,'stop_loss',102))
            p['last_exit_attempt_at']=102
            schedule_failed_sell_retry(s,o,103)
            self.assertEqual(p['exit_retry_at'],105)
            p['last_exit_attempt_at']=105
            schedule_failed_sell_retry(s,o,106)
            self.assertNotIn('exit_retry_at',p)
            self.assertFalse(exit_attempt_ready(p,'stop_loss',134))
            self.assertTrue(exit_attempt_ready(p,'stop_loss',135))

    def test_uncertain_or_buy_orders_never_enable_new_sell_retry(self):
        for side,status,ident in [('buy','failed','p'),('sell','unknown','p'),('sell','submitted','p'),('sell','signed','p'),('sell','confirmed','p'),('sell','failed','missing')]:
            p={'last_exit_attempt_at':99};s={'positions':{'p':p}}
            schedule_failed_sell_retry(s,{'side':side,'status':status,'position_id':ident},100)
            self.assertEqual(p,{'last_exit_attempt_at':99})

    def test_full_exit_does_not_wait_for_failed_profit_trim(self):
        p={'last_exit_attempt_at':100,'last_exit_reason':'profit_take_half','exit_retry_at':102}
        for reason in ('stop_loss','source_sell','manual','profit_protection'):
            self.assertTrue(exit_attempt_ready(p,reason,100))
        self.assertFalse(exit_attempt_ready(p,'profit_take_half',100))
