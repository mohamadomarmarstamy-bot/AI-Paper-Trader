from contextlib import closing
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from types import SimpleNamespace
import database
import solana_copy_trader as copy
import solana_copy_discovery as discovery
import solana_copy_solscan as solscan
import solana_profile as profile

WALLET='1'*31+'2'
TOKEN='1'*31+'3'
SOL='So11111111111111111111111111111111111111112'


def swap(at=105,tx='2'*88,kind='ACTIVITY_TOKEN_SWAP'):
    return {'trans_id':tx,'block_time':at,'from_address':WALLET,'activity_type':kind,
            'routers':{'token1':SOL,'token2':TOKEN,'amount1':'1000','amount2':'500'}}


class SourceTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'test.sqlite3');p.start();self.addCleanup(p.stop)

    def test_solscan_key_encrypted_independent_and_blank_preserved(self):
        payload={'name':'Omar','wallet_address':'','api_key':'birdeye-test','remove_api_key':False,'expected_revision':0,
                 'solscan_api_key':'solscan-test','remove_solscan_api_key':False}
        result=profile.save(payload)
        self.assertTrue(result['solscan_api_key_configured']);self.assertNotIn('solscan-test',str(result))
        self.assertNotIn(b'solscan-test',Path(database.DATABASE_PATH).read_bytes())
        self.assertEqual(profile.api_key(),'birdeye-test');self.assertEqual(profile.solscan_api_key(),'solscan-test')
        profile.save({**payload,'api_key':'','solscan_api_key':'','expected_revision':1})
        self.assertEqual(profile.solscan_api_key(),'solscan-test')
        profile.save({**payload,'api_key':'','solscan_api_key':'','remove_solscan_api_key':True,'expected_revision':2})
        self.assertEqual(profile.solscan_api_key(),'');self.assertEqual(profile.api_key(),'birdeye-test')

    def test_solscan_direction_owner_time_and_duplicate_aggregate(self):
        rows=solscan.normalized([swap(),swap(),swap(kind='ACTIVITY_AGG_TOKEN_SWAP')],WALLET,100,110)
        parsed=copy.signals(rows,WALLET)
        self.assertEqual(len(parsed),1);self.assertEqual(parsed[0]['side'],'buy');self.assertEqual(parsed[0]['source'],'solscan')
        row=swap();row['routers']['token1']=TOKEN;row['routers']['token2']=SOL
        self.assertEqual(copy.signals(solscan.normalized([row],WALLET,100,110),WALLET)[0]['side'],'sell')
        row['from_address']=TOKEN;self.assertEqual(solscan.normalized([row],WALLET,100,110),[])
        self.assertEqual(solscan.normalized([swap(at=99)],WALLET,100,110),[])
        row=swap();row['routers']['amount1']='nan';self.assertEqual(solscan.normalized([row],WALLET,100,110),[])

    def test_solscan_ambiguous_multiple_swaps_are_not_invented(self):
        row=swap();row['routers']['amount2']='999'
        self.assertEqual(solscan.normalized([swap(),row],WALLET,100,110),[])
        row=swap();row['routers']=[row['routers'],deepcopy(row['routers'])]
        self.assertEqual(solscan.normalized([row],WALLET,100,110),[])

    def test_solscan_frozen_pages_buffer_and_overlap_has_stable_ids(self):
        page=[swap(tx='2'*87+str(i%9+1)) for i in range(100)]
        with patch.object(solscan,'request',side_effect=[page,[]]) as request:
            rows,cursor=solscan.trades(WALLET,{'at':100,'provider':'solscan'},110)
            self.assertEqual(rows,[]);self.assertEqual(cursor['solscan_window']['before'],110)
            rows,cursor=solscan.trades(WALLET,cursor,999)
            self.assertEqual(cursor,{'at':110,'provider':'solscan'})
            self.assertEqual(request.call_args.args[0]['to_time'],110)
            self.assertEqual(len(rows),9)
        one=copy.signals(solscan.normalized([swap()],WALLET,100,110),WALLET)
        two=copy.signals(solscan.normalized([swap()],WALLET,104,115),WALLET)
        self.assertEqual(one[0]['id'],two[0]['id'])

    def test_solscan_auth_header_fixed_host_no_redirect_and_safe_errors(self):
        with patch.object(profile,'solscan_api_key',return_value='private-solscan-test'),patch.object(solscan,'_calls',solscan.deque()),patch.object(solscan,'_cooldown',0),patch.object(solscan.requests,'get',return_value=Mock(status_code=403)) as get:
            with self.assertRaises(copy.DataUnavailable) as error:solscan.request({'address':WALLET})
            self.assertNotIn('private-solscan-test',str(error.exception))
            self.assertEqual(get.call_args.kwargs['headers']['token'],'private-solscan-test')
            self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_continuous_discovery_enrolls_more_than_five_without_backfill(self):
        state=copy.initial();provider=copy.Birdeye();calls=[]
        addresses=['1'*31+c for c in '23456789ABCDEFGH']
        def request(path,**kw):
            calls.append((path,kw))
            if path=='/trader/gainers-losers':return {'items':[{'address':a,'realized_pnl':1000,'network':'solana'} for a in addresses]}
            return {'summary':{'counts':{'win_rate':.7,'total_sell':30},'pnl':{'realized_profit_usd':10000}}}
        provider.request=request
        for i in range(8):discovery.advance(state,provider,1000+i*10,lambda:'solscan')
        self.assertEqual(len(state['traders']),16)
        self.assertEqual(calls[0][0],'/trader/gainers-losers');self.assertEqual(calls[0][1]['params']['type'],'1W')
        self.assertEqual(calls[0][1]['params']['sort_by'],'realized_pnl')
        self.assertEqual(state['traders'][-1]['selected_at'],1070)
        self.assertEqual(state['cursors'][addresses[-1]],{'at':1070,'provider':'solscan'})
        discovery.advance(state,provider,1100,lambda:'birdeye')
        self.assertEqual(len(state['traders']),16)
        self.assertEqual(state['cursors'][addresses[0]]['provider'],'solscan')

    def test_discovery_failure_preserves_queue_and_no_false_enrollment(self):
        state=copy.initial();provider=Mock()
        provider.request.return_value={'items':[{'address':WALLET,'realized_pnl':1000}]}
        provider.qualify.side_effect=copy.DataUnavailable('offline')
        with self.assertRaises(copy.DataUnavailable):discovery.advance(state,provider,1000,lambda:'birdeye')
        self.assertEqual(state['traders'],[]);self.assertEqual(len(state['discovery']['pending']),1)

    def test_missing_key_can_arm_without_trades_or_requests(self):
        with patch.object(copy,'configured',return_value=False),patch.object(copy,'regular_metrics',return_value={'equity_usd':1000,'net_usd':0}),patch.object(copy.requests,'get') as get:
            result=copy.configure({'enabled':True},0)
            self.assertTrue(result['config']['enabled'])
            result=copy.tick()
            self.assertEqual(result['health'],'waiting_for_data_key');self.assertEqual(result['positions'],[])
            self.assertEqual(result['cash_usd'],100000);get.assert_not_called()

    def test_oldest_poll_rotation_covers_every_followed_wallet(self):
        wallets=['1'*31+c for c in '23456789'];polled=[]
        def trades(wallet,cursor,now):polled.append(wallet);return [],{'at':now}
        source=SimpleNamespace(leaders=lambda:[{'wallet':w} for w in wallets],trades=trades)
        with patch.object(copy,'configured',return_value=True),patch.object(copy,'regular_metrics',return_value={'equity_usd':100000,'net_usd':0}):
            copy.configure({'enabled':True},0)
            for now in (1000,1010,1020):copy.tick(provider=source,clock=lambda:now)
        self.assertEqual(set(polled),set(wallets));self.assertEqual(len(polled),9)

    def test_solscan_prospective_buy_sell_and_permanent_dedup_use_paper_ledger(self):
        now=[1000.];provider=copy.Birdeye();rows=[]
        def request(path,**kw):
            if path=='/trader/gainers-losers':return {'items':[{'address':WALLET,'realized_pnl':1000}]}
            if path=='/wallet/v2/pnl/details':return {'summary':{'counts':{'win_rate':.7,'total_sell':30},'pnl':{'realized_profit_usd':10000}}}
            if path=='/defi/v3/token/meme/detail/single':return {'address':TOKEN,'meme_info':{'source':'provider'}}
            self.fail('Unexpected endpoint '+path)
        provider.request=request
        market=SimpleNamespace(pairs=lambda addresses:[{'chainId':'solana','pairAddress':'1'*31+'4','baseToken':{'address':a,'symbol':'MEME'},'priceUsd':'2'} for a in addresses])
        with patch.object(copy,'configured',return_value=True),patch.object(solscan,'configured',return_value=True),patch.object(solscan,'request',side_effect=lambda params:deepcopy(rows)),patch.object(copy,'regular_metrics',return_value={'equity_usd':100000,'net_usd':0}):
            copy.configure({'enabled':True},0)
            copy.tick(provider=provider,market=market,clock=lambda:now[0])
            state,_=copy.load();self.assertEqual(state['cursors'][WALLET]['provider'],'solscan')
            now[0]=1010;rows[:]=[swap(at=1009)]
            result=copy.tick(provider=provider,market=market,clock=lambda:now[0])
            self.assertEqual(len(result['positions']),1);self.assertEqual(result['events'][0]['source'],'solscan')
            cash=result['cash_usd'];now[0]=1020
            result=copy.tick(provider=provider,market=market,clock=lambda:now[0]);self.assertEqual(result['cash_usd'],cash)
            row=swap(at=1029,tx='3'*88);row['routers']['token1']=TOKEN;row['routers']['token2']=SOL
            rows[:]=[row];now[0]=1030
            result=copy.tick(provider=provider,market=market,clock=lambda:now[0])
            self.assertEqual(result['positions'],[]);self.assertEqual(result['closed_count'],1)
            self.assertEqual(result['closed'][0]['reason'],'trader_sell')
            cash=result['cash_usd'];now[0]=1040
            self.assertEqual(copy.tick(provider=provider,market=market,clock=lambda:now[0])['cash_usd'],cash)


if __name__=='__main__':unittest.main()
