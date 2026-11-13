from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import live_packing as live
from model_catalog import manifest, selected


def envelope(usage=True):
    result = {'stopReason': 'end_turn', 'output': {'message': {'content': [{'text': json.dumps({
        'rationale': 'The change preserves behavior.', 'findings': [], 'abstention': ''})}]}}}
    if usage:
        result['usage'] = {'inputTokens': 100, 'outputTokens': 20}
    return result


class LivePackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'packing-quality-v1'

    def test_new_identity_and_concurrent_exact_cap(self):
        import research_ledger
        model = {'context_tokens': 1, 'pricing': {'input_nanodollars': 999999744, 'output_nanodollars': 1}}
        ledger = live.Ledger.initialize(self.root, {'routing': {'models': {'test': model}}})
        def reserve(_):
            try:
                live.Ledger(self.root).reserve(1, 256, 'review', 'test', model=model)
                return True
            except live.Stopped:
                return False
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(sum(executor.map(reserve, range(8))), 4)
        self.assertEqual(ledger.summary()['outstanding_nanodollars'], 4000000000)
        self.assertEqual(ledger.summary()['experiment'], 'packing-quality-v1')
        self.assertEqual(research_ledger.EXPERIMENT, 'review-designs-v1')
        ledger.path.unlink()
        with self.assertRaises(live.Stopped):
            live.Ledger(self.root)
        with self.assertRaises(live.Stopped):
            live.Ledger.initialize(self.root, {})

    def test_mocked_transport_settles_and_restores_scope(self):
        ledger = live.Ledger.initialize(self.root, {'routing': manifest()})
        original = live.bedrock_client.Ledger
        with patch.dict(os.environ, {'AWS_REGION': 'us-east-1', 'GITHUB_ACTIONS': 'false'}), \
                patch.object(live.bedrock_client, 'invoke', return_value=envelope()):
            with live.transport(ledger, {}, check=lambda _: None):
                answer = live.bedrock_client.complete([{'role': 'user', 'content': 'synthetic'}], float('inf'))
        self.assertEqual(answer['findings'], [])
        self.assertIs(live.bedrock_client.Ledger, original)
        self.assertEqual(ledger.summary()['outstanding_nanodollars'], 0)
        self.assertGreater(ledger.summary()['charged_nanodollars'], 0)

    def test_uncertain_usage_and_interruption_retain_reservations(self):
        ledger = live.Ledger.initialize(self.root, {'routing': manifest()})
        for response, error in ((envelope(False), live.Stopped), (KeyboardInterrupt(), KeyboardInterrupt)):
            kwargs = {'side_effect': response} if isinstance(response, BaseException) else {'return_value': response}
            with patch.dict(os.environ, {'AWS_REGION': 'us-east-1', 'GITHUB_ACTIONS': 'false'}), \
                    patch.object(live.bedrock_client, 'invoke', **kwargs):
                with live.transport(ledger, {}, check=lambda _: None):
                    with self.assertRaises(error):
                        live.bedrock_client.complete([{'role': 'user', 'content': 'synthetic'}], float('inf'))
        summary = live.Ledger(self.root).summary()
        self.assertEqual(summary['charged_nanodollars'], 0)
        self.assertEqual(len(summary['calls']), 2)
        self.assertGreater(summary['outstanding_nanodollars'], 0)

    def test_identity_and_ci_checks_precede_aws(self):
        with patch.dict(os.environ, {'AWS_PROFILE': 'wrong', 'AWS_REGION': 'us-east-1'}), patch.object(live, 'aws') as aws:
            with self.assertRaises(live.Stopped):
                live.prepare({})
            aws.assert_not_called()
        with patch.dict(os.environ, {'AWS_PROFILE': 'awill', 'AWS_REGION': 'us-east-1', 'GITHUB_ACTIONS': 'true'}):
            with self.assertRaises(live.Stopped):
                live.environment()

    def test_frozen_prompt_mismatch_blocks_execution(self):
        from qualify_packing import assess, fixtures
        case = fixtures()[0]
        row = {'case': case, 'arms': {'baseline': assess(case, 'baseline')}}
        self.assertEqual(len(live.requests(row, 'baseline')), 3)
        changed = copy.deepcopy(row)
        changed['arms']['baseline']['assessment_requests'][0]['messages'][0]['content'] += ' changed'
        with self.assertRaises(live.Stopped):
            live.requests(changed, 'baseline')


if __name__ == '__main__':
    unittest.main()
