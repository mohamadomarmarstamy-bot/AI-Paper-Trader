from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from scripts.friday_watchlist_pilot import record


class PilotTests(unittest.TestCase):
    def test_manual_fixture_replay_and_unknown_delay(self):
        with TemporaryDirectory() as d:
            args = dict(path=Path(d)/'pilot.db',event='fixture',symbol='AAPL',allowed={'AAPL'},
                        first_seen='2026-11-01T10:00:12Z',clock=datetime(2026,11,1,11,tzinfo=timezone.utc))
            self.assertIsNone(record(**args)['delay_seconds'])
            self.assertEqual(record(**args,published='2026-11-01T10:00:00Z')['delay_seconds'],12)
            self.assertEqual(record(**{**args,'first_seen':'2026-11-01T10:05:00Z'})['delay_seconds'],12)
            for symbol in ('aapl','AAPL;DROP','MSFT'):
                with self.assertRaises(ValueError):
                    record(**{**args,'symbol':symbol})
