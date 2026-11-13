import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from verification import candidates, validate
from adversarial_review import _run_model_reviewer, review_messages
from bedrock_client import estimate_input_units
from review_plan import verification_bound

ANCHORS = [{'file': 'test.py', 'line': 2, 'side': 'new', 'evidence': 'return raw'}]
FINDING = {'location': 1, 'invariant': 'sanitizer failures must withhold input',
           'scenario': 'a sanitizer exception returns private raw input',
           'correction': 'propagate the error without forwarding input'}


def decision(finding=FINDING, verdict='retain'):
    return {'finding_id': candidates([finding], ANCHORS)[0]['finding_id'], 'verdict': verdict,
            'rationale': 'The supplied old and new outcomes demonstrate this decision.'}


class VerificationTests(unittest.TestCase):
    def test_saved_qualification_rewording_replays_without_rewriting(self):
        from qualify import fixture, CASES
        from evidence import locations
        saved = json.loads((Path(__file__).parent/'fixtures/verification-rewording.json').read_text())
        diff, path = fixture(saved['case_index'], CASES[saved['case_index']])
        anchors = [a for a in locations(diff).values() if a['evidence'].strip()]
        self.assertNotEqual(saved['candidates'], saved['old_response']['findings'])
        response = {'decisions': [{'finding_id': r['finding_id'], 'verdict': 'retain',
                                  'rationale': saved['old_response']['rationale']}
                                 for r in candidates(saved['candidates'], anchors)]}
        with patch('adversarial_review.complete', return_value=response):
            result = _run_model_reviewer(diff, [path], '', anchors, candidate=saved['candidates'])
        self.assertEqual(result[0], 'REQUEST_CHANGES')
        self.assertEqual(result[1][0]['details'], saved['candidates'][0]['scenario']+' Correction: '+saved['candidates'][0]['correction'])

    def test_changed_explanation_keeps_original_finding(self):
        original = copy.deepcopy(FINDING)
        response = {'decisions': [{**decision(), 'rationale': 'A different explanation of the same demonstrated failure.'}]}
        with patch('adversarial_review.complete', return_value=response):
            result = _run_model_reviewer('', ['test.py'], '', ANCHORS, candidate=[FINDING])
        self.assertEqual(result[0], 'REQUEST_CHANGES')
        self.assertEqual(result[1][0]['title'], FINDING['invariant'])
        self.assertEqual(result[1][0]['details'], FINDING['scenario']+' Correction: '+FINDING['correction'])
        self.assertEqual(FINDING, original)

    def test_decisions_cover_every_unique_candidate(self):
        second = {**FINDING, 'scenario': 'a different input violates the same source boundary'}
        self.assertNotEqual(decision()['finding_id'], decision(second)['finding_id'])
        result = validate({'decisions': [decision(second, 'abstain'), decision()]}, [FINDING, FINDING, second], ANCHORS)
        self.assertEqual(result[0], 'COMMENT')
        self.assertEqual(len(result[2]), 1)
        self.assertTrue(result[1].startswith('review incomplete:'))
        self.assertEqual(validate({'decisions': [decision(verdict='reject')]}, [FINDING], ANCHORS)[0], 'APPROVE')

    def test_invalid_decisions_fail_closed(self):
        for response in [None, {'decisions': {}}, {'decisions': []}, {'decisions': [decision(), decision()]},
                         {'decisions': [{**decision(), 'finding_id': 'unknown'}]},
                         {'decisions': [{**decision(), 'verdict': 'approve'}]},
                         {'decisions': [{**decision(), 'scenario': 'rewritten claim'}]},
                         {'decisions': [{**decision(), 'rationale': ''}]}]:
            with self.subTest(response=response), self.assertRaises(ValueError):
                validate(response, [FINDING], ANCHORS)

    def test_identity_binds_source_and_survives_batching(self):
        identifier = decision()['finding_id']
        self.assertEqual(candidates([FINDING, FINDING], ANCHORS), candidates([FINDING], ANCHORS))
        self.assertNotEqual(identifier, candidates([FINDING], [{**ANCHORS[0], 'side': 'old'}])[0]['finding_id'])
        self.assertNotEqual(identifier, candidates([FINDING], [{**ANCHORS[0], 'line': 9}])[0]['finding_id'])

    def test_bound_covers_maximum_unicode_candidate(self):
        for character in ['🧪', '\x00', '\\', '"', '\n']:
            finding = {**FINDING, **{k: character*600 for k in ('invariant', 'scenario', 'correction')}}
            base = review_messages('', ['test.py'], '', ANCHORS)
            verified = review_messages('', ['test.py'], '', ANCHORS, [finding])
            self.assertLessEqual(estimate_input_units(verified), verification_bound(base))

    def test_invalid_original_never_reaches_transport(self):
        with patch('adversarial_review.complete') as call:
            result = _run_model_reviewer('', [], '', ANCHORS, candidate=[{**FINDING, 'location': 2}])
        self.assertEqual(result[0], 'COMMENT')
        call.assert_not_called()
