from contextlib import closing
from pathlib import Path
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from solders.keypair import Keypair
from solders.pubkey import Pubkey
import database
import solana_copy_trader as paper
import solana_real_copy as link
import solana_real_budget as budget
import solana_real_worker as live
import solana_real_execution as ex
from solana_meme_bot import Conflict


class Chain:
    def __init__(self):self.prepared=[];self.sent=[];self.receipt={'state':'pending'};self.exit_value=215_000_000;self.timeout=True
    def network(self):pass
    def balance(self,owner):return 1_000_000_000
    def finalized_token_balance(self,owner,mint,program):
        p=next(p for p in live.load()[0]['positions'].values() if p['mint']==mint)
        return {'quantity':p['quantity_raw'],'slot':int(time.time()*10)}
    def entry_rent(self,mint):return 4_000_000
    def rpc(self,method,params):
        if method=='getMinimumBalanceForRentExemption':return 2_000_000
        raise AssertionError(method)
    def build(self,owner,input_mint,output_mint,amount,policy):return {'otherAmountThreshold':str(self.exit_value)}
    def prepare(self,owner,side,mint,amount,policy,authorize,quote=None):
        authorize();value={'wire':'wire-'+str(len(self.prepared)),'signature':'signature-'+str(len(self.prepared)),
            'input_amount':amount,'minimum_output':1,'last_valid_block_height':1000,'decimals':6}
        self.prepared.append((side,amount,value));return value
    def send(self,prepared):
        self.sent.append(prepared['wire'])
        if self.timeout:raise ex.Unavailable('Synthetic timeout')
    def reconcile(self,order):return self.receipt


class RealWorkerTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(temp.name)/'live.db');p.start();self.addCleanup(p.stop)
        self.owner=str(Keypair().pubkey());self.source=str(Keypair().pubkey());self.mint=str(Pubkey.new_unique());self.chain=Chain()
        self.flags={'release_allowed':True,'signer_configured':True,'signer_matches':True,'jupiter_configured':True,'signer_address':self.owner,'executor_implemented':True,'network':'Solana mainnet'}
        for p in (patch.object(ex,'configuration',return_value=self.flags),patch.object(ex,'release_allowed',return_value=True),patch.object(budget,'sol_price',return_value={'usd':100})):
            p.start();self.addCleanup(p.stop)
        with closing(database.get_connection()) as conn:
            link.initialize(conn);paper.initialize(conn)
            conn.execute('UPDATE solana_real_copy_wallet SET wallet_address=?,verified_at=?,revision=1 WHERE id=1',(self.owner,time.time()))
            source=paper.initial();source['config']['enabled']=True;source['traders']=[{'wallet':self.source,'active':True,'name':'Synthetic source'}]
            source['last_cycle']=time.time();source['cursors'][self.source]={'provider':'rpc','rpc_coverage':{'checked_at':time.time()}}
            conn.execute('UPDATE solana_copy_state SET state_json=? WHERE id=1',(json.dumps(source),));conn.commit()
        budget.configure({'buy_usd':20},0)

    def activate(self):return live.activate(live.public()['revision'],1,budget.settings()['revision'],'ENABLE REAL SOLANA COPY TRADING',self.chain)

    def event(self,ident='a'*64,mint=None,side='buy',status='paper_buy',source_at=None):
        event={'id':ident,'wallet':self.source,'mint':mint or self.mint,'side':side,'status':status,'source_at':source_at or time.time(),'observed_at':time.time()}
        with closing(database.get_connection()) as conn:
            conn.execute('INSERT INTO solana_copy_events VALUES(?,?,?)',(ident,time.time(),json.dumps(event)));conn.commit()
        return event

    def enter(self):
        self.activate();self.event();live.tick(self.chain)
        self.chain.receipt={'state':'confirmed','token_delta':10_000_000,'native_delta':-200_005_000,'fee_lamports':5000,'slot':1}
        live.tick(self.chain)
        self.chain.receipt={'state':'pending'}
        return live.load()[0]['positions']['buy:'+'a'*64]

    def test_profitable_monitor_quote_cannot_sign_a_losing_half_sale(self):
        self.enter();original=self.chain.build
        def quote(owner,input_mint,output_mint,amount,policy):
            return {'otherAmountThreshold':str(300_105_000 if amount==10_000_000 else 90_000_000)}
        self.chain.build=quote;live.tick(self.chain)
        self.assertEqual(len(self.chain.prepared),1);self.assertFalse(live.orders(True))
        self.assertEqual(live.orders()[0]['status'],'rejected');self.assertIn('Profit trim deferred',live.orders()[0]['error'])
        self.assertIsNone(next(iter(live.load()[0]['positions'].values()))['pending_reason'])
        # A subsequent stop remains free to submit a full exit; no extra profit gate.
        self.chain.exit_value=100_000_000;self.chain.build=original
        state,_=live.load();next(iter(state['positions'].values()))['last_exit_attempt_at']=0
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE solana_real_run SET state_json=? WHERE id=1',(json.dumps(state),));conn.commit()
        live.tick(self.chain);self.assertEqual(live.orders(True)[0]['reason'],'stop_loss')

    def test_trial_wallet_cannot_buy_live_even_if_main_discovery_enrolls_it(self):
        from solana_paper_trials import CANDIDATES
        for _,wallet in CANDIDATES:
            self.source=wallet
            with closing(database.get_connection()) as conn:
                source,_=paper.load()
                source['traders']=[{'wallet':wallet,'active':True}]
                source['cursors']={wallet:{'provider':'rpc','rpc_coverage':{'checked_at':time.time()}}}
                conn.execute('UPDATE solana_copy_state SET state_json=? WHERE id=1',(json.dumps(source),));conn.commit()
            self.activate();self.event(ident=wallet);live.tick(self.chain)
        self.assertFalse(self.chain.prepared)
        self.assertFalse(self.chain.sent)

    def test_expiry_timer_survives_frequent_polling_and_restart(self):
        self.activate();self.event();live.tick(self.chain)
        self.chain.receipt={'state':'expired_unseen'}
        start=time.time()+1
        for offset in (0,5,10):
            with patch.object(live.time,'time',return_value=start+offset):live.tick(self.chain)
            self.assertEqual(live.orders(True)[0]['first_expired_at'],start)
        with patch.object(live.time,'time',return_value=start+16):live.tick(self.chain)
        self.assertFalse(live.orders(True));self.assertEqual(live.orders()[0]['status'],'expired')
        self.assertEqual(len(self.chain.prepared),1)

    def test_manual_full_exit_archives_missing_cost_without_inventing_proceeds(self):
        p=self.enter();live.pause(live.public()['revision']);start=time.time()+1
        with patch.object(self.chain,'finalized_token_balance',return_value={'quantity':0,'slot':20}):
            with patch.object(live.time,'time',return_value=start):live.tick(self.chain)
        state,_=live.load();self.assertEqual(state['positions'][p['id']]['quantity_raw'],10_000_000)
        self.assertFalse(live.public()['enabled']);self.assertEqual(len(self.chain.prepared),1)
        with patch.object(self.chain,'finalized_token_balance',return_value={'quantity':0,'slot':21}):
            with patch.object(live.time,'time',return_value=start+16):live.tick(self.chain)
        state,_=live.load();self.assertFalse(state['positions']);a=state['balance_adjustments'][0]
        self.assertEqual(a['unreconciled_cost_lamports'],200_005_000);self.assertIsNone(a['proceeds_lamports'])
        self.assertEqual(state['realized_lamports'],0);self.assertFalse(live.public()['accounting_complete'])
        status=link.public();self.assertFalse(status['can_start']);self.assertIsNone(status['live_pnl_sol'])
        self.assertEqual(status['operational_health']['label'],'Wallet accounting review')
        with self.assertRaisesRegex(Conflict,'accounting review'):self.activate()
        with self.assertRaises(Conflict):link.unlink(1)
        live.tick(self.chain);self.assertEqual(len(live.load()[0]['balance_adjustments']),1)
        self.assertEqual(len(self.chain.prepared),1)

    def test_external_partial_reduction_preserves_remaining_cost_and_blocks_affected_exit(self):
        p=self.enter();start=time.time()+1
        for offset,slot in ((0,20),(16,21),(32,22)):
            with patch.object(self.chain,'finalized_token_balance',return_value={'quantity':4_000_000,'slot':slot}),patch.object(live.time,'time',return_value=start+offset):live.tick(self.chain)
        state,_=live.load();p=state['positions'][p['id']]
        self.assertEqual(p['quantity_raw'],4_000_000);self.assertEqual(p['cost_lamports'],80_002_000)
        self.assertEqual(state['balance_adjustments'][0]['unreconciled_cost_lamports'],120_003_000)
        self.assertEqual(len(self.chain.prepared),1);self.assertFalse(state['enabled'])

    def test_balance_read_failure_stale_slot_and_same_slot_never_remove_holdings(self):
        p=self.enter();start=time.time()+1
        with patch.object(self.chain,'finalized_token_balance',side_effect=ex.Unavailable('RPC unavailable')):live.tick(self.chain)
        for offset,slot in ((0,0),(1,20),(17,20)):
            with patch.object(self.chain,'finalized_token_balance',return_value={'quantity':0,'slot':slot}),patch.object(live.time,'time',return_value=start+offset):live.tick(self.chain)
        state,_=live.load();self.assertIn(p['id'],state['positions']);self.assertFalse(state['balance_adjustments'])
        self.assertEqual(len(self.chain.prepared),1)

    def test_uncertain_bot_order_is_settled_before_external_balance_checks(self):
        self.enter();self.chain.exit_value=300_105_000;live.tick(self.chain)
        with patch.object(self.chain,'finalized_token_balance',side_effect=AssertionError('Pending swap first')):live.tick(self.chain)
        self.assertEqual(len(self.chain.prepared),2);self.assertTrue(live.orders(True))

    def test_recovered_balance_and_external_deposit_do_not_create_fake_profit(self):
        p=self.enter();start=time.time()+1
        with patch.object(self.chain,'finalized_token_balance',return_value={'quantity':20_000_000,'slot':20}),patch.object(live.time,'time',return_value=start):live.tick(self.chain)
        with patch.object(self.chain,'finalized_token_balance',return_value={'quantity':20_000_000,'slot':21}),patch.object(live.time,'time',return_value=start+16):live.tick(self.chain)
        state,_=live.load();self.assertFalse(state['balance_adjustments']);self.assertEqual(state['positions'][p['id']]['quantity_raw'],10_000_000)
        with patch.object(self.chain,'finalized_token_balance',return_value={'quantity':10_000_000,'slot':22}):live.tick(self.chain)
        self.assertNotIn('balance_observation',live.load()[0]['positions'][p['id']]);self.assertFalse(live.public()['enabled'])
        self.assertEqual(live.public()['realized_sol'],0)

    def test_disabled_default_never_fetches_or_signs_and_activation_is_explicit(self):
        with patch.object(self.chain,'network',side_effect=AssertionError('No network while disabled')):live.tick(self.chain)
        self.assertFalse(live.public()['enabled']);self.assertEqual(self.chain.prepared,[])
        with self.assertRaises(ValueError):live.activate(0,1,1,'',self.chain)
        with patch.object(ex,'configuration',return_value={**self.flags,'release_allowed':False}):
            with self.assertRaises(Conflict):self.activate()

    def test_activation_never_replays_old_source_buys(self):
        self.event();self.activate();live.tick(self.chain)
        self.assertEqual(self.chain.prepared,[]);self.assertEqual(live.orders(),[])
        self.event('b'*64,source_at=time.time()-100);live.tick(self.chain);self.assertEqual(self.chain.prepared,[])

    def test_failed_readonly_swap_probe_cannot_activate_or_create_an_order(self):
        with patch.object(self.chain,'build',side_effect=ex.Unavailable('Jupiter API key or plan does not permit swap building.')):
            with self.assertRaises(ex.Unavailable):self.activate()
        self.assertFalse(live.public()['enabled']);self.assertFalse(live.orders());self.assertFalse(self.chain.prepared)

    def test_timeout_reconciles_exact_stored_signature_and_posts_once(self):
        self.activate();self.event();live.tick(self.chain)
        self.assertEqual(live.orders(True)[0]['status'],'unknown');self.assertEqual(len(self.chain.prepared),1)
        live.tick(self.chain);self.assertEqual(len(self.chain.prepared),1);self.assertEqual(live.public()['positions'],[])
        pending=live.orders(True)[0];pending['send_started_at']=time.time()-20;live.save_order(pending)
        live.tick(self.chain);self.assertEqual(self.chain.sent,['wire-0','wire-0']);self.assertEqual(len(self.chain.prepared),1)
        self.chain.receipt={'state':'confirmed','token_delta':10_000_000,'native_delta':-200_005_000,'fee_lamports':5000,'slot':1}
        live.tick(self.chain);self.assertEqual(len(live.public()['positions']),1);self.assertEqual(live.orders(True),[])
        live.tick(self.chain);self.assertEqual(len(live.public()['positions']),1);self.assertEqual(len(self.chain.prepared),1)

    def test_repeated_coin_buys_and_skipped_source_signals_are_not_copied(self):
        self.enter();self.event('b'*64);self.event('c'*64,mint=str(Pubkey.new_unique()),status='skipped');live.tick(self.chain)
        self.assertEqual(len(self.chain.prepared),1)
        with closing(database.get_connection()) as conn:
            reasons=[r[0] for r in conn.execute('SELECT reason FROM solana_real_decisions')]
        self.assertIn('One live position per coin; repeated buys skipped.',reasons)
        self.assertIn('Source buy was not admitted by the paper copy rules.',reasons)

    def test_half_profit_then_net_break_even_exit_and_cooldown(self):
        self.enter();self.chain.exit_value=300_105_000;live.tick(self.chain)
        self.assertEqual(self.chain.prepared[-1][:2],('sell',5_000_000))
        self.chain.receipt={'state':'confirmed','token_delta':-5_000_000,'native_delta':150_000_000,'fee_lamports':5000,'slot':2};live.tick(self.chain)
        p=live.load()[0]['positions']['buy:'+'a'*64]
        self.assertEqual(p['quantity_raw'],5_000_000);self.assertEqual(p['cost_lamports'],100_002_500);self.assertEqual(p['stages'],1)
        self.chain.exit_value=100_000_000;self.chain.receipt={'state':'pending'}
        with closing(database.get_connection()) as conn:
            state,rev=live.load();state['positions'][p['id']]['last_exit_attempt_at']=0
            conn.execute('UPDATE solana_real_run SET state_json=? WHERE id=1',(json.dumps(state),));conn.commit()
        live.tick(self.chain);self.assertEqual(live.orders(True)[0]['reason'],'profit_protection')
        self.chain.receipt={'state':'confirmed','token_delta':-5_000_000,'native_delta':99_000_000,'fee_lamports':5000,'slot':3};live.tick(self.chain)
        state,_=live.load();self.assertFalse(state['positions']);self.assertGreater(state['cooldowns'][self.mint],time.time())
        self.assertEqual(state['realized_lamports'],48_995_000)

    def test_pause_keeps_exits_live_and_settings_changes_pause_new_buys(self):
        self.enter();old=live.public()['revision'];live.pause(old);live.pause(old)
        self.assertFalse(live.public()['enabled']);self.chain.exit_value=100_000_000;live.tick(self.chain)
        self.assertEqual(live.orders(True)[0]['reason'],'stop_loss')
        with self.assertRaises(Conflict):link.unlink(1)

    def test_unsent_signed_buy_is_canceled_on_pause_and_unknown_order_blocks_wallet_change(self):
        self.activate();self.event();live.tick(self.chain)
        with self.assertRaises(Conflict):link.unlink(1)
        order=live.orders(True)[0];order.pop('send_started_at');order['status']='signed';live.save_order(order)
        live.pause(live.public()['revision']);live.tick(self.chain)
        self.assertFalse(live.orders(True));self.assertEqual(live.orders()[0]['status'],'canceled')

    def test_failures_charge_only_confirmed_fees_and_expiry_needs_two_reads(self):
        self.activate();self.event();live.tick(self.chain)
        self.chain.receipt={'state':'failed','fee_lamports':5000};live.tick(self.chain)
        self.assertEqual(live.public()['realized_sol'],-.000005);self.assertFalse(live.public()['positions'])
        self.event('b'*64);self.chain.receipt={'state':'pending'};live.tick(self.chain)
        self.chain.receipt={'state':'expired_unseen'};live.tick(self.chain);self.assertTrue(live.orders(True))
        order=live.orders(True)[0];order['first_expired_at']=time.time()-20;live.save_order(order);live.tick(self.chain)
        self.assertFalse(live.orders(True));self.assertEqual(live.orders()[0]['status'],'expired')

    def test_real_worker_does_not_modify_paper_cash_positions_or_fills(self):
        self.activate();self.event();before=paper.load();live.tick(self.chain)
        self.assertEqual(paper.load(),before)
        self.assertNotIn('wire',json.dumps(live.public()))

    def test_token2022_receipts_keep_program_and_partial_exit_accounting_through_recovery(self):
        original=self.chain.prepare
        def prepare(*args,**kwargs):
            value=original(*args,**kwargs);value['token_program']=ex.TOKEN2022;return value
        self.chain.prepare=prepare;p=self.enter()
        self.assertEqual(p['token_program'],ex.TOKEN2022)
        self.chain.exit_value=300_105_000;live.tick(self.chain)
        pending=live.orders(True)[0];self.assertEqual(pending['prepared']['token_program'],ex.TOKEN2022)
        # Unknown submission survives another cycle without creating a second swap.
        count=len(self.chain.prepared);live.tick(self.chain);self.assertEqual(len(self.chain.prepared),count)
        self.chain.receipt={'state':'confirmed','token_delta':-5_000_000,'native_delta':150_000_000,'fee_lamports':5000,'slot':2}
        live.tick(self.chain);live.tick(self.chain)
        state,_=live.load();remaining=state['positions'][p['id']]
        self.assertEqual(remaining['quantity_raw'],5_000_000)
        self.assertEqual(remaining['token_program'],ex.TOKEN2022)
        self.assertEqual(remaining['cost_lamports'],100_002_500)
        self.assertEqual(state['realized_lamports'],49_997_500)

    def test_mint_or_rent_admission_failure_is_recorded_without_signing(self):
        self.activate();self.event()
        with patch.object(self.chain,'entry_rent',side_effect=ex.Unavailable('Token freeze authority is still active.')):live.tick(self.chain)
        self.assertEqual(self.chain.prepared,[]);self.assertEqual(self.chain.sent,[])
        self.assertFalse(live.orders(True));self.assertEqual(live.orders()[0]['status'],'rejected')
        self.assertIn('freeze authority',live.orders()[0]['error'])

    def test_changed_sizing_pauses_entries_but_keeps_existing_book(self):
        self.enter();budget.configure({'buy_usd':30},1)
        self.event('b'*64,mint=str(Pubkey.new_unique()));live.tick(self.chain)
        state,_=live.load();self.assertFalse(state['enabled']);self.assertEqual(len(state['positions']),1)
        self.assertEqual(len(self.chain.prepared),1);self.assertIn('Buy settings changed',state['error'])

    def test_pause_between_signing_and_broadcast_does_not_send(self):
        self.activate();self.event();original=self.chain.prepare
        def prepare(*args,**kwargs):
            value=original(*args,**kwargs);live.pause(live.public()['revision']);return value
        self.chain.prepare=prepare;live.tick(self.chain)
        self.assertEqual(self.chain.sent,[]);self.assertEqual(live.orders()[0]['status'],'rejected')
        self.assertFalse(live.public()['enabled'])

    def test_source_sale_during_preparation_cancels_the_unsent_buy(self):
        self.activate();self.event();original=self.chain.prepare
        def prepare(*args,**kwargs):
            value=original(*args,**kwargs);self.event('s'*64,side='sell',status='observed');return value
        self.chain.prepare=prepare;live.tick(self.chain)
        self.assertEqual(self.chain.sent,[]);self.assertEqual(live.orders()[0]['status'],'rejected')
        self.assertIn('Source sold before live broadcast',live.orders()[0]['error'])

    def test_same_second_source_sale_cancels_despite_fractional_clock_skew(self):
        now=int(time.time())+0.8
        with patch.object(live.time,'time',return_value=now):
            self.activate();self.event(source_at=now);original=self.chain.prepare
            def prepare(*args,**kwargs):
                value=original(*args,**kwargs)
                self.event('s'*64,side='sell',status='observed',source_at=now-0.5)
                return value
            self.chain.prepare=prepare;live.tick(self.chain)
        self.assertEqual(self.chain.sent,[])
        self.assertEqual(live.orders()[0]['status'],'rejected')

    def test_prior_second_or_unrelated_sale_does_not_cancel_fresh_buy(self):
        now=int(time.time())+0.8
        with patch.object(live.time,'time',return_value=now):
            self.activate();self.event(source_at=now);original=self.chain.prepare
            def prepare(*args,**kwargs):
                value=original(*args,**kwargs)
                self.event('s'*64,side='sell',status='observed',source_at=now-1)
                self.event('t'*64,mint=str(Pubkey.new_unique()),side='sell',status='observed',source_at=now)
                return value
            self.chain.prepare=prepare;live.tick(self.chain)
        self.assertEqual(self.chain.sent,['wire-0'])

    def test_recovered_unsent_buy_rechecks_source_sale_before_broadcast(self):
        self.activate();self.event();live.tick(self.chain)
        order=live.orders(True)[0];order.pop('send_started_at');order['status']='signed';live.save_order(order)
        self.chain.sent.clear();self.event('s'*64,side='sell',status='observed')
        live.tick(self.chain)
        self.assertEqual(self.chain.sent,[])
        self.assertEqual(live.orders()[0]['status'],'canceled')

    def test_revoked_server_setup_cancels_recovered_unsent_transaction(self):
        self.activate();self.event();live.tick(self.chain)
        order=live.orders(True)[0];order.pop('send_started_at');order['status']='signed';live.save_order(order)
        self.chain.sent.clear()
        with patch.object(ex,'configuration',return_value={**self.flags,'release_allowed':False}):live.tick(self.chain)
        self.assertEqual(self.chain.sent,[])
        self.assertEqual(live.orders()[0]['status'],'canceled')

    def test_expired_worker_cannot_persist_or_broadcast_a_late_preparation(self):
        self.activate();self.event();original=self.chain.prepare
        def prepare(*args,**kwargs):
            value=original(*args,**kwargs)
            with closing(database.get_connection()) as conn:
                conn.execute('UPDATE solana_real_run SET lease_until=0 WHERE id=1');conn.commit()
            return value
        self.chain.prepare=prepare;live.tick(self.chain)
        self.assertEqual(self.chain.sent,[]);self.assertEqual(live.orders(True)[0]['status'],'preparing')
        live.tick(self.chain);self.assertFalse(live.orders(True));self.assertEqual(live.orders()[0]['status'],'abandoned')

    def test_terminal_receipt_cannot_be_reopened_by_late_broadcast_update(self):
        self.enter();order=live.orders()[0];self.assertEqual(order['status'],'confirmed')
        order['status']='submitted'
        with self.assertRaises(Conflict):live.save_order(order)
        self.assertEqual(live.orders()[0]['status'],'confirmed');self.assertFalse(live.orders(True))


if __name__=='__main__':unittest.main()
