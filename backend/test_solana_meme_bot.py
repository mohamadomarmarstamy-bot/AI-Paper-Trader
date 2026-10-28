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
    def test_liquidity_and_volume_settings_control_entries(self):
        self.configure({'enabled':True,'watchlist':[MINT],'discover':False,
                        'minimum_liquidity_usd':300000,'minimum_m5_volume_usd':60000})
        self.assertIn('Liquidity below $300,000',self.tick()['candidates'][0]['reason'])
        self.configure({'minimum_liquidity_usd':200000})
        self.assertIn('Five-minute volume below $60,000',self.tick()['candidates'][0]['reason'])
        self.configure({'minimum_m5_volume_usd':50000})
        self.now+=60;self.providers.price=1.01
        self.assertEqual(len(self.tick()['positions']),1)

    def test_legacy_saved_settings_receive_defaults_without_reset(self):
        self.configure({'trade_usd':75})
        with database.get_connection() as conn:
            import json
            state=json.loads(conn.execute('SELECT state_json FROM solana_meme_paper_state').fetchone()[0])
            state['config'].pop('minimum_liquidity_usd');state['config'].pop('minimum_m5_volume_usd')
            conn.execute('UPDATE solana_meme_paper_state SET state_json=?',(json.dumps(state),));conn.commit()
        result=bot.public()
        self.assertEqual(result['config']['minimum_liquidity_usd'],100000)
        self.assertEqual(result['config']['minimum_m5_volume_usd'],20000)
        self.assertEqual(result['config']['trade_usd'],75)
        for value in (-1,float('nan'),float('inf'),True,1000000001):
            with self.assertRaises(ValueError):self.configure({'minimum_liquidity_usd':value})
        self.configure({'minimum_liquidity_usd':0,'minimum_m5_volume_usd':0})
        self.assertEqual(bot.public()['rules']['minimum_liquidity_usd'],0)

    def test_scans_do_not_invalidate_settings_and_saves_survive_reload(self):
        self.configure({'enabled':True,'watchlist':[MINT],'discover':False})
        edited_revision=bot.public()['revision']
        for _ in range(3):self.now+=10;self.tick()
        result=bot.configure({'minimum_liquidity_usd':12345,'minimum_m5_volume_usd':6789},edited_revision)
        self.assertEqual(result['config']['minimum_liquidity_usd'],12345)
        self.assertEqual(bot.public()['config']['minimum_m5_volume_usd'],6789)
        self.assertGreater(result['revision'],edited_revision)
        with self.assertRaises(bot.Conflict):bot.configure({'trade_usd':90},edited_revision)

    def test_missing_batch_pool_recovers_with_alternate_lookup(self):
        self.configure({'watchlist':[MINT],'discover':False})
        good=self.providers.pairs([MINT])
        self.providers.pairs=lambda addresses:[]
        self.providers.token_pairs=lambda address:good
        result=self.tick(force=True)
        self.assertNotEqual(result['candidates'][0]['status'],'unavailable')
        self.assertNotIn(MINT,bot.load()[0]['unavailable_until'])

    def test_incomplete_discovery_is_backed_off_but_watchlist_remains(self):
        state=bot.initial();profiles=['1'*31+c for c in bot.ALPHABET]
        first=bot.select_candidates(state,profiles,self.now)
        state['unavailable_until']={first[0]:self.now+300}
        second=bot.select_candidates(state,profiles,self.now+10)
        self.assertEqual(len(second),20);self.assertNotIn(first[0],second)
        self.assertIn(first[1],second)
        state['config']['watchlist']=[first[0]]
        self.assertEqual(bot.select_candidates(state,profiles,self.now+20)[0],first[0])

    def test_alternate_pool_cannot_replace_held_pool(self):
        self.open();good=self.providers.pairs([MINT])
        self.providers.pairs=lambda addresses:[]
        self.providers.token_pairs=lambda address:[{**r,'pairAddress':OTHER} for r in good]
        self.now+=10
        result=self.tick()
        self.assertEqual(result['positions'][0]['pair'],PAIR)
        self.assertFalse(result['positions'][0]['mark_fresh'])

    def test_confirmation_metadata_and_equity_history_are_saved(self):
        self.configure({'enabled':True,'watchlist':[MINT],'discover':False})
        result=self.tick();queue=result['candidates'][0]
        self.assertEqual(queue['confirmation_due_at'],self.now+45)
        self.assertEqual(result['equity_history'][0]['equity_usd'],1000)
        self.now+=10;self.tick()
        self.assertEqual(len(bot.public()['equity_history']),1)
        self.now+=50;self.providers.price=1.01
        result=self.tick()
        self.assertEqual(len(result['equity_history']),2)
        self.assertAlmostEqual(result['unrealized_pnl_usd'],result['positions'][0]['current_modeled_value_usd']-result['positions'][0]['cost_usd'],places=3)
        self.now+=60;self.providers.missing=True
        result=self.tick()
        self.assertIsNone(result['equity_history'][-1]['equity_usd'])
        self.assertIsNone(result['unrealized_pnl_usd'])

    def test_confirmation_can_be_disabled_without_bypassing_other_rules(self):
        self.configure({'enabled':True,'watchlist':[MINT],'discover':False,'confirmation_enabled':False})
        self.providers.failure='Active mint or freeze authority.'
        self.assertFalse(self.tick()['positions'])
        self.providers.failure=None
        self.assertEqual(len(self.tick()['positions']),1)

    def test_custom_confirmation_wait_and_price_range(self):
        self.configure({'enabled':True,'watchlist':[MINT],'discover':False,'confirmation_seconds':90,
                        'confirmation_min_percent':2,'confirmation_max_percent':4})
        self.tick();self.now+=60;self.providers.price=1.03
        result=self.tick();self.assertFalse(result['positions'])
        self.assertEqual(result['candidates'][0]['confirmation_due_at'],self.now+30)
        self.now+=30;self.providers.price=1.01
        self.assertIn('Required rise: 2%–4%',self.tick()['candidates'][0]['reason'])
        self.now+=10;self.providers.price=1.03
        self.assertEqual(len(self.tick()['positions']),1)

    def test_confirmation_bounds_and_discovery_hold(self):
        for change in ({'confirmation_enabled':1},{'confirmation_seconds':9},{'confirmation_seconds':10.5},
                       {'confirmation_min_percent':9},{'confirmation_max_percent':0}):
            with self.assertRaises(ValueError):self.configure(change)
        self.configure({'confirmation_seconds':180})
        state=bot.load()[0];profiles=['1'*31+c for c in bot.ALPHABET]
        first=bot.select_candidates(state,profiles,self.now)
        for _ in range(19):self.assertEqual(bot.select_candidates(state,profiles,self.now),first)
        self.assertNotEqual(bot.select_candidates(state,profiles,self.now),first)

    def test_exploration_buys_price_only_without_entry_or_token_checks(self):
        self.configure({'enabled':True,'watchlist':[MINT],'discover':False,'entry_mode':'explore','auto_learning':True})
        self.providers.pairs=lambda addresses:[{'chainId':'solana','pairAddress':PAIR,'baseToken':{'address':MINT,'symbol':'TEST'},'priceUsd':'1'}]
        self.providers.failure='Active mint or freeze authority.'
        result=self.tick();self.assertEqual(len(result['positions']),1)
        self.assertEqual(self.providers.checks,0)
        self.assertIsNone(result['positions'][0]['entry_features']['liquidity_usd'])
        self.assertEqual(result['positions'][0]['entry_mode'],'explore')
        self.assertLess(result['positions'][0]['current_modeled_value_usd'],50)

    def test_exploration_keeps_cash_position_pause_and_missing_price_limits(self):
        self.configure({'watchlist':[MINT],'discover':False,'entry_mode':'explore'})
        self.assertFalse(self.tick(force=True)['positions'])
        self.configure({'enabled':True})
        self.providers.pairs=lambda addresses:[{'chainId':'solana','pairAddress':PAIR,'baseToken':{'address':MINT},'priceUsd':'0'}]
        self.assertFalse(self.tick()['positions'])
        self.assertEqual(bot.public()['cash_usd'],1000)
        with self.assertRaises(ValueError):self.configure({'entry_mode':'live'})

    def test_archived_notes_survive_closed_list_trimming_and_are_idempotent(self):
        self.configure({'entry_mode':'explore','auto_learning':True});self.open()
        self.now+=60;self.providers.price=.8
        result=self.tick();self.assertEqual(result['learning_summary']['total'],1)
        note=result['learning_summary']['notes'][0]
        self.assertLess(note['pnl_usd'],0);self.assertIn('Loss',note['note'])
        self.assertEqual(note['entry_features']['liquidity_usd'],200000)
        import json
        with database.get_connection() as conn:
            state=json.loads(conn.execute('SELECT state_json FROM solana_meme_paper_state').fetchone()[0]);state['closed']=[]
            conn.execute('UPDATE solana_meme_paper_state SET state_json=?',(json.dumps(state),))
            bot.learner.archive(conn,[note,note]);conn.commit()
        self.assertEqual(bot.public()['learning_summary']['total'],1)
        self.assertFalse(bot.public()['closed'])

    def test_virtual_funding_is_idempotent_and_not_daily_profit(self):
        self.open();before=bot.public();day_before=bot.load()[0]['day_equity']
        self.configure({'paper_capital_usd':100000,'max_positions':1000})
        result=bot.public()
        self.assertAlmostEqual(result['cash_usd']-before['cash_usd'],99000)
        self.assertAlmostEqual(result['paper_equity_usd']-100000,before['paper_equity_usd']-1000)
        self.assertAlmostEqual(bot.load()[0]['day_equity']-day_before,99000)
        self.assertEqual(result['positions'],before['positions'])
        self.configure({'paper_capital_usd':100000})
        self.assertEqual(bot.public()['cash_usd'],result['cash_usd'])
        self.assertEqual(len(bot.public()['funding_events']),1)
        for change in ({'paper_capital_usd':1000},{'max_positions':1001},{'max_positions':2.5}):
            with self.assertRaises(ValueError):self.configure(change)

    def test_all_thousand_positions_are_monitored_without_starving_discovery(self):
        self.configure({'enabled':True,'entry_mode':'explore','max_positions':1000,'paper_capital_usd':100000})
        state,revision=bot.load()
        addresses=[]
        for i in range(1000):
            n=i+1;encoded=''
            while n:n,r=divmod(n,58);encoded=bot.ALPHABET[r]+encoded
            addresses.append('1'*(32-((i+1).bit_length()+7)//8)+encoded)
        state['positions']={a:{'symbol':'TEST','pair':PAIR,'opened_at':self.now,'quantity':10,'cost_usd':10,
            'entry_liquidity_usd':200000,'peak_percent':0,'stop_percent':8,'target_percent':20,
            'trail_arm_percent':12,'trail_percent':6,'max_hold_minutes':30} for a in addresses}
        state['cash_usd']=90000
        import json
        with database.get_connection() as conn:conn.execute('UPDATE solana_meme_paper_state SET state_json=?',(json.dumps(state),));conn.commit()
        calls=[];base=self.providers.pairs([MINT])[0]
        def pairs(batch):
            self.assertLessEqual(len(batch),20);calls.extend(batch)
            return [{**base,'baseToken':{'address':a,'symbol':'TEST'},'priceUsd':'1.02'} for a in batch]
        self.providers.pairs=pairs
        fresh='1'*30+'zz'
        self.providers.profiles=lambda:[fresh]
        result=self.tick()
        self.assertEqual(len(result['positions']),1000)
        self.assertTrue(all(p['mark_fresh'] for p in result['positions']))
        self.assertTrue(set(addresses)<=set(calls))
        self.assertIn(fresh,calls)
        self.assertIsNotNone(result['paper_equity_usd'])
        self.assertEqual(result['candidates'][0]['mint'],fresh)

    def test_manual_entry_settings_receive_separate_outcome_version(self):
        self.configure({'minimum_liquidity_usd':12345})
        first=bot.public()['learning']['active_version']
        self.assertTrue(first.startswith('manual-'))
        self.configure({'minimum_m5_volume_usd':300})
        self.assertNotEqual(bot.public()['learning']['active_version'],first)
        self.assertEqual(bot.public()['learning'].get('change_count',0),0)

    def test_manual_settings_changes_are_archived_without_noop_or_email(self):
        self.configure({'minimum_liquidity_usd':12345})
        result=bot.public()['learning_summary']
        self.assertEqual(result['settings_change_count'],1)
        change=result['settings_changes'][0]
        self.assertEqual(change['before'],{'minimum_liquidity_usd':100000})
        self.assertEqual(change['after'],{'minimum_liquidity_usd':12345})
        self.assertEqual(change['actor'],'You')
        self.configure({'minimum_liquidity_usd':12345})
        self.assertEqual(bot.public()['learning_summary']['settings_change_count'],1)
        with database.get_connection() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM solana_meme_learning_mail').fetchone()[0],0)

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
        for changes in ({'private_key':'secret'},{'trade_usd':float('nan')},{'enabled':'true'},{'watchlist':['DOGE']},{'max_positions':1001}):
            with self.assertRaises(ValueError):self.configure(changes)
    def test_manual_scan_while_paused_does_not_buy(self):
        self.configure({'watchlist':[MINT],'discover':False});self.tick(force=True)
        self.now+=60;self.providers.price=1.01
        self.assertFalse(self.tick(force=True)['positions'])
    def test_early_manual_scans_do_not_restart_confirmation_timer(self):
        self.configure({'enabled':True,'watchlist':[MINT],'discover':False});self.tick()
        self.now+=10;self.providers.price=1.002
        self.assertFalse(self.tick(force=True)['positions'])
        self.now+=40;self.providers.price=1.01
        self.assertEqual(len(self.tick(force=True)['positions']),1)

    def test_discovery_rotates_after_confirmation_wait_without_dropping_positions(self):
        state=bot.initial();profiles=['1'*31+c for c in bot.ALPHABET]
        first=bot.select_candidates(state,profiles)
        self.assertEqual(len(first),20)
        self.assertEqual(bot.select_candidates(state,profiles[::-1]),first)
        for _ in range(5):
            self.assertEqual(bot.select_candidates(state,profiles),first)
        rotated=bot.select_candidates(state,profiles)
        self.assertNotEqual(rotated,first)
        state['positions']={MINT:{}}
        state['config']['watchlist']=[MINT]
        self.assertEqual(bot.select_candidates(state,profiles)[0],MINT)

    def test_missing_liquidity_retains_symbol_and_specific_reason(self):
        self.configure({'watchlist':[MINT],'discover':False})
        original=self.providers.pairs
        def no_liquidity(addresses):
            rows=original(addresses)
            del rows[0]['liquidity']
            return rows
        self.providers.pairs=no_liquidity
        result=self.tick(force=True)['candidates'][0]
        self.assertEqual(result['status'],'unavailable')
        self.assertIn('TEST',result['symbol'])
        self.assertEqual(result['reason'],'Liquidity data missing.')

    def test_http_auth_origin_and_execution_input_boundaries(self):
        allowed=False
        def auth(request):
            if not allowed:raise HTTPException(401,'Owner session required.')
        app=FastAPI();app.include_router(build_router(require_session=auth))
        with TestClient(app) as client:
            self.assertEqual(client.get('/meme-coins').status_code,401)
            self.assertEqual(client.get('/meme-coins/status').status_code,401)
            for route in ('trades','scanner','settings','history'):self.assertEqual(client.get('/meme-coins/'+route).status_code,401)
            allowed=True
            for route in ('trades','scanner','settings','history'):self.assertEqual(client.get('/meme-coins/'+route).status_code,200)
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
