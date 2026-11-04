import concurrent.futures
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from budget import BudgetError, BudgetExhausted, Ledger, cost
from settle import settle


class ApprovalBudgetTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.ledger = Ledger(self.directory/'ledger.db')
        self.grant = {'id': 'comment-42', 'cap_nanodollars': cost(100, 100), 'run_id': 1, 'head': 'revision'}
        self.ledger.authorize(self.grant)

    def test_parallel_reservations_cannot_exceed_scoped_cap(self):
        def reserve(index):
            try:
                Ledger(self.ledger.path).reserve(60, 60, 'review', str(index))
                return True
            except BudgetExhausted:
                return False
        with patch.dict(os.environ, REVIEW_BUDGET_GRANT=self.grant['id']), concurrent.futures.ThreadPoolExecutor() as pool:
            self.assertEqual(sum(pool.map(reserve, range(3))), 1)
        summary = self.ledger.summary()
        self.assertEqual(summary['pilot_committed_nanodollars'], 0)
        self.assertEqual(summary['grants'][0]['committed_nanodollars'], cost(60, 60))

    def test_settlement_resume_preserves_uncertain_cost_and_prevents_replay(self):
        with patch.dict(os.environ, REVIEW_BUDGET_GRANT=self.grant['id']):
            key = self.ledger.reserve(80, 80, 'qualification', 'first')
            with self.assertRaises(BudgetError):
                self.ledger.reconcile(key, {}, 1)
            settle(self.directory)
            resumed = Ledger(self.directory/'resumed.db')
            resumed.merge(self.directory/'recovery/ledger.db')
            resumed.authorize(self.grant)
            with self.assertRaises(BudgetExhausted):
                resumed.reserve(21, 21, 'review', 'retry')
            resumed.reconcile(key, {'usage': {'inputTokens': 10, 'outputTokens': 10}}, 1)
            resumed.reserve(90, 90, 'review', 'remaining')
        for changed in ({'run_id': 2}, {'head': 'new-revision'}, {'cap_nanodollars': cost(200, 200)}):
            with self.assertRaises(BudgetError):
                resumed.authorize({**self.grant, **changed})

    def test_approval_neither_resets_pilot_nor_waives_overruns(self):
        self.ledger.record_external_reservation(2_000_000_000)
        with patch.dict(os.environ, REVIEW_BUDGET_GRANT=self.grant['id']):
            key = self.ledger.reserve(1, 1, 'qualification', 'approved')
        self.assertEqual(self.ledger.summary()['qualification_committed_nanodollars'], 2_000_000_000)
        with self.assertRaises(BudgetExhausted):
            self.ledger.reserve(1, 1, 'qualification', 'pilot')
        with self.assertRaises(BudgetError):
            self.ledger.reconcile(key, {'usage': {'inputTokens': 2, 'outputTokens': 2}}, 1)
        with patch.dict(os.environ, REVIEW_BUDGET_GRANT=self.grant['id']), self.assertRaisesRegex(BudgetError, 'halted'):
            self.ledger.reserve(1, 1, 'review', 'blocked')

    def test_legacy_ledger_and_grants_merge_without_repricing(self):
        legacy = self.directory/'legacy.db'
        with closing(sqlite3.connect(legacy)) as db, db:
            db.execute('CREATE TABLE calls (id TEXT,phase TEXT,identity TEXT,reserved INTEGER,charged INTEGER,metrics TEXT)')
            db.execute("INSERT INTO calls VALUES ('old','review','old',500,NULL,NULL)")
        self.ledger.merge(legacy)
        with patch.dict(os.environ, REVIEW_BUDGET_GRANT=self.grant['id']):
            self.ledger.reserve(10, 10, 'review', 'new')
        other = Ledger(self.directory/'other.db')
        other.merge(self.ledger.path)
        other.merge(self.ledger.path)
        self.assertEqual(other.summary(), self.ledger.summary())
        self.assertEqual(other.summary()['pilot_committed_nanodollars'], 500)
        with other.connect() as db:
            db.execute('UPDATE grants SET cap=cap+1')
        with self.assertRaisesRegex(BudgetError, 'invalid historical allowance'):
            self.ledger.merge(other.path)
