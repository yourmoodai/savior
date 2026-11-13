import concurrent.futures
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from budget import Ledger, BudgetExhausted
from bedrock_client import complete
from chat_transport import ChatError, invoke_chat, normalize
from diagnostics import capture
from model_catalog import load


class InvestigationTests(unittest.TestCase):
    def test_ceiling_survives_grants_merge_and_uncertain_concurrent_calls(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, REVIEW_BUDGET_GRANT='old'):
            ledger = Ledger(Path(directory)/'ledger.db')
            ledger.authorize({'id': 'old', 'cap_nanodollars': 10_000_000_000})
            ledger.reserve(1, 0, 'review', 'prior')
            ledger.set_ceiling(620*3)
            ledger.set_ceiling(10_000_000_000)
            ledger.authorize({'id': 'new-config', 'cap_nanodollars': 10_000_000_000})
            def reserve(i):
                try:
                    ledger.reserve(1, 0, 'qualification', str(i))
                    return True
                except BudgetExhausted:
                    return False
            with concurrent.futures.ThreadPoolExecutor() as pool:
                self.assertEqual(sum(pool.map(reserve, range(8))), 2)
            resumed = Ledger(Path(directory)/'resumed.db')
            resumed.merge(ledger.path)
            with patch.dict(os.environ, REVIEW_BUDGET_GRANT='new-config'), self.assertRaises(BudgetExhausted):
                resumed.reserve(1, 0, 'qualification', 'new-code')
            self.assertEqual(resumed.summary()['ceiling_nanodollars'], 1860)

    def test_http_failure_metadata_excludes_response_and_exception_text(self):
        try:
            import botocore
        except ImportError:
            self.skipTest('signing dependency required')
        model = load()[0]['models']['gpt_oss_20b']
        response = SimpleNamespace(status_code=429, content=b'private canary', headers={
            'x-amzn-errortype': 'ThrottlingException', 'x-amzn-requestid': '12345678-1234-1234-1234-123456789012',
            'authorization': 'private canary'})
        with patch.dict(os.environ, AWS_ACCESS_KEY_ID='synthetic', AWS_SECRET_ACCESS_KEY='synthetic', clear=True), \
                patch('botocore.httpsession.URLLib3Session.send', return_value=response), capture() as events:
            with self.assertRaises(ChatError) as error:
                invoke_chat(model, [], {'maxTokens': 1024, 'temperature': 0}, time.monotonic()+10)
        self.assertTrue(error.exception.retryable)
        self.assertEqual(events[-1]['http_status'], 429)
        self.assertNotIn('canary', json.dumps(events)+str(error.exception))

    def test_gpt_reasoning_effort_is_explicit_in_signed_request(self):
        try:
            import botocore
        except ImportError:
            self.skipTest('signing dependency required')
        model = load()[0]['models']['gpt_oss_20b']
        value = {'usage': {'prompt_tokens': 2, 'completion_tokens': 3},
                 'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]}
        response = SimpleNamespace(status_code=200, headers={}, content=json.dumps(value).encode())
        with patch.dict(os.environ, AWS_ACCESS_KEY_ID='synthetic', AWS_SECRET_ACCESS_KEY='synthetic', clear=True), \
                patch('botocore.httpsession.URLLib3Session.send', return_value=response) as send:
            invoke_chat(model, [{'role': 'system', 'content': 'synthetic instructions'},
                                {'role': 'user', 'content': 'synthetic diff'}],
                        {'maxTokens': 1024, 'temperature': 0}, time.monotonic()+10)
        self.assertEqual(json.loads(send.call_args.args[0].body)['reasoning_effort'], 'low')
        self.assertEqual([m['role'] for m in json.loads(send.call_args.args[0].body)['messages']], ['developer', 'user'])
        self.assertNotIn('response_format', json.loads(send.call_args.args[0].body))

    def test_quote_reserves_all_chat_transport_attempts(self):
        from quote import item
        from model_catalog import cost
        model = load()[0]['models']['gpt_oss_20b']
        with patch('model_catalog.selected', return_value=model):
            quoted = item('assessment', [100], 1024)
        self.assertEqual(quoted['calls'], 6)
        self.assertEqual(quoted['reserved_nanodollars'], 6*cost(model, model['context_tokens'], 1024))
        self.assertEqual(quoted['estimated_nanodollars'], cost(model, 100, 1024))

    def test_usage_survives_invalid_content(self):
        value = {'usage': {'prompt_tokens': 2, 'completion_tokens': 3},
                 'choices': [{'message': {'content': None}}]}
        with self.assertRaises(ChatError) as error:
            normalize(value)
        self.assertEqual(error.exception.category, 'invalid_content')
        self.assertEqual(error.exception.envelope['usage']['outputTokens'], 3)

    def test_automatic_cached_usage_is_bounded_without_assuming_discount(self):
        from model_catalog import cost
        model = load()[0]['models']['gpt_oss_20b']
        value = {'usage': {'prompt_tokens': 1235, 'completion_tokens': 150,
                          'prompt_tokens_details': {'cached_tokens': 16}},
                 'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]}
        envelope = normalize(value)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, REVIEW_BUDGET_GRANT='test'):
            ledger = Ledger(Path(directory)/'ledger.db')
            ledger.authorize({'id': 'test', 'cap_nanodollars': 1_000_000_000})
            key = ledger.reserve(model['context_tokens'], 1024, 'qualification', 'cached', model=model)
            ledger.reconcile(key, envelope, 1)
            call = ledger.summary()['calls'][0]
            self.assertEqual(call['actual_usd'], cost(model, 1235, 150)/1e9)
            self.assertEqual(call['metrics']['cost_basis'], 'standard_input_upper_bound')
        for cached in (-1, 1236, True, '16'):
            value['usage']['prompt_tokens_details']['cached_tokens'] = cached
            with self.assertRaises(ChatError):
                normalize(value)

    def test_transient_retry_reserves_again_and_cannot_exceed_ceiling(self):
        model = load()[0]['models']['gpt_oss_20b']
        from model_catalog import cost
        amount = cost(model, model['context_tokens'], 1024)
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory)/'ledger.db')
            ledger.authorize({'id': 'test', 'cap_nanodollars': 10_000_000_000})
            ledger.set_ceiling(amount*2)
            with patch.dict(os.environ, AWS_REGION='us-east-1', BEDROCK_REVIEW_ENABLED='true',
                            GITHUB_ACTIONS='false', REVIEW_COST_POLICY='false',
                            REVIEW_BUDGET_GRANT='test', REVIEW_BUDGET_LEDGER=str(ledger.path)), \
                    patch('bedrock_client.selected', return_value=model), patch('bedrock_client.time.sleep'), \
                    patch('chat_transport.invoke_chat', side_effect=ChatError('http_error', retryable=True)) as invoke:
                with self.assertRaisesRegex(Exception, 'cumulative'):
                    complete([], time.monotonic()+10)
            self.assertEqual(invoke.call_count, 2)
            self.assertEqual(len(ledger.summary()['calls']), 2)
