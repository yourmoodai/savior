import copy
import io
import json
import os
import runpy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from budget import Ledger
from bedrock_client import complete
import preflight
from quote import create, estimate, inspect_archive, validate

ENV = {'GITHUB_REPOSITORY': 'org/repo', 'PR_NUMBER': '5', 'BASE_SHA': 'base', 'HEAD_SHA': 'head',
       'BEDROCK_REVIEW_MODEL': 'deepseek.v3.2', 'AWS_REGION': 'us-east-1', 'GITHUB_RUN_ID': '7',
       'BEDROCK_REVIEW_ENABLED': 'true'}
MANIFESTS = {s: {'chunks': [{'input_tokens': 1000, 'cached_assessment': False}]} for s in ('baseline', 'adaptive')}


class QuoteTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, ENV)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.ledger = Ledger(self.directory/'ledger.db')
        self.quote = create(MANIFESTS, self.ledger.summary(), {'strategy': 'baseline'})

    def test_quote_is_offline_and_counts_retries_verification_and_summaries(self):
        with patch('bedrock_client.complete', side_effect=AssertionError('inference is forbidden')):
            quote = create(MANIFESTS, self.ledger.summary(), None)
        self.assertGreater(quote['items'][0]['calls'], 0)
        self.assertEqual([i['calls'] for i in quote['items'][1:]], [1]*14+[4])
        self.assertGreater(quote['cap_nanodollars'], quote['estimated_nanodollars'])
        self.assertEqual(quote['cap_nanodollars'] % 10_000_000, 0)
        cached = estimate({'chunks': [{'input_tokens': 1000, 'cached_assessment': True}]})
        self.assertEqual([i['calls'] for i in cached], [0]*14+[4])

    def test_digest_expiration_archive_and_model_pricing(self):
        validate(self.quote)
        for changed in ({'cap_nanodollars': 1}, {'binding': {}}):
            with self.assertRaises(ValueError):
                validate({**self.quote, **changed})
        with self.assertRaises(ValueError):
            validate(self.quote, now=self.quote['expires_at'])
        for filename in ('quote.json', '../quote.json'):
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w') as zipped:
                zipped.writestr(filename, json.dumps(self.quote))
            if filename == 'quote.json':
                self.assertEqual(inspect_archive(archive.getvalue()), self.quote)
            else:
                with self.assertRaises(ValueError):
                    inspect_archive(archive.getvalue())
        for change in ({'AWS_REGION': 'eu-west-1'},):
            with patch.dict(os.environ, change), self.assertRaisesRegex(ValueError, 'unsupported_pricing'):
                create(MANIFESTS, self.ledger.summary(), {'strategy': 'baseline'})

    def approval(self):
        b = self.quote['binding']
        return {'quote': self.quote, 'grant': {'id': 'comment-42', 'quote_id': self.quote['id'],
            **{k: b[k] for k in ('head', 'base', 'repository', 'pr', 'configuration')},
            'run_id': 7, 'cap_nanodollars': self.quote['cap_nanodollars']}}

    def test_approval_binds_configuration_revision_plan_and_logical_run(self):
        approval = self.approval()
        for key in ('head', 'base', 'configuration', 'model', 'region', 'plans'):
            changed = copy.deepcopy(self.quote)
            changed['binding'][key] = 'changed'
            self.assertEqual(preflight.authorize(self.ledger, changed, approval), ('quote_stale', None))
        self.assertEqual(preflight.authorize(self.ledger, self.quote, approval), (None, 'comment-42'))
        self.assertEqual(preflight.authorize(self.ledger, self.quote, approval), (None, 'comment-42'))
        with patch.dict(os.environ, GITHUB_RUN_ID='8'):
            self.assertEqual(preflight.authorize(self.ledger, self.quote, approval), ('quote_stale', None))
            approval['grant']['run_id'] = 8
            self.assertEqual(preflight.authorize(self.ledger, self.quote, approval), ('approval_used', None))

    def test_low_cap_expired_quote_and_consumed_cap_stop_spending(self):
        approval = self.approval()
        approval['grant']['cap_nanodollars'] = 1
        self.assertEqual(preflight.authorize(self.ledger, self.quote, approval), ('approval_below_estimate', None))
        approval = self.approval()
        with patch('quote.time.time', return_value=self.quote['expires_at']):
            self.assertEqual(preflight.authorize(self.ledger, self.quote, approval), ('quote_expired_or_invalid', None))
        preflight.authorize(self.ledger, self.quote, approval)
        with patch.dict(os.environ, REVIEW_BUDGET_GRANT='comment-42'):
            self.ledger.reserve(100000, 10000, 'review', 'spent')
        self.assertEqual(preflight.authorize(self.ledger, self.quote, approval), ('budget_exhausted', None))

    def test_same_run_resumes_cached_work_with_only_its_remaining_allowance(self):
        approval = self.approval()
        preflight.authorize(self.ledger, self.quote, approval)
        with patch.dict(os.environ, REVIEW_BUDGET_GRANT='comment-42'):
            self.ledger.reserve(100000, 10000, 'review', 'partial')
        cached = copy.deepcopy(MANIFESTS)
        for manifest in cached.values():
            manifest['chunks'][0]['cached_assessment'] = True
        remaining = create(cached, self.ledger.summary(), {'strategy': 'baseline'})
        self.assertLess(remaining['cap_nanodollars'], self.quote['cap_nanodollars'])
        self.assertEqual(preflight.authorize(self.ledger, remaining, approval), (None, 'comment-42'))

    def test_preflight_reports_budget_without_invoking_inference(self):
        self.ledger.record_external_reservation(10_000_000_000)
        output = self.directory/'outputs'
        with patch.object(preflight, 'DIRECTORY', self.directory), \
                patch.object(preflight, 'restore', return_value=self.ledger), \
                patch.object(preflight, 'build_quote', return_value=self.quote), \
                patch.dict(os.environ, GITHUB_OUTPUT=str(output)), \
                patch('bedrock_client.complete', side_effect=AssertionError('inference forbidden')):
            preflight.main()
        report = json.loads((self.directory/'report.json').read_text())
        self.assertEqual(report['blocked_reason'], 'awaiting_budget')
        self.assertEqual(report['quote'], self.quote)
        self.assertIn('spend=false', output.read_text())

    def test_missing_history_is_not_a_request_to_approve_more_spending(self):
        output = self.directory/'outputs'
        with patch.object(preflight, 'DIRECTORY', self.directory), \
                patch.dict(os.environ, GITHUB_OUTPUT=str(output)), \
                patch.object(preflight, 'build_quote') as planner:
            preflight.main()
        planner.assert_not_called()
        self.assertEqual(json.loads((self.directory/'report.json').read_text())['blocked_reason'], 'spending_unresolved')

    def test_approved_preflight_execution_and_settlement_share_the_allowance(self):
        self.exercise_execution()

    def test_budget_exit_preserves_partial_coverage_and_quotes_remaining_work(self):
        self.exercise_execution(exhausted=True)

    def exercise_execution(self, exhausted=False):
        script = Path(__file__).with_name('run_ci.py').resolve()
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(self.directory)
        directory = Path('review-result')
        directory.mkdir()
        (directory/'recoveries.json').write_text('[]')
        (directory/'reservation.json').write_text('{"maximum_usd":10}')
        (directory/'approval.json').write_text(json.dumps(self.approval()))
        for strategy, manifest in MANIFESTS.items():
            (directory/(strategy+'-manifest.json')).write_text(json.dumps({
                'manifest': manifest, 'files':1, 'total_lines':2, 'total_chunks':1,
                'total_units':1, 'excluded_assets':[], 'test_evidence': {'head':'head'}}))
        with patch.object(preflight, 'build_quote', return_value=self.quote), \
                patch.dict(os.environ, GITHUB_OUTPUT=str(self.directory/'outputs')):
            preflight.main()
        self.assertIn('spend=true', (self.directory/'outputs').read_text())
        self.assertEqual(json.loads((directory/'reservation.json').read_text())['grant']['id'], 'comment-42')

        def assessment(argv, **kwargs):
            import time
            complete([{'role':'user','content':'synthetic review'}], time.monotonic()+5)
            Path(argv[-1]).write_text(json.dumps({'commit_id':'head','complete':not exhausted,'qualified':True,
                'total_chunks':2,'completed_chunks':1 if exhausted else 2,
                'synthesis_complete':True,'review_failures':[],'overview':'word '*50,
                'disposition':'APPROVE','findings':[]}))
            if exhausted:
                (directory/'budget-blocked.json').write_text('{"reason":"budget_exhausted"}')
                if kwargs.get('check'):
                    import subprocess
                    raise subprocess.CalledProcessError(2, argv)
        envelope = {'usage':{'inputTokens':100,'outputTokens':10},'stopReason':'end_turn',
                    'output':{'message':{'content':[{'text':'{}'}]}}}
        with patch('qualify.qualify', return_value={'qualified':True,'strategy':'baseline'}), \
                patch('subprocess.run', side_effect=assessment), \
                patch.object(preflight, 'build_quote', return_value=self.quote), \
                patch('bedrock_client.invoke', return_value=envelope) as provider, \
                patch.dict(os.environ, REVIEW_BUDGET_GRANT='comment-42',GITHUB_STEP_SUMMARY=''):
            runpy.run_path(str(script), run_name='__main__')
        provider.assert_called_once()
        report = json.loads((directory/'report.json').read_text())
        self.assertEqual(report['complete'], not exhausted)
        if exhausted:
            self.assertEqual(report['blocked_reason'], 'budget_exhausted')
            self.assertEqual(report['completed_chunks'], 1)
            self.assertEqual(report['quote'], self.quote)
        else:
            self.assertNotIn('blocked_reason', report)
        self.assertEqual(report['budget']['pilot_committed_nanodollars'], 30_000_000)
        self.assertGreater(report['budget']['grants'][0]['committed_nanodollars'], 0)
        self.assertLess(report['budget']['grants'][0]['committed_nanodollars'], self.quote['cap_nanodollars'])
