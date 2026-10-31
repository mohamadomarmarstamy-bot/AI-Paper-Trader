from copy import deepcopy
import unittest
from solana_copy_health import summary
from solana_copy_trader import initial


class CopyHealthTests(unittest.TestCase):
    def setUp(self):
        self.now=1800000000
        self.state=initial();self.state['config']['enabled']=True
        self.state.update(last_cycle=self.now-4,started_at=self.now-1000,health='running')
        self.state['traders']=[{'wallet':'A','active':True},{'wallet':'B','active':True}]
        self.state['cursors']={w:{'provider':'rpc','rpc_coverage':{'checked_at':self.now-10,'queued':0}} for w in ('A','B')}
    def health(self):return summary(self.state,self.now)
    def test_current_feed_is_green_even_without_recent_trades_and_does_not_mutate(self):
        before=deepcopy(self.state);h=self.health()
        self.assertEqual(h['level'],'ok');self.assertEqual(h['label'],'Watching')
        self.assertEqual(h['watched_wallets'],2);self.assertEqual(self.state,before)
        self.assertEqual(h['valid_until'],self.now+50)
    def test_recent_attempt_cannot_hide_stale_successful_rpc_check(self):
        self.state['cursors']['A']['rpc_coverage']['checked_at']=self.now-80
        self.state['last_polled']={'A':self.now}
        h=self.health();self.assertEqual(h['level'],'warning');self.assertEqual(h['delayed_wallets'],1)
    def test_dead_worker_and_all_stale_sources_are_red(self):
        self.state['last_cycle']=self.now-121
        self.assertEqual(self.health()['level'],'error')
        self.state['last_cycle']=self.now
        for cursor in self.state['cursors'].values():cursor['rpc_coverage']['checked_at']=self.now-121
        self.assertEqual(self.health()['label'],'Wallet feed unavailable')
    def test_backlog_and_per_wallet_error_cannot_be_green(self):
        self.state['cursors']['A']['rpc_coverage']['queued']=1000
        self.assertEqual(self.health()['label'],'Catching up')
        self.state['cursors']['A']['rpc_coverage'].update(queued=0,error='timeout')
        self.assertEqual(self.health()['label'],'Feed issue')
    def test_missing_prices_are_red_even_when_worker_and_wallets_are_fresh(self):
        self.state['positions']={'lot':{'wallet':'A','marked_at':self.now,'mark_usd':None}}
        h=self.health();self.assertEqual(h['label'],'Prices unavailable');self.assertEqual(h['unpriced_holdings'],1)
        self.state['positions']['lot'].update(mark_usd=50,marked_at=self.now-121)
        self.assertEqual(self.health()['level'],'error')
    def test_inactive_wallet_is_ignored_until_it_has_a_holding(self):
        self.state['traders'][1]['active']=False;self.state['cursors']['B']['rpc_coverage']['checked_at']=self.now-80
        self.assertEqual(self.health()['level'],'ok')
        self.state['positions']={'lot':{'wallet':'B','marked_at':self.now,'mark_usd':50}}
        self.assertEqual(self.health()['level'],'warning')
    def test_paused_is_distinct_and_existing_exits_still_need_a_live_worker(self):
        self.state['config']['enabled']=False;self.state['last_cycle']=self.now-1000
        self.assertEqual(self.health()['level'],'paused')
        self.state['positions']={'lot':{'wallet':'A','marked_at':self.now,'mark_usd':50}}
        self.assertEqual(self.health()['level'],'error')
        self.state['last_cycle']=self.now
        self.assertEqual(self.health()['label'],'Buys paused')
    def test_stop_off_and_unconnected_feed_are_visible(self):
        self.state['config']['stop_loss_percent']=0
        self.assertEqual(self.health()['label'],'Loss stop off')
        self.state['health']='waiting_for_data_key'
        self.assertEqual(self.health()['label'],'Feed not connected')
    def test_startup_grace_is_bounded(self):
        self.state.update(last_cycle=None,inactivity_enabled_at=self.now-5)
        self.assertEqual(self.health()['label'],'Starting')
        self.state['inactivity_enabled_at']=self.now-121
        self.assertEqual(self.health()['level'],'error')
    def test_clock_anomaly_never_looks_fresh(self):
        self.state['last_cycle']=self.now+100
        self.assertEqual(self.health()['level'],'error')
