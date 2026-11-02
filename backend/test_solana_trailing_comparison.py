from contextlib import closing
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import database
import solana_copy_trader as paper
import solana_trailing_comparison as trial

class TrailingComparisonTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(tmp.name)/'paper.db');p.start();self.addCleanup(p.stop)
        self.config=deepcopy(paper.DEFAULTS)
        self.position={'id':'entry','wallet':'wallet','mint':'mint','pool':'pool','symbol':'TEST','quantity':100.,'cost_usd':100.,'entry_price_usd':1.,'opened_at':1000,'source_at':999,'latency_seconds':1,'tx_hash':'source'}
    def quote(self,price,at):return {('mint','pool'):{'price':price,'at':at,'pool':'pool','symbol':'TEST'}}
    def advance(self,actions,now):
        with closing(database.get_connection()) as c:trial.advance(c,actions,self.config,now);c.commit()
    def entry(self):self.advance([('entry',self.position)],1000)
    def mark(self,price,at):self.advance([('mark',self.quote(price,at),at,True)],at)
    def state(self):
        with closing(database.get_connection()) as c:return trial.read(c)
    def test_same_entries_no_historical_holdings_import(self):
        self.assertFalse(trial.status(1000)['started']);self.entry()
        s=self.state();self.assertEqual(s['entry_count'],1)
        self.assertEqual(s['arms']['baseline']['positions'],s['arms']['trailing_20']['positions'])
        self.assertNotIn('trailing_peak_price',self.position)
    def test_no_trail_before_first_profit_sale(self):
        self.entry();self.mark(1.15,1001);self.mark(.9,1002)
        for arm in self.state()['arms'].values():self.assertEqual(len(arm['positions']),1)
    def test_trail_exits_before_break_even_and_counts_one_position(self):
        self.entry();self.mark(1.3,1001);self.mark(1.7,1002);self.mark(1.3,1003)
        s=trial.status(1003)
        self.assertEqual(s['arms']['trailing_20']['completed_positions'],1)
        self.assertEqual(s['arms']['baseline']['completed_positions'],0)
        self.assertEqual(s['arms']['trailing_20']['recent_exits'][-1]['reason'],'trailing_profit_protection')
        self.assertEqual(s['arms']['trailing_20']['winning_completed_positions'],1)
        self.mark(2.,1004);self.assertEqual(trial.status(1004)['arms']['trailing_20']['open_holdings'],0)
    def test_break_even_floor_and_missing_quote(self):
        self.entry();self.mark(1.3,1001)
        self.advance([('mark',{},1002,True)],1002)
        self.assertEqual(trial.status(1002)['arms']['trailing_20']['unpriced_holdings'],1)
        self.mark(1.0,1003)
        self.assertEqual(trial.status(1003)['arms']['trailing_20']['completed_positions'],1)
    def test_source_sell_applies_to_both(self):
        self.entry();self.advance([('sell',{'id':'sale','wallet':'wallet','mint':'mint','source_at':1001}),('mark',self.quote(1.1,1002),1002,True)],1002)
        for arm in self.state()['arms'].values():self.assertEqual(arm['closed'][-1]['reason'],'trader_sell')
    def test_restart_preserves_peak_and_no_stale_price_fill(self):
        self.entry();self.mark(1.3,1001);self.mark(1.7,1002)
        self.advance([('mark',self.quote(1.,900),1003,True)],1003)
        self.assertEqual(self.state()['arms']['trailing_20']['positions']['entry']['trailing_peak_price'],1.7)
        self.assertEqual(trial.status(1003)['arms']['trailing_20']['open_holdings'],1)
    def test_savepoint_failure_preserves_primary_transaction(self):
        with closing(database.get_connection()) as c:
            paper.initialize(c);c.execute('BEGIN IMMEDIATE');c.execute('UPDATE solana_copy_state SET revision=5')
            c.execute('SAVEPOINT shadow')
            try:trial.advance(c,[('entry',{'id':'bad'})],self.config,1000)
            except KeyError:c.execute('ROLLBACK TO shadow');c.execute('RELEASE shadow')
            c.commit();self.assertEqual(c.execute('SELECT revision FROM solana_copy_state').fetchone()[0],5)

if __name__=='__main__':unittest.main()
