"""Regression tests for quote, evidence and verification boundaries."""
import copy
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from adversarial_review import verify_candidates, _run_model_reviewer
from quote import create, execution_strategy
from result_cache import source_identity


class HarnessContracts(unittest.TestCase):
    def test_cheapest_qualified_plan_is_bound(self):
        plans = {s: {'chunks': [{'input_tokens': 100}] * n}
                 for s, n in [('baseline', 1), ('adaptive', 3)]}
        qualification = {'qualified': True, 'strategy': 'adaptive',
                         'comparison': {s: {'passed': True} for s in plans}}
        with patch.dict(os.environ, AWS_REGION='us-east-1', GITHUB_REPOSITORY='org/repo',
                        PR_NUMBER='1', BASE_SHA='base', HEAD_SHA='head'):
            quote = create(plans, {'pilot_committed_nanodollars': 0}, qualification)
        self.assertEqual(execution_strategy(quote, plans, qualification), 'baseline')
        changed = copy.deepcopy(plans)
        changed['baseline']['chunks'][0]['input_tokens'] = 101
        with self.assertRaisesRegex(ValueError, 'changed'):
            execution_strategy(quote, changed, qualification)
        with self.assertRaisesRegex(ValueError, 'changed'):
            execution_strategy(quote, plans, {**qualification, 'strategy': 'baseline'})

    def test_comments_do_not_invalidate_behavior_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'example.py'
            source.write_text('value = 1 # original comment\n')
            original = source_identity(source)
            source.write_text('value = 1 # clarified comment\n')
            self.assertEqual(source_identity(source), original)
            source.write_text('value = 2\n')
            self.assertNotEqual(source_identity(source), original)

    def test_verification_batches_preserve_findings_and_incompleteness(self):
        candidates = [{'location': 1}, {'location': 2}]
        def size(messages):
            return 49000 if '"location": 1}, {"location": 2' in messages[-1]['content'] else 47000
        retained = [{'file': 'x:1', 'details': 'verified first finding'}]
        with patch('adversarial_review.estimate_input_units', side_effect=size), \
                patch('adversarial_review._run_model_reviewer', side_effect=[
                    ('REQUEST_CHANGES', retained, 'verified'), ('COMMENT', [], 'review incomplete: missing context')]) as assess:
            result = verify_candidates('', [], '', [], candidates, deadline=time.monotonic()+5, supporting_context='')
        self.assertEqual(assess.call_count, 2)
        self.assertEqual(result[:2], ('COMMENT', retained))
        self.assertTrue(result[2].startswith('review incomplete:'))

    def test_indivisible_verification_never_calls_transport(self):
        with patch('adversarial_review.estimate_input_units', return_value=48001), \
                patch('adversarial_review.complete') as transport:
            result = verify_candidates('', [], '', [], [{'location': 1}], deadline=time.monotonic()+5, supporting_context='')
        transport.assert_not_called()
        self.assertEqual(result[0], 'COMMENT')

    def test_verification_cannot_rewrite_claim_at_same_location(self):
        finding = {'location': 1, 'invariant': 'preserve protected source',
                   'scenario': 'a concrete input violates protection', 'correction': 'reject ambiguous overlapping spans'}
        response = {'rationale': 'the evidence demonstrates the failure', 'abstention': '',
                    'findings': [{**finding, 'scenario': 'an entirely different failure scenario'}]}
        anchors = [{'file': 'x', 'line': 1, 'side': 'old', 'evidence': 'raise ValueError()'}]
        with patch('adversarial_review.complete', return_value=response):
            result = _run_model_reviewer('', ['x'], '', anchors, candidate=[finding])
        self.assertEqual(result[0], 'COMMENT')


if __name__ == '__main__':
    unittest.main()
