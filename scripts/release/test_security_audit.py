import importlib.util
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import tarfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'security_audit', Path(__file__).resolve().parents[1] / 'security-audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
installer_spec = importlib.util.spec_from_file_location(
    'security_install', Path(__file__).resolve().parents[1] / 'security-install.py')
installer = importlib.util.module_from_spec(installer_spec)
installer_spec.loader.exec_module(installer)


class SecurityAuditTests(unittest.TestCase):
    def test_scanner_checksum_is_verified_before_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'scripts').mkdir()
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode='w:gz') as bundle:
                info = tarfile.TarInfo('trivy')
                info.size = 9
                bundle.addfile(info, io.BytesIO(b'synthetic'))
            payload = archive.getvalue()
            lock = {'assets': {'macOS': {'url': 'https://example.invalid/scanner',
                                        'sha256': '0' * 64}}}
            path = root / 'scripts/security-tools.lock.json'
            path.write_text(json.dumps(lock))
            def download(url, destination):
                Path(destination).write_bytes(payload)
            with patch.object(installer, 'ROOT', root), \
                    patch.object(installer.platform, 'system', return_value='Darwin'), \
                    patch.object(installer.platform, 'machine', return_value='arm64'), \
                    patch.object(installer.urllib.request, 'urlretrieve', side_effect=download):
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    installer.install()
                self.assertFalse((root / '.cache/security/bin/trivy').exists())
                lock['assets']['macOS']['sha256'] = hashlib.sha256(payload).hexdigest()
                path.write_text(json.dumps(lock))
                installer.install()
                self.assertEqual((root / '.cache/security/bin/trivy').read_bytes(), b'synthetic')

    def test_npm_rejects_low_severity_and_service_errors(self):
        clean = {'metadata': {'vulnerabilities': {'total': 0}}, 'vulnerabilities': {}}
        self.assertTrue(audit.validate_report('npm', clean))
        vulnerable = {'metadata': {'vulnerabilities': {'total': 1}},
                      'vulnerabilities': {'synthetic': {'severity': 'low'}}}
        self.assertFalse(audit.validate_report('npm', vulnerable))
        self.assertFalse(audit.validate_report('npm', {**clean, 'error': {'code': 'EAUDIT'}}))

    def test_rust_unsoundness_blocks_and_maintenance_remains_visible(self):
        clean = {'vulnerabilities': {'count': 0, 'list': []},
                 'settings': {'ignore': [], 'severity': None, 'target_arch': [], 'target_os': []}}
        self.assertTrue(audit.validate_report('rust', clean))
        self.assertFalse(audit.validate_report('rust', {**clean, 'warnings': {'unsound': [{}]}}))
        self.assertTrue(audit.validate_report('rust', {**clean, 'warnings': {'unmaintained': [{}]}}))
        for field, value in [('ignore', ['RUSTSEC-2026-0459']), ('severity', 'high'),
                             ('target_arch', ['x86_64']), ('target_os', ['linux'])]:
            with self.subTest(field=field):
                report = {**clean, 'settings': {**clean['settings'], field: value}}
                self.assertFalse(audit.validate_report('rust', report))

    def test_python_rejects_skipped_or_vulnerable_dependencies(self):
        self.assertTrue(audit.validate_report('python', {'dependencies': [{'vulns': []}]}))
        for dependency in [{'vulns': [{'id': 'synthetic'}]}, {'skip_reason': 'unavailable'}]:
            self.assertFalse(audit.validate_report('python', {'dependencies': [dependency]}))
        self.assertFalse(audit.validate_report('python', {'dependencies': []}))

    def test_container_includes_unfixed_vulnerabilities(self):
        report = {'Metadata': {'ImageID': 'synthetic'}, 'Results': []}
        self.assertTrue(audit.validate_report('container', report))
        report['Results'] = [{'Vulnerabilities': [{'VulnerabilityID': 'synthetic', 'FixedVersion': ''}]}]
        self.assertFalse(audit.validate_report('container', report))

    def test_scanner_failure_or_malformed_output_cannot_pass(self):
        clean = json.dumps({'metadata': {'vulnerabilities': {'total': 0}}, 'vulnerabilities': {}})
        with tempfile.TemporaryDirectory() as temporary, patch.object(audit, 'REPORTS', Path(temporary)):
            for code, output in [(2, clean), (0, '{}'), (0, 'not json'), (0, 'null')]:
                result = subprocess.CompletedProcess(['synthetic'], code, output, 'diagnostic')
                with patch.object(audit.subprocess, 'run', return_value=result):
                    self.assertFalse(audit.run_audit('synthetic', 'npm', ['synthetic']))
                self.assertEqual((Path(temporary) / 'synthetic.json').read_text(), output)


if __name__ == '__main__':
    unittest.main()
