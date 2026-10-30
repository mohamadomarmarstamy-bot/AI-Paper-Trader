from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
import uuid
import database
import app_controls as controls
import stock_momentum as momentum
import stock_auto_learning as learning


class StockLearningTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'stocks.sqlite3');p.start();self.addCleanup(p.stop)
        with closing(controls.connect()) as conn:
            conn.executescript('''CREATE TABLE trade_book(id INTEGER PRIMARY KEY,status TEXT,entry_reason TEXT,entry_order_id TEXT);
            CREATE TABLE learning_outcomes(id INTEGER PRIMARY KEY,trade_book_id INTEGER UNIQUE,symbol TEXT,entry_price REAL,
            entry_score REAL,spread_percent REAL,realized_profit_loss REAL,realized_return_percent REAL);''')
            learning.initialize(conn)
        self.config=controls.snapshot()['settings']

    def add(self,start,count,*,pattern=False,entry_reason='auto_trader_entry',closed=True):
        for i in range(start,start+count):
            won=pattern and i%2==0
            features={'captured_at':time.time(),'price':5.,'float_shares':15e6,
                      'volume_ratio':5. if won else 3. if i%4==1 else 2.1,
                      'average_volume':500000.,'spread_percent':.2,'day_change_percent':5.,'atr_percent':4.}
            order='entry-'+str(i)
            learning.record_entry(order,'S'+str(i%6),features,controls.snapshot())
            with closing(controls.connect()) as conn:
                conn.execute('INSERT INTO trade_book VALUES(?,?,?,?)',(i+1,'CLOSED' if closed else 'OPEN',entry_reason,order))
                conn.execute('INSERT INTO learning_outcomes VALUES(?,?,?,?,?,?,?,?)',
                             (i+1,i+1,'S'+str(i%6),5,80,.2,2 if won else -1,2 if won else -1));conn.commit()

    def test_25_losing_closes_actually_save_audited_smaller_position_and_never_repeat(self):
        self.add(0,24);self.assertIsNone(learning.review());self.assertEqual(controls.snapshot()['settings']['position_size_percent'],2)
        self.add(24,1);report=learning.review()
        self.assertTrue(report['changed']);self.assertEqual(report['before'],{'position_size_percent':2})
        self.assertEqual(report['after'],{'position_size_percent':1.8})
        self.assertEqual(controls.snapshot()['settings']['position_size_percent'],1.8)
        self.assertEqual(controls.snapshot()['audit'][0]['actor'],'stock_auto_25')
        self.assertEqual(controls.snapshot()['settings']['daily_loss_limit_percent'],1)
        self.assertEqual(controls.snapshot()['settings']['stop_loss_percent'],2)
        self.assertIsNone(learning.review());self.assertEqual(len(learning.status()['reviews']),1)
        self.add(25,25);learning.review();self.assertEqual(controls.snapshot()['settings']['position_size_percent'],1.62)

    def test_winner_loser_volume_pattern_changes_real_filter_using_later_validation_rows(self):
        self.add(0,25,pattern=True);report=learning.review()
        self.assertTrue(report['changed']);self.assertEqual(report['after'],{'stock_min_volume_ratio':2.2})
        config=controls.snapshot()['settings'];self.assertEqual(config['stock_min_volume_ratio'],2.2)
        self.assertGreater(report['validation_improvement_percent'],0)
        values={'price':5,'float_shares':15e6,'volume_ratio':2.1,'average_volume':500000,
                'spread_percent':.1,'day_change_percent':5,'atr_percent':4}
        self.assertNotIn('stock_min_volume_ratio',momentum.evaluate(values,self.config,now=time.time()))
        self.assertIn('stock_min_volume_ratio',momentum.evaluate(values,config,now=time.time()))

    def test_failed_audit_transaction_rolls_back_settings_cursor_and_batch(self):
        self.add(0,25);original=controls._apply
        def fail(*args):original(*args);raise RuntimeError('synthetic transaction failure')
        with patch.object(controls,'_apply',side_effect=fail),self.assertRaises(RuntimeError):learning.review()
        self.assertEqual(controls.snapshot()['settings']['position_size_percent'],2)
        self.assertEqual(controls.snapshot()['audit'],[]);self.assertEqual(learning.status()['reviews'],[])
        self.assertEqual(learning.status()['pending_closed_count'],25)
        self.assertTrue(learning.review()['changed'])

    def test_open_and_manual_trades_are_excluded_and_pause_skips_its_backlog(self):
        self.add(0,25,entry_reason='manual');self.add(25,25,closed=False)
        self.assertEqual(learning.status()['pending_closed_count'],0)
        current=controls.snapshot();controls.update({'stock_auto_adjust_enabled':False},expected_revision=current['revision'],request_id=str(uuid.uuid4()))
        self.add(50,25);self.assertIsNone(learning.review())
        current=controls.snapshot();controls.update({'stock_auto_adjust_enabled':True},expected_revision=current['revision'],request_id=str(uuid.uuid4()))
        self.assertIsNone(learning.review());self.assertEqual(learning.status()['pending_closed_count'],0)

    def test_manual_changes_are_preserved_and_source_snapshots_are_immutable(self):
        self.add(0,25)
        current=controls.snapshot();controls.update({'position_size_percent':3,'max_order_size_percent':4},expected_revision=current['revision'],request_id=str(uuid.uuid4()))
        learning.record_entry('entry-0','WRONG',{'captured_at':0},controls.snapshot())
        report=learning.review();self.assertEqual(report['before'],{'position_size_percent':3})
        self.assertEqual(report['after'],{'position_size_percent':2.7})
        with closing(controls.connect()) as conn:
            self.assertEqual(conn.execute("SELECT symbol FROM stock_entry_evidence WHERE order_id='entry-0'").fetchone()[0],'S0')

    def test_production_buy_adapter_enforces_saved_float_at_current_ask_and_captures_accepted_order(self):
        import test_pro_ticker_manual_scanner_adapters as adapters
        quote={'ask':10.,'spread_percent':.1}
        fn,ns=adapters.adapter('submit_alpaca_auto_bracket_buy',time=time,automatic_entries_paused=lambda:False,
            validate_alpaca_paper_order_risk=Mock(return_value={'approved':True,'quote':quote}),
            get_auto_trader_setting=lambda key:controls.snapshot()['settings'][key],
            normalize_alpaca_paper_order=lambda *a,**k:{'success':True,'trade':{'status':'accepted'}})
        candidate={'price':1000,'volume_ratio':3,'average_volume':500000,'momentum_move_percent':5,'atr_percent':4}
        with patch.object(momentum,'reported_float',return_value={'float_shares':10e6}):
            result=fn(symbol='TEST',shares=10,reference_price=10,scanner_result=candidate)
            self.assertTrue(result['success']);self.assertEqual(ns['alpaca_paper_request'].call_count,1)
            with closing(controls.connect()) as conn:
                evidence=json.loads(conn.execute('SELECT features_json FROM stock_entry_evidence').fetchone()[0])
                self.assertEqual(evidence['price'],10);self.assertEqual(evidence['float_shares'],10e6)
            ns['alpaca_paper_request'].reset_mock()
            current=controls.snapshot();controls.update({'stock_max_float_millions':9},expected_revision=current['revision'],request_id=str(uuid.uuid4()))
            result=fn(symbol='TEST',shares=10,reference_price=10,scanner_result=candidate)
            self.assertFalse(result['success']);self.assertIn('stock_max_float_millions',result['stock_entry_gate']['failed_requirements'])
            ns['alpaca_paper_request'].assert_not_called()


