from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import database
import solana_exit_alerts as alerts
import solana_real_worker as live

class ExitAlertsTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(tmp.name)/'test.db');p.start();self.addCleanup(p.stop)
        self.now=1000
        with closing(database.get_connection()) as c: live.initialize(c)
        alerts.observe(lambda:self.now)
    def order(self,status='submitted',ident='sale',created=1000):
        o={'id':ident,'side':'sell','position_id':'holding','mint':'mint','created_at':created,'status':status,'send_started_at':created,'signature':'sig','reason':'stop_loss'}
        with closing(database.get_connection()) as c:
            c.execute('INSERT OR REPLACE INTO solana_real_orders VALUES (?,?,?,?)',(ident,created,status,json.dumps(o)));c.commit()
    def mails(self):
        with closing(database.get_connection()) as c:return [dict(r) for r in c.execute('SELECT * FROM solana_exit_mail')]
    def tick(self,now):alerts.observe(lambda:now)
    def test_pending_threshold_and_dedup(self):
        self.order();self.tick(1029);self.assertEqual(self.mails(),[])
        self.tick(1030);self.tick(1032);self.assertEqual(len(self.mails()),1)
    def test_failure_then_retry_then_recovery(self):
        self.order('failed');self.tick(1001);self.tick(1002)
        self.order('rejected','retry',1003);self.tick(1004)
        self.assertEqual(len(self.mails()),1)
        self.order('confirmed','retry',1003);self.tick(1005);self.tick(1006)
        self.assertEqual(len(self.mails()),2)
        self.assertIn('recovered',json.loads(self.mails()[-1]['payload_json'])['subject'])
    def test_long_pending_recovery(self):
        self.order();self.tick(1100);self.order('confirmed');self.tick(1102)
        self.assertEqual(len(self.mails()),2)
    def test_pending_escalates_to_failure_once(self):
        self.order();self.tick(1030);self.order('failed');self.tick(1032);self.tick(1034)
        self.assertEqual(len(self.mails()),2)
    def test_no_historical_failure_on_first_start(self):
        with closing(database.get_connection()) as c:c.execute('DELETE FROM solana_exit_alert_state');c.commit()
        self.order('failed',created=900);self.tick(1001);self.assertEqual(self.mails(),[])
    def test_provider_retry_uses_stable_payload_and_key(self):
        self.order('failed');self.tick(1001)
        env={'RESEND_API_KEY':'secret','HEALTH_ALERT_EMAIL_FROM':'from@example.test','HEALTH_ALERT_EMAIL_TO':'to@example.test'}
        post=Mock(side_effect=TimeoutError)
        with patch.dict('os.environ',env):
            self.assertFalse(alerts.deliver_one(lambda:1002,post))
            first=post.call_args.kwargs
            response=Mock();response.json.return_value={'id':'accepted'};post.side_effect=None;post.return_value=response
            self.assertTrue(alerts.deliver_one(lambda:1012,post))
            self.assertEqual(first['json'],post.call_args.kwargs['json'])
            self.assertEqual(first['headers']['Idempotency-Key'],post.call_args.kwargs['headers']['Idempotency-Key'])
            self.assertFalse(alerts.deliver_one(lambda:1014,post))
    def test_upstream_errors_not_emailed(self):
        self.order('failed');self.tick(1001)
        payload=json.loads(self.mails()[0]['payload_json']);self.assertNotIn('error',payload)
    def test_intentional_profit_deferral_is_not_failure(self):
        self.order('rejected')
        with closing(database.get_connection()) as c:
            o=json.loads(c.execute('SELECT details_json FROM solana_real_orders').fetchone()[0])
            o.update(reason='profit_take_half',error='Profit trim deferred: target not met')
            c.execute('UPDATE solana_real_orders SET details_json=?',(json.dumps(o),));c.commit()
        self.tick(1001);self.assertEqual(self.mails(),[])
    def test_adaptive_delay(self):
        self.assertEqual(live.cycle_delay(),5)
        self.order();self.assertEqual(live.cycle_delay(),1)
        self.order('confirmed')
        with closing(database.get_connection()) as c:
            s=live.initial();s['positions']={'x':{}}
            c.execute('UPDATE solana_real_run SET state_json=?',(json.dumps(s),));c.commit()
        self.assertEqual(live.cycle_delay(),2)

if __name__=='__main__':unittest.main()
