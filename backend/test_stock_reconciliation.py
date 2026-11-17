from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import database
import stock_auto_learning as learning
import stock_reconciliation as reconciliation

class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'paper.sqlite3');p.start();self.addCleanup(p.stop)
        database.initialize_database()
        self.now=datetime(2026,11,17,18,tzinfo=timezone.utc).timestamp()
        self.trade={'symbol':'TEST','status':'CLOSED','pnl_complete':True,'entry_order_id':'entry-1',
            'entry_timestamp':'2026-11-16T15:00:00Z','exit_timestamp':'2026-11-17T17:00:00Z',
            'entry_shares':10.,'entry_price':5.,'realized_profit_loss':2.,'realized_return_percent':4.,
            'exit_allocations':[{'exit_order_id':'sell-1','match_method':'broker_bracket_leg','shares':4.,'price':5.5,'timestamp':'2026-11-17T16:00:00Z'},
                                {'exit_order_id':'sell-2','match_method':'broker_bracket_leg','shares':6.,'price':5.,'timestamp':'2026-11-17T17:00:00Z'}]}
        with closing(database.get_connection()) as conn:learning.initialize(conn)
    def payload(self,trades=None):
        return {'paper':True,'source':'broker_fills_trade_book_link_then_fifo','summary':{'ledger_limit_reached':False},'trades':trades if trades is not None else [self.trade]}
    def entry(self):
        return database.create_trade_book_entry(symbol='TEST',shares=10,entry_price=5,entry_timestamp=self.trade['entry_timestamp'],created_at=self.trade['entry_timestamp'],entry_order_id='entry-1',entry_reason='auto_trader_entry')
    def test_weighted_exits_repair_stale_open_once_and_exclude_old_close_from_new_learning(self):
        ident=self.entry();result=reconciliation.reconcile(self.payload(),now=self.now)
        self.assertEqual(result['repaired'],1)
        with closing(database.get_connection()) as conn:
            row=conn.execute('SELECT * FROM trade_book WHERE id=?',(ident,)).fetchone()
            self.assertEqual(row['status'],'CLOSED');self.assertAlmostEqual(row['exit_price'],5.2)
            self.assertEqual(row['realized_profit_loss'],2.);self.assertEqual(learning.outcomes(conn,0,25),[])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM stock_reconciliation_audit').fetchone()[0],1)
        result=reconciliation.reconcile(self.payload(),now=self.now+300)
        self.assertEqual(result['unchanged'],1);self.assertEqual(result['repaired'],0)
    def test_missing_linked_entry_imports_without_inventing_features(self):
        self.assertEqual(reconciliation.reconcile(self.payload(),now=self.now)['imported'],1)
        with closing(database.get_connection()) as conn:
            row=conn.execute('SELECT * FROM learning_outcomes').fetchone()
            self.assertIsNone(row['entry_score']);self.assertIsNone(row['scanner_rank']);self.assertEqual(row['realized_profit_loss'],2.)
    def test_incomplete_fifo_conflicting_or_duplicate_evidence_never_closes_trade(self):
        ident=self.entry()
        for change in ({'status':'PARTIAL'},{'pnl_complete':False},{'entry_shares':11},{'entry_timestamp':'2026-11-18T00:00:00Z'},{'realized_profit_loss':100}):
            self.assertEqual(reconciliation.reconcile(self.payload([{**deepcopy(self.trade),**change}]),now=self.now)['repaired'],0)
        altered=deepcopy(self.trade);altered['exit_allocations'][0]['match_method']='fifo_fallback'
        self.assertEqual(reconciliation.reconcile(self.payload([altered]),now=self.now)['blocked']['fifo_needs_review'],1)
        self.assertEqual(reconciliation.reconcile(self.payload([self.trade,self.trade]),now=self.now)['blocked']['duplicate_or_missing_entry_id'],2)
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT status FROM trade_book WHERE id=?',(ident,)).fetchone()[0],'OPEN')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM learning_outcomes').fetchone()[0],0)
    def test_future_close_counts_once_after_installation(self):
        reconciliation.reconcile(self.payload([]),now=self.now-7200)
        reconciliation.reconcile(self.payload(),now=self.now)
        with closing(database.get_connection()) as conn:self.assertEqual(len(learning.outcomes(conn,0,25)),1)
        reconciliation.reconcile(self.payload(),now=self.now+1)
        with closing(database.get_connection()) as conn:self.assertEqual(len(learning.outcomes(conn,0,25)),1)
    def test_ledger_limits_and_nonpaper_inputs_fail_closed(self):
        for payload in ({**self.payload(),'paper':False},{**self.payload(),'summary':{'ledger_limit_reached':True}}):
            with self.assertRaises(ValueError):reconciliation.reconcile(payload,now=self.now)
    def test_atomic_failure_rolls_back_trade_outcome_and_audit(self):
        self.entry()
        with closing(database.get_connection()) as conn:
            reconciliation.initialize(conn,self.now)
            conn.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON stock_reconciliation_audit BEGIN SELECT RAISE(ABORT,'audit unavailable'); END;");conn.commit()
        with self.assertRaises(Exception):reconciliation.reconcile(self.payload(),now=self.now)
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT status FROM trade_book').fetchone()[0],'OPEN')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM learning_outcomes').fetchone()[0],0)
    def test_conflicting_quantity_preserves_saved_entry(self):
        self.entry()
        with closing(database.get_connection()) as conn:conn.execute('UPDATE trade_book SET shares=9');conn.commit()
        self.assertEqual(reconciliation.reconcile(self.payload(),now=self.now)['blocked']['internal_entry_conflict'],1)

    def test_actual_canonical_bracket_ledger_round_trips_into_recovery(self):
        from test_reporting_metrics import actual_functions,fill
        fills=[fill('entry-1','BUY',10,5,'2026-11-16T15:00:00Z',legs=[{'id':'sell-1','side':'sell'}]),
               fill('sell-1','SELL',10,5.2,'2026-11-17T17:00:00Z',auto=False)]
        payload=actual_functions(load_broker_fills=lambda **kwargs:fills)['auto_trader_canonical_broker_trades']()
        self.assertEqual(payload['trades'][0]['exit_allocations'][0]['match_method'],'broker_bracket_leg')
        result=reconciliation.reconcile(payload,now=self.now)
        self.assertEqual(result['imported'],1)
        with closing(database.get_connection()) as conn:
            self.assertEqual(reconciliation.status(conn)['total_imports'],1)
