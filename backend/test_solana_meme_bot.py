from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
import database
import solana_meme_bot as bot
from solana_meme_routes import build_router

MINT='1'*31+'2'
PAIR='1'*31+'3'
OTHER='1'*31+'4'


class Providers:
    def __init__(self,clock):self.clock=clock;self.price=1.;self.missing=False;self.checks=0;self.failure=None;self.on_pairs=None
    def profiles(self):return [MINT]
    def pairs(self,addresses):
        if self.on_pairs:self.on_pairs()
        if self.missing:return []
        return [{'chainId':'solana','pairAddress':PAIR,'baseToken':{'address':MINT,'symbol':'<script>TEST</script>'},
                 'priceUsd':str(self.price),'liquidity':{'usd':200000},'pairCreatedAt':(self.clock()-7200)*1000,
                 'txns':{'m5':{'buys':100,'sells':50}},'volume':{'m5':50000},'priceChange':{'m5':5}}]
    def safety(self,address):
        self.checks+=1
        if self.failure:raise ValueError(self.failure)
        return {'checked_at':self.clock(),'mint_authority':None,'freeze_authority':None}


class EngineTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'test.sqlite3');p.start();self.addCleanup(p.stop)
        self.now=1793130000.;self.providers=Providers(lambda:self.now)
        fake=SimpleNamespace(time=lambda:self.now,gmtime=time.gmtime,strftime=time.strftime)
        p=patch.object(bot,'time',fake);p.start();self.addCleanup(p.stop)
    def configure(self,changes):return bot.configure(changes,bot.public()['revision'])
    def tick(self,force=False):return bot.tick(providers=self.providers,clock=lambda:self.now,force=force)
    def open(self):
        self.configure({'enabled':True,'watchlist':[MINT],'discover':False})
        self.tick();self.now+=60;self.providers.price=1.01
        result=self.tick();self.assertEqual(len(result['positions']),1);return result
    def test_default_paused_makes_no_provider_requests(self):
        with patch.object(self.providers,'profiles',side_effect=AssertionError),patch.object(self.providers,'pairs',side_effect=AssertionError):
            self.assertEqual(self.tick()['health'],'paused')
        self.assertFalse(bot.public()['live_execution_enabled'])
    def test_two_observations_and_costs_are_required(self):
        self.configure({'enabled':True,'watchlist':[MINT],'discover':False})
        first=self.tick();self.assertEqual(len(first['positions']),0);self.assertEqual(self.providers.checks,0)
        self.now+=60;self.providers.price=1.01
        result=self.tick();self.assertEqual(len(result['positions']),1)
        self.assertAlmostEqual(result['cash_usd'],949.9)
        self.assertLess(result['paper_equity_usd'],1000)
        self.assertEqual(self.providers.checks,1)
    def test_stop_exit_and_cooldown_prevent_rebuy(self):
        self.open();self.now+=60;self.providers.price=.8
        result=self.tick();self.assertEqual(len(result['positions']),0)
        self.assertEqual(result['closed'][0]['reason'],'stop_loss');self.assertLess(result['realized_pnl_usd'],0)
        self.assertIn('cooldown',result['candidates'][0]['reason'])
        count=len(result['closed']);self.now+=60;self.tick();self.assertEqual(len(bot.public()['closed']),count)
    def test_pause_keeps_exit_management(self):
        self.open();self.configure({'enabled':False});self.now+=60;self.providers.price=1.4
        result=self.tick();self.assertEqual(result['closed'][0]['reason'],'take_profit')
        self.assertFalse(result['config']['enabled']);self.assertFalse(result['positions'])
    def test_activated_trail_protects_modeled_gain(self):
        self.open();self.now+=60;self.providers.price=1.2
        self.assertEqual(len(self.tick()['positions']),1)
        self.now+=60;self.providers.price=1.12
        result=self.tick();self.assertEqual(result['closed'][0]['reason'],'profit_trail')
    def test_position_keeps_entry_exit_rules_after_settings_change(self):
        self.open();self.configure({'stop_percent':20});self.now+=60;self.providers.price=.9
        self.assertEqual(self.tick()['closed'][0]['reason'],'stop_loss')
    def test_missing_price_never_invents_exit_or_equity(self):
        self.open();self.now+=60;self.providers.missing=True
        result=self.tick();self.assertEqual(len(result['positions']),1);self.assertIsNone(result['paper_equity_usd'])
        self.assertFalse(result['positions'][0]['mark_fresh']);self.assertEqual(len(result['closed']),0)
    def test_daily_marked_loss_brake_blocks_new_entries(self):
        self.open();self.configure({'daily_loss_usd':10});self.now+=60;self.providers.price=.7
        result=self.tick();self.assertEqual(result['health'],'entry_brake');self.assertFalse(result['positions'])
    def test_time_exit_and_restart_persistence(self):
        self.open();self.now+=1801
        self.assertEqual(len(bot.load()[0]['positions']),1)
        self.assertEqual(self.tick()['closed'][0]['reason'],'maximum_hold')
    def test_configuration_race_discards_simulated_changes(self):
        self.configure({'enabled':True,'watchlist':[MINT],'discover':False});self.tick()
        self.now+=60;self.providers.price=1.01;self.providers.on_pairs=lambda:self.configure({'enabled':False})
        with self.assertRaises(bot.Conflict):self.tick()
        self.assertFalse(bot.public()['positions']);self.assertFalse(bot.public()['config']['enabled'])
    def test_authority_failure_blocks_paper_entry(self):
        self.configure({'enabled':True,'watchlist':[MINT],'discover':False});self.tick()
        self.now+=60;self.providers.price=1.01;self.providers.failure='Active mint or freeze authority.'
        result=self.tick();self.assertFalse(result['positions']);self.assertIn('authority',result['candidates'][0]['reason'])
    def test_wrong_chain_and_different_pool_cannot_mark_a_position(self):
        self.open();self.now+=60
        original=self.providers.pairs
        self.providers.pairs=lambda addresses:[{**row,'pairAddress':OTHER} for row in original(addresses)]
        result=self.tick();self.assertIsNone(result['paper_equity_usd']);self.assertEqual(len(result['positions']),1)
    def test_stale_revision_and_unknown_settings_are_rejected(self):
        self.configure({'trade_usd':100})
        with self.assertRaises(bot.Conflict):bot.configure({'enabled':True},0)
        for changes in ({'private_key':'secret'},{'trade_usd':float('nan')},{'enabled':'true'},{'watchlist':['DOGE']},{'max_positions':100}):
            with self.assertRaises(ValueError):self.configure(changes)
    def test_manual_scan_while_paused_does_not_buy(self):
        self.configure({'watchlist':[MINT],'discover':False});self.tick(force=True)
        self.now+=60;self.providers.price=1.01
        self.assertFalse(self.tick(force=True)['positions'])
    def test_http_auth_origin_and_execution_input_boundaries(self):
        allowed=False
        def auth(request):
            if not allowed:raise HTTPException(401,'Owner session required.')
        app=FastAPI();app.include_router(build_router(require_session=auth))
        with TestClient(app) as client:
            self.assertEqual(client.get('/meme-coins').status_code,401)
            self.assertEqual(client.get('/meme-coins/status').status_code,401);allowed=True
            self.assertEqual(client.post('/meme-coins/config',json={}).status_code,403)
            response=client.post('/meme-coins/scan',headers={'Origin':'http://testserver'},json={'wallet':'anything'})
            self.assertEqual(response.status_code,422)
            self.assertEqual(client.get('/meme-coins/status').json()['live_execution_enabled'],False)


