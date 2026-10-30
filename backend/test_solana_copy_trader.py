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
        self.config({'max_open_lots_per_coin':2})
        self.buy();self.now+=10;self.source.rows=[trade(self.now-2,index=2)];self.tick()
        self.assertEqual(len(copy.public()['positions']),2)
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
    def test_active_same_mint_pool_recovers_queued_exit_and_keeps_original_entry(self):
        data=self.buy();quantity=data['positions'][0]['quantity'];self.now+=10
        original=self.market.pairs
        self.market.pairs=lambda addresses:[{**r,'pairAddress':OTHER,'priceUsd':'0.2','quoteToken':{'address':SOL},'liquidity':{'usd':2000},'volume':{'m5':100},'txns':{'m5':{'buys':0,'sells':3}}} for r in original(addresses)]
        self.source.rows=[trade(self.now-1,'sell',index=2)]
        data=self.tick();self.assertEqual(data['positions'],[]);self.assertEqual(data['closed_count'],1)
        closed=data['closed'][0];self.assertEqual(closed['entry_pool'],POOL);self.assertEqual(closed['pool'],OTHER)
        self.assertEqual(closed['entry_price_usd'],2);self.assertEqual(closed['quantity'],quantity)
        self.assertEqual(closed['pool_changes'][0]['from'],POOL)
        self.assertAlmostEqual(closed['proceeds_usd'],quantity*.2*.99-.1)
        self.assertAlmostEqual(data['net_usd'],closed['pnl_usd']);self.assertTrue(data['valuation']['complete'])
    def test_price_only_inactive_wrong_mint_or_uncashed_alternates_do_not_close(self):
        data=self.buy();self.now+=10;self.source.rows=[trade(self.now-1,'sell',index=2)]
        base={'chainId':'solana','pairAddress':OTHER,'baseToken':{'address':TOKEN,'symbol':'COIN'},'priceUsd':'0.2','quoteToken':{'address':SOL},'liquidity':{'usd':2000},'volume':{'m5':100},'txns':{'m5':{'buys':1,'sells':3}}}
        for changes in ({'baseToken':{'address':WALLET}}, {'quoteToken':{'address':OTHER}}, {'liquidity':None}, {'volume':{'m5':0}}, {'txns':{'m5':{'buys':0,'sells':0}}}):
            self.market.pairs=lambda addresses,changes=changes:[{**base,**changes}]
            data=self.tick();self.assertEqual(data['closed_count'],0);self.assertIsNone(data['equity_usd'])
        self.assertEqual(data['valuation']['unpriced_holdings'],1)
        self.assertEqual(data['valuation']['cash_and_priced_holdings_usd'],data['cash_usd'])
    def test_inactive_original_pool_price_cannot_close_queued_exit(self):
        self.buy();self.now+=10;self.source.rows=[trade(self.now-1,'sell',index=2)]
        original=self.market.pairs
        self.market.pairs=lambda addresses:[{**r,'liquidity':None,'volume':{'m5':0}} for r in original(addresses)]
        data=self.tick();self.assertEqual(data['closed_count'],0);self.assertIsNone(data['equity_usd'])
    def test_unpriced_holdings_stay_excluded_from_total_and_partial_subtotal(self):
        data=self.buy();self.market.missing=True;self.now+=10;data=self.tick()
        self.assertIsNone(data['equity_usd']);self.assertIsNone(data['net_usd']);self.assertFalse(data['valuation']['complete'])
        self.assertEqual(data['valuation']['cash_and_priced_holdings_usd'],99949.9)
        self.assertEqual(data['valuation']['unpriced_holdings'],1)

    def test_closed_return_uses_net_earnings_and_all_completed_entry_costs(self):
        self.buy();self.now+=10;self.market.price=3;self.source.rows=[trade(self.now-1,'sell',index=2)]
        data=self.tick();self.assertAlmostEqual(data['closed_cost_usd'],50.1)
        self.assertAlmostEqual(data['closed_return_percent'],data['realized_pnl_usd']/50.1*100)
        # The 100-row UI history limit must not truncate the earnings denominator.
        with closing(database.get_connection()) as conn:
            for i in range(110):
                row={**data['closed'][0],'id':'synthetic-'+str(i),'pnl_usd':-1,'cost_usd':10}
                conn.execute('INSERT INTO solana_copy_closes(id,closed_at,details_json) VALUES(?,?,?)',(row['id'],self.now,json.dumps(row)))
            conn.commit()
        data=copy.public();self.assertEqual(len(data['closed']),100);self.assertEqual(data['closed_count'],111)
        self.assertAlmostEqual(data['closed_cost_usd'],1150.1)
        self.assertAlmostEqual(data['closed_return_percent'],data['realized_pnl_usd']/1150.1*100)
        self.assertAlmostEqual(data['average_closed_pnl_usd'],data['realized_pnl_usd']/111)
    def test_return_without_completed_trades_is_not_invented(self):
        data=self.buy();self.assertIsNone(data['closed_return_percent']);self.assertIsNone(data['average_closed_pnl_usd'])

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


    def test_six_distinct_adds_only_open_one_lot_and_never_replay(self):
        self.start();self.now+=10
        self.source.rows=[trade(self.now-6+i,index=i+1,tx=str(i+2)*88) for i in range(6)]
        data=self.tick();self.assertEqual(len(data['positions']),1);self.assertAlmostEqual(data['cash_usd'],100000-50.1)
        self.assertEqual(sum(e['status']=='paper_buy' for e in data['events']),1)
        self.assertEqual(sum('open-lot limit' in e['reason'] for e in data['events']),5)
        self.tick();self.assertEqual(copy.public()['cash_usd'],data['cash_usd'])
        self.now+=10;self.source.rows=[trade(self.now-1,'sell',index=9)];self.tick()
        self.source.rows=[trade(self.now-16+i,index=i+1,tx=str(i+2)*88) for i in range(6)]
        self.assertEqual(self.tick()['positions'],[])

    def test_same_coin_limit_applies_across_wallets(self):
        self.buy();state,_=copy.load()
        state['traders'].append({'wallet':OTHER,'selected_at':self.now,'active':True})
        state['cursors'][OTHER]={'at':self.now}
        with closing(database.get_connection()) as conn:conn.execute('UPDATE solana_copy_state SET state_json=? WHERE id=1',(json.dumps(state),));conn.commit()
        self.now+=10;self.source.rows=[trade(self.now-1,wallet=OTHER,index=2)]
        data=self.tick();self.assertEqual(len(data['positions']),1)
        self.assertIn('across all followed wallets',data['events'][0]['reason'])

    def test_new_buy_after_exit_is_copied_without_cooldown(self):
        self.buy();self.now+=10;self.source.rows=[trade(self.now-1,'sell',index=2)];self.tick()
        self.now+=10;self.source.rows=[trade(self.now-1,index=3)];data=self.tick()
        self.assertEqual(len(data['positions']),1)
        self.now+=1801;self.source.rows=[trade(self.now-1,index=4)]
        self.assertEqual(len(self.tick()['positions']),1)

    def test_legacy_cooldown_does_not_block_new_buys(self):
        self.buy();self.now+=10;self.source.rows=[trade(self.now-1,'sell',index=2)];self.tick()
        state,_=copy.load();state['mint_cooldowns']={TOKEN:self.now+1800};state['config']['reentry_cooldown_seconds']=1800
        with closing(database.get_connection()) as conn:conn.execute('UPDATE solana_copy_state SET state_json=? WHERE id=1',(json.dumps(state),));conn.commit()
        self.now+=10;self.source.rows=[trade(self.now-1,index=3)]
        self.assertEqual(len(self.tick()['positions']),1)

    def test_supported_source_add_is_copied_even_without_a_copied_position(self):
        self.start();self.now+=10;row=trade(self.now-1);row.update(source='solana_rpc_pump',source_position_before=100)
        self.source.rows=[row];data=self.tick();self.assertEqual(len(data['positions']),1)
        self.assertEqual(data['events'][0]['status'],'paper_buy')

    def test_twenty_percent_stop_uses_net_cost_and_works_with_source_offline_and_paused(self):
        data=self.buy();lot=data['positions'][0];self.config({'enabled':False});self.source.missing=True
        self.now+=10;self.market.price=(lot['cost_usd']*.8001+.1)/(lot['quantity']*.99)
        self.assertEqual(len(self.tick()['positions']),1)
        self.now+=10;self.market.price=(lot['cost_usd']*.7999+.1)/(lot['quantity']*.99)
        data=self.tick();self.assertEqual(data['positions'],[]);self.assertEqual(data['closed'][0]['reason'],'stop_loss')
        self.assertAlmostEqual(data['closed'][0]['pnl_usd'],-10.02501)
        self.assertAlmostEqual(data['cash_usd']-100000,data['realized_pnl_usd'])
        self.now+=10;self.tick();self.assertEqual(copy.public()['closed_count'],1)

    def test_stop_checks_run_before_wallet_polling_and_allow_gap_losses(self):
        self.buy();self.now+=10;self.market.price=.2
        def inspect():
            self.assertEqual(len(self.market_calls),1)
            raise copy.DataUnavailable('source offline')
        original=self.market.pairs;self.market_calls=[]
        self.market.pairs=lambda addresses:self.market_calls.append(addresses) or original(addresses)
        self.source.on_trade=inspect
        data=self.tick();self.assertEqual(data['closed_count'],1)
        self.assertLess(data['closed'][0]['pnl_usd'],-40)
        self.assertEqual(data['closed'][0]['stop_loss_percent_at_exit'],20)

    def test_stop_needs_current_price_and_recovers_without_backdating(self):
        self.buy();self.source.rows=[];self.now+=10;self.market.price=.2;self.market.missing=True
        data=self.tick();self.assertEqual(len(data['positions']),1);self.assertEqual(data['closed_count'],0)
        self.market.missing=False;self.now+=10;data=self.tick()
        self.assertEqual(data['closed_count'],1);self.assertEqual(data['closed'][0]['closed_at'],self.now)
        self.assertEqual(data['closed'][0]['exit_price_usd'],.2)

    def test_addon_lots_stop_individually_and_real_workspace_inherits_rule(self):
        self.config({'max_open_lots_per_coin':2})
        self.buy();self.now+=10;self.market.price=2.4;self.source.rows=[trade(self.now-1,index=2)];self.tick()
        self.now+=10;self.market.price=1.9;self.source.rows=[];data=self.tick()
        self.assertEqual(len(data['positions']),1);self.assertEqual(data['closed_count'],1)
        self.assertEqual(data['positions'][0]['entry_price_usd'],2)
        self.assertEqual(data['closed'][0]['entry_price_usd'],2.4)
        import solana_real_copy
        live=solana_real_copy.public();self.assertFalse(live['live_execution_enabled'])
        self.assertEqual(live['paper_sync']['blueprint']['settings']['stop_loss_percent'],20)
        self.assertFalse(live['paper_sync']['blueprint']['entry_protection']['one_open_position_per_coin'])
        self.assertEqual(live['paper_sync']['blueprint']['settings']['stop_loss_cooldown_seconds'],1800)

    def test_spacecat_pattern_stops_once_blocks_rebuys_and_other_coins_continue(self):
        self.buy();self.now+=10;self.market.price=1.4
        self.source.rows=[trade(self.now-1,index=i+2,tx=str(i+3)*88) for i in range(6)]
        data=self.tick();self.assertEqual(data['closed_count'],1);self.assertEqual(data['positions'],[])
        deadline=data['stop_loss_cooldowns'][TOKEN]
        self.assertEqual(deadline,self.now+1800)
        self.assertEqual(sum(e['reason'].startswith('Coin is in protective-exit cooldown') for e in data['events']),6)
        # A different mint still trades during this mint's cooldown.
        self.now+=10;self.source.rows=[trade(self.now-1,token=OTHER,index=11)]
        self.assertEqual(self.tick()['positions'][0]['mint'],OTHER)
        # The state is reloaded from SQLite on every tick, including the stop.
        self.now=deadline+1;self.source.rows=[trade(deadline-1,index=12)]
        data=self.tick();self.assertIn('protective-exit cooldown',data['events'][0]['reason'])
        # Only a fresh, never-seen source buy after expiry can open this coin.
        self.now+=10;self.source.rows=[trade(self.now-1,index=13)]
        data=self.tick();self.assertEqual(len(data['positions']),2)
        self.assertEqual(data['closed_count'],1)

    def test_missing_quote_does_not_free_a_coin_slot_and_source_exit_has_no_cooldown(self):
        self.buy();self.now+=10;self.market.missing=True;self.source.rows=[trade(self.now-1,index=2)]
        data=self.tick();self.assertEqual(len(data['positions']),1)
        self.assertIn('open-lot limit',data['events'][0]['reason'])
        self.market.missing=False;self.now+=10;self.source.rows=[trade(self.now-1,'sell',index=3)]
        data=self.tick();self.assertEqual(data['stop_loss_cooldowns'],{})
        self.now+=10;self.source.rows=[trade(self.now-1,index=4)]
        self.assertEqual(len(self.tick()['positions']),1)

    def test_upgrade_seeds_only_recent_stop_closes_and_preserves_live_lot(self):
        self.buy();self.now+=10;self.market.price=1.4;self.source.rows=[];self.tick()
        state,_=copy.load();state.pop('stop_loss_cooldowns')
        state['config'].pop('max_open_lots_per_coin');state['config'].pop('stop_loss_cooldown_seconds')
        state['config']['reentry_cooldown_seconds']=0
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE solana_copy_state SET state_json=? WHERE id=1',(json.dumps(state),));conn.commit()
        loaded,_=copy.load();self.assertEqual(loaded['stop_loss_cooldowns'][TOKEN],self.now+1800)
        self.assertEqual(loaded['config']['max_open_lots_per_coin'],1)
        self.now+=10;self.source.rows=[trade(self.now-1,index=2)]
        self.assertEqual(self.tick()['positions'],[])
        # Migration ignores ordinary losing source/manual exits.
        state,_=copy.load();state.pop('stop_loss_cooldowns')
        with closing(database.get_connection()) as conn:
            conn.execute("UPDATE solana_copy_closes SET details_json=json_set(details_json,'$.reason','trader_sell')")
            conn.execute('UPDATE solana_copy_state SET state_json=? WHERE id=1',(json.dumps(state),));conn.commit()
        self.assertEqual(copy.load()[0]['stop_loss_cooldowns'],{})

    def test_raising_limit_is_explicit_existing_lots_are_not_liquidated(self):
        self.config({'max_open_lots_per_coin':3});self.buy()
        self.now+=10;self.source.rows=[trade(self.now-1,index=2),trade(self.now-1,index=3)];self.tick()
        self.config({'max_open_lots_per_coin':1});self.now+=10;self.source.rows=[trade(self.now-1,index=4)]
        data=self.tick();self.assertEqual(len(data['positions']),3);self.assertEqual(data['closed_count'],0)
        self.assertIn('open-lot limit',data['events'][0]['reason'])
        for value in (-1,0,1.5,float('nan'),float('inf')):
            with self.assertRaises(ValueError):self.config({'max_open_lots_per_coin':value})
        with self.assertRaises(ValueError):self.config({'stop_loss_cooldown_seconds':-1})

    def test_zero_future_stop_cooldown_does_not_block_a_new_buy(self):
        self.config({'stop_loss_cooldown_seconds':0});self.buy();self.now+=10;self.market.price=1.4
        self.source.rows=[trade(self.now-1,index=2)]
        data=self.tick();self.assertEqual(data['closed_count'],1);self.assertEqual(len(data['positions']),1)
        self.assertEqual(data['stop_loss_cooldowns'],{})

    def test_stop_setting_validation_and_zero_disables(self):
        for value in (-1,100.01,float('nan'),float('inf')):
            with self.assertRaises(ValueError):self.config({'stop_loss_percent':value})
        self.buy();self.config({'stop_loss_percent':0});self.now+=10;self.market.price=.2;self.source.rows=[]
        self.assertEqual(len(self.tick()['positions']),1)

    def target_price(self,lot,gain):
        return (lot['cost_usd']*(1+gain/100)+.1)/(lot['quantity']*.99)

    def test_halves_at_20_40_80_and_restarts_do_not_repeat_a_stage(self):
        lot=self.buy()['positions'][0];original_quantity=lot['quantity'];original_cost=lot['cost_usd']
        for stage,target in enumerate((20,40,80),1):
            self.now+=10;self.source.rows=[];self.market.price=self.target_price(lot,target+.001)
            data=self.tick();self.assertEqual(len(data['positions']),1)
            lot=data['positions'][0]
            self.assertAlmostEqual(lot['quantity'],original_quantity/(2**stage))
            self.assertAlmostEqual(lot['cost_usd'],original_cost/(2**stage))
            self.assertEqual(lot['profit_take_stages'],stage)
            self.assertEqual(lot['profit_take_next_percent'],target*2)
            self.assertEqual(data['partial_exit_count'],stage);self.assertEqual(data['completed_lot_count'],0)
            self.assertEqual(data['closed'][0]['reason'],'profit_take_half')
            self.assertEqual(data['closed'][0]['lot_id'],lot['id'])
            self.assertAlmostEqual(data['net_usd'],data['realized_pnl_usd']+lot['mark_usd']-lot['cost_usd'])
            self.assertAlmostEqual(sum(c['cost_usd'] for c in data['closed'])+lot['cost_usd'],original_cost)
            self.now+=10;again=self.tick();self.assertEqual(again['closed_count'],stage)
        # Source sell closes only the remaining eighth, with one more exit fee.
        self.now+=10;self.source.rows=[trade(self.now-1,'sell',index=20)]
        data=self.tick();self.assertEqual(data['positions'],[]);self.assertEqual(data['closed_count'],4)
        self.assertEqual(data['completed_lot_count'],1);self.assertEqual(data['partial_exit_count'],3)
        self.assertAlmostEqual(data['closed_cost_usd'],original_cost)
        self.assertAlmostEqual(sum(c['quantity'] for c in data['closed']),original_quantity)
        self.assertAlmostEqual(data['cash_usd']-100000,data['realized_pnl_usd'])

    def test_gap_trims_once_and_advances_to_next_unreached_level(self):
        lot=self.buy()['positions'][0];self.now+=10;self.source.rows=[]
        self.market.price=self.target_price(lot,100)
        data=self.tick();self.assertEqual(data['closed_count'],1)
        self.assertEqual(data['positions'][0]['profit_take_next_percent'],160)
        for _ in range(3):self.now+=10;self.assertEqual(self.tick()['closed_count'],1)

    def test_first_trim_arms_net_break_even_and_cooldown_but_does_not_free_slot(self):
        lot=self.buy()['positions'][0];self.now+=10;self.source.rows=[];self.market.price=self.target_price(lot,20.01)
        data=self.tick();runner=data['positions'][0];banked=data['realized_pnl_usd']
        self.now+=10;self.source.rows=[trade(self.now-1,index=2)]
        data=self.tick();self.assertEqual(len(data['positions']),1);self.assertIn('open-lot limit',data['events'][0]['reason'])
        self.market.price=self.target_price(runner,-.001);self.now+=10;self.source.rows=[trade(self.now-1,index=3)]
        data=self.tick();self.assertEqual(data['positions'],[])
        self.assertEqual(data['closed'][0]['reason'],'profit_protection')
        self.assertGreater(data['realized_pnl_usd'],banked-.01)
        self.assertIn(TOKEN,data['stop_loss_cooldowns'])
        self.assertIn('cooldown',data['events'][0]['reason'])

    def test_partial_waits_for_quote_works_paused_offline_and_keeps_runner_protection_if_disabled(self):
        lot=self.buy()['positions'][0];self.now+=10;self.market.price=self.target_price(lot,20.01)
        self.source.rows=[];self.market.missing=True
        self.assertEqual(self.tick()['closed_count'],0)
        self.config({'enabled':False});self.source.missing=True;self.market.missing=False;self.now+=10
        data=self.tick();self.assertEqual(data['partial_exit_count'],1)
        runner=data['positions'][0];self.config({'profit_take_enabled':False})
        self.now+=10;self.market.price=self.target_price(runner,80);self.assertEqual(self.tick()['closed_count'],1)
        self.now+=10;self.market.price=self.target_price(runner,-1)
        self.assertEqual(self.tick()['closed'][0]['reason'],'profit_protection')

    def test_manual_and_source_exits_take_priority_over_a_profit_trim(self):
        lot=self.buy()['positions'][0];copy.queue_exits([lot['id']],False,copy.public()['revision'])
        self.now+=10;self.market.price=self.target_price(lot,50);self.source.rows=[]
        data=self.tick();self.assertEqual(data['closed_count'],1);self.assertFalse(data['closed'][0]['partial_exit'])
        self.assertEqual(data['closed'][0]['reason'],'manual_sell')

    def test_profit_settings_validate_and_do_not_reset_an_existing_ladder(self):
        for value in (0,-1,100.1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):self.config({'profit_take_first_percent':value})
        with self.assertRaises(ValueError):self.config({'profit_take_enabled':1})
        lot=self.buy()['positions'][0];self.config({'profit_take_first_percent':10})
        self.now+=10;self.source.rows=[];self.market.price=self.target_price(lot,10.01)
        data=self.tick();self.assertEqual(data['positions'][0]['profit_take_next_percent'],20)
        self.config({'profit_take_first_percent':5});self.now+=10
        self.assertEqual(self.tick()['closed_count'],1)

    def test_partial_never_realizes_a_loss_due_to_tiny_slice_exit_charge(self):
        self.config({'trade_usd':.1,'profit_take_first_percent':.01,'stop_loss_percent':0})
        lot=self.buy()['positions'][0];self.now+=10;self.source.rows=[];self.market.price=self.target_price(lot,1)
        data=self.tick();self.assertEqual(data['closed_count'],0);self.assertEqual(data['positions'][0]['quantity'],lot['quantity'])

    def test_settings_race_rolls_back_partial_cash_quantity_and_journal_together(self):
        before=self.buy();lot=before['positions'][0]
        self.now+=10;self.source.rows=[];self.market.price=self.target_price(lot,20.01)
        self.source.on_trade=lambda:self.config({'trade_usd':75})
        with self.assertRaises(regular.Conflict):self.tick()
        data=copy.public();self.assertEqual(data['closed_count'],0)
        self.assertEqual(data['cash_usd'],before['cash_usd'])
        self.assertEqual(data['positions'][0]['quantity'],lot['quantity'])


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
