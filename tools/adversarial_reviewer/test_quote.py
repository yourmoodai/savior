import copy
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from budget import Ledger
from bedrock_client import complete
import preflight
from quote import create, estimate, inspect_archive, validate, apply_policy

ENV = {'GITHUB_REPOSITORY':'org/repo', 'PR_NUMBER':'5', 'BASE_SHA':'base', 'HEAD_SHA':'head',
       'AWS_REGION':'us-east-1', 'GITHUB_RUN_ID':'7', 'GITHUB_ACTIONS':'true', 'BEDROCK_REVIEW_ENABLED':'true', 'REVIEW_COST_POLICY':'true'}
MANIFESTS = {s: {'chunks':[{'input_tokens':1000, 'cached_assessment':False}]} for s in ('baseline','adaptive')}
QUALIFIED = {'qualified': True, 'strategy': 'baseline', 'comparison': {'baseline': {'passed': True}}}


class QuoteTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, ENV)
        env.start()
        self.addCleanup(env.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.ledger = Ledger(self.directory/'ledger.db')
        self.quote = apply_policy(create(MANIFESTS, self.ledger.summary(), QUALIFIED), self.ledger.policy_budget())

    def test_offline_projection_covers_retries_verification_and_summaries(self):
        with patch('bedrock_client.complete', side_effect=AssertionError('inference forbidden')):
            quote = create(MANIFESTS, self.ledger.summary(), None)
        self.assertGreater(quote['items'][0]['calls'], 0)
        self.assertEqual(quote['phase'], 'qualification_required')
        self.assertIsNone(quote['strategy'])
        self.assertEqual(len(quote['items']), 1)
        self.assertEqual(quote['projected_nanodollars'], sum(i['reserved_nanodollars'] for i in quote['items']))
        self.assertGreater(quote['projected_nanodollars'], quote['estimated_nanodollars'])
        cached = estimate({'chunks':[{'input_tokens':1000,'cached_assessment':True}]})
        self.assertEqual([i['calls'] for i in cached], [0]*12)

    def test_digest_expiration_archive_and_pricing(self):
        validate(self.quote)
        for changed in ({'projected_nanodollars':1}, {'binding':{}}):
            with self.assertRaises(ValueError):
                validate({**self.quote, **changed})
        with self.assertRaises(ValueError):
            validate(self.quote, now=self.quote['expires_at'])
        for filename in ('quote.json','../quote.json'):
            archive = io.BytesIO()
            with zipfile.ZipFile(archive,'w') as zipped:
                zipped.writestr(filename, json.dumps(self.quote))
            if filename == 'quote.json':
                self.assertEqual(inspect_archive(archive.getvalue()), self.quote)
            else:
                with self.assertRaises(ValueError):
                    inspect_archive(archive.getvalue())
        from model_catalog import current_pricing, selected
        for changed in ({'checked':'2000-01-01'}, {'checked':'2999-01-01'}, {'source':''}, {'input_nanodollars':0}):
            model = copy.deepcopy(selected())
            model['pricing'].update(changed)
            with self.assertRaisesRegex(ValueError,'unsupported_pricing'):
                current_pricing(model)

    def test_projected_cost_equality_is_allowed_but_one_nanodollar_over_blocks(self):
        for amount, reason in [(2_000_000_000,None),(2_000_000_001,'cost_limit')]:
            quote = apply_policy({**self.quote,'projected_nanodollars':amount},self.ledger.policy_budget())
            self.assertEqual(quote['blocked_reason'],reason)
            validate(quote)

    def test_preflight_never_calls_provider_and_ignores_old_grant_files(self):
        (self.directory/'approval.json').write_text('{"grant":{"cap_nanodollars":100000000000}}')
        quote = apply_policy({**self.quote,'projected_nanodollars':2_000_000_001},self.ledger.policy_budget())
        with patch.object(preflight,'DIRECTORY',self.directory), patch.object(preflight,'restore',return_value=self.ledger), \
                patch.object(preflight,'build_quote',return_value=quote), patch.dict(os.environ,GITHUB_OUTPUT=str(self.directory/'outputs')), \
                patch('bedrock_client.complete',side_effect=AssertionError('inference forbidden')):
            preflight.main()
        self.assertEqual(json.loads((self.directory/'report.json').read_text())['blocked_reason'],'cost_limit')
        self.assertIn('spend=false',(self.directory/'outputs').read_text())
        self.assertEqual(self.ledger.summary()['grants'],[])

    def test_missing_or_unattributed_history_blocks_before_planning(self):
        with patch.object(preflight,'DIRECTORY',self.directory), patch.dict(os.environ,GITHUB_OUTPUT=str(self.directory/'outputs')), \
                patch.object(preflight,'build_quote') as planner:
            preflight.main()
        planner.assert_not_called()
        self.assertEqual(json.loads((self.directory/'report.json').read_text())['blocked_reason'],'spending_unresolved')

    def test_qualified_execution_uses_policy_reservations_and_settles(self):
        manifests = copy.deepcopy(MANIFESTS)
        for manifest in manifests.values():
            manifest['chunks'][0]['cached_assessment'] = True
        self.quote = apply_policy(create(manifests, self.ledger.summary(), QUALIFIED), self.ledger.policy_budget())
        script = Path(__file__).with_name('run_ci.py').resolve()
        previous = Path.cwd()
        self.addCleanup(os.chdir,previous)
        os.chdir(self.directory)
        directory = Path('review-result')
        directory.mkdir()
        (directory/'reservation.json').write_text('{}')
        for strategy, manifest in manifests.items():
            (directory/(strategy+'-manifest.json')).write_text(json.dumps({'manifest':manifest,'files':1,
                'total_lines':2,'total_chunks':1,'total_units':1,'excluded_assets':[],'test_evidence':{'head':'head'}}))
        # One chunk with cached qualification fits the fixed policy.
        self.assertIsNone(self.quote['blocked_reason'])
        with patch.object(preflight,'restore',return_value=self.ledger), patch.object(preflight,'build_quote',return_value=self.quote), \
                patch.dict(os.environ,GITHUB_OUTPUT=str(self.directory/'outputs')):
            preflight.main()
        self.assertIn('spend=true',(self.directory/'outputs').read_text())
        def assessment(argv, **kwargs):
            import time
            complete([{'role':'user','content':'synthetic review'}],time.monotonic()+5)
            Path(argv[-1]).write_text(json.dumps({'commit_id':'head','complete':True,'qualified':True,
                'synthesis_complete':True,'review_failures':[],'overview':'word '*50,'disposition':'APPROVE','findings':[]}))
        envelope = {'usage':{'inputTokens':100,'outputTokens':10},'stopReason':'end_turn',
                    'output':{'message':{'content':[{'text':'{}'}]}}}
        with patch.object(preflight,'restore',return_value=self.ledger), patch('qualify.qualify',return_value=QUALIFIED), \
                patch('subprocess.run',side_effect=assessment), patch('bedrock_client.invoke',return_value=envelope) as provider, \
                patch.dict(os.environ,REVIEW_BUDGET_LEDGER=str(self.ledger.path),GITHUB_STEP_SUMMARY=''):
            runpy.run_path(str(script),run_name='__main__')
        provider.assert_called_once()
        self.assertTrue(json.loads((directory/'report.json').read_text())['complete'])
        self.assertLess(self.ledger.policy_budget()['pr_remaining_nanodollars'],2_000_000_000)
