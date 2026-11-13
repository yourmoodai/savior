"""Development distribution must retain identity and production boundaries."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import development
import package
from prepare import version_changes
from public_install import installed_path
from test_release_flow import fixture
from version_gate import development_version, release_version


class DevelopmentTests(unittest.TestCase):
    def test_tag_namespace_is_narrow(self):
        self.assertTrue(development_version('0.1.0-dev.1'))
        for value in ['0.1.0','0.1.0-rc.1','0.1.0-dev.0','0.1.0-dev.01','01.1.0-dev.1','0.1.0-dev.1+build']:
            self.assertFalse(development_version(value),value)

    def test_initial_prerelease_needs_explicit_unpublished_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); fixture(root)
            with self.assertRaises(ValueError):
                version_changes(root,'0.1.0-dev.1')
            for name,contents in version_changes(root,'0.1.0-dev.1',initial_development=True).items():
                (root/name).write_text(contents)
            self.assertEqual(release_version(root),'0.1.0-dev.1')
            with self.assertRaises(ValueError):
                version_changes(root,'0.1.0-dev.1',initial_development=True)
            self.assertTrue(version_changes(root,'0.1.0-dev.2'))

    def test_packaging_rejects_wrong_tag_or_production_mode_before_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'package.json').write_text('{"version":"0.1.0-dev.1"}')
            for ref,signed in [('refs/tags/v0.1.0',False),('refs/heads/main',False),('refs/tags/v0.1.0-dev.1',True)]:
                with patch.object(package,'ROOT',root),patch.dict(os.environ,{'GITHUB_REF':ref}):
                    with self.assertRaisesRegex(ValueError,'matching development tag'):
                        package.assemble(root/'parts',root/'out',require_signed=signed,development=True)
                    self.assertFalse((root/'out').exists())

    def test_key_substitution_stops_before_verification_or_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'release-public.pem').write_bytes(b'wrong key')
            with patch('development.subprocess.run') as command:
                with self.assertRaisesRegex(ValueError,'development_key_mismatch'):
                    development.verify(root,'0.1.0-dev.1','a'*40,'b'*64)
                command.assert_not_called()

    def test_installed_registry_requires_one_matching_user_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile=Path(temporary);(profile/'plugins').mkdir()
            entry={'scope':'user','version':'0.1.0-dev.1','installPath':str(profile/'plugins/cache/savior')}
            registry=profile/'plugins/installed_plugins.json'
            def write(rows):
                registry.write_text(json.dumps({'plugins':{'savior@yourmood.ai-dev':rows}}))
            write([entry])
            self.assertEqual(installed_path(profile,entry['version']),Path(entry['installPath']).resolve())
            for rows in [[],[entry,entry],[{**entry,'scope':'project'}],[{**entry,'installPath':'/tmp/outside'}]]:
                write(rows)
                with self.assertRaises(ValueError): installed_path(profile,entry['version'])


if __name__ == '__main__':
    unittest.main()
