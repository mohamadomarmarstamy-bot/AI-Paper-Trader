"""Read-only PAPER release eligibility evidence tests."""
import unittest

from pro_ticker_release_eligibility import review_paper_release_eligibility


class ReleaseEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.reservation = {"status": "filled", "symbol": "AAPL", "broker_order_id": "entry-1"}
        self.exits = [{
            "broker_order_id": "exit-1", "symbol": "AAPL", "side": "sell",
            "accounted_shares": 5, "ownership_verified": True, "fill_verified": True,
        }]

    def review(self, **changes):
        args = dict(
            reservation=self.reservation, entry_order_id="entry-1",
            entry_filled_shares=5, exit_fills=self.exits,
            remaining_strategy_shares=0, broker_evidence_verified=True,
        )
        args.update(changes)
        return review_paper_release_eligibility(**args)

    def test_complete_verified_exit_requires_separate_authorization(self):
        result = self.review()
        self.assertTrue(result["eligible_for_independent_review"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["budget_released"])
        self.assertFalse(result["database_updated"])

    def test_partial_exit_does_not_qualify(self):
        self.assertEqual(self.review(exit_fills=[{**self.exits[0], "accounted_shares": 3}])["reason"],
                         "entry_exit_share_mismatch")

    def test_open_strategy_position_does_not_qualify(self):
        self.assertEqual(self.review(remaining_strategy_shares=1)["reason"],
                         "strategy_position_still_open")

    def test_wrong_entry_order_does_not_qualify(self):
        self.assertEqual(self.review(entry_order_id="other")["reason"], "entry_order_link_mismatch")

    def test_unverified_broker_evidence_does_not_qualify(self):
        self.assertEqual(self.review(broker_evidence_verified=False)["reason"],
                         "broker_evidence_unverified")

    def test_wrong_strategy_exit_does_not_qualify(self):
        for updates in ({"symbol": "MSFT"}, {"side": "buy"},
                        {"ownership_verified": False}, {"fill_verified": False}):
            with self.subTest(updates=updates):
                self.assertEqual(self.review(exit_fills=[{**self.exits[0], **updates}])["reason"],
                                 "invalid_exit_evidence")

    def test_duplicate_exit_order_cannot_double_count(self):
        first = {**self.exits[0], "accounted_shares": 2}
        second = {**self.exits[0], "accounted_shares": 3}
        self.assertEqual(self.review(exit_fills=[first, second])["reason"], "invalid_exit_evidence")

    def test_invalid_share_counts_do_not_qualify(self):
        for value in (True, 0, -1, 2.5):
            with self.subTest(value=value):
                self.assertFalse(self.review(entry_filled_shares=value)["eligible_for_independent_review"])
                if value != 0:\n                    self.assertFalse(self.review(remaining_strategy_shares=value)["eligible_for_independent_review"])


if __name__ == "__main__":
    unittest.main()
