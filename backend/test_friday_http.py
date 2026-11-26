import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from test_friday_research import SCAN, NOW


class FridayHttpTests(unittest.TestCase):
    def test_dashboard_to_authenticated_review(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'DATABASE_PATH': str(Path(d)/'trader.db')}):
            import database
            with patch.object(database, 'DATABASE_PATH', Path(d)/'trader.db'):
                import main
                with patch.object(main, 'APP_SESSION_SECRET', 'friday-fixture'), patch('friday_routes.latest_friday_scan', return_value=SCAN):
                    asyncio.run(self.check(main))

    async def check(self, main):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://testserver') as client:
            self.assertEqual((await client.post('/ai-friday/research')).status_code, 401)
            anonymous = await client.get('/ai-friday/paper-history')
            self.assertEqual(anonymous.status_code,401)
            self.assertEqual(anonymous.headers['cache-control'],'private, no-store')
            client.cookies.set(main.APP_SESSION_COOKIE, main.create_app_session_token())
            historical = await client.get('/ai-friday/paper-history')
            self.assertEqual(historical.headers['cache-control'],'private, no-store')
            self.assertIn(historical.status_code, (200,503))
            self.assertIn('href="/ai-friday"', (await client.get('/')).text)
            self.assertIn('Record latest stock scan', (await client.get('/ai-friday')).text)
            self.assertEqual((await client.get('/js/friday.js')).status_code, 200)
            self.assertEqual((await client.post('/ai-friday/research')).status_code, 403)
            response = await client.post('/ai-friday/research', headers={'origin':'http://testserver'})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['execution_enabled'])
            self.assertEqual((await client.get('/ai-friday/research')).json()['latest']['finished'], NOW)
