from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import bedrock_client
from model_catalog import selected
import research
from research_cases import cases
from research_ledger import CAP, ResearchLedger, ResearchStopped
from research_reporting import collect, deterministic, lossless, report


def response(value=None, *, usage=True, stop='end_turn'):
    result = {'stopReason': stop, 'output': {'message': {'content': [{'text': json.dumps(value or {
        'rationale': 'The change preserves the stated invariant.', 'findings': [], 'abstention': ''})}]}}}
    if usage:
        result['usage'] = {'inputTokens': 100, 'outputTokens': 20}
    return result


def finding(detail='A concrete failing synthetic input.'):
    return {'location': 2, 'invariant': 'The stated invariant must remain satisfied.',
            'scenario': detail, 'correction': 'Preserve the original guarded behavior.'}


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'experiment'

    def initialize(self, amount=1_000_000_000):
        self.model = {'context_tokens': 1, 'pricing': {'input_nanodollars': amount - 256, 'output_nanodollars': 1}}
        return ResearchLedger.initialize(self.root, {'routing': {'models': {'test': self.model}}})

    def reserve(self, ledger):
        return ledger.reserve(1, 256, 'review', 'test', model=self.model)

    def test_exact_ceiling_and_next_reservation(self):
        ledger = self.initialize(CAP)
        self.reserve(ledger)
        self.assertEqual(ledger.summary()['remaining_nanodollars'], 0)
        with self.assertRaises(ResearchStopped):
            self.reserve(ledger)

    def test_concurrent_reservations_share_ceiling(self):
        self.initialize()

        def reserve(_):
            try:
                self.reserve(ResearchLedger(self.root))
                return True
            except ResearchStopped:
                return False

        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(sum(executor.map(reserve, range(12))), 4)
        self.assertEqual(ResearchLedger(self.root).summary()['outstanding_nanodollars'], CAP)

    def test_restart_and_frozen_pricing(self):
        ledger = self.initialize()
        key = self.reserve(ledger)
        restored = ResearchLedger(self.root)
        restored.reconcile(key, {'usage': {'inputTokens': 0, 'outputTokens': 1}}, 0.1)
        self.assertEqual(restored.summary()['charged_nanodollars'], 1)
        changed = copy.deepcopy(self.model)
        changed['pricing']['input_nanodollars'] = 2
        with self.assertRaises(ResearchStopped):
            restored.reserve(1, 256, 'review', 'changed', model=changed)

    def test_missing_usage_preserves_reservation(self):
        ledger = self.initialize()
        key = self.reserve(ledger)
        with self.assertRaisesRegex(ResearchStopped, 'missing usage'):
            ledger.reconcile(key, {}, 0.1)
        self.assertEqual(ResearchLedger(self.root).summary()['outstanding_nanodollars'], 1_000_000_000)

    def test_provider_overrun_settles_and_halts(self):
        ledger = self.initialize()
        key = self.reserve(ledger)
        with self.assertRaisesRegex(ResearchStopped, 'exceeded'):
            ledger.reconcile(key, {'usage': {'inputTokens': 2, 'outputTokens': 256}}, 1)
        with self.assertRaisesRegex(ResearchStopped, 'exceeded'):
            self.reserve(ledger)

    def test_lost_or_truncated_history_is_not_recreated(self):
        ledger = self.initialize()
        self.reserve(ledger)
        content = ledger.path.read_text()
        ledger.path.write_text(content.splitlines()[0] + '\n')
        with self.assertRaises(ResearchStopped):
            ResearchLedger(self.root)
        ledger.path.unlink()
        with self.assertRaises(ResearchStopped):
            ResearchLedger(self.root)
        self.assertFalse(ledger.path.exists())
        with self.assertRaises(ResearchStopped):
            ResearchLedger.initialize(self.root, {})

    def test_journal_ahead_of_checkpoint_retains_unexposed_reservation(self):
        ledger = self.initialize()
        with patch('research_ledger.durable_json', side_effect=OSError('interrupted checkpoint')):
            with self.assertRaises(ResearchStopped):
                self.reserve(ledger)
        self.assertEqual(ResearchLedger(self.root).summary()['outstanding_nanodollars'], 1_000_000_000)

    def test_missing_anchor_and_conflicting_manifest_block(self):
        ledger = self.initialize()
        manifest = self.root / 'frozen.json'
        original = manifest.read_text()
        manifest.write_text('{}')
        with self.assertRaises(ResearchStopped):
            ledger.events()
        manifest.write_text(original)
        ledger.anchor.unlink()
        with self.assertRaises(ResearchStopped):
            ledger.events()