class OnChainTests(unittest.TestCase):
    def provider(self,*,authority=None,owner=bot.TOKEN_PROGRAM,amount='10'):
        p=bot.ReadOnlyProviders()
        def rpc(method,params):
            if method=='getAccountInfo':return {'value':{'owner':owner,'executable':False,'data':{'parsed':{'type':'mint','info':{
                'isInitialized':True,'mintAuthority':authority,'freezeAuthority':None,'supply':'1000'}}}}}
            return {'context':{'slot':100},'value':[{'amount':amount}]}
        p.rpc=rpc;return p
    def test_supported_revoked_mint_and_account_concentration(self):
        result=self.provider().safety(MINT)
        self.assertEqual(result['largest_account_percent'],1)
        self.assertIsNone(result['mint_authority'])
    def test_authority_unsupported_program_and_concentration_rejected(self):
        for kw in ({'authority':MINT},{'owner':'Token2022'},{'amount':'800'}):
            with self.assertRaises(ValueError):self.provider(**kw).safety(MINT)
    def test_rpc_cannot_submit_transactions(self):
        with self.assertRaises(ValueError):bot.ReadOnlyProviders().rpc('sendTransaction',[MINT])
    def test_mint_identity_rejects_tickers_invalid_base58_and_wrong_size(self):
        self.assertEqual(bot.mint(MINT),MINT)
        for address in ('DOGE','0'*32,'2'*32,'1'*44):
            with self.assertRaises(ValueError):bot.mint(address)
