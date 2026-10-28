from contextlib import closing
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import database
import solana_copy_trader as copy
import solana_profile as profile
from solana_meme_bot import Conflict
from solana_meme_routes import build_router


class ProfileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'paper.sqlite3'
        for p in (patch.object(database, 'DATABASE_PATH', self.path), patch.dict(os.environ, {'BIRDEYE_API_KEY': 'server-test-key'})):
            p.start(); self.addCleanup(p.stop)
        self.payload = dict(name='Omar', wallet_address='1'*31+'2', api_key='new-synthetic-provider-key', remove_api_key=False, expected_revision=0)
        app = FastAPI()
        def session(request):
            if request.headers.get('X-Test-Owner') != 'yes': raise HTTPException(401)
        app.include_router(build_router(require_session=session))
        self.client = TestClient(app)
        self.headers = {'X-Test-Owner': 'yes', 'Origin': 'http://testserver'}

    def save(self, **changes):
        return profile.save({**self.payload, **changes})

    def test_default_read_does_not_create_profile_or_secret_file(self):
        data = profile.public()
        self.assertEqual(data['revision'], 0)
        self.assertEqual(data['api_key_source'], 'server')
        self.assertEqual(profile.api_key(), 'server-test-key')
        self.assertFalse(Path(str(self.path)+'.profile-key').exists())

    def test_encryption_public_redaction_and_restart_persistence(self):
        data = self.save()
        self.assertNotIn(self.payload['api_key'], str(data))
        self.assertEqual(data['name'], 'Omar')
        self.assertEqual(data['wallet_address'], self.payload['wallet_address'])
        self.assertEqual(data['api_key_source'], 'profile')
        self.assertNotIn(self.payload['api_key'].encode(), self.path.read_bytes())
        key_path = Path(str(self.path)+'.profile-key')
        if os.name != 'nt': self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
        importlib.reload(profile)
        self.assertEqual(profile.api_key(), self.payload['api_key'])
        self.assertEqual(profile.public()['revision'], 1)

    def test_blank_preserves_key_replace_and_remove_suppresses_environment_fallback(self):
        self.save()
        self.save(name='Updated', api_key='', expected_revision=1)
        self.assertEqual(profile.api_key(), self.payload['api_key'])
        self.save(api_key='replacement-test-key', expected_revision=2)
        self.assertEqual(profile.api_key(), 'replacement-test-key')
        data = self.save(api_key='', remove_api_key=True, expected_revision=3)
        self.assertFalse(data['api_key_configured'])
        self.assertEqual(profile.api_key(), '')
        self.assertFalse(copy.configured())
        self.save(api_key='', expected_revision=4)
        self.assertEqual(profile.api_key(), '')

    def test_profile_does_not_change_accounts_or_trader_cohort(self):
        with closing(database.get_connection()) as conn: copy.initialize(conn)
        before = copy.load()
        self.save()
        self.assertEqual(copy.load(), before)

    def test_stale_tab_cannot_overwrite_key_or_details(self):
        self.save()
        with self.assertRaises(Conflict): self.save(api_key='stale-key', name='stale')
        self.assertEqual(profile.api_key(), self.payload['api_key'])
        self.assertEqual(profile.public()['name'], 'Omar')

    def test_invalid_fields_never_save_or_echo_key(self):
        for changes in ({'wallet_address':'not-a-wallet'}, {'name':'x'*101}, {'name':'bad\nname'},
                        {'api_key':'bad\nkey'}, {'api_key':123}, {'remove_api_key':True},
                        {'expected_revision':True}, {'wallet_address':None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.save(**changes)
        self.assertEqual(profile.public()['revision'], 0)
        self.assertFalse(Path(str(self.path)+'.profile-key').exists())

    def test_missing_encryption_file_fails_closed_and_replacement_recovers(self):
        self.save()
        Path(str(self.path)+'.profile-key').unlink()
        self.assertFalse(copy.configured())
        with self.assertRaises(profile.Unavailable): profile.api_key()
        self.assertIn('Replace', profile.public()['error'])
        self.save(api_key='recovery-test-key', expected_revision=1)
        self.assertEqual(profile.api_key(), 'recovery-test-key')

    def test_provider_uses_saved_key_without_returning_it(self):
        self.save()
        response = Mock(status_code=200, content=b'{}')
        response.json.return_value = {'data': {'items':[]}}
        with patch.object(copy.requests, 'get', return_value=response) as get:
            self.assertEqual(copy.Birdeye().request('/test'), {'items':[]})
            self.assertEqual(get.call_args.kwargs['headers']['X-API-KEY'], self.payload['api_key'])

    def test_owner_origin_validation_and_no_store(self):
        for path in ('/meme-coins/profile', '/meme-coins/profile/status'):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post('/meme-coins/profile', json=self.payload).status_code, 401)
        response = self.client.post('/meme-coins/profile', json=self.payload, headers={**self.headers, 'Origin':'https://foreign.example'})
        self.assertEqual(response.status_code, 403)
        response = self.client.post('/meme-coins/profile', json=self.payload, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertNotIn(self.payload['api_key'], response.text)
        response = self.client.get('/meme-coins/profile/status', headers=self.headers)
        self.assertNotIn('encrypted_key', response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        response = self.client.post('/meme-coins/profile', json=self.payload, headers=self.headers)
        self.assertEqual(response.status_code, 409)

    def test_rejected_body_and_storage_failure_do_not_disclose_secret(self):
        for payload in ({**self.payload, 'extra':'private'}, {**self.payload, 'wallet_address':'bad'}, ['bad']):
            response = self.client.post('/meme-coins/profile', json=payload, headers=self.headers)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(self.payload['api_key'], response.text)
        with patch.object(profile, '_cipher', side_effect=OSError('internal detail')):
            response = self.client.post('/meme-coins/profile', json=self.payload, headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('internal detail', response.text)
        self.assertEqual(profile.public()['revision'], 0)


if __name__ == '__main__': unittest.main()
