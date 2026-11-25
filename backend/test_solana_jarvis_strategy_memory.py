import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import database
import jarvis_test_bot as api
import jarvis_brain as brain
import jarvis_local_lab as local
import jarvis_strategy_memory as memory

NOW=1800000000
RULE={'name':'Pressure','hypothesis':'Pressure test','entry':'buys_m5 > sells_m5 * 2','exit':'gain >= 15'}
SOURCES=[{'url':'https://example.org','title':'Research','snippet':''}]

class ResearchMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database,'DATABASE_PATH',Path(self.tmp)/'db'))
        with api.connection() as conn,conn:
            state,_=api.read(conn);state['enabled']=True
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
    def submit(self,rule=RULE,now=NOW-100):return local.submit(api,{'rule':rule,'sources':SOURCES},now)
    def test_threshold_and_name_changes_do_not_create_a_new_structure(self):
        self.assertEqual(memory.shape('rsi < 20'),memory.shape('rsi < 30'))
        self.assertNotEqual(memory.shape('rsi < 30'),memory.shape('rsi > 30'))
        self.assertNotEqual(memory.shape('rsi < 30'),memory.shape('buys_m5 > sells_m5'))
    def test_cloud_rejects_crowded_structure_but_accepts_distinct_hypothesis(self):
        for n in (2,3):self.submit({**RULE,'entry':f'buys_m5 > sells_m5 * {n}'})
        plan={'strategy':{**RULE,'entry':'buys_m5 > sells_m5 * 4'},'sources':SOURCES,'summary':'Pressure','checks':[]}
        brain.apply_plan(api,{'kind':'strategy'},plan,None,NOW)
        with api.connection() as conn:
            row=conn.execute('SELECT * FROM brain_proposals').fetchone()
            self.assertEqual(row['status'],'diversity_blocked')
            self.assertEqual(json.loads(row['report'])['experiment']['similar_active_or_queued'],2)
            self.assertEqual(memory.specification(conn,{**RULE,'entry':'liquidity >= 70000'},NOW)['admission'],'waiting_slot')
    def test_frozen_control_and_single_expression_change_are_recorded(self):
        control=self.submit()
        with api.connection() as conn:
            spec=memory.specification(conn,{**RULE,'exit':'gain >= 20'},NOW)
        self.assertEqual(spec['control_id'],control['id'])
        self.assertEqual(spec['control_rule'],RULE)
        self.assertEqual(spec['changed_expressions'],['exit'])
        self.assertEqual(spec['design'],'single_expression_change')
    def test_retired_rule_is_remembered_without_blocking_distinct_research(self):
        old=self.submit()
        with api.connection() as conn,conn:
            conn.execute("UPDATE local_strategies SET status='inactive' WHERE id=?",(old['id'],))
            state,_=api.read(conn);value=memory.context(conn,state,NOW)
            self.assertEqual(value['recent_trials'][0]['status'],'inactive')
            self.assertEqual(value['active_families'],{})
            self.assertEqual(memory.specification(conn,RULE,NOW)['admission'],'waiting_slot')
    def test_queue_counts_toward_crowding(self):
        self.submit()
        with api.connection() as conn,conn:
            conn.execute("INSERT INTO brain_proposals VALUES ('q',?,'strategy','waiting_slot',?,'{}')",(NOW,json.dumps({'rule':RULE})))
            self.assertEqual(memory.specification(conn,RULE,NOW)['admission'],'diversity_blocked')
    def test_matched_coins_exclude_old_future_held_and_unmatched_results(self):
        control=self.submit();candidate=self.submit({**RULE,'exit':'gain >= 20'},NOW)
        spec={'control_id':control['id'],'frozen_at':NOW}
        with api.connection() as conn,conn:
            conn.execute("INSERT INTO brain_proposals VALUES (?,?,'strategy','testing','{}',?)",(candidate['id'],NOW,json.dumps({'experiment':spec})))
            state,_=api.read(conn);state['bots']['research-02']['positions']={'held':{}}
            for ident,bot,pnl in ((control['id'],'research-01',-1000000),(candidate['id'],'research-02',2000000)):
                for mint,opened,closed in (('shared',NOW+1,NOW+10),('old',NOW-10,NOW+10),('future',NOW+1,NOW+1000),('held',NOW+1,NOW+10)):
                    conn.execute('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',(bot,mint,opened,closed,'day',pnl,50250000,json.dumps({'policy':{'local_rule':{'id':ident}}})))
            conn.execute('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',('research-02','unmatched',NOW+1,NOW+10,'day',999000000,50250000,json.dumps({'policy':{'local_rule':{'id':candidate['id']}}})))
            value=memory.comparisons(conn,state,NOW+20)[0]
            self.assertEqual(value['shared_closed_coins'],1)
            self.assertEqual(value['candidate_net_usd'],2)
            self.assertEqual(value['control_net_usd'],-1)
            self.assertEqual(conn.execute('SELECT status FROM local_strategies WHERE id=?',(candidate['id'],)).fetchone()[0],'testing')
        self.assertEqual(local.MIN_COINS,100)
    def test_families_describe_inputs_without_claiming_price_patterns(self):
        self.assertEqual(memory.family(RULE),'order_pressure')
        self.assertEqual(memory.family({**RULE,'entry':'rsi < 30 and buys_m5 > sells_m5'}),'rsi')
        self.assertEqual(memory.family({**RULE,'entry':'volume_m5_usd > 10000'}),'absolute_volume')

if __name__=='__main__':unittest.main()
