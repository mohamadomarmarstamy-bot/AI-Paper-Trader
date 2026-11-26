import json
import sqlite3
import unittest

import jarvis_sell_quote_study as study


class SellQuoteStudyTests(unittest.TestCase):
    def setUp(self):
        self.data=study.queued()
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE run(id INTEGER, state TEXT)')

    def entry(self,mint):
        p={'mint':mint,'quantity':200,'opened':20,'pair':'pool','entry_cost':1000000,'policy':{'exit':'step'}}
        study.enter(self.data,p)
        return p

    def report(self):
        self.db.execute('DELETE FROM run')
        self.db.execute('INSERT INTO run VALUES(1,?)',(json.dumps({'sell_quote_study':self.data}),))
        return study.report(self.db)

    def test_matched_arms_and_missing_observations(self):
        for mint,kind,at,size in [('a','usable',19,200),('b','no_route',19,200),('c','temporary',19,200),
                                 ('d','usable',19,199),('e','usable',20,200),('f','usable',21,200),('g','usable',4,200)]:
            study.observe_quote(self.data,mint,size,at,kind)
            p=self.entry(mint)
            study.exit_failure(self.data,'jarvis',p,'no_route')
            study.settle(self.data,[dict(p,bot='jarvis',closed=30,pnl=-500000 if mint=='a' else 2000000)])
        self.assertEqual(self.data['samples']['e']['evidence'],'missing')
        study.observe_quote(self.data,'d',200,40,'usable')
        self.entry('d')
        report=self.report()
        self.assertEqual((report['matched'],report['missing'],report['unknown'],report['unavailable']),(2,4,5,1))
        self.assertEqual(report['blocked_winners'],1)
        self.assertEqual(report['arms']['baseline']['net_usd'],1.5)
        self.assertEqual(report['arms']['challenger']['net_usd'],-.5)
        self.assertEqual(report['arms']['baseline']['realized_drawdown_usd'],.5)
        self.assertEqual(report['arms']['challenger']['no_route_attempts'],1)

    def test_bounded_deduplicated_suggestion_and_cohort(self):
        anchor={'pair':'p','quantity':200,'price':1,'amount':100,'at':10}
        self.assertTrue(study.diverges(anchor,{'pair':'p','price_usd':1},200,75,20))
        self.assertFalse(study.diverges(anchor,{'pair':'p','price_usd':1},199,75,20))
        for at,mint in enumerate(['a','a','b','c','d']):
            study.observe_quote(self.data,mint,200,at,'usable',True)
        self.assertEqual(self.data['proposal']['at'],3)
        for i in range(120):self.entry(str(i))
        self.assertEqual(len(self.data['samples']),100)
