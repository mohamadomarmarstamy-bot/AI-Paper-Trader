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
