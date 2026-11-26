"""Synthetic only. Never fetch quotes or send orders."""
import json
import sqlite3
from unittest import TestCase
from friday_replay import record, SCHEMA


class ReplayTests(TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript(SCHEMA)

    def observe(self,t,reason='phase_daily_cap',bid=101,start=900):
        raw={'latestQuote':{'t':'fixture'},'minuteBar':{'t':'fixture'}}
        record(self.db,'after_hours',start,4000,t,(t,{'AMD':raw}),
               {'AMD':({'bid':bid,'ask':bid+.01},t)},{'AMD':(reason,[{'c':100}])},'no_fresh_in_phase_quote_and_trade')

    def test_forward_evidence_cannot_change_decision(self):
        self.observe(1000)
        self.observe(1002,'prospective_probe_opened',400)
        self.assertEqual(self.db.execute('SELECT count(*) FROM friday_replays').fetchone()[0],4)
        self.observe(1060,bid=105)
        reason,outcome,net,evidence=self.db.execute("SELECT reason,outcome,net,evidence FROM friday_replays WHERE symbol='AMD' AND decision_at=1000").fetchone()
        self.assertEqual((reason,outcome),('phase_daily_cap','missed_qualified_move'))
        self.assertGreater(net,0)
        self.assertEqual(json.loads(evidence)['decision_quote']['ask'],101.01)
        self.assertEqual(self.db.execute("SELECT reason FROM friday_replays WHERE symbol='MSFT' LIMIT 1").fetchone()[0],'scanner_coverage_gap')

    def test_other_phase_and_missing_horizon(self):
        self.observe(1000)
        self.observe(1060,start=1020)
        self.observe(1080)
        self.assertEqual(self.db.execute("SELECT outcome,net FROM friday_replays WHERE decision_at=1000 AND symbol='AMD'").fetchone(),('unmeasurable_path',None))

    def test_failed_gate_never_becomes_qualified_miss(self):
        self.observe(1000,'phase_momentum_or_liquidity_skip')
        self.observe(1060,bid=120)
        self.assertEqual(self.db.execute("SELECT reason,outcome FROM friday_replays WHERE decision_at=1000 AND symbol='AMD'").fetchone(),('phase_momentum_or_liquidity_skip','measured'))
