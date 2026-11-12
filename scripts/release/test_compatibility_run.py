import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('compatibility_run', Path(__file__).with_name('compatibility-run.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class CompatibilityRunTests(unittest.TestCase):
    def test_probe_profile_excludes_real_credentials_and_settings(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'private', 'GH_TOKEN': 'private'}):
            home = Path(directory)
            env = runner.isolated_environment(home)
            self.assertEqual(env['HOME'], directory)
            self.assertEqual(env['CLAUDE_CONFIG_DIR'], str(home / '.claude'))
            self.assertEqual(env['ANTHROPIC_API_KEY'], 'synthetic-local-key')
            self.assertEqual(env['ANTHROPIC_BASE_URL'], 'http://127.0.0.1:1')
            self.assertNotIn('GH_TOKEN', env)
            self.assertNotIn('private', env.values())

    def test_missing_candidate_retains_a_failed_assessment(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            release = {'version': '2.1.322', 'artifacts': {'darwin-arm64': {'integrity': 'synthetic'}}}
            runner.assess(release, 'darwin-arm64', 'inventory', output,
                          {'adapters': []}, None, None, {'harness': 'identity'})
            row = json.loads(next(output.rglob('assessment.json')).read_text())
            self.assertEqual(row['status'], 'failed')
            self.assertEqual(row['reason'], 'candidate_unavailable')
            self.assertEqual(row['cases'], [])

    def test_reuse_requires_unchanged_candidate_artifact_adapter_and_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = root / 'previous'
            previous.mkdir()
            release = {'version': '2.1.322', 'artifacts': {'darwin-arm64': {'integrity': 'locked'}}}
            catalog = {'adapters': [{'id': 'adapter', 'client_versions': ['2.1.322']}]}
            row = {'version': '2.1.322', 'target': 'darwin-arm64', 'candidate_sha256': 'candidate',
                   'qualification_inputs': {'harness': 'identity'}, 'artifact': {'integrity': 'locked'},
                   'adapter_id': 'adapter', 'status': 'passed', 'recorded_at': 'original-date'}
            file = previous / 'assessment.json'
            file.write_text(json.dumps(row))
            for field, value in [('candidate_sha256', 'changed'), ('qualification_inputs', {}),
                                 ('artifact', {}), ('adapter_id', 'other'), ('status', 'failed')]:
                changed = dict(row)
                changed[field] = value
                file.write_text(json.dumps(changed))
                self.assertFalse(runner.reuse(previous, release, 'darwin-arm64', catalog, 'candidate',
                                             {'harness': 'identity'}, 'inventory', root / 'output'))
            file.write_text(json.dumps(row))
            self.assertTrue(runner.reuse(previous, release, 'darwin-arm64', catalog, 'candidate',
                                        {'harness': 'identity'}, 'inventory', root / 'output'))
            reused = json.loads(next((root / 'output').rglob('assessment.json')).read_text())
            self.assertEqual(reused['recorded_at'], 'original-date')
            self.assertEqual(reused['inventory_sha256'], 'inventory')
            self.assertEqual(reused['reused_from_sha256'], runner.digest(file))


if __name__ == '__main__':
    unittest.main()
