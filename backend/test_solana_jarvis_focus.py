from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch
import copy
import json
import os
import tempfile
import unittest

import database
import jarvis_focus as focus
import jarvis_hourly_mail as mail
import jarvis_test_bot as api


class FocusTests(unittest.TestCase):
    def setUp(self):
        temp=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database,'DATABASE_PATH',Path(temp)/'trader.sqlite3'))
        self.enterContext(patch.dict(os.environ,{'JARVIS_FOCUS_MODE':'true'}))

    def test_pause_preserves_holdings_orders_cash_and_only_applies_once(self):
        states={}
        with closing(database.get_connection()) as conn:
            for table,live in (('solana_meme_paper_state',False),('solana_copy_state',False),('solana_real_run',True)):
                state={'positions':{'coin':{'quantity':17,'stop':40}},'pending':['transaction'],
                       'cash':123,'enabled':True} if live else {
                       'config':{'enabled':True,'stop_loss_percent':20},'settings_revision':3,
                       'positions':{'coin':{'quantity':17}},'cash_usd':123,'history':[{'pnl':2}]}
                states[table]=copy.deepcopy(state)
                conn.execute('CREATE TABLE '+table+'(id INTEGER PRIMARY KEY,revision INTEGER,state_json TEXT)')
                conn.execute('INSERT INTO '+table+' VALUES(1,7,?)',(json.dumps(state),))
            conn.execute('CREATE TABLE untouched_stock(value INTEGER)')
            conn.execute('INSERT INTO untouched_stock VALUES (99)')
            conn.commit()
        focus.apply_startup_pause()
        with closing(database.get_connection()) as conn:
            for table,old in states.items():
                row=conn.execute('SELECT revision,state_json FROM '+table).fetchone()
                new=json.loads(row[1]);self.assertEqual(row[0],8)
                expected=copy.deepcopy(old)
                if table=='solana_real_run':expected['enabled']=False
                else:
                    expected['config']['enabled']=False
                    expected['settings_revision']=4
                    expected['health']='paused_entries_monitoring_exits'
                self.assertEqual(new,expected)
            self.assertEqual(conn.execute('SELECT value FROM untouched_stock').fetchone()[0],99)
            conn.execute("UPDATE solana_real_run SET state_json=json_set(state_json,'$.enabled',json('true'))")
            conn.commit()
        focus.apply_startup_pause()
        with closing(database.get_connection()) as conn:
            self.assertTrue(json.loads(conn.execute('SELECT state_json FROM solana_real_run').fetchone()[0])['enabled'])

    def test_all_other_transports_stop_before_leasing_or_network(self):
        import stock_learning_mail, solana_meme_mail, solana_exit_alerts, crypto_idle_alerts, jarvis_backlog, jarvis_research
        post=Mock()
        for sender in (stock_learning_mail.deliver_one,solana_meme_mail.deliver_one,
                       solana_exit_alerts.deliver_one,crypto_idle_alerts.deliver_one):
            self.assertFalse(sender(post=post))
        with patch('solana_http.post',post):
            self.assertFalse(jarvis_research.deliver(1,'test'))
        post.assert_not_called()
        self.assertTrue(focus.email_allowed('jarvis_hourly'))
        self.assertTrue(focus.email_allowed('jarvis_health'))
        self.assertTrue(focus.email_allowed('jarvis_backlog'))
        with patch.dict(os.environ,{'JARVIS_FOCUS_MODE':'false'}):
            self.assertTrue(focus.email_allowed('stock_learning'))
            focus.apply_startup_pause()


class JarvisHealthTests(unittest.TestCase):
    def setUp(self):
        temp=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database,'DATABASE_PATH',Path(temp)/'trader.sqlite3'))
        self.enterContext(patch.dict(os.environ,{'JARVIS_FOCUS_MODE':'true','JARVIS_HOURLY_EMAIL_ENABLED':'true',
            'RESEND_API_KEY':'fake','HEALTH_ALERT_EMAIL_FROM':'bot@example.com','HEALTH_ALERT_EMAIL_TO':'owner@example.com'}))
        self.now=10000
        self.post=Mock(return_value=Mock(status_code=200,json=lambda:{'id':'accepted'}))
        self.update(enabled=True,last_cycle=self.now)
        self.assertEqual(self.tick(),'accepted')

    def update(self,**changes):
        with api.connection() as conn,conn:
            state,revision=api.read(conn)
            state.update(changes)
            conn.execute('UPDATE run SET state=?,revision=? WHERE id=1',(json.dumps(state),revision+1))

    def tick(self):return mail.tick(api,clock=lambda:self.now,post=self.post)

    def activity(self,at):
        with api.connection() as conn,conn:
            state,revision=api.read(conn)
            state['bots']['research-01']['last_trade_at']=at
            conn.execute('UPDATE run SET state=? WHERE id=1',(json.dumps(state),))

    def test_idle_uses_research_trades_not_scans_or_baseline_activity(self):
        self.now+=1799;self.update(last_cycle=self.now)
        self.assertEqual(self.tick(),'not_due')
        with api.connection() as conn,conn:
            state,_=api.read(conn);state['bots']['instant-step']['last_trade_at']=self.now
            conn.execute('UPDATE run SET state=? WHERE id=1',(json.dumps(state),))
        self.now+=1;self.update(last_cycle=self.now)
        self.assertEqual(self.tick(),'accepted')
        self.assertIn('No research trades',self.post.call_args.kwargs['json']['subject'])
        self.now+=20;self.assertEqual(self.tick(),'not_due')
        self.activity(self.now);self.tick()
        self.now+=1800;self.update(last_cycle=self.now);self.tick()  # Hourly report and a rearmed idle episode.
        self.now+=20;self.tick()
        subjects=[call.kwargs['json']['subject'] for call in self.post.call_args_list]
        self.assertEqual(sum('No research trades' in subject for subject in subjects),2)

    def test_stale_worker_alert_is_independent_and_rearms_after_recovery(self):
        self.now+=1800;self.activity(self.now)
        self.assertEqual(self.tick(),'accepted')
        self.assertIn('heartbeat delayed',self.post.call_args.kwargs['json']['subject'])
        self.now+=20;self.activity(self.now);self.assertEqual(self.tick(),'not_due')
        self.update(last_cycle=self.now);self.tick()
        self.now+=1800;self.activity(self.now);self.tick()
        self.now+=20;self.tick()
        self.assertEqual(sum('heartbeat delayed' in c.kwargs['json']['subject'] for c in self.post.call_args_list),2)

    def test_pause_cancels_unsent_idle_and_resume_has_fresh_grace(self):
        self.post.side_effect=TimeoutError()
        self.now+=1800;self.update(last_cycle=self.now);self.assertEqual(self.tick(),'retry')
        self.update(enabled=False);self.now+=60;self.tick()
        with api.connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM jarvis_digest_mail WHERE id LIKE 'jarvis-health/idle/%'").fetchone()[0],'cancelled')
        self.update(enabled=True,last_cycle=self.now);self.tick()
        self.now+=1799;self.update(last_cycle=self.now);self.tick()
        with api.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM jarvis_digest_mail WHERE id LIKE 'jarvis-health/idle/%'").fetchone()[0],1)


if __name__=='__main__':unittest.main()
