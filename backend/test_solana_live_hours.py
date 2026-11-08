from datetime import datetime
import unittest
from zoneinfo import ZoneInfo
from solana_live_hours import entry_allowed, entry_status, ALL_DAY_SOURCES

class HoursTests(unittest.TestCase):
    def test_boundaries_and_dst(self):
        for day in ('2026-10-31','2026-11-01','2027-03-14'):
            for hour,allowed in ((0,True),(13,True),(14,False),(23,False)):
                at=datetime.fromisoformat(f'{day}T{hour:02}:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()
                self.assertEqual(entry_allowed(at),allowed,(day,hour))

    def test_selected_live_roster_has_all_day_exception(self):
        self.assertEqual(len(ALL_DAY_SOURCES),3)
        for hour in (0,13,14,23):
            at=datetime(2026,11,2,hour,tzinfo=ZoneInfo('America/New_York')).timestamp()
            for wallet in ALL_DAY_SOURCES:self.assertTrue(entry_allowed(at, wallet))
            self.assertEqual(entry_allowed(at, 'another-wallet'), hour<14)

    def test_excluded_sources_are_not_promoted_by_schedule(self):
        from solana_live_source_policy import PAPER_ONLY_SOURCES
        self.assertFalse(ALL_DAY_SOURCES.intersection(PAPER_ONLY_SOURCES))
        after_cutoff=datetime(2026,11,2,17,tzinfo=ZoneInfo('America/New_York')).timestamp()
        for wallet in PAPER_ONLY_SOURCES:self.assertFalse(entry_allowed(after_cutoff,wallet))

    def test_status_identifies_exact_all_day_roster(self):
        status=entry_status()
        self.assertEqual(set(status['all_day_source_names']),
                         {'Mr. Frog','Solkrow','Latuche'})
        self.assertEqual(set(status['all_day_sources']),ALL_DAY_SOURCES)
