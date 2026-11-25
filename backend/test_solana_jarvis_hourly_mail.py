import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import database
import jarvis_test_bot as api
import jarvis_local_lab as lab
import jarvis_hourly_mail as mail

RULE={'name':'Pressure & trailing','hypothesis':'Untested buy-pressure hypothesis.',
      'entry':'liquidity >= 50000 and buys_m5 > sells_m5 * 1.5',
      'exit':'peak >= 10 and gain <= peak - 5'}
SOURCES=[{'url':'https://search.brave.com/','title':'Brave Search research; result content not retained','snippet':''}]


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.tmp=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database,'DATABASE_PATH',Path(self.tmp)/'trader.db'))
        self.enterContext(patch.dict(os.environ,{'RESEND_API_KEY':'private-key',
            'HEALTH_ALERT_EMAIL_TO':'owner@example.com','HEALTH_ALERT_EMAIL_FROM':'jarvis@example.com',
            'JARVIS_HOURLY_EMAIL_ENABLED':'true'}))
        api.set_enabled(True)
        self.now=10000
        self.response=Mock(status_code=200)
        self.response.json.return_value={'id':'provider-accepted'}
        self.post=Mock(return_value=self.response)

    def tick(self):return mail.tick(api,clock=lambda:self.now,post=self.post)

    def test_first_send_then_hourly_and_restart_is_deduplicated(self):
        self.assertEqual(self.tick(),'accepted')
        self.assertEqual(self.tick(),'not_due')
        self.now+=3599
        self.assertEqual(self.tick(),'not_due')
        self.now+=1
        self.assertEqual(self.tick(),'accepted')
        self.assertEqual(self.post.call_count,2)
        self.assertNotEqual(self.post.call_args_list[0].kwargs['headers']['Idempotency-Key'],
                            self.post.call_args_list[1].kwargs['headers']['Idempotency-Key'])
        with api.connection() as conn:
            self.assertEqual(mail.status(conn)['pending'],0)

    def test_retry_freezes_payload_and_idempotency_key(self):
        self.post.side_effect=[TimeoutError('uncertain'),self.response]
        self.assertEqual(self.tick(),'retry')
        first=self.post.call_args.kwargs
        # A changing environment cannot change the already queued destination/content.
        with patch.dict(os.environ,{'HEALTH_ALERT_EMAIL_TO':'different@example.com'}):
            self.now+=60
            self.assertEqual(self.tick(),'accepted')
        second=self.post.call_args.kwargs
        self.assertEqual(first['json'],second['json'])
        self.assertEqual(first['headers']['Idempotency-Key'],second['headers']['Idempotency-Key'])
        self.assertEqual(second['json']['to'],['owner@example.com'])
        self.assertNotIn('private-key',json.dumps(second['json']))

    def test_provider_must_return_acceptance_id(self):
        self.response.json.return_value={}
        self.assertEqual(self.tick(),'retry')
        with api.connection() as conn:
            self.assertIsNone(mail.status(conn)['last_accepted'])

    def test_report_counts_actual_realized_results_and_separates_main(self):
        result=lab.submit(api,{'rule':RULE,'sources':SOURCES},now=8000)
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            for bot,mint,closed,pnl in [(result['slot'],'old',5000,3000000),
                                       (result['slot'],'new',9000,-1000000),('jarvis','copied',9000,2000000)]:
                data={'policy':{'local_rule':{'id':result['id']}}}
                conn.execute('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',
                    (bot,mint,4000,closed,'day',pnl,50250000,json.dumps(data)))
            state['bots'][result['slot']]['positions']['open']={'policy':{'local_rule':{'id':result['id']}},'mark':900000000}
            report=mail.collect(conn,state,6400,10000)
        self.assertEqual(report['hour_net'],-1)
        self.assertEqual(report['net'],2)
        self.assertEqual(report['main_hour_net'],2)
        self.assertEqual(report['closed'],1)
        self.assertEqual(report['open'],1)
        self.assertEqual(report['strategies'][0]['coins'],2)
        self.assertEqual([e['kind'] for e in report['events']],['added'])
        payload=mail.render(report)
        self.assertIn('PAPER SIMULATION ONLY',payload['html'])
        self.assertIn('−$1.00',payload['text'])
        self.assertIn('Main Jarvis separately',payload['text'])
        self.assertIn('Brave Search research',payload['html'])

    def test_added_duplicate_and_retired_events_are_durable(self):
        result=lab.submit(api,{'rule':RULE,'sources':SOURCES},now=100)
        lab.submit(api,{'rule':RULE,'sources':SOURCES},now=200)
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            lab.review(conn,state,100+48*3600)
            lab.review(conn,state,101+48*3600)
            events=[r['kind'] for r in conn.execute('SELECT kind FROM local_strategy_events ORDER BY id')]
        self.assertEqual(events,['added','duplicate','inactive'])

    def test_html_escapes_generated_content_and_plain_fallback_exists(self):
        bad={**RULE,'name':'<script>bad</script>','hypothesis':'<img src=x onerror=bad>'}
        lab.submit(api,{'rule':bad,'sources':SOURCES},now=8000)
        self.assertEqual(self.tick(),'accepted')
        body=self.post.call_args.kwargs['json']
        self.assertNotIn('<script>',body['html'])
        self.assertIn('&lt;script&gt;',body['html'])
        self.assertNotIn('<img src=x',body['html'])
        self.assertIn('Entry:',body['text'])
        self.assertIn('Laptop has not checked in recently',body['text'])

    def test_disabled_and_missing_config_do_not_send(self):
        with patch.dict(os.environ,{'JARVIS_HOURLY_EMAIL_ENABLED':'false'}):
            self.assertEqual(self.tick(),'disabled_or_unconfigured')
        with patch.dict(os.environ,{'RESEND_API_KEY':''}):
            self.assertEqual(self.tick(),'disabled_or_unconfigured')
        self.post.assert_not_called()

    def test_downtime_sends_one_catchup_report_without_gaps(self):
        self.tick();self.now+=4*3600
        self.assertEqual(self.tick(),'accepted')
        self.assertEqual(self.tick(),'not_due')
        with api.connection() as conn:
            row=conn.execute('SELECT start,end FROM jarvis_digest_mail ORDER BY end DESC LIMIT 1').fetchone()
            self.assertEqual(row['start'],10000)
            self.assertEqual(row['end'],24400)

    def test_uncertain_delivery_stops_retrying_before_provider_key_expires(self):
        self.post.side_effect=TimeoutError('uncertain')
        self.assertEqual(self.tick(),'retry')
        self.now+=23*3600
        self.assertEqual(self.tick(),'delivery_uncertain')
        with api.connection() as conn:
            self.assertEqual(mail.status(conn)['attention'],1)

    def test_laptop_validator_remains_standalone(self):
        import shutil
        directory=Path(self.tmp)/'validator';directory.mkdir()
        shutil.copy(Path(lab.__file__),directory/'jarvis_local_lab.py')
        subprocess.run([sys.executable,'-I','-c',
            "import sys; sys.path.insert(0,sys.argv[1]); import jarvis_local_lab as x; x.validate("+repr(RULE)+")",str(directory)],check=True)
