"""Model-specific framing must not weaken evidence or spending checks."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from adversarial_review import _run_model_reviewer
from bedrock_client import BedrockError, complete
from budget import Ledger
from chat_transport import normalize
from evidence import validate as validate_evidence
from model_catalog import cost, load, manifest, routing_override, ROLES, validate

FIXTURE = json.loads((Path(__file__).parent/'fixtures/gpt_oss_reasoning_response.json').read_text())
FINAL = {'rationale': 'The fixture preserves the original output and introduces no observable failure.',
         'findings': [], 'abstention': ''}


class ResponseFormatTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.ledger = Ledger(Path(directory.name)/'ledger.db')
        self.ledger.authorize({'id': 'test', 'cap_nanodollars': 1_000_000_000})
        env = patch.dict(os.environ, BEDROCK_REVIEW_ENABLED='true', AWS_REGION='us-east-1',
                         REVIEW_BUDGET_GRANT='test', REVIEW_BUDGET_LEDGER=str(self.ledger.path),
                         REVIEW_CACHE_DISABLED='true')
        env.start()
        self.addCleanup(env.stop)
        self.model = load()[0]['models']['gpt_oss_20b']
        self.model['json_attempts'] = 1
        selected = patch('bedrock_client.selected', return_value=self.model)
        selected.start()
        self.addCleanup(selected.stop)

    def response(self, text=None, stop='stop'):
        value = copy.deepcopy(FIXTURE)
        value['choices'][0]['finish_reason'] = stop
        if text is not None:
            value['choices'][0]['message']['content'] = text
        return value

    def complete(self, value):
        with patch('chat_transport.invoke_chat', side_effect=lambda *args: normalize(value)) as provider:
            result = complete([], time.monotonic()+10)
        self.assertEqual(provider.call_count, 1)
        return result

    def test_observed_shape_passes_without_changing_usage(self):
        self.assertEqual(self.complete(FIXTURE), FINAL)
        self.assertEqual(validate_evidence(FINAL, [])[0], 'APPROVE')
        call, = self.ledger.summary()['calls']
        self.assertEqual(call['metrics']['usage'], {'inputTokens': 1220, 'outputTokens': 904})
        self.assertEqual(call['actual_usd'], cost(self.model, 1220, 904)/1e9)

    def test_invalid_json_regenerates_once_without_reusing_invalid_output(self):
        self.model['json_attempts'] = 2
        invalid = normalize(self.response('{malformed synthetic canary'))
        valid = normalize(self.response(json.dumps(FINAL)))
        with patch('chat_transport.invoke_chat', side_effect=[invalid, valid]) as provider:
            self.assertEqual(complete([{'role': 'user', 'content': 'original synthetic evidence'}], time.monotonic()+10), FINAL)
        retry_messages = provider.call_args.args[1]
        self.assertEqual(retry_messages[0]['content'], 'original synthetic evidence')
        self.assertNotIn('canary', json.dumps(retry_messages))
        self.assertEqual(len(self.ledger.summary()['calls']), 2)
        with patch('chat_transport.invoke_chat', return_value=invalid) as provider:
            with self.assertRaisesRegex(BedrockError, 'invalid JSON'):
                complete([], time.monotonic()+10)
        self.assertEqual(provider.call_count, 2)
        self.assertTrue(all(call['actual_usd'] is not None for call in self.ledger.summary()['calls']))

    def test_plain_and_fenced_answers_remain_supported(self):
        for text in (json.dumps(FINAL), '```json\n'+json.dumps(FINAL)+'\n```'):
            for prefix in ('', '<reasoning>synthetic analysis</reasoning>'):
                with self.subTest(text=text, prefix=prefix):
                    self.assertEqual(self.complete(self.response(' \n'+prefix+text+'\n ')), FINAL)

    def test_json_in_reasoning_cannot_replace_the_final_answer(self):
        answer = {**FINAL, 'abstention': 'missing evidence'}
        result = self.complete(self.response('<reasoning>'+json.dumps(FINAL)+'</reasoning>'+json.dumps(answer)))
        self.assertEqual(result, answer)
        with self.assertRaises(ValueError):
            validate_evidence(result, [])

    def test_malformed_or_ambiguous_answers_fail_after_usage_is_settled(self):
        valid = json.dumps(FINAL)
        malformed = [
            '<reasoning>'+valid,
            '<reasoning>analysis</reasoning>',
            '<reasoning><reasoning>nested</reasoning></reasoning>'+valid,
            '<reasoning>first</reasoning><reasoning>second</reasoning>'+valid,
            '<reasoning>analysis</reasoning></reasoning>'+valid,
            'preface <reasoning>analysis</reasoning>'+valid,
            '<reasoning>analysis</reasoning>preface '+valid,
            '<reasoning>analysis</reasoning>'+valid+' trailing prose',
            '<reasoning>analysis</reasoning>'+valid+valid,
            '<reasoning>analysis</reasoning>[]',
            '<reasoning>analysis</reasoning>{',
        ]
        for text in malformed:
            with self.subTest(text=text), self.assertRaisesRegex(BedrockError, 'invalid JSON'):
                self.complete(self.response(text))
        calls = self.ledger.summary()['calls']
        self.assertEqual(len(calls), len(malformed))
        self.assertTrue(all(c['actual_usd'] == cost(self.model, 1220, 904)/1e9 for c in calls))

    def test_missing_opt_in_keeps_strict_parsing(self):
        self.model = {k: v for k, v in self.model.items() if k != 'response_format'}
        with patch('bedrock_client.selected', return_value=self.model):
            with self.assertRaises(BedrockError):
                self.complete(FIXTURE)
            self.assertEqual(self.complete(self.response(json.dumps(FINAL))), FINAL)

    def test_refusal_and_truncation_cannot_be_hidden_by_a_valid_final_answer(self):
        for stop in ('content_filter', 'tool_calls', 'length'):
            with self.subTest(stop=stop), patch('chat_transport.invoke_chat',
                                               side_effect=lambda *args: normalize(self.response(stop=stop))) as provider:
                with self.assertRaises(BedrockError):
                    complete([], time.monotonic()+10)
                self.assertEqual(provider.call_count, 2 if stop == 'length' else 1)
        refusal = self.response()
        refusal['choices'][0]['message']['refusal'] = 'refused'
        with self.assertRaises(BedrockError):
            self.complete(refusal)

    def test_extracted_json_still_requires_valid_review_evidence(self):
        for text in ('<reasoning>no closing tag', '<reasoning>analysis</reasoning>{"findings":[]}'):
            with patch('chat_transport.invoke_chat', return_value=normalize(self.response(text))):
                result = _run_model_reviewer('synthetic diff', [], '', [], candidate=[])
            self.assertEqual(result[0], 'COMMENT')
            self.assertTrue(result[2].startswith('review incomplete:'))

    def test_catalog_rejects_unknown_formats_and_only_gpt_oss_opts_in(self):
        catalog, routing = load()
        enabled = [name for name, model in catalog['models'].items() if model.get('response_format', 'json') != 'json']
        self.assertEqual(enabled, ['gpt_oss_20b'])
        for value in ('unknown', None, [], True):
            bad = copy.deepcopy(catalog)
            bad['models']['gpt_oss_20b']['response_format'] = value
            with self.assertRaises(ValueError):
                validate(bad, routing)
        bad = copy.deepcopy(catalog)
        bad['models']['deepseek_v3_2']['response_format'] = 'reasoning_prefix_json'
        with self.assertRaises(ValueError):
            validate(bad, routing)

    def test_format_changes_quote_binding_and_qualification_cache_identity(self):
        from models import qualification_quote
        from qualify import cached_qualification
        catalog, routing = load()
        token = routing_override.set({'schema': 1, 'roles': {r: 'gpt_oss_20b' for r in ROLES}})
        self.addCleanup(routing_override.reset, token)
        selected_routing = manifest()['routing']
        with patch('model_catalog.load', return_value=(catalog, selected_routing)):
            first_quote, first_cache = qualification_quote(), cached_qualification()[0]
        changed = copy.deepcopy(catalog)
        changed['models']['gpt_oss_20b']['response_format'] = 'json'
        with patch('model_catalog.load', return_value=(changed, selected_routing)):
            second_quote, second_cache = qualification_quote(), cached_qualification()[0]
        self.assertNotEqual(first_quote['routing'], second_quote['routing'])
        self.assertNotEqual(first_quote['id'], second_quote['id'])
        self.assertNotEqual(first_cache, second_cache)


if __name__ == '__main__':
    unittest.main()
