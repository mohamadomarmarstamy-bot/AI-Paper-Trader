import json
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
import database
import solana_meme_bot as bot
import solana_meme_learning as learning
import solana_meme_mail as mail


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(self.temp.name)/'test.sqlite3');p.start();self.addCleanup(p.stop)
        self.state=bot.initial();self.state['config'].update(entry_mode='filtered',auto_learning=True)
        with database.get_connection() as conn:bot.initialize(conn)
    def row(self,i,win=True,liq=200000,**extra):
        return {'id':str(i),'mint':str(i%10),'symbol':'TEST','opened_at':10001+i,'closed_at':10002+i,'entry_mode':'filtered',
                'exit_policy':learning.scope(self.state['config']),'entry_usd':50,'pnl_usd':2 if win else -2,
                'entry_features':{'liquidity_usd':liq if win else 100000,'volume_m5_usd':20000 if win else 10000,
                                  'buys_m5':100,'sells_m5':50,'age_minutes':120,'change_m5_percent':5},**extra}
    def process(self,rows=()):
        with database.get_connection() as conn:
            learning.archive(conn,rows);learning.process(conn,self.state,10000);conn.commit()
    def count(self,table):
        with database.get_connection() as conn:return conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]

    def test_daily_pnl_uses_all_archived_exits_and_resets_at_eastern_midnight(self):
        zone=ZoneInfo('America/New_York')
        now=datetime(2026,10,29,23,59,59,tzinfo=zone).timestamp()
        start=datetime(2026,10,29,tzinfo=zone).timestamp()
        end=datetime(2026,10,30,tzinfo=zone).timestamp()
        rows=[self.row(i,closed_at=start+i,pnl_usd=-1) for i in range(250)]
        rows+=[self.row(300,closed_at=start-1,pnl_usd=999),self.row(301,closed_at=end,pnl_usd=20)]
        with database.get_connection() as conn:
            learning.archive(conn,rows);learning.archive(conn,rows)
            before=learning.daily_pnl(conn,now)
            self.assertEqual(before['closed_count'],250);self.assertEqual(before['realized_pnl_usd'],-250)
            self.assertEqual(before['losses'],250);self.assertEqual(before['resets_at'],end)
            after=learning.daily_pnl(conn,end)
            self.assertEqual(after['realized_pnl_usd'],20);self.assertEqual(after['closed_count'],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM solana_meme_learning_outcomes').fetchone()[0],252)
        # Reporting never changes funding, positions, or the loss anchor.
        self.assertEqual(self.state['cash_usd'],1000);self.assertIsNone(self.state['day_equity'])

    def test_daily_pnl_zero_day_and_daylight_saving_boundaries(self):
        zone=ZoneInfo('America/New_York')
        with database.get_connection() as conn:
            for month,day,hours in [(3,8,23),(11,1,25),(10,29,24)]:
                now=datetime(2026,month,day,12,tzinfo=zone).timestamp()
                result=learning.daily_pnl(conn,now)
                self.assertEqual(result['resets_at']-result['starts_at'],hours*3600)
                self.assertEqual(result['realized_pnl_usd'],0);self.assertEqual(result['closed_count'],0)
                self.assertEqual(result['timezone'],'America/New_York')
    def test_fifty_boundary_repeats_without_duplicate_evaluations_or_mail(self):
        self.process([self.row(i,i%2==0) for i in range(49)])
        self.assertEqual(self.count('solana_meme_learning_evaluations'),0)
        self.process([self.row(49,False)])
        self.assertEqual(self.state['learning']['batch_count'],1)
        self.assertEqual(self.state['learning'].get('change_count',0),0)
        self.assertIn('forward_proposal',self.state['learning'])
        self.process();self.assertEqual(self.count('solana_meme_learning_mail'),0)
        self.process([self.row(i,i%2==0) for i in range(50,70)])
        self.assertEqual(self.state['learning']['change_count'],1)
        self.assertEqual(self.state['config']['entry_mode'],'learned')
        second=[self.row(i,i%2==0,liq=500000) for i in range(70,120)]
        for r in second:
            if r['pnl_usd']>0:r['entry_features']['volume_m5_usd']=50000
        self.process(second)
        self.assertEqual(self.state['learning']['batch_count'],2)
        self.assertEqual(self.state['learning']['change_count'],1)
        forward=[self.row(i,i%2==0,liq=500000) for i in range(120,140)]
        for r in forward:
            if r['pnl_usd']>0:r['entry_features']['volume_m5_usd']=50000
        self.process(forward)
        self.assertEqual(self.state['learning']['change_count'],2)
        self.assertEqual(self.state['config']['minimum_m5_volume_usd'],50000)
        self.assertEqual(self.count('solana_meme_learning_evaluations'),4)
        with database.get_connection() as conn:
            result=learning.summary(conn)
            self.assertEqual(result['settings_change_count'],2)
            self.assertTrue(all(r['changed'] for r in result['settings_changes']))
            self.assertTrue(all(r['actor']=='Bot' for r in result['settings_changes']))
    def test_no_evidence_and_no_improvement_still_count_fifty_without_email(self):
        self.process([self.row(i,False) for i in range(50)])
        self.assertEqual(self.state['learning']['batch_count'],1)
        self.assertEqual(self.count('solana_meme_learning_mail'),0)
        self.assertEqual(self.state['config']['entry_mode'],'filtered')
        rows=[self.row(i) for i in range(50,100)]
        for r in rows:r['entry_features']={}
        self.process(rows);self.assertEqual(self.state['learning']['batch_count'],1)
        self.assertEqual(self.state['learning']['excluded_count'],50)
        self.assertEqual(self.count('solana_meme_learning_mail'),0)
    def test_paused_learning_retains_cursor_then_resumes(self):
        self.state['config']['auto_learning']=False
        self.process([self.row(i,i%2==0) for i in range(50)])
        self.assertNotIn('cycle_cursor',self.state['learning'])
        self.state['config']['auto_learning']=True;self.process()
        self.assertEqual(self.state['learning']['batch_count'],1)

    def test_only_qualified_current_version_closes_fill_batch(self):
        self.process([self.row(i,i%2==0) for i in range(25)])
        bad=[self.row(i,entry_mode='explore') for i in range(25,50)]
        bad += [self.row(i,entry_features={}) for i in range(50,75)]
        self.process(bad)
        self.assertEqual(len(self.state['learning']['eligible_buffer']),25)
        self.assertEqual(self.state['learning']['excluded_count'],50)
        self.process([self.row(i,i%2==0) for i in range(75,100)])
        self.assertEqual(self.state['learning']['batch_count'],1)
        self.assertTrue(self.state['learning']['forward_proposal'])
        self.assertEqual(self.state['config']['minimum_liquidity_usd'],100000)

    def test_forward_rejects_losses_and_never_reuses_old_open_entries(self):
        self.process([self.row(i,i%2==0) for i in range(50)])
        self.process([self.row(i,opened_at=9999) for i in range(50,70)])
        self.assertEqual(self.state['learning']['forward_proposal']['rows'],[])
        self.process([self.row(i,False) for i in range(70,90)])
        self.assertNotIn('forward_proposal',self.state['learning'])
        self.assertEqual(self.state['learning'].get('change_count',0),0)
        self.assertEqual(self.count('solana_meme_learning_mail'),0)
        self.assertIn('rejected',self.state['learning']['last_evaluation']['evidence']['reason'])

    def test_manual_policy_change_cancels_pending_forward_candidate(self):
        self.process([self.row(i,i%2==0) for i in range(50)])
        self.state['learning']['active_version']='manual-change'
        self.process([self.row(i,i%2==0) for i in range(50,70)])
        self.assertNotIn('forward_proposal',self.state['learning'])
        self.assertEqual(self.state['learning'].get('change_count',0),0)

    def test_automatic_rules_keep_minimum_liquidity_and_volume_floors(self):
        rows=[self.row(i,i%2==0,liq=100) for i in range(50)]
        for r in rows:r['entry_features']['volume_m5_usd']=10
        rule,evidence=learning.evaluate_batch(rows,self.state['config'])
        self.assertIsNone(rule)
    def test_atomic_rollback_discards_evaluation_and_mail(self):
        with database.get_connection() as conn:
            learning.archive(conn,[self.row(i,i%2==0) for i in range(50)])
            learning.process(conn,self.state,10000);conn.rollback()
        self.assertEqual(self.count('solana_meme_learning_evaluations'),0)
        self.assertEqual(self.count('solana_meme_learning_mail'),0)
    def test_ten_change_review_waits_for_actual_version_outcomes_then_selects_best(self):
        changes=[{'id':f'v{i}','number':i+1,'rule':{'liquidity_usd':(i+1)*1000,'volume_m5_usd':100},'policy':learning.scope(self.state['config'])} for i in range(10)]
        self.state['learning']={'change_count':10,'changes':changes,'pending_reviews':[{'number':1,'changes':changes}]}
        rows=[self.row(i*10+j,pnl_usd=(i+1)/10,settings_version=f'v{i}') for i in range(10) for j in range(10)]
        self.process(rows[:-1]);self.assertEqual(len(self.state['learning']['pending_reviews']),1)
        self.process(rows[-1:]);self.assertFalse(self.state['learning']['pending_reviews'])
        self.assertEqual(self.state['learning']['last_review']['evidence']['best_version'],'v9')
        self.assertEqual(self.state['config']['minimum_liquidity_usd'],100000)
        self.assertEqual(self.state['learning']['change_count'],10)
        self.process();self.assertEqual(self.count('solana_meme_learning_mail'),0)
    def test_provider_retry_is_idempotent_and_does_not_resend_accepted(self):
        self.process([self.row(i,i%2==0) for i in range(50)])
        self.process([self.row(i,i%2==0) for i in range(50,70)])
        env={'RESEND_API_KEY':'synthetic-key','HEALTH_ALERT_EMAIL_TO':'owner@example.com','HEALTH_ALERT_EMAIL_FROM':'bot@example.com'}
        now=[10000.];post=Mock(side_effect=TimeoutError)
        with patch.dict('os.environ',env):
            self.assertFalse(mail.deliver_one(clock=lambda:now[0],post=post))
            first=post.call_args.kwargs
            now[0]+=61;response=Mock();response.json.return_value={'id':'provider-123'};post.side_effect=None;post.return_value=response
            self.assertTrue(mail.deliver_one(clock=lambda:now[0],post=post))
            self.assertEqual(post.call_args.kwargs['headers']['Idempotency-Key'],first['headers']['Idempotency-Key'])
            self.assertEqual(post.call_args.kwargs['json'],first['json'])
            self.assertFalse(mail.deliver_one(clock=lambda:now[0],post=post));self.assertEqual(post.call_count,2)
        with database.get_connection() as conn:self.assertEqual(conn.execute('SELECT status FROM solana_meme_learning_mail').fetchone()[0],'accepted')
    def test_missing_email_configuration_keeps_queue_and_expired_retry_needs_attention(self):
        self.process([self.row(i,i%2==0) for i in range(50)])
        self.process([self.row(i,i%2==0) for i in range(50,70)])
        with patch.dict('os.environ',{},clear=True):self.assertFalse(mail.deliver_one())
        with database.get_connection() as conn:conn.execute('UPDATE solana_meme_learning_mail SET first_attempt=1');conn.commit()
        with patch.dict('os.environ',{'RESEND_API_KEY':'key','HEALTH_ALERT_EMAIL_TO':'owner@example.com','HEALTH_ALERT_EMAIL_FROM':'bot@example.com'}):
            post=Mock();self.assertFalse(mail.deliver_one(clock=lambda:90000,post=post));post.assert_not_called()
        with database.get_connection() as conn:self.assertEqual(conn.execute('SELECT status FROM solana_meme_learning_mail').fetchone()[0],'attention')
