from contextlib import closing
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import database
import solana_meme_bot as regular
import solana_copy_trader as copy
from solana_meme_routes import build_router

WALLET='1'*31+'2'
TOKEN='1'*31+'3'
POOL='1'*31+'4'
OTHER='1'*31+'5'
SOL='So11111111111111111111111111111111111111112'


def trade(at, side='buy', wallet=WALLET, token=TOKEN, index=1, tx='2'*88):
    return {'owner':wallet,'tx_hash':tx,'ins_index':index,'inner_ins_index':0,'block_unix_time':at,
            'base':{'address':token,'type_swap':'to' if side=='buy' else 'from'},
            'quote':{'address':SOL,'type_swap':'from' if side=='buy' else 'to'}}


class Provider:
    def __init__(self):self.rows=[];self.is_meme=True;self.missing=False;self.on_trade=None
    def leaders(self):return [{'wallet':WALLET,'seven_day_realized_usd':100000,'historical_realized_usd':500000,'win_rate_percent':70,'sell_count':100}]
    def meme(self,mint):return self.is_meme
    def trades(self,wallet,cursor,now):
        if self.on_trade:self.on_trade()
        if self.missing:raise copy.DataUnavailable('offline')
        return self.rows,{'at':now}


class Market:
    def __init__(self):self.price=2.;self.pool=POOL;self.missing=False
    def pairs(self,addresses):
        if self.missing:return []
        return [{'chainId':'solana','pairAddress':self.pool,'baseToken':{'address':a,'symbol':'<script>COIN</script>'},'priceUsd':str(self.price)} for a in addresses]


class CopyTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(t.name)/'paper.sqlite3');p.start();self.addCleanup(p.stop)
        self.now=1800000000.;self.baseline={'equity_usd':100000.,'net_usd':-200.}
        for p in (patch.object(copy,'configured',return_value=True),patch.object(copy.time,'time',side_effect=lambda:self.now),patch.object(copy,'regular_metrics',side_effect=lambda:deepcopy(self.baseline))):p.start();self.addCleanup(p.stop)
        self.source=Provider();self.market=Market()
    def config(self,changes):return copy.configure(changes,copy.public()['revision'])
    def tick(self):return copy.tick(provider=self.source,market=self.market,clock=lambda:self.now)
    def start(self):self.config({'enabled':True});return self.tick()
    def buy(self):
        self.start();self.now+=10;self.source.rows=[trade(self.now-2)];return self.tick()
    def test_new_swaps_only_costs_and_no_liquidity_or_volume_fields(self):
        data=self.buy();self.assertEqual(len(data['positions']),1)
        p=data['positions'][0];self.assertAlmostEqual(p['quantity'],50/(2*1.01));self.assertEqual(data['cash_usd'],99949.9)
        self.assertLess(data['net_usd'],0);self.assertEqual(p['latency_seconds'],2);self.assertEqual(p['entry_price_usd'],2)
        self.assertEqual(data['events'][0]['status'],'paper_buy')
        self.now+=10;self.tick();self.assertEqual(len(copy.public()['positions']),1)
        self.assertEqual(copy.public()['cash_usd'],data['cash_usd'])
        self.now+=86401;self.tick();self.assertEqual(len(copy.public()['positions']),1)
    def test_source_sell_closes_all_copied_lots_and_persists_accounting(self):
        self.buy();self.now+=10;self.source.rows=[trade(self.now-2,index=2)];self.tick()
        self.now+=10;self.market.price=3;self.source.rows=[trade(self.now-1,'sell',index=3)]
        data=self.tick();self.assertEqual(len(data['positions']),0);self.assertEqual(data['closed_count'],2)
        self.assertAlmostEqual(data['cash_usd']-100000,data['realized_pnl_usd']);self.assertEqual(data['wins'],2)
        before=data['cash_usd'];self.tick();self.assertEqual(copy.public()['cash_usd'],before)
    def test_stale_pre_enrollment_future_and_completed_roundtrip_are_not_bought(self):
        self.start();self.now+=200
        self.source.rows=[trade(self.now-100),trade(self.now+10,index=2),trade(self.now-201,index=3),trade(self.now-5,index=4),trade(self.now-4,'sell',index=5)]
        data=self.tick();self.assertEqual(data['positions'],[]);self.assertEqual(data['cash_usd'],100000)
    def test_missing_mark_stays_unresolved_and_never_substitutes_pool(self):
        data=self.buy();self.now+=10;self.market.pool=OTHER;self.source.rows=[trade(self.now-1,'sell',index=2)]
        data=self.tick();self.assertEqual(len(data['positions']),1);self.assertIsNone(data['equity_usd']);self.assertEqual(data['closed_count'],0)
        self.assertIn('pending_sell',data['positions'][0]);self.now+=10;self.market.pool=POOL;self.source.rows=[]
        data=self.tick();self.assertEqual(data['closed_count'],1)
    def test_non_meme_and_missing_prices_do_not_fill(self):
        self.start();self.now+=10;self.source.rows=[trade(self.now-1)];self.source.is_meme=False
        self.assertEqual(self.tick()['positions'],[])
        self.now+=10;self.source.rows=[trade(self.now-1,token=OTHER,index=2)];self.source.is_meme=True;self.market.missing=True
        self.assertEqual(self.tick()['positions'],[])
    def test_pause_keeps_sells_and_manual_queue_is_idempotent(self):
        data=self.buy();key=data['positions'][0]['id'];self.config({'enabled':False});self.now+=10
        self.source.rows=[trade(self.now-1,index=2),trade(self.now-1,'sell',index=3)]
        data=self.tick();self.assertEqual(data['closed_count'],1);self.assertEqual(data['positions'],[])
        copy.queue_exits([key],True,data['revision']);self.tick();self.assertEqual(copy.public()['closed_count'],1)
    def test_manual_exit_works_without_source_data_and_waits_for_a_price(self):
        data=self.buy();key=data['positions'][0]['id'];copy.queue_exits([key],True,data['revision']);self.source.missing=True;self.market.missing=True;self.now+=10
        self.assertEqual(len(self.tick()['positions']),1)
        self.market.missing=False;self.now+=10;data=self.tick();self.assertEqual(data['closed_count'],1);self.assertEqual(data['closed'][0]['reason'],'manual_sell')
    def test_configuration_race_discards_simulation_and_events(self):
        self.start();self.now+=10;self.source.rows=[trade(self.now-1)]
        self.source.on_trade=lambda:self.config({'trade_usd':75})
        with self.assertRaises(regular.Conflict):self.tick()
        data=copy.public();self.assertEqual(data['positions'],[]);self.assertEqual(data['events'],[]);self.assertEqual(data['cash_usd'],100000)
    def test_lease_prevents_overlapping_workers(self):
        self.start()
        with closing(database.get_connection()) as c:c.execute('UPDATE solana_copy_state SET lease_until=?',(self.now+100,));c.commit()
        with self.assertRaises(regular.Conflict):self.tick()
    def test_comparison_excludes_deposits_and_does_not_touch_regular_state(self):
        self.buy();self.baseline['net_usd']+=100
        data=copy.public();self.assertEqual(data['comparison']['regular_change_usd'],100);self.assertAlmostEqual(data['comparison']['regular_return_percent'],.1)
        before=data['net_usd'];self.config({'paper_capital_usd':200000});data=copy.public();self.assertAlmostEqual(data['net_usd'],before)
        self.assertAlmostEqual(data['comparison']['copy_return_percent'],100*before/100000)
        self.assertEqual(regular.load()[0]['positions'],{});self.assertEqual(regular.load()[0]['cash_usd'],1000)
    def test_settings_persist_and_reject_invalid_numbers_or_missing_key(self):
        self.config({'trade_usd':500,'poll_seconds':500,'paper_capital_usd':1000000})
        self.assertEqual(copy.public()['config']['trade_usd'],500)
        for change in ({'trade_usd':-1},{'poll_seconds':float('inf')},{'enabled':1},{'wallet_url':'bad'}):
            with self.assertRaises(ValueError):self.config(change)
        with patch.object(copy,'configured',return_value=False):
            self.assertTrue(self.config({'enabled':True})['config']['enabled'])
    def test_resume_held_wallet_does_not_backfill_paused_buys_but_retains_sells(self):
        self.buy();self.config({'enabled':False});self.now+=10
        self.source.rows=[trade(self.now-1,index=2)];self.config({'enabled':True})
        result=self.tick();self.assertEqual(len(result['positions']),1)
        self.assertEqual(result['events'][0]['reason'],'Buy occurred before copying resumed.')
        self.source.rows=[trade(self.now-2,'sell',index=3)];self.now+=10
        result=self.tick();self.assertEqual(result['positions'],[]);self.assertEqual(result['closed_count'],1)

    def test_missing_regular_marks_do_not_block_copy_and_shared_window_waits(self):
        self.baseline['net_usd']=None;self.baseline['equity_usd']=None
        data=self.buy();self.assertEqual(len(data['positions']),1);self.assertIsNone(data['comparison']['since'])
        self.baseline={'equity_usd':100000.,'net_usd':0.};self.now+=10;self.source.rows=[]
        data=self.tick();self.assertEqual(data['comparison']['since'],self.now);self.assertEqual(data['comparison']['copy_return_percent'],0)

    def test_incomplete_pagination_buffers_signals_before_simulation(self):
        self.start();self.now+=10
        self.source.trades=lambda wallet,cursor,now:([trade(now-1)],{'at':now-10,'window':{'after':now-10,'before':now,'offset':100}})
        self.assertEqual(self.tick()['positions'],[])
        self.now+=10;self.source.trades=lambda wallet,cursor,now:([trade(now-5,'sell',index=2)],{'at':now-10})
        data=self.tick();self.assertEqual(data['positions'],[]);self.assertEqual(data['cash_usd'],100000)

    def test_trades_parse_directions_owner_and_instruction_identity(self):
        rows=[trade(self.now),trade(self.now,index=2),trade(self.now,wallet=OTHER)]
        parsed=copy.signals(rows,WALLET);self.assertEqual(len(parsed),2);self.assertEqual(len({r['id'] for r in parsed}),2);self.assertTrue(all(r['side']=='buy' for r in parsed))
        row=trade(self.now);row['quote']['type_swap']='to';self.assertEqual(copy.signals([row],WALLET),[])
    def test_routes_require_auth_origin_and_exact_fields(self):
        allowed=[False]
        def auth(req):
            if not allowed[0]:raise HTTPException(401,'login')
        app=FastAPI();app.include_router(build_router(require_session=auth));client=TestClient(app)
        self.assertEqual(client.get('/meme-coins/copy').status_code,401);self.assertEqual(client.get('/meme-coins/copy/status').status_code,401)
        allowed[0]=True;self.assertEqual(client.get('/meme-coins/copy').status_code,200)
        self.assertEqual(client.post('/meme-coins/copy/config',json={'changes':{'trade_usd':500},'expected_revision':0}).status_code,403)
        r=client.post('/meme-coins/copy/config',headers={'Origin':'http://testserver'},json={'changes':{'trade_usd':500},'expected_revision':0});self.assertEqual(r.status_code,200)
        self.assertEqual(client.post('/meme-coins/copy/close',headers={'Origin':'http://testserver'},json={'price':2}).status_code,422)


