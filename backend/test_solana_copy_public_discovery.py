from copy import deepcopy
from unittest import TestCase,main
from unittest.mock import Mock,patch
import solana_copy_public_discovery as discovery
import solana_copy_trader as copy
from test_solana_copy_trader import WALLET,OTHER,Market
from pathlib import Path
from contextlib import closing
import tempfile,json
import database


def row(wallet=WALLET,**changes):
    attrs={'class':'board-row','data-address':wallet,'data-wallet':wallet.lower(),'data-slug':'example-trader',
        'data-kol-name':'Example','data-pnl30d':'1000','data-pnl7d':'100','data-wr30d':'60',
        'data-avghold':'120','data-txcount':'100','data-last-tx-ts':'990',**changes}
    return '<tr '+ ' '.join(f'{key}="{value}"' for key,value in attrs.items())+'></tr>'

class PublicDiscoveryTests(TestCase):
    def test_case_sensitive_public_address_is_preserved_and_search_key_is_never_used(self):
        address='4DdrfiDHpmx55i4SPssxVzS9ZaKLb8qr45NKY9Er9nNh'
        rows,checked=discovery.parse(row(address),1000,60)
        self.assertEqual(rows[0]['wallet'],address);self.assertEqual(checked,1)
        html=row(address).replace('data-address="'+address+'"','')
        self.assertEqual(discovery.parse(html,1000,60)[0],[])
    def test_reported_quality_hold_activity_and_missing_fields_are_checked(self):
        for changes in ({'data-pnl30d':'-1'},{'data-pnl7d':'-1'},{'data-wr30d':'50'},{'data-avghold':'15'},
                        {'data-txcount':'2'},{'data-last-tx-ts':'1005'},{'data-slug':'../../evil'},{'data-pnl30d':'NaN'}):
            self.assertEqual(discovery.parse(row(**changes),1000,60)[0],[])
        with self.assertRaises(ValueError):discovery.parse('<html>unavailable</html>',1000,60)
    def test_continuous_refresh_grows_roster_without_backfill_and_deduplicates(self):
        state=copy.initial();state['public_rpc_enabled']=True
        now=[1000.]
        first,_=discovery.parse(row(),1000,60);second,_=discovery.parse(row(OTHER,**{'data-slug':'second','data-kol-name':'Second'}),1000,60)
        fetch=Mock(return_value=(first,1));discovery.advance(state,lambda:now[0],fetcher=fetch)
        self.assertEqual(len(state['traders']),1);self.assertTrue(state['traders'][0]['active'])
        self.assertIsNone(state['traders'][0]['historical_realized_usd'])
        self.assertEqual(state['cursors'][WALLET]['at'],1000)
        now[0]=1100;discovery.advance(state,lambda:now[0],fetcher=fetch);self.assertEqual(fetch.call_count,1)
        now[0]=1600;fetch.return_value=(first+second,2);discovery.advance(state,lambda:now[0],fetcher=fetch)
        self.assertEqual(len(state['traders']),2);self.assertEqual(state['cursors'][OTHER]['at'],1600)
        self.assertEqual(state['cursors'][WALLET]['at'],1000)
    def test_rank_failure_keeps_existing_follows_and_reports_retry(self):
        state=copy.initial();rows,_=discovery.parse(row(),1000,60)
        discovery.advance(state,lambda:1000,fetcher=lambda *args:(rows,1))
        before=deepcopy(state['traders'])
        discovery.advance(state,lambda:1600,fetcher=Mock(side_effect=ValueError('offline')))
        self.assertEqual(state['traders'],before);self.assertIsNotNone(state['public_discovery']['error'])
    def test_active_pool_respects_polling_coverage_and_keeps_extra_wallets_queued(self):
        state=copy.initial();state['public_discovery']={};state['config']['max_active_traders']=1
        rows,_=discovery.parse(row()+row(OTHER,**{'data-slug':'second','data-kol-name':'Second'}),1000,60)
        discovery.advance(state,lambda:1000,fetcher=lambda *args:(rows,2))
        self.assertEqual(len(state['traders']),2);self.assertEqual(sum(r['active'] for r in state['traders']),1)
        self.assertEqual(sum(r['discovery_status']=='queued_capacity' for r in state['traders']),1)
        state['config']['max_active_traders']=2
        discovery.advance(state,lambda:1010,fetcher=Mock(side_effect=AssertionError('Refresh not due')))
        self.assertEqual(sum(r['active'] for r in state['traders']),2)
        self.assertEqual(discovery.scheduling_limit({**state['config'],'poll_seconds':25,'max_active_traders':100}),6)
    def test_key_free_discovery_bootstraps_without_birdeye_or_preexisting_follows(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        rows,_=discovery.parse(row(),1000,60)
        with patch.object(database,'DATABASE_PATH',Path(temp.name)/'copy.db'),patch.object(copy,'configured',return_value=False),patch.object(copy,'regular_metrics',return_value={'equity_usd':100000,'net_usd':0}),patch.object(copy.time,'time',return_value=1000):
            copy.configure({'enabled':True},0);state,_=copy.load();state['public_rpc_enabled']=True
            with closing(database.get_connection()) as conn:
                conn.execute('UPDATE solana_copy_state SET state_json=? WHERE id=1',(json.dumps(state),));conn.commit()
            with patch.object(copy.Birdeye,'discover',side_effect=copy.DataUnavailable('Birdeye HTTP 401')),patch.object(discovery,'fetch',return_value=(rows,1)):
                result=copy.tick(provider=copy.Birdeye(),market=Market(),clock=lambda:1000)
            self.assertEqual(len(result['traders']),1);self.assertEqual(result['health'],'running');self.assertIsNone(result['error'])
            self.assertEqual(copy.load()[0]['cursors'][WALLET]['at'],1000)

    def test_fetch_is_bounded_and_does_not_redirect_or_send_credentials(self):
        response=Mock(status_code=200);response.iter_content.return_value=[row().encode()]
        get=Mock(return_value=response);self.assertEqual(len(discovery.fetch(1000,60,get=get)[0]),1)
        self.assertFalse(get.call_args.kwargs['allow_redirects']);self.assertNotIn('headers',get.call_args.kwargs)
        response.iter_content.return_value=[b'x'*2_000_001]
        with self.assertRaises(copy.DataUnavailable):discovery.fetch(1000,60,get=get)
        self.assertTrue(response.close.called)

if __name__=='__main__':main()
