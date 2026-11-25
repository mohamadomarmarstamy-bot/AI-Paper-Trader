import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

import database
import jarvis_local_lab as local
import jarvis_test_bot as api

RULE={'name':'Test','hypothesis':'Untested pressure hypothesis','entry':'liquidity > 50000','exit':'gain > 20'}
SOURCES=[{'url':'https://example.com','title':'Reference','snippet':''}]


class PbhCloseoutTests(unittest.TestCase):
    def setUp(self):
        temp=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database,'DATABASE_PATH',Path(temp)/'main.db'))
        self.enterContext(patch.dict('os.environ',{'JARVIS_FOCUS_MODE':'true'}))
        api.set_enabled(True)
        self.active=local.submit(api,{'rule':RULE,'sources':SOURCES},now=100)
        self.row={'mint':'coin','symbol':'Coin','pair':'pool','first':101,'last':101,'liquidity_usd':100000,'age_minutes':120}

    def state(self):
        with api.connection() as conn:return api.read(conn)[0]

    def test_one_time_closeout_targets_presets_and_retired_versions_only(self):
        with api.connection() as conn,conn:
            state,_=api.read(conn)
            api.open_trade(state,'instant-trim',self.row,1000,101)
            api.open_trade(state,self.active['slot'],self.row,1000,101)
            retired=copy.deepcopy(state['bots'][self.active['slot']]['positions']['coin'])
            retired['policy']['local_rule']['id']='retired'
            state['bots']['research-02']['positions']['coin']=retired
            conn.execute('INSERT INTO local_strategies VALUES (?,?,?,?,?,?,?)',('retired','research-02',50,json.dumps(RULE),json.dumps(SOURCES),'failed','{}'))
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
        api.apply_pbh_closeout();state=self.state()
        self.assertEqual(state['pbh_closeout_v1']['positions'],2)
        self.assertTrue(state['bots']['instant-trim']['positions']['coin']['owner_closeout'])
        self.assertTrue(state['bots']['research-02']['positions']['coin']['owner_closeout'])
        self.assertNotIn('owner_closeout',state['bots'][self.active['slot']]['positions']['coin'])
        self.assertTrue(state['enabled']);self.assertTrue(state['preset_entries_paused'])
        before=copy.deepcopy(state);api.apply_pbh_closeout();self.assertEqual(self.state(),before)

    def test_closeout_uses_full_quote_without_trim_or_profit_requirements(self):
        state=self.state();api.open_trade(state,'instant-trim',self.row,1000,101)
        portfolio=state['bots']['instant-trim'];p=portfolio['positions']['coin']
        p.update(owner_closeout=True,pending='pbh_closeout')
        completed=[]
        api.apply_exit(completed,portfolio,'instant-trim',p,500,30_000000,102)
        self.assertFalse(completed);self.assertEqual(p['quantity'],1000)
        api.apply_exit(completed,portfolio,'instant-trim',p,1000,49_000000,103)
        self.assertFalse(portfolio['positions'])
        self.assertEqual(completed[0]['reason'],'PBH owner-requested paper closeout')
        self.assertEqual(completed[0]['pnl'],-1_350000)
        self.assertNotIn('paper_writeoff',completed[0])

    def test_active_checks_receive_reserved_share_and_actual_exits_stay_first(self):
        state=self.state();api.open_trade(state,'instant-step',self.row,1000,101)
        api.open_trade(state,self.active['slot'],{**self.row,'mint':'active'},2000,101)
        old=state['bots']['instant-step']['positions']['coin'];old.update(owner_closeout=True,pending='pbh_closeout')
        active=state['bots'][self.active['slot']]['positions']['active']
        jobs=api.quote_jobs(state,200)
        self.assertEqual(api.next_exit(jobs,0),('active',2000))
        self.assertEqual(api.next_exit(jobs,1),('coin',1000))
        active['exit_signal']={'at':150,'basis':'confirmed_quote'}
        self.assertEqual(api.next_exit(jobs,1),('active',2000))
        old.update(mark_stale=True,quote_due_since=103,due=1000)
        api.schedule_market_exits(state,200)
        self.assertEqual(old['due'],1000)

    def test_400_diagnostics_read_multiple_fields_and_do_not_echo_body(self):
        cases=[({'errorCode':'COULD_NOT_FIND_ANY_ROUTE'},'no_route'),
               ({'errorMessage':'Invalid amount SECRET'},'invalid_parameter'),
               ({'code':'ROUTE_PLAN_DOES_NOT_CONSUME_ALL_THE_AMOUNT'},'partial_route'),
               ({'error':{'private':'SECRET'}},'request')]
        for data,category in cases:
            error=api.quote_error(Mock(status_code=400),data)
            self.assertEqual(error.category,category);self.assertNotIn('SECRET',str(error))
        with patch.object(api.solana_http,'get') as get:
            with self.assertRaises(api.QuoteUnavailable):api.quote('invalid',api.USDC,100)
            with self.assertRaises(api.QuoteUnavailable):api.quote(api.USDC,api.USDC,0)
            get.assert_not_called()


if __name__=='__main__':unittest.main()
