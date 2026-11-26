from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from friday_research import capture, classify, latest, record_authorized_alert

NOW = 1800000000
STAMP = datetime.fromtimestamp(NOW, timezone.utc).isoformat()
ROW = dict(snapshot_trade_at=STAMP, snapshot_quote_at=STAMP, feed='iex',
           above_session_vwap=True, percent_change=4, spread_percent=0.1,
           session_dollar_volume=7000000)
SCAN = (NOW, 5000, ('NVDA', 'MSFT', 'AMD', 'MISS'), dict.fromkeys(('NVDA','MSFT','AMD'), ROW))


class FridayTests(unittest.TestCase):
    def test_atomic_capped_replay(self):
        with TemporaryDirectory() as d:
            path = Path(d) / 'friday.db'
            with ThreadPoolExecutor(4) as pool:
                outcomes = list(pool.map(lambda _: capture(SCAN, path=path, clock=lambda: NOW), range(8)))
            self.assertEqual(outcomes.count('Stock scan recorded.'), 1)
            report = latest(path)
            self.assertEqual([r['symbol'] for r in report['candidates']], ['AMD', 'MSFT'])
            self.assertEqual(sum(r['count'] for r in report['reasons']), 4)

    def test_evidence_gate(self):
        for age in (-1, 91):
            self.assertEqual(classify('AMD', ROW, NOW+age), 'stale_or_invalid_time')
        self.assertEqual(classify('AMD', {**ROW,'percent_change':16}, NOW), 'overextended')
        self.assertEqual(classify('AMD', {}, NOW), 'missing_snapshot')

    def test_authorized_adapter_preserves_late_observation(self):
        with TemporaryDirectory() as d:
            args = dict(path=Path(d)/'friday.db', event_id='one', symbol='MSFT',
                        published_at=STAMP, received_at=STAMP, scan=SCAN)
            self.assertEqual(record_authorized_alert(**args, clock=lambda: NOW+91), 'late_observation')
            with self.assertRaises(ValueError):
                record_authorized_alert(**{**args, 'symbol':'AMD'}, clock=lambda: NOW+92)
