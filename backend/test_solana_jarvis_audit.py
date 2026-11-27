import json
import sqlite3
import unittest
import jarvis_audit as audit


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript(audit.SCHEMA+'''CREATE TABLE local_strategies(id TEXT,slot TEXT,status TEXT,rule TEXT,created REAL);
        CREATE TABLE trades(id INTEGER PRIMARY KEY,bot TEXT,mint TEXT,pnl INTEGER,data TEXT,closed REAL);''')
        self.conn.execute('INSERT INTO local_strategies VALUES (?,?,?,?,?)',('v1','research-01','testing',json.dumps({'name':'Example'}),0))
        self.state={'local_rules':{'research-01':{'id':'v1'}}}
        self.entries={'research-01':{'label':'Waiting for fresh data: rsi','fresh_eligible_coins':4,'complete_observations':1,'matching_coins':0,'missing_observations':{'rsi':3}}}

    def trade(self,mint,pnl,**extra):
        data={'policy':{'local_rule':{'id':'v1'}},**extra}
        self.conn.execute('INSERT INTO trades(bot,mint,pnl,data,closed) VALUES (?,?,?,?,?)',('research-01',mint,int(pnl*1e6),json.dumps(data),100))

    def test_snapshots_are_deduplicated_and_do_not_mutate_state(self):
        original=json.dumps(self.state)
        audit.refresh(self.conn,self.state,self.entries,1000)
        audit.refresh(self.conn,self.state,self.entries,1001)
        report=audit.latest(self.conn)
        self.assertEqual(report['strategies'][0]['observations']['samples'],1)
        self.assertEqual(report['strategies'][0]['observations']['missing_fields']['rsi'],3)
        self.assertEqual(json.dumps(self.state),original)
        self.assertFalse(report['automatic_actions'])
        self.assertIn('missing-data',audit.brief(report))

    def test_digest_exit_templates_are_diagnostic_only(self):
        trailing='peak >= 10 and gain <= peak - 5'
        either=f'(gain >= 15 or ({trailing}))'
        guarded=either+' and gain > 15'
        expressions=[guarded, guarded+' and held_seconds > 60', 'held_seconds > 120 and '+guarded,
                     trailing, trailing+' and held_seconds > 60', either+' and gain > 5',
                     guarded+' or held_seconds > 60', either+' and (gain > 15 or held_seconds > 60)',
                     None, 'unknown_name > 7', guarded]
        for i,expression in enumerate(expressions):
            self.conn.execute('INSERT INTO local_strategies VALUES (?,?,?,?,?)',
                (str(i),'paper','failed' if i==10 else 'testing',json.dumps({'name':'fixture','exit':expression}),i))
        before=list(self.conn.execute('SELECT * FROM local_strategies ORDER BY id'))
        audit.refresh(self.conn,self.state,self.entries,1000)
        report=audit.latest(self.conn)
        self.assertEqual(set(report['fixed_gain_guard_redundant_trailing']),{'0','1','2'})
        self.assertIn('3 active paper rules',audit.brief(report))
        self.assertFalse(report['automatic_actions'])
        self.assertEqual(before,list(self.conn.execute('SELECT * FROM local_strategies ORDER BY id')))
        del report['fixed_gain_guard_redundant_trailing']
        self.assertNotIn('trailing branch',audit.brief(report))

    def test_coin_concentration_writeoffs_and_unknown_instrumentation(self):
        self.trade('winner',20)
        self.trade('loser',-5,paper_writeoff={'category':'no_route'})
        self.trade('winner',2,exit_quote_wait=120,signal_to_close_seconds=3)
        self.trade('owner',100,owner_closeout=True)
        row=audit.build(self.conn,1000)['strategies'][0]
        self.assertEqual(row['net'],17)
        self.assertEqual(row['distinct_coins'],2)
        self.assertEqual(row['net_without_best_coin'],-5)
        self.assertEqual(row['writeoffs'],1)
        self.assertEqual(row['owner_closeout_net'],100)
        self.assertEqual(row['quote_wait_measured'],1)
        self.assertEqual(row['quote_wait_over_90s'],1)
        self.assertIn('positive_result_depends_on_best_coin',row['flags'])

    def test_missing_history_is_unknown_not_zero_wait(self):
        self.trade('coin',-1)
        row=audit.build(self.conn,1000)['strategies'][0]
        self.assertIsNone(row['mean_quote_wait'])
        self.assertEqual(row['quote_wait_measured'],0)
        self.assertEqual(row['observations'],{})
        self.assertEqual(row['profit_factor'],0)

    def test_observation_window_and_retention(self):
        audit.refresh(self.conn,self.state,self.entries,1000)
        self.assertEqual(audit.build(self.conn,1000+86401)['strategies'][0]['observations'],{})
        audit.refresh(self.conn,self.state,self.entries,1000+31*86400)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM jarvis_audit_samples').fetchone()[0],1)

if __name__=='__main__':unittest.main()
