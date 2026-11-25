import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from native_target import native_target
from runtime_cache import seed


class NativeRuntimeTests(unittest.TestCase):
    def test_unsupported_linux_architecture_reports_its_identity(self):
        with patch('platform.system', return_value='Linux'), patch('platform.machine', return_value='ppc64le'):
            with self.assertRaisesRegex(ValueError, 'unsupported native target: Linux ppc64le'):
                native_target()

    @unittest.skipIf(os.name == 'nt', 'requires POSIX file modes')
    def test_seed_grants_owner_execute_without_removing_existing_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'node-asset'
            source.write_bytes(b'synthetic node')
            entry = {'asset':source.name, 'name':'node', 'bytes':source.stat().st_size,
                     'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
            manifest = {'schema':2, 'version':'1.0.0', 'targets':{'linux-arm64':[entry]}}
            (root/'runtime-manifest.json').write_text(json.dumps(manifest))
            for initial, expected in ((0o644, 0o744), (0o755, 0o755)):
                with self.subTest(initial=initial):
                    source.chmod(initial)
                    directory = seed(root, root, root/'cache', 'linux-arm64')
                    self.assertEqual(stat.S_IMODE((directory/'node').stat().st_mode), expected)
