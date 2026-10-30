from contextlib import closing
from pathlib import Path
import base64
import json
import tempfile
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
import database
import solana_real_copy as real
import solana_copy_trader as paper
from solana_meme_routes import build_router
from solana_meme_bot import ALPHABET,Conflict


def encode58(data):
    number=int.from_bytes(data,'big');result=''
    while number:number,r=divmod(number,58);result=ALPHABET[r]+result
    return '1'*(len(data)-len(data.lstrip(b'\0')))+result

class RealCopyTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'test.db');p.start();self.addCleanup(p.stop)
        for p in (patch.object(paper,'configured',return_value=False),patch.object(paper,'regular_metrics',return_value={'equity_usd':100000,'net_usd':0})):p.start();self.addCleanup(p.stop)
        self.key=Ed25519PrivateKey.generate();self.address=encode58(self.key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw))
        self.allowed=True
        def auth(request):
            if not self.allowed:raise HTTPException(401)
        app=FastAPI();app.include_router(build_router(require_session=auth));self.client=TestClient(app);self.headers={'Origin':'http://testserver'}
    def proof(self,challenge,key=None):return base64.b64encode((key or self.key).sign(challenge['message'].encode())).decode()
    def test_wallet_proof_persists_without_trading_authority(self):
        c=real.challenge(self.address,0,'https://app.example',now=1000)
        self.assertIn('Live trading remains DISABLED',c['message']);self.assertIn('Origin: https://app.example',c['message'])
        real.verify(c['challenge_id'],self.proof(c),'https://app.example',now=1010)
        status=real.public();self.assertTrue(status['wallet']['ownership_verified']);self.assertEqual(status['wallet']['address'],self.address)
        self.assertFalse(status['can_start']);self.assertFalse(status['live_execution_enabled']);self.assertFalse(status['wallet']['spending_authorized'])
        self.assertEqual(status['live_trade_count'],0);self.assertEqual(status['live_positions'],[]);self.assertIsNone(status['live_pnl_usd'])
    def test_wrong_key_origin_expiry_and_replay_are_rejected(self):
        c=real.challenge(self.address,0,'https://app.example',now=1000)
        for signature,origin,now in ((self.proof(c,Ed25519PrivateKey.generate()),'https://app.example',1010),(self.proof(c),'https://other.example',1010),(self.proof(c),'https://app.example',1300)):
            with self.assertRaises(ValueError):real.verify(c['challenge_id'],signature,origin,now=now)
        self.assertEqual(real.wallet()['wallet_address'],'')
        real.verify(c['challenge_id'],self.proof(c),'https://app.example',now=1010)
        with self.assertRaises(ValueError):real.verify(c['challenge_id'],self.proof(c),'https://app.example',now=1011)
    def test_unlink_invalidates_old_challenge_and_revision(self):
        c=real.challenge(self.address,0,'https://app.example',now=1000)
        real.unlink(0)
        with self.assertRaises(ValueError):real.verify(c['challenge_id'],self.proof(c),'https://app.example',now=1010)
        with self.assertRaises(Conflict):real.challenge(self.address,0,'https://app.example',now=1010)
    def test_new_challenge_supersedes_previous_signing_prompt(self):
        a=real.challenge(self.address,0,'https://app.example',now=1000);b=real.challenge(self.address,0,'https://app.example',now=1001)
        with self.assertRaises(ValueError):real.verify(a['challenge_id'],self.proof(a),'https://app.example',now=1002)
        real.verify(b['challenge_id'],self.proof(b),'https://app.example',now=1002)
    def test_paper_sync_has_one_canonical_source_and_never_imports_enabled_or_funds(self):
        before=paper.load()[0]
        with closing(database.get_connection()) as conn:
            paper.initialize(conn);state=paper.initial();state['config'].update(enabled=True,trade_usd=123,reentry_cooldown_seconds=600)
            state['traders']=[{'wallet':self.address,'name':'New research follow','active':True,'qualification':'public_ranked_research'}]
            conn.execute('UPDATE solana_copy_state SET state_json=? WHERE id=1',(json.dumps(state),));conn.commit()
        first=real.public();blueprint=first['paper_sync']['blueprint']
        self.assertEqual(blueprint['settings']['trade_usd'],123);self.assertEqual(blueprint['traders'][0]['wallet'],self.address)
        self.assertNotIn('enabled',blueprint['settings']);self.assertNotIn('paper_capital_usd',blueprint['settings']);self.assertFalse(first['live_execution_enabled'])
        unchanged=paper.load();self.assertEqual(real.public()['paper_sync']['snapshot'],first['paper_sync']['snapshot']);self.assertEqual(paper.load(),unchanged)
        paper.configure({'trade_usd':150},0)
        second=real.public();self.assertEqual(second['paper_sync']['blueprint']['settings']['trade_usd'],150);self.assertNotEqual(first['paper_sync']['snapshot'],second['paper_sync']['snapshot']);self.assertFalse(second['can_start'])
    def test_real_workspace_stylesheet_is_served_by_production_router(self):
        response=self.client.get('/real-copy.css')
        self.assertEqual(response.status_code,200);self.assertIn('text/css',response.headers['content-type'])
        self.assertIn('#real-copy-section .rc-hero',response.text)

    def test_http_auth_origin_body_and_hard_execution_lock(self):
        self.allowed=False;self.assertEqual(self.client.get('/real-copy/status').status_code,401)
        self.assertEqual(self.client.post('/real-copy/start',json={}).status_code,401)
        self.allowed=True
        self.assertEqual(self.client.post('/real-copy/start',json={}).status_code,403)
        self.assertEqual(self.client.post('/real-copy/start',json={'enabled':True},headers=self.headers).status_code,423)
        self.assertEqual(self.client.post('/real-copy/challenge',json={'wallet_address':self.address,'expected_revision':0,'private_key':'never'},headers=self.headers).status_code,422)
        self.assertEqual(self.client.post('/real-copy/verify',json={'challenge_id':'fake','signature':'bad'},headers=self.headers).status_code,422)
        c=self.client.post('/real-copy/challenge',json={'wallet_address':self.address,'expected_revision':0},headers=self.headers).json()
        linked=self.client.post('/real-copy/verify',json={'challenge_id':c['challenge_id'],'signature':self.proof(c)},headers=self.headers)
        self.assertEqual(linked.status_code,200);self.assertFalse(linked.json()['can_start'])
        self.assertEqual(self.client.post('/real-copy/start',json={},headers=self.headers).status_code,423)
        self.assertEqual(self.client.get('/real-copy/status').headers['cache-control'],'no-store')
        self.assertEqual(self.client.post('/real-copy/unlink',json={'expected_revision':1},headers=self.headers).status_code,200)

if __name__=='__main__':unittest.main()
