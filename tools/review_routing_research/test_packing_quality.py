import unittest

from packing import anchors, candidate, count_tokens, messages
from qualify_packing import assess, context_available, fixtures


class PackingQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = {c['id']: c for c in fixtures()}

    def test_helper_context_survives_both_source_orders(self):
        for name in ('helper_first', 'helper_last'):
            for outcome in ('benign', 'defective'):
                case = self.fixtures[name+'_'+outcome]
                packed = candidate(case['diff'])
                self.assertTrue(case['oracle']['resolved'])
                self.assertEqual(context_available(packed, case['required_context']), [True])
                for chunk in packed['chunks']:
                    self.assertFalse({a['id'] for a in anchors(chunk['units'])} & set(chunk['support_evidence_ids']))
                    self.assertLessEqual(count_tokens(messages(chunk['units'], chunk['support'])), 48000)

    def test_semantic_sibling_omissions_are_marked(self):
        packed = candidate(self.fixtures['helper_first_benign']['diff'])
        self.assertTrue(any(c['omitted_support'] for c in packed['chunks']))
        for chunk in packed['chunks']:
            if chunk['omitted_support']:
                self.assertIn('Context gaps:', chunk['support'])

    def test_json_record_context_and_labels(self):
        for outcome in ('benign', 'defective'):
            case = self.fixtures['json_records_'+outcome]
            self.assertTrue(case['oracle']['resolved'])
            self.assertEqual(context_available(candidate(case['diff']), case['required_context']), [True])

    def test_capacity_control_is_not_claimed_as_complete_context(self):
        case = self.fixtures['oversized_context_control']
        for strategy in ('baseline', 'semantic_bytes'):
            result = assess(case, strategy)
            self.assertTrue(result['coverage_complete'])
            self.assertEqual(result['required_context_available'], [False])
        self.assertFalse(case['oracle']['resolved'])

    def test_role_prompts_are_frozen_with_fixture_contract(self):
        case = self.fixtures['helper_last_defective']
        result = assess(case, 'semantic_bytes')
        self.assertEqual({r['role'] for r in result['assessment_requests']},
                         {'correctness', 'security', 'compatibility'})
        self.assertTrue(result['requests_fit'])
        self.assertTrue(all(case['context'] in r['messages'][1]['content'] for r in result['assessment_requests']))


if __name__ == '__main__':
    unittest.main()
