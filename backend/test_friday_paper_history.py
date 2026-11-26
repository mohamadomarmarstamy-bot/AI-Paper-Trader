import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from friday_paper_history import analyze


class HistoryTests(unittest.TestCase):
    def test_only_linked_paper_equities_and_no_db_changes(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'paper.db'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE broker_fills(order_id,symbol,side,shares,price,filled_at,raw_order_json,source)')
            def add(ident,side,price,client='',legs=None,asset='us_equity',source='alpaca_paper'):
                at='2026-11-25T15:00:00Z' if side=='BUY' else '2026-11-25T16:00:00Z'
                raw=dict(id=ident,symbol='TEST',side=side.lower(),filled_qty='2',filled_avg_price=str(price),
                         filled_at=at,asset_class=asset,status='filled',client_order_id=client,legs=legs)
                db.execute('INSERT INTO broker_fills VALUES(?,?,?,?,?,?,?,?)',(ident,'TEST',side,2,price,at,json.dumps(raw),source))
            add('entry','BUY',10,'auto-entry-fixture',[{'side':'sell','id':'exit'}])
            add('exit','SELL',12);add('other','BUY',10)
            add('excluded','BUY',10,asset='crypto');add('wrong','BUY',10,source='other')
            db.commit();db.close();original=path.read_bytes()
            r=analyze(path,{'2026-11-25':{'open':'09:30','close':'16:00'}})
            self.assertEqual(r['verified_closed'],1)
            self.assertEqual(r['counts']['paper_equity_orders'],3)
            self.assertEqual(r['counts']['open_incomplete_or_unlinked_entries'],1)
            self.assertEqual(r['groups']['phase:regular']['modeled_net_usd']['10'],3.94)
            self.assertEqual(original,path.read_bytes())
