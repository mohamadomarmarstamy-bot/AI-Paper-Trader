import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import database
import jarvis_test_bot as api
import jarvis_local_lab as local
import jarvis_rotation as rotation
import jarvis_learning as learning

NOW=1800000000
SOURCES=[{'url':'https://example.org/research','title':'Research','snippet':''}]
CHALLENGER={'name':'Pressure challenger','hypothesis':'Test a distinct pressure signal','entry':'buys_m5 > sells_m5 * 2','exit':'gain >= 15'}

class RotationTests(unittest.TestCase):
    def setUp(self):
        tmp=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database,'DATABASE_PATH',Path(tmp)/'db'))
        self.enterContext(patch.dict(os.environ,{'JARVIS_BRAIN_ENABLED':'true'}))
        with api.connection() as conn,conn:
            state,_=api.read(conn);state.update(enabled=True,last_cycle=NOW)
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
        for index in range(10):
            rule={'name':'Original '+str(index),'hypothesis':'Original trial','entry':f'liquidity >= {50000+index}','exit':'gain >= 15'}
            local.submit(api,{'rule':rule,'sources':SOURCES},NOW-10800-index*60)
    def state(self):
        with api.connection() as conn:return api.read(conn)[0]
    def submit(self,now=NOW):return local.submit(api,{'rule':CHALLENGER,'sources':SOURCES},now,allow_rotation=True)
    def advance(self,now):
        with api.connection() as conn,conn:
            state,_=api.read(conn);rotation.advance(conn,state,now)
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
    def test_rotation_is_atomic_preserves_cash_sources_and_history(self):
        original=self.state()['local_rules']['research-10'];before=self.state()['bots']['research-10'].copy()
        result=self.submit();self.assertEqual(result['slot'],'research-10')
        with api.connection() as conn:
            old=conn.execute('SELECT * FROM local_strategies WHERE id=?',(original['id'],)).fetchone()
            self.assertEqual(old['status'],'paused');self.assertEqual(json.loads(old['sources']),SOURCES)
            self.assertEqual(conn.execute("SELECT count(*) FROM local_strategies WHERE status IN ('testing','proven')").fetchone()[0],10)
        self.assertEqual(self.state()['bots']['research-10'],before)
        self.assertEqual(self.state()['local_rules']['research-10']['challenger_ends'],NOW+21600)
    def test_one_rotation_at_a_time_and_held_or_proven_trials_are_protected(self):
        with api.connection() as conn,conn:
            state,_=api.read(conn);state['bots']['research-10']['positions']={'held':{'due':NOW+1000}}
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
            ident=state['local_rules']['research-09']['id']
            conn.execute("UPDATE local_strategies SET status='proven' WHERE id=?",(ident,))
        result=self.submit();self.assertEqual(result['slot'],'research-08')
        extra={**CHALLENGER,'entry':'buys_m5 > sells_m5 * 3'}
        self.assertEqual(local.submit(api,{'rule':extra,'sources':SOURCES},NOW+1,allow_rotation=True)['status'],'full')
        self.assertIn('held',self.state()['bots']['research-10']['positions'])
    def test_no_rotation_before_idle_threshold_or_during_owner_pause(self):
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            for rule in state['local_rules'].values():rule['resumed_at']=NOW-60
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
        self.assertEqual(self.submit()['status'],'full')
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            for rule in state['local_rules'].values():rule.pop('resumed_at')
            state['entry_pause_until_all']=True
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
        self.assertEqual(self.submit()['status'],'full')
    def test_waiting_reason_explains_protected_and_recent_slots_without_overriding_them(self):
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            for rule in state['local_rules'].values():rule['resumed_at']=NOW-60
            protected=state['local_rules']['research-10']['id']
            choice,reason=rotation.selection(conn,state,NOW,api.BUY_COST,protect_control=protected)
        self.assertIsNone(choice)
        self.assertEqual(reason['reason'],'no_safe_rotation')
        self.assertEqual(reason['excluded']['protected'],1)
        self.assertEqual(reason['excluded']['recent_activity'],9)
        self.assertEqual(reason['next_idle_at'],NOW-60+rotation.STALL_SECONDS)

    def test_window_blocks_entries_and_resumes_original_without_reset(self):
        old=self.state()['local_rules']['research-10'];result=self.submit();end=NOW+rotation.WINDOW_SECONDS
        row={'first':end,'buys_m5':20,'sells_m5':1,'liquidity_usd':90000}
        self.assertFalse(local.entry_allowed(self.state(),result['slot'],row,{},end))
        self.advance(end)
        state=self.state();resumed=state['local_rules'][result['slot']]
        self.assertEqual(resumed['id'],old['id']);self.assertEqual(resumed['created'],old['created'])
        self.assertEqual(resumed['resumed_at'],end)
        self.assertFalse(local.entry_allowed(state,result['slot'],{**row,'first':end-1},{},end))
        with api.connection() as conn:
            self.assertEqual(conn.execute('SELECT status FROM local_strategies WHERE id=?',(result['id'],)).fetchone()[0],'paused')
            self.assertEqual(conn.execute('SELECT status FROM challenger_rotations').fetchone()[0],'completed')
    def test_open_challenger_positions_are_drained_without_forced_close(self):
        result=self.submit();end=NOW+rotation.WINDOW_SECONDS
        with api.connection() as conn,conn:
            state,_=api.read(conn);state['bots'][result['slot']]['positions']={'held':{'due':end+100}}
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
        self.advance(end)
        self.assertTrue(self.state()['local_rules'][result['slot']]['entry_paused'])
        self.assertIn('held',self.state()['bots'][result['slot']]['positions'])
        with api.connection() as conn:self.assertEqual(conn.execute('SELECT status FROM challenger_rotations').fetchone()[0],'draining')
    def test_proven_challenger_can_be_retained_but_not_promoted_by_rotation(self):
        result=self.submit()
        with api.connection() as conn,conn:conn.execute("UPDATE local_strategies SET status='proven' WHERE id=?",(result['id'],))
        self.advance(NOW+1)
        self.assertNotIn('challenger_ends',self.state()['local_rules'][result['slot']])
        self.assertIsNone(self.state()['strategy'])
        with api.connection() as conn:self.assertEqual(conn.execute('SELECT status FROM challenger_rotations').fetchone()[0],'retained')
    def test_no_fresh_funding_when_rotating_and_cash_poor_slot_is_skipped(self):
        with api.connection() as conn,conn:
            state,_=api.read(conn);state['bots']['research-10']['cash']=1
            state['bots']['research-09'].update(cash=900_000000,local_funded=False)
            before=state['bots']['research-09']['funding'];conn.execute('UPDATE run SET state=?',(json.dumps(state),))
        result=self.submit();self.assertEqual(result['slot'],'research-09')
        self.assertEqual(self.state()['bots']['research-09']['cash'],900_000000)
        self.assertEqual(self.state()['bots']['research-09']['funding'],before)

