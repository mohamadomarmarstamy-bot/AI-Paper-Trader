import json
import sqlite3
import unittest
import friday_rsi as study


class RsiStudyTests(unittest.TestCase):
    def test_wilder_warmup_threshold_and_future_isolation(self):
        rows=[{'t':i*60,'c':100+i} for i in range(15)]
        def calc(data):
            return study.measure(data,0,901,900)
        self.assertEqual(calc(rows)['rsi'],100)
        self.assertEqual(calc(rows+[{'t':900,'c':1}]),calc(rows))
        self.assertEqual(calc(rows[:7]+rows[8:])['decision'],'missing_or_gapped')
        self.assertEqual(study.measure(rows[:14],0,841,840)['decision'],'insufficient_warmup')
        changes=[11,-9]*7
        prices=[100]
        for change in changes:
            prices.append(prices[-1]+change)
        self.assertEqual(calc([dict(r,c=c) for r,c in zip(rows,prices)])['decision'],'confirm')
        prices[-1]-=.01
        self.assertEqual(calc([dict(r,c=c) for r,c in zip(rows,prices)])['decision'],'filter')

    def test_matched_denominators_and_explicit_missing(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.executescript(study.SCHEMA+'CREATE TABLE friday_trials(chart_id TEXT PRIMARY KEY,outcome TEXT);')
        for i,(value,pnl) in enumerate([(60,3),(54,4),(20,-5),(35,0),(90,None),(None,3)]):
            chart={'bar_as_of':900}
            if value is not None:
                chart['friday_rsi']={'rule':study.RULE,'rsi':value,'decision':'confirm' if value>=55 else 'filter','bars':15,'as_of':900}
            study.freeze(db,str(i),901,chart)
            db.execute('INSERT INTO friday_trials VALUES(?,?)',(str(i),None if pnl is None else json.dumps({'scenarios':[{'bps_per_side':10,'modeled_pnl_usd':pnl}]})))
            study.freeze(db,str(i),999,{'bar_as_of':None})
        r=study.summary(db)
        self.assertEqual([r[k] for k in ('admissions','missing','pending','matched','confirmed','blocked_winners','filtered_false_moves','filtered_flat')],[6,1,1,4,1,1,1,1])
        self.assertEqual((r['baseline_net'],r['rsi_net']),(2,3))
