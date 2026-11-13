import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from packing import anchors, candidate, compare, parse, semantic_boundaries, verify_binding
from local_model import Client, CONFIG, validate
from routers import features, mandatory, route, train
from run import dataset


def addition(source, path='sample.py'):
    return f'diff --git a/{path} b/{path}\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(source.splitlines())} @@\n'+''.join('+'+r+'\n' for r in source.splitlines())


class PackingTests(unittest.TestCase):
    def test_function_and_record_boundaries(self):
        diff = addition('def first():\n    return 1\n\n@decorator\ndef second():\n    return 2\n')
        self.assertEqual(semantic_boundaries(parse(diff)[0]), [3])
        diff = addition(json.dumps([{'x': 'a'}, {'x': 'b'}], indent=2), 'sample.json')
        self.assertEqual(len(semantic_boundaries(parse(diff)[0])), 1)

    def test_coverage_and_sizing_with_unicode_and_large_units(self):
        diff = addition(''.join(f'def f{i}():\n    return "'+('é'*1000)+'"\n' for i in range(40)))
        planned = candidate(diff)
        self.assertGreater(len(planned['chunks']), 1)
        self.assertEqual(sorted(a['id'] for a in anchors(parse(diff))),
                         sorted(a['id'] for c in planned['chunks'] for a in anchors(c['units'])))
        self.assertTrue(all(c['input_tokens'] <= 48000 for c in planned['chunks']))
        self.assertTrue(all(not c['split_context'] for c in planned['chunks']))

    def test_fallback_marks_missing_context(self):
        diff = addition(''.join('unknown '+('a'*1000)+'\n' for _ in range(60)), 'file.txt')
        self.assertTrue(all(c['split_context'] for c in candidate(diff)['chunks']))

    def test_binding_and_reservations(self):
        diff = addition('x = 1\n')
        report = compare(diff)
        verify_binding(report, diff)
        plans = report['plans']
        self.assertEqual(plans[report['selected']]['reservation_nanodollars'],
                         min(p['reservation_nanodollars'] for p in plans.values()))
        altered = copy.deepcopy(report)
        altered['selected'] = 'wrong'
        with self.assertRaises(ValueError):
            verify_binding(altered, diff)
        with self.assertRaises(ValueError):
            verify_binding(report, diff+'+y = 2\n')

    def test_indivisible_input_is_unavailable(self):
        report = compare(addition('a'*100000))
        self.assertIsNone(report['selected'])


class RoutingTests(unittest.TestCase):
    def test_holdout_and_label_isolation(self):
        rows = [r for r in dataset() if r['source'] == 'simulated_review']
        family = rows[0]['family']
        model = train([r for r in rows if r['family'] != family])
        self.assertNotIn(family, model['training_families'])
        before = features(rows[0]['evidence'])
        rows[0]['label'] = not rows[0]['label']
        rows[0]['id'] = 'invented'
        self.assertEqual(before, features(rows[0]['evidence']))
        self.assertIsNone(train(rows[:2]))
        self.assertIsNone(train([{**r, 'label': True} for r in rows]))

    def test_mandatory_precedence_and_unavailable_classifier(self):
        evidence = dataset()[0]['evidence']
        self.assertTrue(mandatory(evidence))
        prediction = route('classifier', evidence, {'weights': [-100]*10, 'threshold': 0.5})
        self.assertEqual(prediction['raw_decision'], 'retain_cheap_review')
        self.assertEqual(prediction['decision'], 'escalate')
        evidence = {**evidence, 'diff': 'x = 1', 'context': '', 'findings': [], 'incomplete': False}
        self.assertEqual(route('classifier', evidence)['decision'], 'abstain')

    def test_invalid_and_timeout(self):
        client = Client('http://127.0.0.1:1')
        responses = [{'prompt': 'x'}, {'tokens': [1]}, {'content': '{bad'}]
        with patch.object(client, 'post', side_effect=responses):
            with self.assertRaises(ValueError):
                client.decide({})
        with patch.object(client, 'post', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                client.decide({})
        self.assertTrue(client.failed)

    def test_overflow_and_truncation(self):
        for responses in ([{'prompt': 'x'}, {'tokens': [1]*8192}],
                          [{'prompt': 'x'}, {'tokens': [1]}, {'content': '{}', 'stopped_limit': True}]):
            client = Client('http://127.0.0.1:1')
            with patch.object(client, 'post', side_effect=responses):
                with self.assertRaises(ValueError):
                    client.decide({})

    def test_artifact_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'setup.json').write_text(json.dumps({'config': CONFIG}))
            (root/CONFIG['file']).write_bytes(b'wrong')
            with self.assertRaisesRegex(ValueError, 'artifact mismatch'):
                validate(root)


if __name__ == '__main__':
    unittest.main()