class ProviderTests(unittest.TestCase):
    def test_ranking_requires_profit_winrate_and_enough_sells(self):
        provider=copy.Birdeye();calls=[]
        provider.request=lambda path,**kw:calls.append((path,kw)) or {'summary':{'counts':{'win_rate':.7,'total_sell':30},'pnl':{'realized_profit_usd':1000}}}
        row={'address':WALLET,'realized_pnl':100,'ranking_window':'1W'}
        self.assertEqual(provider.qualify(row)['wallet'],WALLET)
        self.assertEqual(calls[0][1]['payload']['position_scope'],'cumulative')
        provider.request=lambda *a,**kw:{'summary':{'counts':{'win_rate':.2,'total_sell':100},'pnl':{'realized_profit_usd':1000}}}
        self.assertIsNone(provider.qualify(row))
    def test_history_uses_frozen_pagination_window_and_checks_schema(self):
        provider=copy.Birdeye();calls=[]
        provider.request=lambda path,params: calls.append(params) or {'items':[],'hasNext':len(calls)==1}
        _,cur=provider.trades(WALLET,{'at':100},200);_,cur=provider.trades(WALLET,cur,300)
        self.assertEqual(calls[0]['before_time'],calls[1]['before_time']);self.assertEqual(calls[1]['offset'],100);self.assertEqual(cur,{'at':200})
        provider.request=lambda *a,**k:{'items':[]}
        with self.assertRaises(copy.DataUnavailable):provider.trades(WALLET,cur,300)
    def test_provider_errors_never_leak_keys_or_redirect(self):
        p=copy.Birdeye()
        with patch.dict(os.environ,{'BIRDEYE_API_KEY':'private-test-key'}),patch.object(copy,'_calls',copy.deque()),patch.object(copy,'_cooling_until',0),patch.object(copy.requests,'get',return_value=SimpleNamespace(status_code=403)) as request:
            with self.assertRaises(copy.DataUnavailable) as error:p.request('/wallet/v2/leaderboard')
            self.assertNotIn('private-test-key',str(error.exception));self.assertFalse(request.call_args.kwargs['allow_redirects'])
