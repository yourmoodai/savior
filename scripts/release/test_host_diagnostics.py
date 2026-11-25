import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class HostDiagnosticsTests(unittest.TestCase):
    def test_failed_process_retains_diagnostics_without_exception_text(self):
        script = Path(__file__).resolve().parents[1] / 'test-installed-mod.py'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**os.environ, 'SAVIOR_HOST_REPORT_DIR': directory, 'SAVIOR_MODEL_DIR': directory,
                   'SAVIOR_CLAUDE_BINARY': str(root / 'private-fixture'), 'SAVIOR_HOST_SCENARIO': 'success',
                   'SAVIOR_REPLACEMENT_STRATEGY': 'fixed_phrase'}
            process = subprocess.run([sys.executable, script], env=env, capture_output=True, timeout=10)
            self.assertNotEqual(process.returncode, 0)
            encoded = (root / 'fixed_phrase-success.error.jsonl').read_text()
            diagnostic = json.loads(encoded)
            self.assertEqual(diagnostic['exception'], 'FileNotFoundError')
            self.assertTrue(diagnostic['frames'])
            self.assertNotIn('private-fixture', encoded)
            self.assertNotIn(directory, encoded)
            self.assertEqual(process.stderr, b'')
