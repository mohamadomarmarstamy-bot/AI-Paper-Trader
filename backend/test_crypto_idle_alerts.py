from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import tempfile
import unittest
import database
import crypto_idle_alerts as idle
import solana_copy_trader as copy
import solana_meme_bot as auto

class CryptoIdleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(self.temp.name)/'test.sqlite3');p.start();self.addCleanup(p.stop)
        self.states={'copy':{'config':{'enabled':True},'positions':{},'health':'data_unavailable','error':'RPC timeout','last_cycle':999},'auto':{'config':{'enabled':True},'positions':{},'closed':[],'health':'running','last_cycle':999}}
        with closing(database.get_connection()) as c:c.executescript(copy.SCHEMA+auto.SCHEMA)
        self.save()
    def save(self):
        with closing(database.get_connection()) as c:
            for bot,table in [('copy','solana_copy_state'),('auto','solana_meme_paper_state')]:c.execute('INSERT OR REPLACE INTO '+table+'(id,revision,state_json) VALUES(1,0,?)',(json.dumps(self.states[bot]),))
            c.commit()
    def rows(self):
        with closing(database.get_connection()) as c:return [dict(r) for r in c.execute('SELECT * FROM crypto_idle_mail ORDER BY rowid')]
    def trade(self,bot,at):
        if bot=='copy':
            with closing(database.get_connection()) as c:c.execute('INSERT INTO solana_copy_events VALUES(?,?,?)',(str(at),at,json.dumps({'status':'paper_buy'})));c.commit()
        else:self.states['auto']['positions']={'x':{'opened_at':at}};self.save()
    def test_independent_threshold_once_per_episode_persisted_and_rearmed(self):
        idle.inspect(1000);self.trade('auto',2000);idle.inspect(2000)
        idle.inspect(2799);self.assertEqual(self.rows(),[])
        idle.inspect(2800);self.assertEqual([r['bot'] for r in self.rows()],['copy'])
        idle.inspect(3800);self.assertEqual(len(self.rows()),2)
        idle.inspect(10000);self.assertEqual(len(self.rows()),2)
        self.trade('copy',10001);idle.inspect(10001);self.assertEqual(self.rows()[0]['state'],'cancelled')
        idle.inspect(11801);self.assertEqual(len(self.rows()),3)
    def test_pause_suppresses_and_resume_starts_new_window(self):
        idle.inspect(1000);self.states['copy']['config']['enabled']=False;self.states['auto']['config']['enabled']=False;self.save();idle.inspect(3000)
        self.assertEqual(self.rows(),[])
        self.states['copy']['config']['enabled']=True;self.save();idle.inspect(4000);idle.inspect(5799);self.assertEqual(self.rows(),[])
        idle.inspect(5800);self.assertEqual(len(self.rows()),1)
        self.states['copy']['config']['enabled']=False;self.save();idle.inspect(5801);self.assertEqual(self.rows()[0]['state'],'cancelled')
    def test_actual_buys_and_sells_count_skipped_signals_do_not(self):
        with closing(database.get_connection()) as c:
            c.execute('INSERT INTO solana_copy_events VALUES(?,?,?)',('skip',3000,json.dumps({'status':'skipped'})))
            c.execute('INSERT INTO solana_copy_closes VALUES(?,?,?)',('close',1000,json.dumps({'closed_at':1000})));c.commit()
        idle.inspect(2799);self.assertEqual(self.rows(),[])
        idle.inspect(2800);self.assertEqual(self.rows()[0]['bot'],'copy')
    def test_first_upgrade_uses_existing_trade_history_and_start_time_prevents_old_alarm(self):
        self.trade('copy',1000);idle.inspect(3000);self.assertEqual(self.rows()[0]['bot'],'copy')
        self.states['copy']['inactivity_enabled_at']=3100;self.save();idle.inspect(3101)
        self.assertEqual(self.rows()[0]['state'],'cancelled');self.assertEqual(idle.summary('copy')['next_alert_at'],4901)
    def test_one_unreadable_bot_does_not_disable_other_monitor(self):
        idle.inspect(1000)
        with closing(database.get_connection()) as c:
            c.execute("UPDATE solana_copy_state SET state_json='invalid-json'");c.commit()
        idle.inspect(2800);self.assertEqual(len(self.rows()),2)
        event=json.loads(self.rows()[0]['event_json']);self.assertEqual(event['health'],'state_unavailable')

    def test_frozen_idempotent_retry_and_separate_subjects(self):
        idle.inspect(1000);idle.inspect(2800)
        sent=[]
        def post(*args,**kw):
            sent.append(kw)
            if len(sent)==1:raise TimeoutError()
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'id':'accepted-'+str(len(sent))})
        env={'RESEND_API_KEY':'secret','HEALTH_ALERT_EMAIL_FROM':'bot@example.test','HEALTH_ALERT_EMAIL_TO':'owner@example.test'}
        with patch.dict('os.environ',env):
            self.assertFalse(idle.deliver_one(clock=lambda:2800,post=post))
            self.assertTrue(idle.deliver_one(clock=lambda:2800,post=post))
            self.assertTrue(idle.deliver_one(clock=lambda:2860,post=post))
        self.assertEqual(sent[0]['json'],sent[2]['json']);self.assertEqual(sent[0]['headers']['Idempotency-Key'],sent[2]['headers']['Idempotency-Key'])
        self.assertNotEqual(sent[0]['json']['subject'],sent[1]['json']['subject']);self.assertFalse(sent[0]['allow_redirects'])
        self.assertTrue(all(r['state']=='accepted' for r in self.rows()))
        self.assertNotIn('secret',json.dumps(self.rows()));self.assertIn('RPC timeout',sent[0]['json']['text'])
    def test_leased_send_not_duplicated_and_23_hour_uncertainty_stops_retry(self):
        idle.inspect(1000);idle.inspect(2800)
        with closing(database.get_connection()) as c:
            c.execute("UPDATE crypto_idle_mail SET lease_until=4000,first_attempt=1")
            c.execute("UPDATE crypto_idle_mail SET first_attempt=1 WHERE bot='auto'");c.commit()
        with patch.object(idle,'configured',return_value=True):
            self.assertFalse(idle.deliver_one(clock=lambda:3000,post=lambda *a,**k:self.fail()))
            self.assertFalse(idle.deliver_one(clock=lambda:90000,post=lambda *a,**k:self.fail()))
            self.assertFalse(idle.deliver_one(clock=lambda:90000,post=lambda *a,**k:self.fail()))
        self.assertTrue(all(r['state']=='attention' for r in self.rows()))

if __name__=='__main__':unittest.main()
