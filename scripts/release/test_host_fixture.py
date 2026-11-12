import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from host_fixture import CASES, prepare, arm, recover, worker_alive


class HostFixtureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'candidate'
        self.home = Path(self.directory.name) / 'home'
        self.root.mkdir()
        self.home.mkdir()
        (self.root / 'bridge').mkdir()
        (self.root / 'bridge/cli.mjs').write_text(
            'console.log(JSON.stringify({command:process.argv[2],model:process.env.SAVIOR_MODEL_DIR}));')
        for name in ('runtime-manifest.json', 'release.json', 'runtime-manifest.sig', 'release-public.pem'):
            (self.root / name).write_text('synthetic inventory')
        (self.root / 'cache').mkdir()
        (self.root / 'cache/runtime').write_text('synthetic runtime')

    def run_cli(self, plugin, command):
        return subprocess.run(['node', str(plugin / 'bridge/cli.mjs'), command],
                              input='{}', text=True, capture_output=True, timeout=5,
                              env={**os.environ, 'SAVIOR_MODEL_DIR': 'verified-model'})

    def test_unmodified_cases_use_the_exact_candidate(self):
        for scenario in ('success', 'unchanged', 'protected'):
            self.assertEqual(prepare(self.root, self.home, scenario), self.root)
        self.assertFalse((self.root / 'bridge/cli.real.mjs').exists())

    def test_full_copy_delegates_startup_and_arms_only_rewrite(self):
        original = {str(file.relative_to(self.root)): file.read_bytes()
                    for file in self.root.rglob('*') if file.is_file()}
        plugin = prepare(self.root, self.home, 'missing_model')
        for name, data in original.items():
            copied = 'bridge/cli.real.mjs' if name == 'bridge/cli.mjs' else name
            self.assertEqual((plugin / copied).read_bytes(), data)
        self.assertFalse((plugin / 'bridge/host-fault.json').exists())
        arm(plugin, 'missing_model')
        for command in ('doctor', 'trace', 'bootstrap'):
            self.assertEqual(json.loads(self.run_cli(plugin, command).stdout),
                             {'command': command, 'model': 'verified-model'})
        self.assertTrue(json.loads(self.run_cli(plugin, 'rewrite').stdout)['model'].endswith('/absent-model'))
        recover(plugin)
        recover(plugin)
        self.assertEqual(json.loads(self.run_cli(plugin, 'rewrite').stdout)['model'], 'verified-model')
        self.assertEqual({str(file.relative_to(self.root)): file.read_bytes()
                          for file in self.root.rglob('*') if file.is_file()}, original)

    def test_startup_failure_is_separate_from_runtime_failure(self):
        plugin = prepare(self.root, self.home, 'startup_failure')
        self.assertEqual(self.run_cli(plugin, 'doctor').returncode, 73)
        self.assertEqual(self.run_cli(plugin, 'rewrite').returncode, 0)
        recover(plugin)
        self.assertEqual(self.run_cli(plugin, 'doctor').returncode, 0)

    def test_injections_have_distinct_failure_modes(self):
        plugin = prepare(self.root, self.home, 'worker_exit')
        for scenario in ('worker_exit', 'malformed_output', 'processing_timeout'):
            arm(plugin, scenario)
            result = self.run_cli(plugin, 'rewrite')
            if scenario == 'worker_exit':
                self.assertEqual(result.returncode, 73)
                self.assertEqual(result.stdout, '')
            elif scenario == 'malformed_output':
                self.assertEqual(result.stdout, '{')
            else:
                self.assertEqual(json.loads(result.stdout)['code'], 'processing_timeout')
                self.assertNotEqual(result.returncode, 0)
        arm(plugin, 'worker_deadline')
        process = subprocess.Popen(['node', str(plugin / 'bridge/cli.mjs'), 'rewrite'], stdin=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while not (plugin / 'bridge/host-worker.pid').exists():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
            self.assertTrue(worker_alive(plugin))
        finally:
            process.kill()
            process.wait(timeout=5)
        self.assertFalse(worker_alive(plugin))

    def test_source_cannot_substitute_for_packaged_startup(self):
        (self.root / 'runtime-manifest.json').unlink()
        with self.assertRaisesRegex(ValueError, 'startup_requires_packaged_candidate'):
            prepare(self.root, self.home, 'startup_failure')