class LearningTests(unittest.TestCase):
    def setUp(self):
        tmp=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database,'DATABASE_PATH',Path(tmp)/'db'))
    def add(self,conn,mint,bot='research-01',closed=NOW,pnl=1_000000):
        data={'mint':mint,'policy':{'local_rule':{'id':bot+'-version'}},'pnl':pnl}
        conn.execute('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',
            (bot,mint,closed-60,closed,'day',pnl,api.BUY_COST,json.dumps(data)))
    def test_100_distinct_coins_not_100_positions_and_no_pool_promotion(self):
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            for index in range(99):
                self.add(conn,str(index));self.add(conn,str(index),'research-02',pnl=-2_000000)
            learning.review(conn,state,NOW)
            self.assertIsNone(learning.status(conn,state,NOW)['latest'])
            self.add(conn,'99');learning.review(conn,state,NOW)
            value=learning.status(conn,state,NOW)
            self.assertEqual(value['reviewed_coins'],100)
            self.assertEqual(value['latest']['report']['closed_positions'],199)
            self.assertEqual(len(value['latest']['report']['by_strategy']),2)
            self.assertEqual(conn.execute("SELECT due FROM research_tasks WHERE name='brain-strategy'").fetchone()[0],NOW)
            self.assertIsNone(state['strategy'])
    def test_holdings_and_future_closes_are_not_completed_learning_coins(self):
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            for index in range(100):self.add(conn,str(index))
            state['bots']['research-02']['positions']={'99':{}}
            learning.review(conn,state,NOW)
            self.assertEqual(learning.status(conn,state,NOW)['pending_closed_coins'],99)
            state['bots']['research-02']['positions'].clear();learning.review(conn,state,NOW)
            self.add(conn,'future',closed=NOW+60)
            self.assertEqual(learning.status(conn,state,NOW)['pending_closed_coins'],0)
            self.assertEqual(learning.status(conn,state,NOW+60)['pending_closed_coins'],1)
    def test_checkpoints_are_non_overlapping_and_persistent(self):
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            for index in range(200):self.add(conn,str(index))
            learning.review(conn,state,NOW);learning.review(conn,state,NOW+1)
            self.assertEqual(conn.execute('SELECT count(*) FROM jarvis_learning_checkpoints').fetchone()[0],2)
        with api.connection() as conn:
            state,_=api.read(conn);self.assertEqual(learning.status(conn,state,NOW+2)['reviewed_coins'],200)
        self.assertEqual(api.REVIEW_EVERY,100);self.assertEqual(local.MIN_COINS,100)

if __name__=='__main__':unittest.main()
