import asyncio
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
            self.assertEqual(page.status_code,200)
            self.assertEqual(page.headers['cache-control'],'private, no-store')
            self.assertIn('Example &lt;Guest&gt; &amp; Co',page.text)
            self.assertNotIn('Example <Guest>',page.text)