class MomentumTests(unittest.TestCase):
    def setUp(self):self.config=deepcopy(controls.ALL_DEFAULTS)
    def test_float_is_not_shares_outstanding_and_missing_enabled_evidence_blocks(self):
        values={'price':5,'float_shares':10e6,'volume_ratio':3,'average_volume':300000,'spread_percent':.2,'day_change_percent':4,'atr_percent':4}
        self.assertEqual(momentum.evaluate(values,self.config,now=100),[])
        for field in ('float_shares','volume_ratio','spread_percent'):
            missing={**values,field:None};self.assertTrue(momentum.evaluate(missing,self.config,now=100))
        self.assertIn('stock_max_float_millions',momentum.evaluate({**values,'float_shares':30e6},self.config,now=100))
        self.assertTrue(momentum.evaluate({**values,'spread_percent':-1},self.config,now=100))
        with patch.object(momentum,'_cache',{}),patch.object(momentum,'_pending',{}):
            result=momentum.reported_float('SYNTH',fetch=lambda _: {'sharesOutstanding':1000000})
            self.assertIsNone(result['float_shares'])
    def test_price_uses_current_ask_and_disabled_float_does_not_fetch(self):
        self.config.update(stock_min_float_millions=0,stock_max_float_millions=0)
        lookup=Mock(side_effect=AssertionError('Disabled float must not fetch'))
        result=momentum.features({'price':5,'volume_ratio':3},{'ask':25,'spread_percent':.1},self.config,symbol='TEST',float_fetch=lookup)
        self.assertEqual(result['price'],25)
        self.assertIn('stock_max_price_usd',momentum.evaluate(result,self.config,now=time.time()))
        lookup.assert_not_called()
    def test_price_float_ranges_are_validated_and_numeric_fields_have_no_arbitrary_ceiling(self):
        self.assertEqual(controls.validate({'stock_max_float_millions':10000000})['stock_max_float_millions'],10000000)
        for value in (-1,float('nan'),float('inf'),True):
            with self.assertRaises(ValueError):controls.validate({'stock_min_price_usd':value})
    def test_news_uses_published_timestamp_and_is_optional(self):
        config={**self.config,'stock_require_recent_news':True}
        values={'price':5,'float_shares':10e6,'volume_ratio':3,'average_volume':300000,'spread_percent':.2,'day_change_percent':4,'atr_percent':4}
        self.assertIn('recent_news_missing',momentum.evaluate(values,config,now=100000))
        self.assertNotIn('recent_news_missing',momentum.evaluate({**values,'news_latest_at':99999},config,now=100000))


if __name__=='__main__':unittest.main()
