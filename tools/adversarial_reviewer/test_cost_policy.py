import concurrent.futures
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from budget import BudgetError, BudgetExhausted, Ledger
from cost_policy import check

ENV = {'REVIEW_COST_POLICY': 'true', 'GITHUB_REPOSITORY': 'org/repo', 'PR_NUMBER': '1'}
MODEL = {'pricing': {'input_nanodollars': 1, 'output_nanodollars': 1}}


class CostPolicyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)/'ledger.db'
        self.ledger = Ledger(self.path)
        env = patch.dict(os.environ, ENV)
        env.start()
        self.addCleanup(env.stop)

    def reserve(self, amount, phase='review'):
        return self.ledger.reserve(amount, 0, phase, 'synthetic', model=MODEL)

    def settle(self, key, amount):
        self.ledger.reconcile(key, {'usage': {'inputTokens': amount, 'outputTokens': 0}}, 0)

    def test_exact_boundaries_and_one_nanodollar_over(self):
        check(2_000_000_000, self.ledger.policy_budget())
        with self.assertRaises(BudgetExhausted):
            check(2_000_000_001, self.ledger.policy_budget())
        self.reserve(2_000_000_000)
        with self.assertRaises(BudgetExhausted):
            self.reserve(1)

    def test_revisions_reruns_grants_and_phases_do_not_reset_pr(self):
        self.reserve(1_000_000_000, 'qualification')
        self.ledger.authorize({'id':'new', 'cap_nanodollars':100_000_000_000})
        with patch.dict(os.environ, HEAD_SHA='new', GITHUB_RUN_ID='new', REVIEW_BUDGET_GRANT='new'):
            self.reserve(1_000_000_000)
            with self.assertRaises(BudgetExhausted):
                self.reserve(1)
        self.assertEqual(Ledger(self.path).policy_budget()['pr_remaining_nanodollars'], 0)

    def test_repository_daily_limit_across_prs(self):
        self.reserve(2_000_000_000)
        with patch.dict(os.environ, PR_NUMBER='2'):
            self.reserve(2_000_000_000)
        with patch.dict(os.environ, PR_NUMBER='3'):
            self.reserve(1_000_000_000)
            with self.assertRaises(BudgetExhausted):
                self.reserve(1)
            self.assertEqual(self.ledger.policy_budget()['daily_remaining_nanodollars'], 0)

    def test_reservations_are_atomic_and_survive_restart(self):
        def reserve(index):
            try:
                Ledger(self.path).reserve(1_100_000_000, 0, 'review', str(index), model=MODEL)
                return True
            except BudgetExhausted:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(reserve, range(4))), 1)
        self.assertEqual(Ledger(self.path).policy_budget()['pr_remaining_nanodollars'], 900_000_000)

    def test_concurrent_prs_cannot_each_claim_the_daily_balance(self):
        worker = threading.local()
        def reserve(number):
            worker.number = number
            try:
                Ledger(self.path).reserve(2_000_000_000, 0, 'review', str(number), model=MODEL)
                return True
            except BudgetExhausted:
                return False
        with patch('cost_policy.context', side_effect=lambda: ('org/repo',worker.number)), \
                concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            self.assertEqual(sum(pool.map(reserve, (1,2,3))), 2)
        self.assertEqual(self.ledger.policy_budget()['daily_remaining_nanodollars'], 1_000_000_000)

    def test_uncertain_and_cross_midnight_calls_are_not_forgiven(self):
        with patch('cost_policy.utc_day', return_value='2026-10-15'):
            pending = self.reserve(1_000_000_000)
            settled = self.reserve(500_000_000)
            self.settle(settled, 400_000_000)
        with patch('cost_policy.utc_day', return_value='2026-10-16'):
            self.assertEqual(self.ledger.policy_budget()['daily_remaining_nanodollars'], 4_000_000_000)
            self.assertEqual(self.ledger.policy_budget()['pr_remaining_nanodollars'], 600_000_000)
            self.settle(pending, 900_000_000)
            self.assertEqual(self.ledger.policy_budget()['daily_remaining_nanodollars'], 4_100_000_000)
        with patch('cost_policy.utc_day', return_value='2026-10-17'):
            self.assertEqual(self.ledger.policy_budget()['daily_remaining_nanodollars'], 5_000_000_000)
            self.assertEqual(self.ledger.policy_budget()['pr_remaining_nanodollars'], 700_000_000)

    def test_missing_usage_keeps_full_reservation(self):
        key = self.reserve(2_000_000_000)
        with self.assertRaises(BudgetError):
            self.ledger.reconcile(key, {}, 0)
        with self.assertRaises(BudgetExhausted):
            self.reserve(1)

    def test_old_unattributed_history_blocks_instead_of_resetting(self):
        self.ledger.record_preflight()
        with self.assertRaisesRegex(BudgetError, 'attribution'):
            self.ledger.policy_budget()
        with self.assertRaises(BudgetError):
            self.reserve(1)

    def test_merge_carries_attribution_and_retains_uncertain_calls(self):
        self.reserve(1_000_000_000)
        merged = Ledger(str(self.path)+'.merged')
        merged.merge(self.path)
        merged.merge(self.path)
        self.assertEqual(merged.policy_budget(), self.ledger.policy_budget())
        with merged.connect() as db:
            db.execute('UPDATE attribution SET pr=99')
        with self.assertRaisesRegex(BudgetError, 'conflicting historical attribution'):
            self.ledger.merge(merged.path)

    def test_cancelled_snapshot_can_later_merge_settlement(self):
        key = self.reserve(1_000_000_000)
        snapshot = Ledger(str(self.path)+'.snapshot')
        snapshot.merge(self.path)
        self.settle(key, 100)
        snapshot.merge(self.path)
        self.assertEqual(snapshot.policy_budget()['pr_remaining_nanodollars'], 2_000_000_000-100)
        # An older snapshot never rolls a settled call back to uncertain.
        self.ledger.merge(snapshot.path)
        self.assertEqual(snapshot.policy_budget(), self.ledger.policy_budget())
