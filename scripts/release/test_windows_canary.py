"""The private canary signs only its built CLI with the pinned release tool."""
from contextlib import chdir
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sign_windows_canary as canary


class CanaryTests(unittest.TestCase):
    def test_validate_jar_before_obtaining_token(self):
        config = {'identities': {'windows_profile_eku': '1.3.6.1.4.1.311.97.1.2.3.4'},
                  'windows': {'authentication': 'github-oidc', 'storetype': 'TRUSTEDSIGNING',
                              'endpoint': 'eus.codesigning.azure.net', 'alias': 'yourmood-signing/yourmood-public',
                              'timestamp_url': 'http://timestamp.acs.microsoft.com'}}
        with tempfile.TemporaryDirectory() as directory, chdir(directory), \
             patch.dict(os.environ, SAVIOR_RELEASE_CONFIG=json.dumps(config)), \
             patch('urllib.request.urlretrieve', side_effect=lambda url, path: path.write_bytes(b'fixture')), \
             patch('sign_windows_canary.credential', return_value=b'fixture-token') as token, \
             patch('subprocess.run') as run:
            binary = Path('target/release/savior.exe')
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'fixture binary')
            with self.assertRaisesRegex(ValueError, 'tool_digest_mismatch'):
                canary.main()
            token.assert_not_called()
            run.assert_not_called()
            spec = {'tools': {'jsign': {'url': 'fixture', 'sha256': hashlib.sha256(b'fixture').hexdigest()}}}
            with patch.object(Path, 'read_text', return_value=json.dumps(spec)):
                canary.main()
            args = run.call_args.args[0]
            self.assertEqual(args[-1], binary.resolve())
            self.assertEqual(args[args.index('--tsmode')+1], 'RFC3161')
            password = args[args.index('--storepass')+1]
            self.assertTrue(password.startswith('file:'))
            self.assertFalse(Path(password[5:]).exists())
            self.assertNotIn('fixture-token', str(args))
