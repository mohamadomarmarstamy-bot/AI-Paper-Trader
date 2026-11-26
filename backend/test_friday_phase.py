from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest
from zoneinfo import ZoneInfo

from friday_phase import tick, window
from friday_paper_boundary import preview,reconcile_fixture

ET = ZoneInfo('America/New_York')


class PhaseTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory()))/'phase.db'

    def examine(self, day, clock, close='16:00', stale=False):
        now = datetime.fromisoformat(day+'T'+clock).replace(tzinfo=ET).timestamp()
        calendar = {'date':day,'open':'09:30','close':close}
        for i in range(3):
            at = now+i*60
            def stamp(t):
                return datetime.fromtimestamp(t,ET).isoformat()
            price = 100+i*.20
            snap = {'latestTrade':{'t':stamp(at),'p':price},
                    'latestQuote':{'t':stamp(at-20 if stale else at),'bp':price-.01,'ap':price+.01},
                    'minuteBar':{'t':stamp(at-60),'o':price-.05,'h':price+.05,'l':price-.1,'c':price,'v':200+i}}
            reason = tick((at,{'AMD':snap}),calendar,now=at,path=self.path)
        return reason,at,snap,calendar

    def test_normal_after_hours_owned_exit_and_contract(self):
        reason,at,snap,cal=self.examine('2026-11-25','16:01')
        self.assertEqual(reason,'prospective_probe_opened')
        for k in ('latestTrade','latestQuote'):
            snap[k]['t']=datetime.fromtimestamp(at+60,ET).isoformat()
        self.assertEqual(tick((at+60,{'AMD':snap}),cal,now=at+60,path=self.path),'owned_probe_measured')
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory=sqlite3.Row
            p=db.execute('SELECT * FROM phase_probes').fetchone()
            self.assertLess(p['net'],0)
            self.assertEqual(p['phase'],'after_hours')
        buy,sell=preview(p,'buy'),preview(p,'sell')
        self.assertFalse(buy['route_enabled'])
        self.assertEqual(buy,preview(p,'buy'))
        reports={o['client_order_id']:{**o,'filled_qty':str(f)} for o,f in [(buy,1),(sell,.5)]}
        self.assertEqual(reconcile_fixture(buy,sell,reports)['owned_quantity'],'0.5')
        reports[sell['client_order_id']]['symbol']='AAPL'
        self.assertFalse(reconcile_fixture(buy,sell,reports)['verified'])

    def test_short_day_premarket_and_missing_exit(self):
        reason,at,snap,cal=self.examine('2026-11-27','08:01','13:00')
        self.assertEqual(reason,'prospective_probe_opened')
        tick(None,cal,now=at+80,path=self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT phase,state,net FROM phase_probes').fetchone(),('pre_market','unmeasured',None))
        self.assertEqual(window(datetime(2026,11,27,13,0,tzinfo=ET).timestamp(),cal)[0],'short_day_post_market_unverified')

    def test_stale_quotes_and_unverified_calendar(self):
        reason,at,snap,cal=self.examine('2026-11-25','08:01',stale=True)
        self.assertEqual(reason,'no_fresh_in_phase_quote_and_trade')
        self.assertEqual(tick((at,{'AMD':snap}),None,now=at,path=self.path),'calendar_unverified')
        self.assertEqual(window(datetime(2026,11,25,7,59,tzinfo=ET).timestamp(),cal)[0],'outside_verified_iex_window')