class RenderingTests(unittest.TestCase):
    def test_all_distinct_findings_gaps_and_abstentions_survive(self):
        first = {'file': 'x.py:2', 'details': 'first defect ' + 'x' * 1500}
        second = {'file': 'x.py:2', 'details': 'different cause <tag> [link] ```'}
        assessments = {
            'correctness': {'disposition': 'REQUEST_CHANGES', 'findings': [first, second], 'summary': 'two defects'},
            'security': {'disposition': 'COMMENT', 'findings': [first], 'summary': 'review incomplete: missing consent context'},
        }
        result = deterministic(assessments, ['correctness', 'security', 'compatibility'])
        self.assertEqual(result['findings'], [first, second])
        self.assertEqual(result['coverage']['completed_roles'], ['correctness'])
        self.assertEqual([item['role'] for item in result['unresolved']], ['security', 'compatibility'])
        self.assertIn('x' * 1500, result['body'])
        self.assertIn('missing consent context', result['body'])
        self.assertIn('assessment missing', result['body'])
        self.assertNotIn('<tag>', result['body'])
        self.assertTrue(lossless(result, assessments, ['correctness', 'security', 'compatibility']))
        result['findings'].pop()
        self.assertFalse(lossless(result, assessments, ['correctness', 'security', 'compatibility']))

    def test_abstention_is_never_clean(self):
        result = deterministic({'general': {'disposition': 'COMMENT', 'findings': [],
                                            'summary': 'review incomplete: model abstained: missing evidence'}}, ['general'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['disposition'], 'COMMENT')


class TrialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {'AWS_PROFILE': 'awill', 'AWS_REGION': 'us-east-1'}):
            cls.frozen = research.freeze()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'experiment'
        self.env = patch.dict(os.environ, {'AWS_PROFILE': 'awill', 'AWS_REGION': 'us-east-1',
                                          'GITHUB_ACTIONS': 'false'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.frozen = copy.deepcopy(self.frozen)
        self.frozen['cases'] = self.frozen['cases'][:1]
        self.ledger = ResearchLedger.initialize(self.root, self.frozen)
        self.ledger.context = {'case': self.frozen['cases'][0]['id'], 'arm': 'current'}

    def test_fixture_labels_and_equivalent_packing(self):
        fixtures = cases()
        self.assertEqual(len(fixtures), 22)
        self.assertTrue(all(c['oracle']['resolved'] for c in fixtures))
        self.assertTrue(self.frozen['packing']['synthetic']['equivalent_primary_coverage'])
        self.assertEqual(self.frozen['execution_order'][fixtures[0]['id']], ['current', 'single_role'])
        self.assertEqual(self.frozen['execution_order'][fixtures[1]['id']], ['single_role', 'current'])

    def test_interruption_retains_reservation_and_restores_transport(self):
        original = bedrock_client.Ledger
        with patch.object(bedrock_client, 'invoke', side_effect=KeyboardInterrupt):
            with research.research_transport(self.ledger, self.frozen):
                with self.assertRaises(KeyboardInterrupt):
                    bedrock_client.complete([{'role': 'user', 'content': 'synthetic'}], time.monotonic() + 10)
        self.assertIs(bedrock_client.Ledger, original)
        self.assertGreater(ResearchLedger(self.root).summary()['outstanding_nanodollars'], 0)

    def test_missing_usage_stops_without_releasing_dollars(self):
        with patch.object(bedrock_client, 'invoke', return_value=response(usage=False)):
            with research.research_transport(self.ledger, self.frozen):
                with self.assertRaisesRegex(ResearchStopped, 'missing usage'):
                    bedrock_client.complete([{'role': 'user', 'content': 'synthetic'}], time.monotonic() + 10)
        self.assertEqual(self.ledger.summary()['charged_nanodollars'], 0)
        self.assertGreater(self.ledger.summary()['outstanding_nanodollars'], 0)

    def test_truncation_retry_gets_its_own_reservation(self):
        with patch.object(bedrock_client, 'invoke', side_effect=[response(stop='max_tokens'), response()]):
            with research.research_transport(self.ledger, self.frozen):
                bedrock_client.complete([{'role': 'user', 'content': 'synthetic'}], time.monotonic() + 10)
        calls = self.ledger.summary()['calls']
        self.assertEqual([c['outputs'] for c in calls], [1024, 4096])
        self.assertTrue(all(c['charged'] is not None for c in calls))

    def test_unaffordable_call_never_reaches_transport(self):
        model = selected()
        while True:
            try:
                self.ledger.reserve(model['context_tokens'], 1024, 'review', 'uncertain', model=model)
            except ResearchStopped:
                break
        with patch.object(bedrock_client, 'invoke') as transport:
            with research.research_transport(self.ledger, self.frozen):
                with self.assertRaisesRegex(ResearchStopped, 'ceiling'):
                    bedrock_client.complete([{'role': 'user', 'content': 'synthetic'}], time.monotonic() + 10)
        self.assertFalse(transport.called)
        self.assertLessEqual(self.ledger.summary()['outstanding_nanodollars'], CAP)

    def test_matched_trial_reuses_assessments_and_resumes_without_repeating(self):
        def invoke(operation, payload, deadline):
            system = payload['system'][0]['text']
            if 'exactly one decision per finding_id' in system:
                content = payload['messages'][0]['content'][0]['text']
                records, _ = json.JSONDecoder().raw_decode(content.split('Candidate findings to verify: ', 1)[1])
                return response({'decisions': [{'finding_id': r['finding_id'], 'verdict': 'retain',
                    'rationale': 'The original synthetic consent defect is supported.'} for r in records]})
            if 'Reconcile independent code reviews' in system:
                return response({'summary': 'The verified findings identify a synthetic consent defect.'})
            if 'Summarize the combined behavior changes' in system:
                return response({'overview': ' '.join(['synthetic'] * 55)})
            return response({'rationale': 'This change loses the current consent check.', 'findings': [finding()], 'abstention': ''})

        with patch.object(bedrock_client, 'invoke', side_effect=invoke) as transport, patch('models.aws', return_value={
                'Account': self.frozen['account'], 'Arn': 'synthetic'}):
            first = research.run(self.ledger, self.frozen)
            self.assertEqual(transport.call_count, 10)
            second = research.run(self.ledger, self.frozen)
            self.assertEqual(transport.call_count, 10)
        self.assertEqual(first['matched_cases'], second['matched_cases'])
        self.assertEqual(len(first['matched_cases']), 1)
        self.assertEqual(first['reporting_losses'], [])
        self.assertEqual(first['metrics']['three_role']['incremental_calls'], 0)
        self.assertEqual(first['budget']['outstanding_nanodollars'], 0)
        self.assertEqual(first['production_qualification'], 'not run; no cache publication')

    def test_partial_current_preserves_verified_findings_and_missing_roles(self):
        case_id = self.frozen['cases'][0]['id']
        self.ledger.append({'kind': 'step_started', 'case': case_id, 'arm': 'current'})
        self.ledger.append({'kind': 'role_result', 'case': case_id, 'arm': 'current', 'role': 'correctness',
                            'result': {'disposition': 'REQUEST_CHANGES', 'findings': [finding()], 'summary': 'verified defect'}})
        rows = collect(self.frozen, self.ledger.events())
        candidate = rows[0]['arms']['three_role']
        self.assertEqual(candidate['findings'], [finding()])
        self.assertFalse(candidate['complete'])
        self.assertEqual(len(candidate['unresolved']), 2)
        self.assertEqual(report(self.frozen, self.ledger)['matched_cases'], [])

    def test_configuration_and_identity_mismatch_prevent_inference(self):
        with patch.object(bedrock_client, 'invoke') as transport, patch('models.aws', return_value={'Account': '000000000000'}):
            result = research.run(self.ledger, self.frozen)
        self.assertFalse(transport.called)
        self.assertIn('trust boundary', result['status'])
        with patch('research.configuration', return_value={}):
            with self.assertRaisesRegex(ResearchStopped, 'configuration changed'):
                research.verify_frozen(self.frozen)

    def test_ci_rejects_research_before_account_or_inference(self):
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}), patch('models.aws') as account, \
                patch.object(bedrock_client, 'invoke') as transport:
            result = research.run(self.ledger, self.frozen)
        self.assertEqual(result['status'], 'research exemption cannot run in production ci')
        account.assert_not_called()
        transport.assert_not_called()
        self.assertEqual(self.ledger.summary()['calls'], [])

    def test_prose_failure_is_separate_from_candidate_coverage(self):
        def invoke(operation, payload, deadline):
            if 'Reconcile independent code reviews' in payload['system'][0]['text']:
                return response({'summary': 'All assessments finished without findings.'})
            if 'Summarize the combined behavior changes' in payload['system'][0]['text']:
                envelope = response({'overview': 'too short'})
                content = envelope['output']['message']['content'][0]
                content['text'] = '```json\n' + content['text'] + '\n```'
                return envelope
            return response()

        with patch.object(bedrock_client, 'invoke', side_effect=invoke), patch('models.aws', return_value={
                'Account': self.frozen['account'], 'Arn': 'synthetic'}):
            result = research.run(self.ledger, self.frozen)
        arms = result['cases'][0]['arms']
        self.assertFalse(arms['current']['complete'])
        self.assertEqual(arms['current']['production_disposition'], 'REQUEST_CHANGES')
        self.assertTrue(arms['three_role']['complete'])
        self.assertEqual(result['metrics']['current']['invalid_responses'], 2)

    def test_interrupted_single_role_renders_its_gap(self):
        self.ledger.append({'kind': 'step_started', 'case': self.frozen['cases'][0]['id'], 'arm': 'single_role'})
        result = report(self.frozen, self.ledger)
        single = result['cases'][0]['arms']['single_role']
        self.assertTrue(single['lossless'])
        self.assertIn('execution interrupted', single['body'])
        self.assertFalse(single['complete'])


if __name__ == '__main__':
    unittest.main()
