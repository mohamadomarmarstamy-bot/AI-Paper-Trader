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
            conn.executescript('''CREATE TABLE trade_book(id INTEGER PRIMARY KEY,status TEXT,entry_reason TEXT,entry_order_id TEXT,entry_price REAL,symbol TEXT);
            CREATE TABLE learning_outcomes(id INTEGER PRIMARY KEY,trade_book_id INTEGER UNIQUE,symbol TEXT,entry_price REAL,
            entry_score REAL,spread_percent REAL,realized_profit_loss REAL,realized_return_percent REAL,shares REAL,holding_seconds REAL,exit_reason TEXT);''')
            learning.initialize(conn)
            conn.execute("INSERT INTO app_settings VALUES('stock_intraday_enabled','false','fixture')");conn.commit()
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
                conn.execute('INSERT INTO trade_book VALUES(?,?,?,?,?,?)',(i+1,'CLOSED' if closed else 'OPEN',entry_reason,order,5,'S'+str(i%6)))
                conn.execute('INSERT INTO learning_outcomes VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                             (i+1,i+1,'S'+str(i%6),5,80,.2,2 if won else -1,2 if won else -1,20,300,'exit'));conn.commit()

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
        fn,ns=adapters.adapter('submit_alpaca_auto_bracket_buy',time=time,alpaca_market_data_request=Mock(),automatic_entries_paused=lambda:False,
            validate_alpaca_paper_order_risk=Mock(return_value={'approved':True,'quote':quote}),
            get_auto_trader_setting=lambda key:controls.snapshot()['settings'][key],
            normalize_alpaca_paper_order=lambda *a,**k:{'success':True,'trade':{'status':'accepted'}})
        candidate={'price':1000,'volume_ratio':3,'average_volume':500000,'momentum_move_percent':5,'atr_percent':4}
        with patch.object(momentum,'reported_float',return_value={'float_shares':10e6}), patch('stock_intraday.fetch',return_value={'available':True,'as_of':time.time(),'latest_bar_at':time.time()-301,'vwap':10,'time_volume_ratio':3,'setup':'breakout'}):
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


    def test_filter_experiment_rolls_back_after_25_new_revision_entries(self):
        self.add(0,25,pattern=True);learning.review()
        self.assertEqual(controls.snapshot()['settings']['stock_min_volume_ratio'],2.2)
        self.add(25,24);self.assertIsNone(learning.review())
        self.assertEqual(learning.status()['experiments'][0]['followup_count'],24)
        self.add(49,1);report=learning.review()
        self.assertTrue(report['changed'])
        self.assertEqual(controls.snapshot()['settings']['stock_min_volume_ratio'],2)
        self.assertEqual(controls.snapshot()['audit'][0]['actor'],'stock_auto_rollback')
        self.assertEqual(learning.status()['experiments'][0]['state'],'reverted')
        self.assertEqual(controls.snapshot()['settings']['position_size_percent'],2)
        self.assertIsNone(learning.review())

    def test_manual_edit_supersedes_pending_rollback(self):
        self.add(0,25,pattern=True);learning.review()
        current=controls.snapshot();controls.update({'stock_min_volume_ratio':4},expected_revision=current['revision'],request_id=str(uuid.uuid4()))
        learning.review()
        self.assertEqual(learning.status()['experiments'][0]['state'],'superseded')
        self.assertEqual(controls.snapshot()['settings']['stock_min_volume_ratio'],4)

    def test_pending_experiment_ignores_old_revision_closures_and_disabled_rollback(self):
        self.add(0,25,pattern=True)
        # These entries predate the applied revision even though they close later.
        self.add(25,25,closed=False)
        learning.review()
        with closing(controls.connect()) as conn:
            conn.execute("UPDATE trade_book SET status='CLOSED'");conn.commit()
        learning.review();self.assertEqual(learning.status()['experiments'][0]['followup_count'],0)
        self.add(50,25,pattern=True);learning.review()
        self.assertEqual(learning.status()['experiments'][0]['state'],'kept')

    def test_costs_turn_small_gross_gain_into_net_loss_and_cut_actual_size(self):
        self.add(0,25)
        with closing(controls.connect()) as conn:
            conn.execute('UPDATE learning_outcomes SET realized_profit_loss=.1,realized_return_percent=.1');conn.commit()
        report=learning.review()
        self.assertGreater(report['realized_pnl_usd'],0)
        self.assertLess(report['estimated_net_pnl_usd'],0)
        self.assertEqual(report['wins'],0)
        self.assertEqual(controls.snapshot()['settings']['position_size_percent'],1.8)

    def test_exit_observations_keep_first_crossing_and_never_touch_closed_or_manual(self):
        self.add(0,1,closed=False)
        learning.observe_position('S0',5.15,now=100)
        learning.observe_position('S0',4.9,now=200)
        learning.observe_position('S0',9,now=150)  # out-of-order telemetry ignored
        with closing(controls.connect()) as conn:
            row=dict(conn.execute('SELECT * FROM stock_exit_observations').fetchone())
            self.assertEqual(row['samples'],2);self.assertAlmostEqual(row['high_return'],3)
            self.assertAlmostEqual(json.loads(row['shadow_json'])['1% stop / 2% target'],3)
            conn.execute("UPDATE trade_book SET status='CLOSED'");conn.commit()
        learning.observe_position('S0',10,now=300)
        with closing(controls.connect()) as conn:
            rows=learning.cost_adjusted(learning.outcomes(conn,0,25),self.config)
        report=learning.exit_analysis(rows,self.config)
        self.assertEqual(report['observed_trades'],1)
        self.assertAlmostEqual(report['median_giveback_percent'],4)
        self.assertAlmostEqual(report['shadow_exits'][0]['estimated_net_mean_return_percent'],2.8)

    def test_rollback_can_be_disabled_without_increasing_exposure(self):
        current=controls.snapshot();controls.update({'stock_auto_rollback_enabled':False},expected_revision=current['revision'],request_id=str(uuid.uuid4()))
        self.add(0,25,pattern=True);learning.review();self.add(25,25);learning.review()
        experiment=learning.status()['experiments'][0]
        self.assertEqual(experiment['state'],'kept');self.assertIn('disabled',experiment['result']['reason'])
        self.assertEqual(controls.snapshot()['settings']['stock_min_volume_ratio'],2.2)

    def test_production_intraday_gate_blocks_missing_setup_before_broker_submission(self):
        import test_pro_ticker_manual_scanner_adapters as adapters
        current=controls.snapshot();controls.update({'stock_intraday_enabled':True},expected_revision=current['revision'],request_id=str(uuid.uuid4()))
        fn,ns=adapters.adapter('submit_alpaca_auto_bracket_buy',time=time,alpaca_market_data_request=Mock(),automatic_entries_paused=lambda:False,
            validate_alpaca_paper_order_risk=Mock(return_value={'approved':True,'quote':{'ask':10,'spread_percent':.1}}),
            get_auto_trader_setting=lambda key:controls.snapshot()['settings'][key])
        data={'available':True,'as_of':time.time(),'latest_bar_at':time.time()-301,'vwap':10,'time_volume_ratio':3,'setup':None}
        with patch.object(momentum,'reported_float',return_value={'float_shares':10e6}),patch('stock_intraday.fetch',return_value=data):
            result=fn(symbol='TEST',shares=10,reference_price=10,scanner_result={'volume_ratio':.05,'average_volume':500000,'momentum_move_percent':5,'atr_percent':4})
        self.assertFalse(result['success']);self.assertIn('completed_breakout_or_vwap_pullback_missing',result['stock_entry_gate']['failed_requirements'])
        ns['alpaca_paper_request'].assert_not_called()

    def test_rollback_transaction_failure_preserves_filter_and_pending_experiment(self):
        self.add(0,25,pattern=True);learning.review();self.add(25,25)
        original=controls._apply
        def fail(*args):original(*args);raise RuntimeError('rollback audit failure')
        with patch.object(controls,'_apply',side_effect=fail),self.assertRaises(RuntimeError):learning.review()
        self.assertEqual(controls.snapshot()['settings']['stock_min_volume_ratio'],2.2)
        self.assertEqual(learning.status()['experiments'][0]['state'],'pending')
        learning.review();self.assertEqual(controls.snapshot()['settings']['stock_min_volume_ratio'],2)


class MomentumTests(unittest.TestCase):
    def setUp(self):self.config={**deepcopy(controls.ALL_DEFAULTS),'stock_intraday_enabled':False}
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
