from copy import deepcopy
import unittest

import solana_copy_trader as paper
from solana_owner_roster import LIVE_ADDITION, enroll
from solana_live_source_policy import paper_only
from solana_paper_trials import CANDIDATES


class OwnerRosterTests(unittest.TestCase):
    def state(self):
        state = paper.initial()
        state.update(started_at=50, public_rpc_enabled=True)
        return state

    def test_owner_source_is_prospective_and_existing_pause_is_preserved(self):
        state = self.state()
        enroll(state, 100)
        row = state['traders'][0]
        wallet = LIVE_ADDITION['wallet']
        self.assertEqual(row['wallet'], wallet)
        self.assertFalse(paper_only(wallet, state['traders']))
        self.assertEqual(row['buy_resume_at'], 100)
        self.assertEqual(state['cursors'][wallet], {'at': 100, 'provider': 'rpc'})
        self.assertIsNone(row['historical_realized_usd'])
        row['active'] = False
        state['cursors'][wallet]['at'] = 150
        before = deepcopy(state)
        enroll(state, 200)
        self.assertEqual(state, before)

    def test_trial_and_unstarted_or_non_rpc_setups_are_unchanged(self):
        for change in ({'trial_started_at': 60}, {'started_at': None}, {'public_rpc_enabled': False}):
            state = self.state()
            state.update(change)
            before = deepcopy(state)
            enroll(state, 100)
            self.assertEqual(state, before)

    def test_ah75_is_isolated_and_cannot_become_live(self):
        wallet = 'AH75Bn6QHSt5K2Ad4p1otns3JTwpSJWZTwiwKWNhmnKx'
        self.assertIn(('AH75', wallet), CANDIDATES)
        self.assertTrue(paper_only(wallet, []))
        state = self.state()
        enroll(state, 100)
        self.assertFalse(any(row['wallet'] == wallet for row in state['traders']))
        self.assertNotIn(LIVE_ADDITION['wallet'], {w for _, w in CANDIDATES})


if __name__ == '__main__':
    unittest.main()
