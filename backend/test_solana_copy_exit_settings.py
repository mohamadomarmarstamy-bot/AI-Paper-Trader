from contextlib import closing
from pathlib import Path
import json
import tempfile
import time
import unittest
from unittest.mock import patch
import database
import solana_copy_trader as paper
import solana_real_worker as live
import solana_copy_exit_settings as exits
from solana_meme_bot import Conflict


class ExitSettingsTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(tmp.name)/'exits.db');p.start();self.addCleanup(p.stop)
        with closing(database.get_connection()) as conn:
            paper.initialize(conn);live.initialize(conn)

    def apply(self):
        return exits.apply_split(paper.load()[0]['settings_revision'],live.load()[1],exits.settings()['revision'],exits.ACK)

    def test_deployment_and_reads_do_not_change_the_existing_shared_rule(self):
        before=(paper.load(),live.load())
        self.assertFalse(exits.settings()['independent'])
        self.assertEqual(exits.live_stop({'stop_loss_percent':17}),17)
        self.assertEqual((paper.load(),live.load()),before)

    def test_split_is_persistent_and_preserves_balances_enablement_and_other_exits(self):
        with closing(database.get_connection()) as conn:
            runtime,_=live.load();runtime['enabled']=True;runtime['realized_lamports']=-1234
            runtime['positions']={reason:{'protection':{'stop_loss_percent':20,'profit_take_enabled':True},
                'pending_reason':reason,'last_exit_attempt_at':123} for reason in ('manual','source_sell','stop_loss')}
            conn.execute('UPDATE solana_real_run SET state_json=? WHERE id=1',(json.dumps(runtime),));conn.commit()
        source_before=paper.load()[0];result=self.apply();source=paper.load()[0];runtime=live.load()[0]
        self.assertEqual(result['stop_loss_percent'],40);self.assertTrue(result['independent'])
        self.assertEqual(source['config']['stop_loss_percent'],0)
        self.assertEqual(source['cash_usd'],source_before['cash_usd'])
        self.assertEqual(source['config']['profit_take_enabled'],source_before['config']['profit_take_enabled'])
        self.assertTrue(runtime['enabled']);self.assertEqual(runtime['realized_lamports'],-1234)
        self.assertEqual(runtime['positions']['manual']['pending_reason'],'manual')
        self.assertEqual(runtime['positions']['source_sell']['pending_reason'],'source_sell')
        self.assertIsNone(runtime['positions']['stop_loss']['pending_reason'])
        self.assertTrue(all(p['protection']['stop_loss_percent']==40 for p in runtime['positions'].values()))
        self.assertEqual(exits.live_stop({'stop_loss_percent':0}),40)
        self.assertEqual(exits.live_stop({'stop_loss_percent':80}),40)
        with closing(database.get_connection()) as conn:
            audit=json.loads(conn.execute('SELECT audit_json FROM solana_live_stop_settings').fetchone()[0])
        self.assertEqual(audit['live_previous_position_stops']['manual'],20)

    def test_pending_transactions_and_active_live_leases_reject_both_changes(self):
        before=(paper.load(),live.load())
        with closing(database.get_connection()) as conn:
            conn.execute('INSERT INTO solana_real_orders VALUES(?,?,?,?)',('pending',time.time(),'signed','{}'));conn.commit()
        with self.assertRaises(Conflict):self.apply()
        self.assertEqual((paper.load(),live.load()),before);self.assertFalse(exits.settings()['independent'])
        with closing(database.get_connection()) as conn:
            conn.execute('DELETE FROM solana_real_orders')
            conn.execute('UPDATE solana_real_run SET lease_until=?',(time.time()+60,));conn.commit()
        with self.assertRaises(Conflict):self.apply()
        self.assertEqual((paper.load(),live.load()),before);self.assertFalse(exits.settings()['independent'])

    def test_stale_revisions_and_missing_acknowledgement_cannot_apply(self):
        before=(paper.load(),live.load())
        for revisions in ((1,0,0),(0,0,1)):
            with self.assertRaises(Conflict):exits.apply_split(*revisions,exits.ACK)
        for ack in ('',None):
            with self.assertRaises(ValueError):exits.apply_split(0,0,0,ack)
        with self.assertRaises(ValueError):exits.apply_split(False,0,0,exits.ACK)
        self.assertEqual((paper.load(),live.load()),before)

    def test_heartbeat_revision_does_not_reject_or_overwrite_the_current_live_ledger(self):
        with closing(database.get_connection()) as conn:
            runtime,_=live.load()
            runtime.update(enabled=True,realized_lamports=-9876,event_cursor=456,last_cycle=time.time())
            conn.execute('UPDATE solana_real_run SET revision=revision+10,state_json=? WHERE id=1',(json.dumps(runtime),));conn.commit()
        # The owner confirmed using revision 0, before ten normal worker ticks.
        exits.apply_split(0,0,0,exits.ACK)
        current,revision=live.load()
        self.assertEqual(current,runtime)
        self.assertEqual(revision,11)
        self.assertEqual(paper.load()[0]['config']['stop_loss_percent'],0)
        self.assertEqual(exits.live_stop({'stop_loss_percent':0}),40)

    def test_applying_while_paused_does_not_start_real_trading(self):
        self.apply();self.assertFalse(live.load()[0]['enabled'])
        self.assertFalse(paper.load()[0]['config']['enabled'])


if __name__=='__main__':unittest.main()
