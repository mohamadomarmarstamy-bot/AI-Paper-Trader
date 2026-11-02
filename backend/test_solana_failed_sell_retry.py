import unittest
from solana_real_worker import schedule_failed_sell_retry

class FailedSellRetryTests(unittest.TestCase):
    def test_two_fast_retries_then_existing_backoff(self):
        p={'last_exit_attempt_at':99};s={'positions':{'p':p}};o={'side':'sell','position_id':'p'}
        schedule_failed_sell_retry(s,o,100)
        self.assertEqual(p['last_exit_attempt_at'],75)
        self.assertLess(104-p['last_exit_attempt_at'],30)
        self.assertEqual(105-p['last_exit_attempt_at'],30)
        schedule_failed_sell_retry(s,o,120);self.assertEqual(p['last_exit_attempt_at'],95)
        p['last_exit_attempt_at']=130
        schedule_failed_sell_retry(s,o,140);self.assertEqual(p['last_exit_attempt_at'],130)
    def test_buy_or_missing_holding_does_not_change_retry(self):
        p={'last_exit_attempt_at':99};s={'positions':{'p':p}}
        schedule_failed_sell_retry(s,{'side':'buy','position_id':'p'},100)
        schedule_failed_sell_retry(s,{'side':'sell','position_id':'missing'},100)
        self.assertEqual(p,{'last_exit_attempt_at':99})
