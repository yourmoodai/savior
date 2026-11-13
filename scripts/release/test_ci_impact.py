import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ci_impact


class ImpactTests(unittest.TestCase):
    def test_website_and_shared_inputs(self):
        self.assertFalse(ci_impact.native_required([
            'site/templates/base.html', 'scripts/site.py', 'scripts/test-site.mjs',
            'docs/assets/social-preview.png', '.github/workflows/site.yml']))
        for path in ['crates/core/src/lib.rs', 'Cargo.lock', 'package-lock.json',
                     'scripts/release/package.py', '.github/workflows/verify.yml',
                     'scripts/release/ci_impact.py', 'unknown-input']:
            with self.subTest(path=path):
                self.assertTrue(ci_impact.native_required(['site/zola.toml', path]))

    def test_missing_history_and_manual_dispatch_require_builds(self):
        self.assertIsNone(ci_impact.changed_paths('', 'a' * 40, 'push'))
        self.assertIsNone(ci_impact.changed_paths('0' * 40, 'a' * 40, 'push'))
        self.assertIsNone(ci_impact.changed_paths('a' * 40, 'b' * 40, 'workflow_dispatch'))
        with patch.object(subprocess, 'check_output', side_effect=subprocess.CalledProcessError(1, 'git')):
            self.assertIsNone(ci_impact.changed_paths('a' * 40, 'b' * 40, 'push'))

    def test_rename_retains_removed_source_path(self):
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.check_output(['git', '-C', directory, *args]).decode().strip()
            git('init', '-q')
            source = Path(directory) / 'runtime.rs'
            source.write_text('runtime input')
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'initial')
            base = git('rev-parse', 'HEAD')
            (Path(directory) / 'site').mkdir()
            git('mv', 'runtime.rs', 'site/runtime.rs')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'move')
            head = git('rev-parse', 'HEAD')
            original = subprocess.check_output
            with patch.object(subprocess, 'check_output', side_effect=lambda cmd: original(cmd, cwd=directory)):
                for event in ['push', 'pull_request']:
                    paths = ci_impact.changed_paths(base, head, event)
                    self.assertIn('runtime.rs', paths)
                    self.assertTrue(ci_impact.native_required(paths))

    def test_gates_accept_only_intentional_skips(self):
        for required in ['true', 'false']:
            expected = 'success' if required == 'true' else 'skipped'
            needs = {'context': {'result': 'success', 'outputs': {'native_required': required}},
                     'tooling': {'result': 'success'}, 'coreml': {'result': expected}}
            ci_impact.check_gate(needs, 'context', ['tooling'], ['coreml'])
            for bad in ['failure', 'cancelled', 'skipped' if required == 'true' else 'success']:
                needs['coreml']['result'] = bad
                with self.assertRaises(ValueError):
                    ci_impact.check_gate(needs, 'context', ['tooling'], ['coreml'])
        for context in [{'result': 'failure'}, {'result': 'success', 'outputs': {}}]:
            with self.assertRaises(ValueError):
                ci_impact.check_gate({'context': context}, 'context', [], [])


if __name__ == '__main__':
    unittest.main()
