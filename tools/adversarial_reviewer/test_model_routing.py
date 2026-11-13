"""Account, context and cost boundaries for model onboarding and review."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import egm
import models
from bedrock_client import BedrockError, complete
from budget import BudgetError, Ledger
from chat_transport import normalize
from model_catalog import active_role, load, selected, validate


class RoutingTests(unittest.TestCase):
    def test_workers_preserve_candidate_routing_and_diagnostics(self):
        from adversarial_review import review_diff
        from diagnostics import capture, record
        from model_catalog import ROLES, routing_override
        token = routing_override.set({'schema': 1, 'roles': {role: 'gpt_oss_20b' for role in ROLES}})
        self.addCleanup(routing_override.reset, token)
        def assess(*args, **kwargs):
            record(stage='worker', model=selected()['model_id'])
            return 'APPROVE', [], 'synthetic review complete'
        diff = ''.join(f'diff --git a/{i} b/{i}\n+{"x"*700}\n' for i in range(4))
        with capture() as events, patch('adversarial_review.CHUNK_BYTES', 1024), \
                patch('adversarial_review.run_model_reviewer', side_effect=assess):
            report = review_diff(diff, [str(i) for i in range(4)])
        self.assertTrue(report['complete'])
        self.assertEqual([event['model'] for event in events], ['openai.gpt-oss-20b-1:0']*4)

    def test_goldfish_contexts_are_independent_and_elephant_cannot_drop_findings(self):
        contexts = []
        finding = {'file': 'a.py:1', 'details': 'private sibling conclusion'}

        def assess(diff, files, docs, anchors, **kwargs):
            contexts.append((active_role.get(), diff, kwargs['supporting_context']))
            return ('REQUEST_CHANGES', [finding], 'a concrete defect exists')

        with patch('bedrock_client.complete', return_value={'summary': 'The candidate findings require changes.'}) as model:
            result = egm.review(assess, 'source diff', ['a.py'], 'invariants', [],
                                deadline=time.monotonic()+10, supporting_context='source dependency')
        self.assertEqual([c[0] for c in contexts], list(egm.FOCUS))
        self.assertTrue(all('private sibling conclusion' not in c[2] for c in contexts))
        self.assertEqual(result[:2], ('REQUEST_CHANGES', [finding]))
        model.assert_not_called()
        self.assertEqual(active_role.get(), 'elephant')

    def test_incomplete_review_or_reconciliation_never_approves(self):
        for failure in ('reviewer', 'elephant'):
            def assess(*args, **kwargs):
                if failure == 'reviewer':
                    return 'COMMENT', [], 'review incomplete: budget exhausted'
                return 'APPROVE', [], 'no concrete defect found'
            with patch('bedrock_client.complete', side_effect=BedrockError('unavailable')) as model:
                result = egm.review(assess, '', [], '', [], deadline=1, supporting_context='')
            self.assertEqual(result[0], 'COMMENT' if failure == 'reviewer' else 'APPROVE')
            if failure == 'reviewer':
                model.assert_not_called()

    def test_mixed_pricing_survives_merge_and_model_changes(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, REVIEW_BUDGET_GRANT='approved'):
            ledger = Ledger(Path(directory)/'ledger.db')
            ledger.authorize({'id': 'approved', 'cap_nanodollars': 1_000_000_000})
            cheap, expensive = copy.deepcopy(selected()), copy.deepcopy(selected())
            cheap['pricing'].update(input_nanodollars=10, output_nanodollars=20)
            expensive['pricing'].update(input_nanodollars=100, output_nanodollars=200)
            keys = [ledger.reserve(100, 100, 'review', str(i), model=m) for i, m in enumerate((cheap, expensive))]
            merged = Ledger(Path(directory)/'merged.db')
            merged.merge(ledger.path)
            for key in keys:
                merged.reconcile(key, {'usage': {'inputTokens': 10, 'outputTokens': 10}}, 1)
            self.assertAlmostEqual(merged.summary()['committed_usd'], 3300/1e9)
            with self.assertRaises(BudgetError):
                merged.reconcile(keys[0], {'usage': {'inputTokens': 10, 'outputTokens': 10}}, 1)

    def test_local_diagnostics_need_a_grant_before_calling_provider(self):
        with patch.dict(os.environ, BEDROCK_REVIEW_ENABLED='true', AWS_REGION='us-east-1',
                        GITHUB_ACTIONS='false', REVIEW_COST_POLICY='false', REVIEW_BUDGET_GRANT=''), patch('bedrock_client.invoke') as invoke:
            with self.assertRaisesRegex(BedrockError, 'approval'):
                complete([], time.monotonic()+10)
            invoke.assert_not_called()

    def test_catalog_rejects_wildcards_and_foreign_regions(self):
        catalog, routing = load()
        for resource in ('*', 'arn:aws:bedrock:eu-west-1::foundation-model/model'):
            bad = copy.deepcopy(catalog)
            next(iter(bad['models'].values()))['resources'] = [resource]
            with self.assertRaises(ValueError):
                validate(bad, routing)

    def test_chat_usage_refusal_tools_and_finish_reasons(self):
        value = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}],
                 'usage': {'prompt_tokens': 12, 'completion_tokens': 8}}
        self.assertEqual(normalize(value)['usage'], {'inputTokens': 12, 'outputTokens': 8})
        for field in ('refusal', 'tool_calls', 'function_call'):
            bad = copy.deepcopy(value)
            bad['choices'][0]['message'][field] = 'blocked'
            with self.assertRaises(BedrockError):
                normalize(bad)
        for reason, expected in [('length', 'max_tokens'), ('content_filter', 'blocked')]:
            bad = copy.deepcopy(value)
            bad['choices'][0]['finish_reason'] = reason
            self.assertEqual(normalize(bad)['stopReason'], expected)
        del value['usage']
        with self.assertRaises(BedrockError):
            normalize(value)

    def test_chat_request_uses_sigv4_and_never_tools_or_shared_history(self):
        try:
            import botocore
        except ImportError:
            self.skipTest('install reviewer requirements to exercise SigV4')
        from types import SimpleNamespace
        from chat_transport import invoke_chat
        model = {**selected(), 'adapter': 'chat_completions', 'temperature_supported': False}
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}],
                    'usage': {'prompt_tokens': 5, 'completion_tokens': 2}}
        with patch.dict(os.environ, AWS_ACCESS_KEY_ID='synthetic', AWS_SECRET_ACCESS_KEY='synthetic',
                        AWS_SESSION_TOKEN='synthetic', AWS_REGION='us-east-1', clear=True), \
                patch('botocore.httpsession.URLLib3Session.send', return_value=SimpleNamespace(
                    status_code=200, headers={}, content=json.dumps(response).encode())) as send:
            result = invoke_chat(model, [{'role': 'user', 'content': 'synthetic diff'}],
                                 {'maxTokens': 1024}, time.monotonic()+10)
        request = send.call_args.args[0]
        self.assertEqual(request.url, 'https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1/chat/completions')
        self.assertIn('AWS4-HMAC-SHA256', str(request.headers['Authorization']))
        payload = json.loads(request.body)
        self.assertEqual(set(payload), {'model', 'messages', 'stream', 'max_completion_tokens'})
        self.assertEqual(result['stopReason'], 'end_turn')


class OnboardingTests(unittest.TestCase):
    def test_missing_pr_identity_cannot_spend_unused_pilot(self):
        import preflight
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = Ledger(root/'ledger.db')
            with patch.object(preflight, 'DIRECTORY', root), patch.object(preflight, 'restore', return_value=ledger), \
                    patch.object(preflight, 'build_quote') as planner, \
                    patch.dict(os.environ, BEDROCK_REVIEW_ENABLED='true', HEAD_SHA='head', PR_NUMBER='', GITHUB_OUTPUT=str(root/'output')):
                preflight.main()
            planner.assert_not_called()
            self.assertEqual(json.loads((root/'preflight.json').read_text())['blocked_reason'], 'spending_unresolved')
            self.assertIn('spend=false', (root/'output').read_text())
            self.assertEqual(ledger.summary()['calls'], [])

    def test_candidate_quote_does_not_promote_or_leak_routing(self):
        from model_catalog import manifest, routing_override, ROLES
        original = manifest()
        token = routing_override.set({'schema': 1, 'roles': {r: 'gpt_oss_20b' for r in ROLES}})
        try:
            quote = models.qualification_quote()
        finally:
            routing_override.reset(token)
        self.assertEqual(manifest(), original)
        self.assertEqual(set(quote['routing']['models']), {'gpt_oss_20b'})
        with patch('models.check_account'), patch('models._qualify', side_effect=ValueError('failed')):
            with self.assertRaises(ValueError):
                models.qualify(quote, quote['id'], '/unused')
        self.assertEqual(manifest(), original)

    def inventory(self):
        return {'account': models.account(), 'region': 'us-east-1', 'discovered_at': int(time.time()),
                'profiles': [], 'models': [{'modelId': 'deepseek.v3.2', 'availability': {
                    'agreementAvailability': {'status': 'AVAILABLE'}, 'authorizationStatus': 'AUTHORIZED',
                    'entitlementAvailability': 'AVAILABLE', 'regionAvailability': 'AVAILABLE'}}]}

    def test_plan_is_offline_and_activation_requires_exact_unexpired_approval(self):
        with patch('models.aws') as aws:
            plan = models.plan(self.inventory(), ['deepseek_v3_2'])
            aws.assert_not_called()
            for approval in (None, 'wrong'):
                with self.assertRaises(ValueError):
                    models.activate(plan, approval)
            aws.assert_not_called()
        with patch('models.time.time', return_value=plan['expires_at']), self.assertRaises(ValueError):
            models.validate_plan(plan, plan['id'])
        changed = {**plan, 'policy': {}}
        with self.assertRaises(ValueError):
            models.validate_plan(changed, plan['id'])

    def test_activation_updates_only_owned_policy_and_never_invokes(self):
        plan = models.plan(self.inventory(), ['deepseek_v3_2'])
        with patch('models.check_account'), patch('models.aws', return_value={}) as aws:
            models.activate(plan, plan['id'])
            models.activate(plan, plan['id'])
        self.assertEqual([c.args for c in aws.call_args_list], [('iam', 'put-role-policy')]*2)
        self.assertEqual(aws.call_args.kwargs['PolicyName'], 'savior-review-models')

    def test_wrong_account_stops_discovery_before_catalog_requests(self):
        with patch.dict(os.environ, AWS_REGION='us-east-1'), patch('models.aws', return_value={'Account': 'wrong'}) as aws:
            with self.assertRaises(ValueError):
                models.discover()
        self.assertEqual(aws.call_count, 1)

    def test_discovery_records_access_denial_without_paid_probe(self):
        def aws(service, operation, **payload):
            if operation == 'list-foundation-models':
                return {'modelSummaries': [{'modelId': 'example'}]}
            if operation == 'list-inference-profiles':
                return {}
            raise ValueError('access denied')
        with patch('models.check_account'), patch('models.aws', side_effect=aws) as transport:
            result = models.discover()
        self.assertEqual(result['models'][0]['state'], 'discovered')
        self.assertFalse(any('invoke' in call.args[1] for call in transport.call_args_list))


if __name__ == '__main__':
    unittest.main()
