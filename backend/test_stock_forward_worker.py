from contextlib import closing
from datetime import datetime
import json
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
import database
import day_trader_lab as lab
import pro_ticker_signal_learning as signals
import stock_forward_worker as forward
import test_stock_auto_learning as fixture
import app_controls as controls

class ForwardCollectionTests(unittest.TestCase):
    setUp=fixture.StockLearningTests.setUp
    def start(self):
        at=datetime(2026,11,17,11,0,tzinfo=ZoneInfo('America/New_York')).timestamp()
        with closing(database.get_connection()) as conn:
            conn.execute("UPDATE app_settings SET value='true' WHERE key='stock_parallel_validation_enabled'");conn.commit()
        with patch.object(forward.time,'time',return_value=at):
            forward.enqueue_scanner([{'symbol':'TEST','price':5,'scanner_stale':False,'signal':'SELL','score':1}])
        return at
    def test_background_intraday_sampling_does_not_require_daily_buy_and_cannot_write_broker(self):
        at=self.start();data=Mock();broker=Mock()
        def observe(payload,**kwargs):
            self.assertEqual(payload['symbol'],'TEST')
            with self.assertRaises(ValueError):kwargs['broker_read']('POST','/v2/orders')
            kwargs['broker_read']('GET','/v2/calendar')
            return {}
        with patch.object(lab,'observe',side_effect=observe) as capture:
            result=forward.step(data,broker,lambda:'iex',Mock(),now=at)
        self.assertEqual(result['intraday_observed'],1);capture.assert_called_once()
        broker.assert_called_once_with('GET','/v2/calendar')
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM trade_book').fetchone()[0],0)
            self.assertEqual(forward.status(conn)['report']['state'],'observed')
    def test_stale_scans_and_paused_research_do_not_capture(self):
        at=self.start()
        with patch.object(lab,'observe') as capture:
            self.assertEqual(forward.step(Mock(),Mock(),lambda:'iex',Mock(),now=at+181)['state'],'stale_scan')
            capture.assert_not_called()
        with closing(database.get_connection()) as conn:
            conn.execute("UPDATE app_settings SET value='false' WHERE key='stock_parallel_validation_enabled'");conn.commit()
        with patch.object(lab,'observe') as capture:
            self.assertEqual(forward.step(Mock(),Mock(),lambda:'iex',Mock(),now=at)['state'],'paused');capture.assert_not_called()
    def test_due_evaluation_is_bounded_and_old_manual_horizons_are_not_backfilled(self):
        at=self.start()
        with closing(database.get_connection()) as conn:
            conn.executescript(lab.SCHEMA+'''CREATE TABLE pro_ticker_manual_signal_reviews(request_id TEXT,status TEXT,completed_at TEXT);''')
            for i in range(4):
                conn.execute('INSERT INTO day_trader_experiments VALUES(?,?,?,?,NULL)',
                    (str(i),'TEST',at-4000,json.dumps({'setups':[{'status':'candidate','expires_at':at-100}]})))
            for ident,age in [('old',72),('due',25),('young',23)]:
                conn.execute('INSERT INTO pro_ticker_manual_signal_reviews VALUES(?,?,?)',
                    (ident,'graded',datetime.fromtimestamp(at-age*3600,ZoneInfo('UTC')).isoformat()))
            conn.commit()
        with patch.object(lab,'evaluate_saved',return_value={'evaluation':[{'status':'modeled'}]}) as evaluate,patch.object(lab,'observe'),patch.object(signals,'evaluate_forward_outcome') as markout:
            result=forward.step(Mock(),Mock(),lambda:'iex',Mock(),now=at)
            self.assertEqual(evaluate.call_count,2)
            forward.step(Mock(),Mock(),lambda:'iex',Mock(),now=at+60)
            self.assertEqual(len({c.args[0] for c in evaluate.call_args_list}),4)
        self.assertEqual(evaluate.call_count,4);self.assertEqual(result['intraday_evaluated'],2)
        self.assertEqual(result['missed_manual_horizons'],1);self.assertEqual(result['manual_reviews_evaluated'],1)
        self.assertEqual(markout.call_args.args,('due',))
    def test_existing_experiment_enforces_five_minute_sampling_interval(self):
        at=self.start()
        with closing(database.get_connection()) as conn:
            conn.executescript(lab.SCHEMA)
            conn.execute('INSERT INTO day_trader_experiments VALUES(?,?,?,?,NULL)',('recent','TEST',at-60,'{"setups":[]}'));conn.commit()
        with patch.object(lab,'observe') as capture:
            result=forward.step(Mock(),Mock(),lambda:'iex',Mock(),now=at)
            self.assertEqual(result['state'],'sampling_interval');capture.assert_not_called()
