from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
from zoneinfo import ZoneInfo
import uuid
import app_controls as controls
import stock_auto_learning as learning
import stock_research as research
import stock_performance as performance
import test_stock_auto_learning as fixture

ET=ZoneInfo('America/New_York')

class StockResearchTests(unittest.TestCase):
    def setUp(self):
        fixture.StockLearningTests.setUp(self)
        current=controls.snapshot();controls.update({'stock_parallel_validation_enabled':True},expected_revision=current['revision'],request_id=str(uuid.uuid4()))
        self.start=datetime(2026,10,19,9,50,tzinfo=ET).timestamp()
    def add(self,*args,**kwargs):return fixture.StockLearningTests.add(self,*args,**kwargs)
    def proposal(self,origin='learner'):
        current=controls.snapshot()
        with closing(controls.connect()) as conn:
            ident=research.propose(conn,current,{'stock_min_volume_ratio':2.2},{'estimated_net_mean_return_percent':-.2},now=self.start-1,origin=origin);conn.commit()
        return ident
    def pairs(self,ident,count=50,*,bad=False):
        with closing(controls.connect()) as conn:
            trial=dict(conn.execute('SELECT * FROM stock_shadow_trials WHERE id=?',(ident,)).fetchone())
            for i in range(count):
                at=self.start+(i//10)*86400+(i%10)*900
                cheap=i%2==0
                values={'captured_at':at,'price':5,'float_shares':15e6,'volume_ratio':2.1 if cheap else 3,
                    'average_volume':500000,'spread_percent':.2,'day_change_percent':5,'atr_percent':4}
                quote={'ask':5,'bid':4.99,'at':at,'spread_percent':.2}
                opportunity=research.record_opportunity(conn,[trial],'S'+str(i%5),values,quote,at)
                self.assertIsNotNone(opportunity)
                exit_bid=5.1 if cheap and bad else 4.9 if cheap else 5.1
                research.settle(conn,{'S'+str(i%5):{'bid':exit_bid,'ask':exit_bid+.01,'at':at+901}},at+901)
            conn.commit()
    def test_25_trade_filter_proposal_waits_for_independent_future_comparison(self):
        self.add(0,25,pattern=True);report=learning.review()
        self.assertFalse(report['changed']);self.assertEqual(report['proposal']['after'],{'stock_min_volume_ratio':2.2})
        self.assertEqual(controls.snapshot()['settings']['stock_min_volume_ratio'],2)
        with closing(controls.connect()) as conn:
            trial=dict(conn.execute("SELECT * FROM stock_shadow_trials WHERE origin='learner'").fetchone())
            event=json.loads(conn.execute('SELECT event_json FROM stock_learning_mail').fetchone()[0])
        self.assertEqual(trial['state'],'collecting');self.assertIn('Proposed only',learning.review_mail.compose(event)[1])
    def test_prospective_same_time_pairs_can_promote_actual_saved_filter_and_arm_real_rollback(self):
        ident=self.proposal();self.pairs(ident)
        with closing(controls.connect()) as conn:
            conn.execute('BEGIN IMMEDIATE');current=controls._snapshot(conn)
            research.evaluate_trials(conn,current)
            supported=conn.execute('SELECT * FROM stock_shadow_trials WHERE id=?',(ident,)).fetchone()
            self.assertEqual(supported['state'],'supported')
            stats=json.loads(supported['decision_json']);self.assertEqual(stats['matured_pairs'],50);self.assertGreater(stats['lower_95_percent'],0)
            receipt=research.promote(conn,current);conn.commit()
            self.assertIsNotNone(receipt)
            self.assertEqual(conn.execute('SELECT state FROM stock_learning_experiments').fetchone()[0],'pending')
            event=json.loads(conn.execute('SELECT event_json FROM stock_learning_mail').fetchone()[0]);self.assertEqual(event['kind'],'promotion')
            self.assertIn('2.0 -> 2.2',learning.review_mail.compose(event)[1])
            self.assertIsNone(research.promote(conn,controls._snapshot(conn)))
        self.assertEqual(controls.snapshot()['settings']['stock_min_volume_ratio'],2.2)
        self.assertEqual(controls.snapshot()['audit'][0]['actor'],'stock_parallel_promote')

    def test_promoted_filter_can_really_roll_back_after_real_paper_outcomes(self):
        ident=self.proposal();self.pairs(ident)
        with closing(controls.connect()) as conn:
            current=controls._snapshot(conn);research.evaluate_trials(conn,current);research.promote(conn,current);conn.commit()
        self.add(0,25);learning.review()
        self.assertEqual(controls.snapshot()['settings']['stock_min_volume_ratio'],2)
        self.assertEqual(controls.snapshot()['audit'][0]['actor'],'stock_auto_rollback')

    def test_failed_promotion_audit_rolls_back_settings_followup_and_email(self):
        ident=self.proposal();self.pairs(ident)
        with closing(controls.connect()) as conn:
            current=controls._snapshot(conn);research.evaluate_trials(conn,current);conn.commit()
        original=controls._apply
        def fail(*args):original(*args);raise RuntimeError('synthetic failed promotion')
        with patch.object(controls,'_apply',side_effect=fail),self.assertRaises(RuntimeError),closing(controls.connect()) as conn:
            conn.execute('BEGIN IMMEDIATE');research.promote(conn,controls._snapshot(conn))
        self.assertEqual(controls.snapshot()['settings']['stock_min_volume_ratio'],2)
        with closing(controls.connect()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM stock_learning_experiments').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM stock_learning_mail').fetchone()[0],0)

    def test_seed_challenger_never_auto_promotes_and_failed_candidate_is_not_supported(self):
        ident=self.proposal(origin='seed');self.pairs(ident)
        with closing(controls.connect()) as conn:
            current=controls._snapshot(conn);research.evaluate_trials(conn,current)
            self.assertIsNone(research.promote(conn,current));conn.commit()
        self.assertEqual(controls.snapshot()['settings']['stock_min_volume_ratio'],2)
        # Fresh independent proposal; profitable rejected opportunities make tightening worse.
        ident=self.proposal()
        # Different time buckets so this trial never borrows older markouts.
        self.start+=7*86400;self.pairs(ident,bad=True)
        with closing(controls.connect()) as conn:
            current=controls._snapshot(conn);research.evaluate_trials(conn,current);conn.commit()
            row=conn.execute('SELECT * FROM stock_shadow_trials WHERE id=?',(ident,)).fetchone()
            self.assertFalse(json.loads(row['decision_json'])['supported']);self.assertIsNone(research.promote(conn,current))
    def test_new_manual_settings_supersede_comparison_and_preserve_user_values(self):
        ident=self.proposal();self.pairs(ident)
        current=controls.snapshot();controls.update({'stock_min_volume_ratio':4},expected_revision=current['revision'],request_id=str(uuid.uuid4()))
        with closing(controls.connect()) as conn:
            current=controls._snapshot(conn);research.evaluate_trials(conn,current);conn.commit()
            self.assertEqual(conn.execute('SELECT state FROM stock_shadow_trials WHERE id=?',(ident,)).fetchone()[0],'superseded')
            self.assertIsNone(research.promote(conn,current))
        self.assertEqual(controls.snapshot()['settings']['stock_min_volume_ratio'],4)
    def test_no_early_or_missing_quote_is_turned_into_a_fill_and_opportunities_deduplicate(self):
        ident=self.proposal()
        with closing(controls.connect()) as conn:
            trial=dict(conn.execute('SELECT * FROM stock_shadow_trials WHERE id=?',(ident,)).fetchone())
            values={'price':5,'float_shares':15e6,'volume_ratio':3,'average_volume':500000,'spread_percent':.2,'day_change_percent':5,'atr_percent':4}
            quote={'ask':5,'bid':4.99,'at':self.start}
            opportunity=research.record_opportunity(conn,[trial],'TEST',values,quote,self.start)
            self.assertIsNone(research.record_opportunity(conn,[trial],'TEST',values,quote,self.start+1))
            research.settle(conn,{'TEST':{'bid':6,'at':self.start+899}},self.start+901)
            self.assertEqual(conn.execute('SELECT state FROM stock_shadow_opportunities').fetchone()[0],'open')
            research.settle(conn,{},self.start+1201)
            self.assertEqual(conn.execute('SELECT state FROM stock_shadow_opportunities').fetchone()[0],'unavailable')
            self.assertEqual(research.trial_stats(conn,trial)['matured_pairs'],0)
    def test_missing_quote_coverage_or_too_few_sessions_blocks_support(self):
        ident=self.proposal();self.pairs(ident,count=40)
        with closing(controls.connect()) as conn:
            trial=conn.execute('SELECT * FROM stock_shadow_trials WHERE id=?',(ident,)).fetchone()
            self.assertFalse(research.trial_stats(conn,trial,bootstrap=True)['supported'])
        self.start+=7*86400;self.pairs(ident,count=10)
        with closing(controls.connect()) as conn:
            conn.execute("UPDATE stock_shadow_opportunities SET state='unavailable' WHERE rowid IN (SELECT rowid FROM stock_shadow_opportunities LIMIT 10)")
            trial=conn.execute('SELECT * FROM stock_shadow_trials WHERE id=?',(ident,)).fetchone()
            stats=research.trial_stats(conn,trial,bootstrap=True)
            self.assertGreater(stats['unavailable_percent'],10);self.assertFalse(stats['supported'])

    def test_production_research_step_uses_only_get_quotes_and_settles_without_broker_orders(self):
        candidate={'symbol':'TEST','signal':'BUY','score':80,'confidence':80,'scanner_rank':1,'scanner_stale':False,
            'atr_percent':4,'volume_ratio':3,'average_volume':500000,'momentum_move_percent':5}
        criteria={'score_min':75,'confidence_min':70,'max_scanner_rank':10,'max_atr_percent':8}
        clock=[self.start]
        def data(method,path,params):
            self.assertEqual(method,'GET');self.assertEqual(path,'/v2/stocks/quotes/latest')
            bid=4.99 if clock[0]==self.start else 5.1
            return {'quotes':{symbol:{'bp':bid,'ap':bid+.01,'bs':100,'as':100,
                't':datetime.fromtimestamp(clock[0],ET).isoformat()} for symbol in params['symbols'].split(',')}}
        request=Mock(side_effect=data)
        with patch.object(research.time,'time',return_value=self.start):research.enqueue_scanner([candidate],criteria)
        with patch('stock_momentum.reported_float',return_value={'float_shares':15e6}):
            self.assertFalse(research.step(request,now=clock[0]))
        with closing(controls.connect()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM stock_shadow_opportunities').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM trade_book').fetchone()[0],0)
        clock[0]+=901;self.assertFalse(research.step(request,now=clock[0]))
        with closing(controls.connect()) as conn:
            self.assertEqual(conn.execute('SELECT state FROM stock_shadow_opportunities').fetchone()[0],'closed')
            self.assertEqual(research.status(conn)['trials'][0]['stats']['matured_pairs'],1)
        self.assertGreaterEqual(request.call_count,3)

    def test_actual_trade_cohorts_keep_legacy_unknowns_and_calculate_costed_expectancy(self):
        self.add(0,2,pattern=True)
        with closing(controls.connect()) as conn:
            conn.execute('ALTER TABLE trade_book ADD COLUMN entry_timestamp TEXT');conn.execute('ALTER TABLE trade_book ADD COLUMN exit_timestamp TEXT')
            for i,setup,floating,hour in ((0,'breakout',3e6,10),(1,'vwap_pullback',15e6,13)):
                row=conn.execute('SELECT features_json FROM stock_entry_evidence WHERE order_id=?',('entry-'+str(i),)).fetchone()
                features={**json.loads(row[0]),'entry_setup':setup,'float_shares':floating}
                conn.execute('UPDATE stock_entry_evidence SET features_json=? WHERE order_id=?',(json.dumps(features),'entry-'+str(i)))
                at=datetime(2026,10,20,hour,0,tzinfo=ET).isoformat()
                conn.execute('UPDATE trade_book SET entry_timestamp=?,exit_timestamp=? WHERE id=?',(at,at,i+1))
            conn.commit();result=performance.report(conn,controls._snapshot(conn)['settings'],days=0)
        self.assertEqual(result['summary']['trades'],2);self.assertAlmostEqual(result['summary']['estimated_net_pnl_usd'],.6)
        self.assertAlmostEqual(result['summary']['expectancy_usd'],.3);self.assertAlmostEqual(result['summary']['profit_factor'],1.5)
        self.assertEqual(result['coverage']['known_setup'],2);self.assertEqual(len(result['groups']['session']),2)
        with closing(controls.connect()) as conn:
            conn.execute('DELETE FROM stock_entry_evidence');conn.commit()
            result=performance.report(conn,controls._snapshot(conn)['settings'],days=0)
        self.assertEqual(result['summary']['trades'],2);self.assertEqual(result['groups']['setup'][0]['label'],'Unknown / legacy setup')
    def test_fresh_quote_requires_current_real_bid_ask_and_sizes(self):
        stamp=datetime.fromtimestamp(self.start,ET).isoformat()
        raw={'bp':5,'ap':5.01,'bs':100,'as':100,'t':stamp}
        self.assertIsNotNone(research.fresh_quote(raw,self.start))
        self.assertIsNone(research.fresh_quote(raw,self.start+31))
        self.assertIsNone(research.fresh_quote({**raw,'bs':0},self.start))
        self.assertIsNone(research.fresh_quote({**raw,'ap':4},self.start))

if __name__=='__main__':unittest.main()
