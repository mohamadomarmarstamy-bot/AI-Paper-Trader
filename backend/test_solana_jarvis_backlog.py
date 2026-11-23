from pathlib import Path
import asyncio
import hashlib
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI, HTTPException
import database
import jarvis_backlog as guard
import jarvis_test_bot as jarvis
from solana_meme_routes import build_router


class BacklogTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database, 'DATABASE_PATH', Path(directory) / 'original.db'))
        self.enterContext(patch.dict('os.environ', {'APP_PUBLIC_ORIGIN': 'https://test', 'RESEND_API_KEY': 'synthetic',
            'HEALTH_ALERT_EMAIL_FROM': 'bot@example.com', 'HEALTH_ALERT_EMAIL_TO': 'owner@example.com',
            'JARVIS_ENTRY_CATCHUP_REQUEST': ''}, clear=True))
        self.now = 2000000000
        self.state = jarvis.initial()
        self.state['enabled'] = True
        for i in range(100):
            self.state['bots']['instant-step']['positions'][str(i)] = {
                'mint': str(i), 'due': self.now - 10, 'pending': None, 'opened': self.now - 20}
        self.store()

    def store(self):
        with jarvis.connection() as conn, conn:
            conn.execute('UPDATE run SET state=? WHERE id=1', (json.dumps(self.state),))

    def read(self):
        with jarvis.connection() as conn:
            return jarvis.read(conn)[0]

    def enqueue(self):
        with jarvis.connection() as conn, conn:
            guard.enqueue(conn, self.state, self.now, 'https://test')
            row = conn.execute('SELECT * FROM backlog_mail ORDER BY created LIMIT 1').fetchone()
            return dict(row) if row else None

    def token(self, minutes=10):
        row = self.enqueue()
        lines = json.loads(row['payload'])['text'].splitlines()
        return next(line.split('#')[1] for line in lines if line.startswith(f'Pause {minutes} minutes:'))

    def test_threshold_and_episode_deduplication_and_rearm(self):
        self.state['bots']['instant-step']['positions'].pop('99')
        self.assertIsNone(self.enqueue())
        self.state['bots']['instant-step']['positions']['99'] = {'mint': '99', 'due': self.now}
        first = self.enqueue()
        self.assertEqual(self.enqueue()['id'], first['id'])
        with jarvis.connection() as conn, conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM backlog_mail').fetchone()[0], 1)
            low = jarvis.initial()
            guard.enqueue(conn, low, self.now + 1, 'https://test')
            guard.enqueue(conn, low, self.now + 61, 'https://test')
            guard.enqueue(conn, self.state, self.now + 62, 'https://test')
            self.assertEqual(conn.execute('SELECT count(*) FROM backlog_mail').fetchone()[0], 2)

    def test_link_is_expiring_single_use_and_cannot_extend_or_resume(self):
        token = self.token(30)
        result = guard.email_control(token, lambda: self.now)
        state = self.read()
        self.assertTrue(result['applied'])
        self.assertTrue(state['enabled'])
        self.assertEqual(state['entry_pause_until'], self.now + 1800)
        self.assertEqual(len(state['bots']['instant-step']['positions']), 100)
        self.assertFalse(guard.email_control(token, lambda: self.now + 60)['applied'])
        self.assertEqual(self.read()['entry_pause_until'], self.now + 1800)
        other = self.token(60)
        self.assertFalse(guard.email_control(other, lambda: self.now + 61)['applied'])
        with self.assertRaises(ValueError): guard.email_control(token, lambda: self.now + 21600)
        with self.assertRaises(ValueError): guard.email_control('x' * 43, lambda: self.now)
        with jarvis.connection() as conn:
            self.assertEqual(conn.execute('SELECT digest FROM backlog_action WHERE digest=?',
                (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()[0], hashlib.sha256(token.encode()).hexdigest())

    def test_timers_expire_resume_clears_catchup_but_honors_master_toggle(self):
        state = jarvis.initial()
        state['entry_catchup_pause'] = {'active': True}
        guard.apply_control(state, 10, self.now)
        self.assertTrue(guard.timer_active(state, self.now + 599))
        self.assertFalse(guard.timer_active(state, self.now + 600))
        self.assertTrue(state['entry_catchup_pause']['active'])
        guard.apply_control(state, 0, self.now)
        self.assertFalse(state['entry_catchup_pause']['active'])
        self.assertFalse(state['enabled'])
        for value in (True, 11, -1, '10', 10.0):
            with self.assertRaises(ValueError): guard.apply_control(state, value, self.now)

    def test_email_outbox_retries_idempotently_and_does_not_repeat_success(self):
        post = Mock(return_value=Mock(status_code=503))
        guard.mail_tick(lambda: self.now, post)
        first = post.call_args.kwargs['headers']['Idempotency-Key']
        post.return_value.status_code = 202
        guard.mail_tick(lambda: self.now + 61, post)
        self.assertEqual(post.call_args.kwargs['headers']['Idempotency-Key'], first)
        guard.mail_tick(lambda: self.now + 100, post)
        self.assertEqual(post.call_count, 2)
        self.assertIn('Pause buys for 60 minutes', post.call_args.kwargs['json']['html'])

    def test_waiting_count_and_disabled_alert(self):
        state = jarvis.initial()
        state['bots']['instant-step']['positions']['x'] = {'mint': 'x', 'due': self.now + 100, 'mark_stale': True}
        state['bots']['instant-trim']['positions']['x'] = {'mint': 'x', 'due': self.now - 1}
        self.assertEqual(guard.waiting(state, self.now), {'positions': 2, 'coins': 1})
        self.state['backlog_settings'] = {'enabled': False, 'threshold': 100}
        self.store()
        post = Mock()
        guard.mail_tick(lambda: self.now, post)
        post.assert_not_called()

    def add_trade(self, conn, mint, closed):
        conn.execute('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES(?,?,?,?,?,?,?,?)',
                     ('instant-step', mint, self.now - 10, closed, 'test', 0, jarvis.BUY_COST, '{}'))

    def start_timer(self, minutes=10):
        guard.apply_control(self.state, minutes, self.now)
        with jarvis.connection() as conn, conn:
            guard.record_pause(conn, self.state, minutes, self.now)
        self.store()

    def test_expiry_email_counts_only_closes_during_timer_and_sends_once_even_alert_disabled(self):
        self.state['backlog_settings'] = {'enabled': False, 'threshold': 100}
        with jarvis.connection() as conn, conn:
            self.add_trade(conn, 'old', self.now - 1)
        self.start_timer()
        with jarvis.connection() as conn, conn:
            self.add_trade(conn, 'during', self.now + 300)
            self.add_trade(conn, 'after', self.now + 601)
        post = Mock(return_value=Mock(status_code=202))
        guard.mail_tick(lambda: self.now + 599, post)
        post.assert_not_called()
        guard.mail_tick(lambda: self.now + 610, post)
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['subject'], 'Jarvis paper: buys back up and running')
        self.assertIn('1 Jarvis paper positions closed during this timer.', payload['text'])
        self.assertIn('100 positions across 100 distinct coins', payload['text'])
        guard.mail_tick(lambda: self.now + 650, post)
        self.assertEqual(post.call_count, 1)

    def test_expiry_email_does_not_claim_resumption_while_catchup_or_master_stop_is_active(self):
        for master, catchup in ((True, True), (False, False)):
            with self.subTest(master=master, catchup=catchup), jarvis.connection() as conn, conn:
                conn.execute('DELETE FROM backlog_pause_report')
                conn.execute('DELETE FROM backlog_mail')
                state = jarvis.initial()
                state.update(enabled=master, entry_catchup_pause={'active': catchup})
                guard.apply_control(state, 10, self.now)
                guard.record_pause(conn, state, 10, self.now)
                guard.enqueue_expired(conn, state, self.now + 600, 'https://test')
                payload = json.loads(conn.execute('SELECT payload FROM backlog_mail').fetchone()[0])
                self.assertIn('buys still paused', payload['subject'])
                self.assertIn('paper buys remain paused', payload['text'])

    def test_replacement_and_manual_resume_cancel_unexpired_timer_reports(self):
        self.start_timer()
        with jarvis.connection() as conn, conn:
            guard.apply_control(self.state, 30, self.now + 10)
            guard.record_pause(conn, self.state, 30, self.now + 10)
            guard.enqueue_expired(conn, self.state, self.now + 600, 'https://test')
            self.assertEqual(conn.execute('SELECT count(*) FROM backlog_mail').fetchone()[0], 0)
            guard.apply_control(self.state, 0, self.now + 700)
            guard.record_pause(conn, self.state, 0, self.now + 700)
            guard.enqueue_expired(conn, self.state, self.now + 2000, 'https://test')
            self.assertEqual(conn.execute('SELECT count(*) FROM backlog_mail').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM backlog_pause_report WHERE cancelled IS NOT NULL').fetchone()[0], 2)

    def test_existing_email_timer_recovers_exact_start_and_only_tracks_once(self):
        token = self.token(30)
        guard.email_control(token, lambda: self.now)
        with jarvis.connection() as conn, conn:
            conn.execute('DELETE FROM backlog_pause_report')
            state, _ = jarvis.read(conn)
            self.add_trade(conn, 'during-existing', self.now + 60)
            guard.track_existing_timer(conn, state, self.now + 120)
            guard.track_existing_timer(conn, state, self.now + 130)
            reports = conn.execute('SELECT * FROM backlog_pause_report').fetchall()
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]['started'], self.now)
            guard.enqueue_expired(conn, state, self.now + 1800, 'https://test')
            payload = json.loads(conn.execute("SELECT payload FROM backlog_mail WHERE id LIKE 'resume/%'").fetchone()[0])
            self.assertIn('1 Jarvis paper positions closed during this timer.', payload['text'])

    def test_older_app_timer_does_not_invent_unknown_start(self):
        state = jarvis.initial()
        state.update(enabled=True, entry_pause_until=self.now + 600)
        with jarvis.connection() as conn, conn:
            guard.track_existing_timer(conn, state, self.now)
            report = conn.execute('SELECT * FROM backlog_pause_report').fetchone()
            self.assertTrue(report['id'].startswith('partial/'))
            self.add_trade(conn, 'tracked', self.now + 100)
            guard.enqueue_expired(conn, state, self.now + 600, 'https://test')
            payload = json.loads(conn.execute('SELECT payload FROM backlog_mail').fetchone()[0])
            self.assertIn('1 Jarvis paper positions closed since expiry-report tracking started', payload['text'])

    def test_all_positions_closed_resumes_early_and_sends_one_summary(self):
        self.start_timer(30)
        self.state['entry_catchup_pause'] = {'active': True}
        with jarvis.connection() as conn, conn:
            self.add_trade(conn, 'last-close', self.now + 100)
            # Even an unavailable route keeps the timer active until the holding closes.
            self.state['bots']['instant-step']['positions'] = {'retry': {'mint': 'retry', 'due': self.now + 1000, 'mark_stale': True}}
            self.assertFalse(guard.resume_when_empty(conn, self.state, self.now + 100))
            self.state['bots']['instant-step']['positions'].clear()
            self.assertTrue(guard.resume_when_empty(conn, self.state, self.now + 100))
            self.assertFalse(self.state['entry_catchup_pause']['active'])
            self.assertEqual(self.state['entry_pause_until'], 0)
            self.assertTrue(self.state['enabled'])
            payload = json.loads(conn.execute('SELECT payload FROM backlog_mail').fetchone()[0])
            self.assertIn('buys resumed early', payload['subject'])
            self.assertIn('1 Jarvis paper positions closed during this timer.', payload['text'])
            self.assertIn('0 positions across 0 distinct coins', payload['text'])
            self.assertFalse(guard.resume_when_empty(conn, self.state, self.now + 110))
            guard.enqueue_expired(conn, self.state, self.now + 1800, 'https://test')
            self.assertEqual(conn.execute('SELECT count(*) FROM backlog_mail').fetchone()[0], 1)

    def test_early_resume_does_not_override_master_stop_or_no_active_timer(self):
        self.state['bots']['instant-step']['positions'].clear()
        self.start_timer()
        self.state['enabled'] = False
        with jarvis.connection() as conn, conn:
            self.assertFalse(guard.resume_when_empty(conn, self.state, self.now + 1))
            self.state['enabled'] = True
            self.state['entry_pause_until'] = 0
            self.assertFalse(guard.resume_when_empty(conn, self.state, self.now + 2))
            self.assertEqual(conn.execute('SELECT count(*) FROM backlog_mail').fetchone()[0], 0)

    def test_http_auth_origin_and_preview_contract(self):
        app = FastAPI()
        def auth(request):
            if request.headers.get('x-owner') != 'yes': raise HTTPException(401)
        app.include_router(build_router(require_session=auth))
        token = self.token()
        async def request(method, path, **kwargs):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://test') as client:
                return await client.request(method, path, **kwargs)
        def req(method, path, **kwargs): return asyncio.run(request(method, path, **kwargs))
        path = '/jarvis-test-bot/email-pause'
        preview = req('GET', path)
        self.assertEqual(preview.status_code, 200)
        self.assertIn('data-auto="no"', preview.text)
        self.assertEqual(self.read().get('entry_pause_until', 0), 0)
        clicked = req('GET', path, headers={'sec-fetch-user': '?1', 'sec-fetch-mode': 'navigate', 'sec-fetch-dest': 'document'})
        self.assertIn('data-auto="yes"', clicked.text)
        self.assertEqual(self.read().get('entry_pause_until', 0), 0)
        self.assertEqual(req('POST', path, json={'token': token}, headers={'origin': 'https://evil'}).status_code, 403)
        self.assertEqual(req('POST', '/jarvis-test-bot/buy-pause', json={'minutes': 0}, headers={'origin': 'https://test'}).status_code, 401)
        self.assertEqual(req('POST', '/jarvis-test-bot/backlog-settings', json={'enabled': True, 'threshold': 1}, headers={'x-owner': 'yes', 'origin': 'https://evil'}).status_code, 403)
        self.assertEqual(req('POST', path, json={'token': token, 'minutes': 0}, headers={'origin': 'https://test'}).status_code, 422)
        response = req('POST', path, json={'token': token}, headers={'origin': 'https://test'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['applied'])

if __name__ == '__main__': unittest.main()
