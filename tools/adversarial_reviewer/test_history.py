import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import os

from budget import BudgetError, Ledger
from history import attribute, fingerprint


class HistoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.ledger = Ledger(Path(directory.name)/'ledger.db')
        self.ledger.record_preflight()
        with self.ledger.connect() as db:
            self.rows = db.execute('SELECT id,phase,identity,reserved,charged,metrics FROM calls').fetchall()
        self.evidence = {'schema':1,'repository':'org/repo','groups':[{
            'ids':['account-probes-20261015'],'pr':0,'start':'2026-10-15T00:00:00Z',
            'count':1,'rows_sha256':fingerprint(self.rows)}]}

    def test_idempotent_attribution_preserves_uncertain_repository_probe_cost(self):
        before = self.ledger.summary()
        attribute(self.ledger,self.evidence)
        attribute(self.ledger,self.evidence)
        self.assertEqual(self.ledger.summary(),before)
        with patch.dict(os.environ,GITHUB_REPOSITORY='org/repo',PR_NUMBER='10'):
            policy = self.ledger.policy_budget()
        self.assertEqual(policy['pr_remaining_nanodollars'],2_000_000_000)
        self.assertEqual(policy['daily_remaining_nanodollars'],4_970_000_000)

    def test_changed_charges_or_conflicting_owners_require_new_evidence(self):
        changed = copy.deepcopy(self.evidence)
        changed['groups'][0]['rows_sha256'] = '0'*64
        with self.assertRaisesRegex(BudgetError,'does not match'):
            attribute(self.ledger,changed)
        attribute(self.ledger,self.evidence)
        changed = copy.deepcopy(self.evidence)
        changed['groups'][0]['pr'] = 99
        with self.assertRaisesRegex(BudgetError,'conflicting historical attribution'):
            attribute(self.ledger,changed)
        self.ledger.record_external_reservation(100)
        attribute(self.ledger,self.evidence)
        with patch.dict(os.environ,GITHUB_REPOSITORY='org/repo',PR_NUMBER='10'), self.assertRaisesRegex(BudgetError,'attribution'):
            self.ledger.policy_budget()
