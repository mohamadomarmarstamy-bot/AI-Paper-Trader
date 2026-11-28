import asyncio
import hashlib
import re
import secrets
import time
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx


class DedicationTests(unittest.TestCase):
    def test_owner_session_only_and_private_server_rendering(self):
        with tempfile.TemporaryDirectory() as directory:
            import database
            with patch.object(database,'DATABASE_PATH',Path(directory)/'test.sqlite3'):
                import main
                with patch.object(main,'APP_SESSION_SECRET','fixture-signing-key'), patch.object(main,'APP_ACCESS_PASSWORD','fixture-password'), patch.dict(os.environ,{'MOM_DEDICATION_NAME':'Example <Guest> & Co'}):
                    asyncio.run(self.check(main))

    async def check(self,main):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='https://testserver') as client:
            for cookie in ('','invalid'):
                client.cookies.set(main.APP_SESSION_COOKIE,cookie)
                response=await client.get('/i-love-mom')
                self.assertEqual(response.status_code,401)
                self.assertEqual(response.headers['cache-control'],'private, no-store')
                self.assertNotIn('Example',response.text)
            for path in ('/mom.html','/templates/mom.html','/js/mom.html'):
                self.assertEqual((await client.get(path)).status_code,404)
            client.cookies.clear()
            self.assertEqual((await client.post('/login',data={'password':'fixture-password'})).status_code,200)
            dashboard=await client.get('/')
            self.assertIn('href="/i-love-mom"',dashboard.text)
            self.assertNotIn('Example &lt;Guest&gt;',dashboard.text)
            page=await client.get('/i-love-mom')
            self.assertEqual(page.headers['cache-control'],'private, no-store')
            self.assertEqual(page.status_code,503)
            salt = secrets.token_bytes(16)
            digits = '007931'
            digest = hashlib.scrypt(digits.encode(),salt=salt,n=16384,r=8,p=1,dklen=32)
            verifier = f'scrypt${salt.hex()}${digest.hex()}'
            with patch.dict(os.environ, {'MOM_DEDICATION_PASSCODE_HASH':verifier}):
                async def screen():
                    return await client.get('/i-love-mom')
                locked = await screen()
                self.assertEqual(locked.status_code,200)
                self.assertNotIn('Example',locked.text)
                self.assertNotIn('audio',locked.text)
                csrf = re.search(r'name="csrf" value="([^"]+)"',locked.text)[1]
                async def submit(code, **kwargs):
                    return await client.post('/i-love-mom/unlock',data={'csrf':csrf,'passcode':code},**kwargs)
                self.assertEqual((await submit(digits,headers={'Sec-Fetch-Site':'cross-site'})).status_code,403)
                self.assertEqual((await submit('1'*512)).status_code,413)
                self.assertEqual((await submit(digits.lstrip('0'))).status_code,403)
                response = await submit(digits)
                self.assertEqual(response.status_code,303)
                for flag in ('Secure','HttpOnly','SameSite=strict','Path=/i-love-mom','Max-Age=600'):
                    self.assertIn(flag,response.headers['set-cookie'])
                opened = await screen()
                self.assertIn('Example &lt;Guest&gt; &amp; Co',opened.text)
                self.assertIn('heart-clip',opened.text)
                with patch('mom_dedication.time.time',return_value=time.time()+601):
                    self.assertNotIn('heart-clip',(await screen()).text)
                with patch.dict(os.environ,{'MOM_DEDICATION_PASSCODE_HASH':verifier[:-1]+('0' if verifier[-1]!='0' else '1')}):
                    self.assertNotIn('heart-clip',(await screen()).text)
                with patch.dict(os.environ,{'MOM_DEDICATION_PASSCODE_HASH':'invalid'}):
                    self.assertEqual((await screen()).status_code,503)
                self.assertEqual((await client.post('/i-love-mom/lock',data={'csrf':csrf})).status_code,303)
                self.assertNotIn('Example',(await screen()).text)
                for _ in range(3):
                    self.assertEqual((await submit('999')).status_code,403)
                self.assertEqual((await submit(digits)).status_code,429)
