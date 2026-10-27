import unittest
from copy import deepcopy
import solana_meme_bot as bot
import solana_meme_learning as learning


class LearningTests(unittest.TestCase):
    def state(self):
        state=bot.initial();state['config'].update(entry_mode='explore',auto_learning=True);return state
    def row(self,state,i,win=True,**extra):
        return {'id':str(i),'mint':str(i),'symbol':'TEST','opened_at':i+1,'closed_at':i+2,
                'entry_mode':'explore','exit_policy':learning.scope(state['config']),
                'entry_usd':50,'pnl_usd':2 if win else -2,
                'entry_features':{'liquidity_usd':200000 if win else 10000,'volume_m5_usd':20000 if win else 100},**extra}
    def training(self,state):return [self.row(state,i,i<20) for i in range(30)]
    def test_no_proposal_from_only_winners_or_small_sample(self):
        state=self.state();learning.update(state,[self.row(state,i) for i in range(29)],100)
        self.assertNotIn('proposal',state['learning'])
        learning.update(state,[self.row(state,i) for i in range(40)],100)
        self.assertNotIn('proposal',state['learning'])
    def test_proposal_is_frozen_and_cannot_use_existing_positions_or_backfill(self):
        state=self.state();training=self.training(state);learning.update(state,training,100)
        original=deepcopy(state['learning']['proposal']);ident=original['id']
        backfilled=[self.row(state,i,learning_candidate_id=ident) for i in range(50,80)]
        learning.update(state,training+backfilled,200)
        self.assertEqual(state['learning']['proposal']['minimums'],original['minimums'])
        self.assertEqual(state['learning']['proposal']['validation_count'],0)
        self.assertEqual(state['config']['entry_mode'],'explore')
    def test_successful_forward_test_applies_only_liquidity_and_volume(self):
        state=self.state();training=self.training(state);learning.update(state,training,100);proposal=state['learning']['proposal']
        future=[self.row(state,i,i%2==0,learning_candidate_id=proposal['id']) for i in range(110,130)]
        learning.update(state,training+future,200)
        self.assertEqual(state['config']['entry_mode'],'learned')
        self.assertEqual(state['config']['minimum_liquidity_usd'],200000)
        self.assertEqual(state['settings_revision'],1)
        self.assertEqual(state['config']['trade_usd'],50)
        self.assertTrue(state['learning']['last_result']['accepted'])
    def test_failed_forward_test_never_promotes_and_missing_data_is_not_a_win(self):
        state=self.state();training=self.training(state);learning.update(state,training,100);proposal=state['learning']['proposal']
        future=[self.row(state,i,False,learning_candidate_id=proposal['id']) for i in range(110,130)]
        learning.update(state,training+future,200)
        self.assertEqual(state['config']['entry_mode'],'explore')
        self.assertFalse(state['learning']['last_result']['accepted'])
        self.assertFalse(learning.matches({'entry_features':{}},proposal['minimums']))
    def test_paused_learning_and_changed_exit_policy_prevent_promotion(self):
        state=self.state();training=self.training(state);learning.update(state,training,100)
        state['config']['auto_learning']=False;learning.update(state,training,200)
        self.assertEqual(state['config']['entry_mode'],'explore')
        state['config']['auto_learning']=True;state['config']['stop_percent']=9
        learning.update(state,training,300)
        self.assertNotIn('proposal',state['learning'])
