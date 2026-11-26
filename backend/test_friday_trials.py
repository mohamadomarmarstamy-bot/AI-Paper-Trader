from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from day_trader_lab import SCHEMA
from day_trader_strategy import evaluate, simulate
from friday_research import capture, record_authorized_alert
from friday_trials import advance, entry_plan, status
from test_day_trader_strategy import CLOSE, NOW, OPEN, bar, fixture, iso
from test_friday_research import ROW


class FridayTrials(unittest.TestCase):
    def setUp(self):
        d = self.enterContext(tempfile.TemporaryDirectory())
        self.source, self.target = Path(d)/'stock.db', Path(d)/'friday.db'
        self.raw, self.quote = fixture()
        self.chart = evaluate(self.raw,self.quote,session_open=OPEN,session_close=CLOSE,
                              observed_at=NOW,opening_volumes=[500]*14)
        with closing(sqlite3.connect(self.source)) as db, db:
            db.executescript(SCHEMA)
            db.execute('INSERT INTO day_trader_experiments VALUES(?,?,?,?,NULL)',
                       ('chart-1','AMD',NOW,json.dumps(self.chart)))
        row = {**ROW,'snapshot_quote_at':iso(NOW),'snapshot_trade_at':iso(NOW)}
        capture((NOW,2000,['AMD'],{'AMD':row}),path=self.target,clock=lambda:NOW)

    def tick(self,t):
        return advance(source=self.source,target=self.target,clock=lambda:t)

    def test_parallel_entry_exit_priority_and_matched_costs(self):
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(self.tick,[NOW]*8))
        self.assertEqual(len(status(self.target)['positions']),1)
        plan = self.chart['setups'][0]
        due = plan['expires_at']+60
        self.assertEqual(self.tick(due),'due_exit_waiting_for_complete_forward_path')
        path = [bar(t,o=10.15,h=10.19,l=10.05,c=10.17) for t in range((int(NOW//60)+1)*60,int(plan['expires_at']),60)]
        path[0].update(o=9.50,l=9.40)
        result = simulate(plan,path,frozen_at=NOW,observed_at=due)
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute('UPDATE day_trader_experiments SET outcome_json=?',
                       (json.dumps([{**result,'strategy':plan['strategy']}]),))
        self.tick(due); self.tick(due+1)
        report = status(self.target)
        self.assertFalse(report['positions'])
        self.assertEqual(len(report['history']),1)
        closed = report['history'][0]
        self.assertEqual(closed['phase'],'regular_session')
        self.assertEqual(closed['outcome']['exit_reason'],'gap_below_stop')
        for cost in closed['outcome']['scenarios']:
            self.assertLess(cost['modeled_pnl_usd'],cost['matched_hold_pnl_usd'])

    def test_missing_late_pre_after_calendar_chart_and_cost(self):
        for changes in ({'feed':'sip'}, {'quote_timestamp':iso(NOW+1)}, {'bar_as_of':NOW-90},
                        {'session_close':NOW+300},{'opening_rvol':None},{'session_open':OPEN-3600},
                        {'spread_percent':1}, {'setups':[]}, {'status':'unavailable'}):
            self.assertIsNone(entry_plan({**self.chart,**changes},NOW))
        for t in (NOW-1,NOW+16,OPEN-60,CLOSE+60):
            self.assertIsNone(entry_plan(self.chart,t))
        self.assertFalse(status(self.target)['broker_enabled'])
        self.tick(NOW+16)
        self.assertFalse(status(self.target)['positions'])

    def test_timely_alert_attribution_is_frozen_at_admission(self):
        row = {**ROW,'snapshot_quote_at':iso(NOW),'snapshot_trade_at':iso(NOW)}
        args = dict(event_id='fixture-update',symbol='AMD',published_at=iso(NOW-2),
                    received_at=iso(NOW-1),scan=(NOW,2000,['AMD'],{'AMD':row}),
                    path=self.target,clock=lambda:NOW)
        record_authorized_alert(**args)
        self.tick(NOW)
        self.assertEqual(status(self.target)['positions'][0]['source_group'],'scanner_and_timely_alert')

    def test_no_hindsight_admission(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE day_trader_experiments SET outcome_json='[]'")
        self.tick(NOW)
        self.assertFalse(status(self.target)['positions'])
