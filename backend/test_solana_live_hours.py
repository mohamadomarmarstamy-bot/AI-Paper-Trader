from datetime import datetime
import unittest
from zoneinfo import ZoneInfo
from solana_live_hours import entry_allowed

class HoursTests(unittest.TestCase):
    def test_boundaries_and_dst(self):
        for day in ('2026-10-31','2026-11-01','2027-03-14'):
            for hour,allowed in ((0,True),(13,True),(14,False),(23,False)):
                at=datetime.fromisoformat(f'{day}T{hour:02}:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()
                self.assertEqual(entry_allowed(at),allowed,(day,hour))

    def test_only_mr_frog_has_all_day_exception(self):
        frog='4DdrfiDHpmx55i4SPssxVzS9ZaKLb8qr45NKY9Er9nNh'
        for hour in (0,13,14,23):
            at=datetime(2026,11,2,hour,tzinfo=ZoneInfo('America/New_York')).timestamp()
            self.assertTrue(entry_allowed(at, frog))
            self.assertEqual(entry_allowed(at, 'another-wallet'), hour<14)
