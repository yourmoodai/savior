import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import local


class LocalReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.parts = self.root/'inputs'
        self.output = self.root/'output'
        self.config_path = self.root/'config.json'
        self.config = local.read_json(local.ROOT/'scripts/release/local.example.json')
        self.config['identities'] = {k: 'a'*64 for k in self.config['identities']}
        self.revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=local.ROOT, text=True).strip()
        self.version = local.read_json(local.ROOT/'package.json')['version']
        self.config_path.write_text(json.dumps(self.config))
        self.release = local.Release(self.config_path, self.parts, self.output)
        for target in local.packaging.TARGETS:
            directory = self.parts/target
            directory.mkdir(parents=True)
            for name in self.release.expected_files(target):
                path = directory/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'synthetic artifact')
            self.metadata(target)
        self.environment = {}
        for key, variable in self.config['credentials'].items():
            secret = self.root/key
            secret.write_text('synthetic private canary')
            secret.chmod(0o600)
            self.environment[variable] = str(secret)
        self.env_patch = patch.dict(os.environ, self.environment)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.which = patch('local.shutil.which', return_value='/synthetic/tool')
        self.which.start()
        self.addCleanup(self.which.stop)
        self.credentials = patch.object(local.Release, 'verify_credentials')
        self.credential_check = self.credentials.start()
        self.addCleanup(self.credentials.stop)

    def metadata(self, target):
        directory = self.parts/target
        files = local.inventory(directory)
        files.pop('stage.json', None)
        (directory/'stage.json').write_text(json.dumps({'schema': 1, 'target': target,
            'version': self.version, 'revision': self.revision, 'files': files}))

    def initialize(self):
        self.release.initialize()

    def test_plan_does_not_create_output_or_call_signers(self):
        with patch('sys.argv', ['local.py', 'plan', '--config', str(self.config_path),
                               '--parts', str(self.parts), '--output', str(self.output)]), \
                patch('local.run', side_effect=AssertionError('external tool invoked')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(local.main(), 0)
        self.assertEqual(json.loads(output.getvalue())['network'], 'not_used')
        self.assertFalse(self.output.exists())

    def test_timestamp_transport_accepts_documented_http_without_credentials(self):
        for url in ('http://timestamp.digicert.com', 'https://timestamp.example.com/rfc3161'):
            self.assertEqual(local.timestamp_url(url), url)
        for url in (None, '', 'file:///timestamp', 'https://', 'https://user:secret@timestamp.example.com',
                    'http://timestamp.example.com/#fragment', 'http://timestamp.example.com:invalid',
                    'http://timestamp.example.com:0', 'http://timestamp.example.com/ unsafe'):
            with self.subTest(url=url):
                with self.assertRaisesRegex(local.ReleaseError, 'invalid_timestamp_url'):
                    local.timestamp_url(url)

    def test_missing_credentials_fail_without_output(self):
        with patch.dict(os.environ, {'SAVIOR_APPLE_P12_FILE': ''}):
            with self.assertRaisesRegex(local.ReleaseError, 'missing_credential'):
                self.initialize()
        self.assertFalse(self.output.exists())

    def test_invalid_signing_credentials_fail_before_copy_or_network(self):
        self.credential_check.side_effect = local.ReleaseError('apple_signer_mismatch')
        with self.assertRaisesRegex(local.ReleaseError, 'apple_signer_mismatch'):
            self.initialize()
        self.assertFalse(self.output.exists())

    def test_apple_certificate_reader_uses_legacy_provider_only_after_rejection(self):
        with patch.object(self.release, 'command', return_value=b'synthetic certificate') as command:
            self.assertEqual(self.release.read_apple_certificate(), b'synthetic certificate')
            self.assertEqual(command.call_count, 1)
            self.assertNotIn('-legacy', command.call_args.args)
        with patch.object(self.release, 'command', side_effect=[local.ReleaseError('tool_failed'), b'synthetic certificate']) as command:
            self.assertEqual(self.release.read_apple_certificate(), b'synthetic certificate')
            self.assertEqual(command.call_count, 2)
            self.assertIn('-legacy', command.call_args.args)
        with patch.object(self.release, 'command', side_effect=local.ReleaseError('tool_unavailable_or_timeout')) as command:
            with self.assertRaisesRegex(local.ReleaseError, 'timeout'):
                self.release.read_apple_certificate()
            self.assertEqual(command.call_count, 1)

    def test_input_provenance_and_inventory(self):
        for field, value, expected in [('revision', 'b'*40, 'stage_revision_mismatch'),
                                       ('version', '9.9.9', 'stage_version_mismatch')]:
            with self.subTest(field=field):
                path = self.parts/'win32-x64/stage.json'
                previous = path.read_text()
                data = json.loads(previous)
                data[field] = value
                path.write_text(json.dumps(data))
                with self.assertRaisesRegex(local.ReleaseError, expected):
                    self.release.input_manifest()
                path.write_text(previous)
        path = self.parts/'win32-x64/runtime/win32-x64/savior.exe'
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(local.ReleaseError, 'stage_digest_mismatch'):
            self.release.input_manifest()

    def test_missing_artifact_and_symlink_rejected(self):
        path = self.parts/'linux-x64/native/index.cjs'
        path.unlink()
        with self.assertRaisesRegex(local.ReleaseError, 'invalid_stage_inventory'):
            self.release.input_manifest()
        path.symlink_to(self.root/'config.json')
        with self.assertRaisesRegex(local.ReleaseError, 'unsafe_artifact_path'):
            self.release.input_manifest()

    def test_secret_cannot_be_copied_with_artifacts(self):
        key = self.parts/'secret.pem'
        key.write_text('synthetic secret')
        key.chmod(0o600)
        with patch.dict(os.environ, {'SAVIOR_APPLE_P12_FILE': str(key)}):
            with self.assertRaisesRegex(local.ReleaseError, 'credential_inside_artifacts'):
                self.release.secret('apple_p12')

    def test_private_permissions_required(self):
        Path(self.environment['SAVIOR_APPLE_P12_FILE']).chmod(0o644)
        with self.assertRaisesRegex(local.ReleaseError, 'credential_permissions_not_private'):
            self.release.preflight()

    def test_stages_are_ordered_and_completed_candidate_is_immutable(self):
        with self.assertRaisesRegex(local.ReleaseError, 'release_stage_out_of_order'):
            self.release.execute('package')
        self.assertFalse(self.output.with_name('output.lock').exists())
        self.release.candidate.mkdir()
        (self.release.candidate/'validated.json').write_text('{}')
        with self.assertRaisesRegex(local.ReleaseError, 'candidate_already_complete'):
            self.release.execute('sign')

    def test_concurrent_preparation_is_rejected(self):
        self.output.with_name('output.lock').write_text('')
        with self.assertRaisesRegex(local.ReleaseError, 'release_preparation_busy'):
            self.release.execute('sign')

    def test_signing_uses_private_copy_and_preserves_node(self):
        self.initialize()
        before = local.inventory(self.parts)
        calls = []
        def command(name, *args, **kwargs):
            calls.append((name, args))
            if name == 'rcodesign':
                path = Path(args[-1])
                if path.is_dir():
                    path = path/'Contents/MacOS/savior-bootstrap'
                path.write_bytes(b'signed synthetic artifact')
            return b''
        with patch.object(self.release, 'command', side_effect=command), \
                patch.object(self.release, 'verify_platforms'):
            self.release.execute('sign')
        self.assertEqual(local.inventory(self.parts), before)
        self.assertEqual(len([c for c in calls if c[0] == 'rcodesign']), 4)
        self.assertIn('file:'+self.environment['SAVIOR_WINDOWS_PASSWORD_FILE'], calls[-1][1])
        self.assertNotIn('synthetic private canary', str(calls))
        self.assertEqual((self.release.work_parts/'darwin-arm64/runtime/darwin-arm64/node').read_bytes(), b'synthetic artifact')
        with patch.object(self.release, 'sign', side_effect=AssertionError('resigned')):
            self.release.execute('sign')

    def signed(self):
        self.initialize()
        local.create_bundle(self.release.work_parts/'darwin-arm64',self.version)
        self.release.state['completed'] = ['sign']
        self.release.state['signed_inventory'] = local.inventory(self.release.work_parts)
        self.release.save()

    def test_notarization_rejection_and_resume_do_not_resubmit(self):
        self.signed()
        submission = '01234567-89ab-cdef-0123-456789abcdef'
        def command(name, *args, **kwargs):
            if args[0] == 'notary-log':
                return json.dumps({'jobId': submission, 'status': 'Invalid', 'statusCode': 4000}).encode()
            return b''
        with patch.object(self.release, 'submit', return_value=submission) as submit, \
                patch.object(self.release, 'command', side_effect=command):
            for _ in range(2):
                with self.assertRaisesRegex(local.ReleaseError, 'notarization_not_accepted'):
                    self.release.execute('notarize')
            self.assertEqual(submit.call_count, 1)
        self.assertNotIn('notarize', self.release.state['completed'])

    def test_notarization_timeout_is_resumable(self):
        self.signed()
        submission = '01234567-89ab-cdef-0123-456789abcdef'
        with patch.object(self.release, 'submit', return_value=submission), \
                patch.object(self.release, 'command', side_effect=local.ReleaseError('tool_unavailable_or_timeout')):
            with self.assertRaisesRegex(local.ReleaseError, 'timeout'):
                self.release.execute('notarize')
        restored = local.Release(self.config_path, self.parts, self.output)
        def command(name, *args, **kwargs):
            if args[0] == 'staple':
                (Path(args[1])/'Contents/CodeResources').write_bytes(b'synthetic ticket')
            return json.dumps({'jobId': submission, 'status': 'Accepted', 'statusCode': 0,
                               'sha256': restored.state['payload_sha256']}).encode() if args[0] == 'notary-log' else b''
        with patch.object(restored, 'submit', side_effect=AssertionError('duplicate submission')), \
                patch.object(restored, 'command', side_effect=command):
            restored.execute('notarize')
        self.assertIn('notarize', restored.state['completed'])

    def test_uncertain_upload_blocks_automatic_resubmission(self):
        self.signed()
        result = subprocess.CompletedProcess([], 1, b'', b'created submission ID: 01234567-89ab-cdef-0123-456789abcdef\nprivate upload url')
        with patch('local.subprocess.run', return_value=result):
            with self.assertRaisesRegex(local.ReleaseError, 'submission_unknown'):
                self.release.submit(self.output/'payload.zip')
        with self.assertRaisesRegex(local.ReleaseError, 'submission_unknown'):
            self.release.notarize()

    def test_changed_signed_bytes_block_notarization(self):
        self.signed()
        self.release.signed_paths('darwin-arm64')[0].write_bytes(b'changed')
        with self.assertRaisesRegex(local.ReleaseError, 'signed_artifacts_changed'):
            self.release.notarize()

    def test_tool_failures_never_echo_diagnostics(self):
        for exception in (subprocess.CompletedProcess([], 1, b'synthetic private canary', b'private url'),
                          subprocess.TimeoutExpired('private command', 1)):
            with patch('local.subprocess.run', **({'return_value': exception} if isinstance(exception, subprocess.CompletedProcess)
                                                  else {'side_effect': exception})):
                with self.assertRaises(local.ReleaseError) as error:
                    local.run(['synthetic-tool'])
                self.assertNotIn('private', str(error.exception))

    def test_windows_timestamp_required_even_if_verifier_exits_zero(self):
        for output in (b'Signature verification: ok',
                       b'Timestamp verified using:\nsynthetic chain\nTimestamp Server Signature verification: failed'):
            with self.subTest(output=output), patch.object(self.release, 'command', return_value=output):
                with self.assertRaisesRegex(local.ReleaseError, 'missing_verified_windows_timestamp'):
                    self.release.verify_windows(Path('synthetic.exe'), 'a'*64)

    def test_apple_signer_pinned_to_actual_cms_signer(self):
        self.initialize()
        info = b'signer #0: signature valid: true\nsigner #0: time-stamp token present: true\ntime-stamp token: signer #0: signature valid: true'
        def command(name, *args, **kwargs):
            if args[:2] == ('extract', 'cms-info'):
                return info
            if name == 'openssl' and args[0] == 'x509':
                return b'synthetic signer DER'
            return b''
        with patch.object(self.release, 'command', side_effect=command):
            with self.assertRaisesRegex(local.ReleaseError, 'apple_signer_mismatch'):
                self.release.verify_apple(Path('synthetic'), 'a'*64)
            self.release.verify_apple(Path('synthetic'), hashlib.sha256(b'synthetic signer DER').hexdigest())

    @unittest.skipUnless(shutil.which('node'), 'node is required for candidate assembly')
    def test_complete_simulated_pipeline_and_archive_tampering(self):
        key_script = r'''
          const {generateKeyPairSync}=require('node:crypto'),fs=require('node:fs');
          const {privateKey,publicKey}=generateKeyPairSync('ed25519');
          fs.writeFileSync(process.argv[1],privateKey.export({type:'pkcs8',format:'pem'}));
          fs.writeFileSync(process.argv[2],publicKey.export({type:'spki',format:'pem'}));
        '''
        subprocess.run(['node', '-e', key_script, self.environment['SAVIOR_MANIFEST_PRIVATE_FILE'],
                        self.environment['SAVIOR_MANIFEST_PUBLIC_FILE']], check=True)
        npm = self.root/'fixture-npm'
        npm.write_text('#!/bin/sh\nexit 0\n')
        npm.chmod(0o700)
        self.release.tools['npm'] = str(npm)
        submission = '01234567-89ab-cdef-0123-456789abcdef'
        original_command = self.release.command
        def command(name, *args, **kwargs):
            if name in ('node', 'npm'):
                return original_command(name, *args, **kwargs)
            if name == 'rcodesign' and args[0] == 'staple':
                (Path(args[1])/'Contents/CodeResources').write_bytes(b'synthetic ticket')
            if name == 'rcodesign' and args[0] == 'notary-log':
                return json.dumps({'jobId': submission, 'status': 'Accepted', 'statusCode': 0,
                                   'sha256': self.release.state['payload_sha256']}).encode()
            return b''
        with patch.object(self.release, 'verify_platforms'), \
                patch.object(self.release, 'submit', return_value=submission), \
                patch.object(self.release, 'command', side_effect=command):
            for stage in local.STAGES:
                self.release.execute(stage)
        report = local.read_json(self.release.candidate/'validated.json')
        self.assertEqual(report['native_os_acceptance'], 'not_run')
        self.assertEqual(report['smoke_tests'], 'not_run')
        self.assertEqual(report['publication'], 'not_run')
        self.assertEqual(report['static_integrity'], 'passed')
        verifier=['node',str(local.ROOT/'scripts/release/verify-release.mjs'),str(self.release.candidate),self.version,self.revision]
        verifier_env={**os.environ,'SAVIOR_RELEASE_PUBLIC_KEY':Path(self.environment['SAVIOR_MANIFEST_PUBLIC_FILE']).read_text()}
        self.assertEqual(subprocess.run(verifier,env=verifier_env,capture_output=True).returncode,0)
        for name in (f'savior-{self.version}.zip','marketplace.json','runtime-manifest.json','release-envelope.sig'):
            path=self.release.candidate/name
            original=path.read_bytes()
            path.write_bytes(original+b'tampered')
            self.assertNotEqual(subprocess.run(verifier,env=verifier_env,capture_output=True).returncode,0,name)
            path.write_bytes(original)
        self.assertNotEqual(subprocess.run([*verifier[:-1],'b'*40],env=verifier_env,capture_output=True).returncode,0)
        # This exercises package assembly and real Ed25519 signatures, not OS signing.
        runtime = local.read_json(self.release.candidate/'runtime-manifest.json')
        first = runtime['targets']['linux-x64'][0]['asset']
        (self.release.candidate/first).write_bytes(b'changed signed bytes')
        with self.assertRaisesRegex(local.ReleaseError, 'signed_runtime_mismatch'):
            local.validate_archive(self.release.candidate, self.release.work_parts, self.version)

    def test_windows_verifier_pins_leaf_and_uses_both_trust_bundles(self):
        with patch.object(self.release, 'command', return_value=b'Timestamp Server Signature verification: ok\n') as command:
            self.release.verify_windows(Path('fixture.node'), 'a'*64)
        argv = command.call_args.args
        self.assertIn('-require-leaf-hash', argv)
        self.assertIn('sha256:'+'a'*64, argv)
        self.assertIn('-CAfile', argv)
        self.assertIn('-TSA-CAfile', argv)


@unittest.skipUnless(shutil.which('node'), 'node is required for signature round trips')
class CandidateIntegrityTests(unittest.TestCase):
    def test_real_ed25519_identity_and_mutated_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            version = local.read_json(local.ROOT/'package.json')['version']
            script = r'''
              const {generateKeyPairSync}=require('node:crypto'),fs=require('node:fs');
              const {privateKey,publicKey}=generateKeyPairSync('ed25519');
              fs.writeFileSync(process.argv[1],privateKey.export({type:'pkcs8',format:'pem'}));
              fs.writeFileSync(process.argv[2],publicKey.export({type:'spki',format:'pem'}));
            '''
            subprocess.run(['node', '-e', script, str(root/'private.pem'), str(root/'expected.pem')], check=True)
            payload = b'synthetic runtime bytes'
            targets = {}
            for target in local.packaging.TARGETS:
                suffix = '.exe' if target == 'win32-x64' else ''
                targets[target] = []
                for name in ('savior'+suffix, 'savior-review'+suffix, 'savior.node'):
                    asset = target+'-'+name
                    (root/asset).write_bytes(payload)
                    targets[target].append({'name': name, 'asset': asset, 'bytes': len(payload),
                                            'sha256': hashlib.sha256(payload).hexdigest()})
            (root/'runtime-manifest.json').write_text(json.dumps({'schema': 1, 'protocol': 1,
                                                                'version': version, 'targets': targets,
                                                                'model':{'artifacts':[]}}))
            env = {**os.environ, 'SAVIOR_RELEASE_SIGNING_KEY': (root/'private.pem').read_text()}
            subprocess.run(['node', str(local.ROOT/'scripts/release/sign.mjs'), str(root/'runtime-manifest.json'),
                            str(root/'runtime-manifest.sig'), str(root/'release-public.pem')], env=env, check=True)
            (root/'private.pem').unlink()
            public = (root/'expected.pem').read_bytes()
            (root/'expected.pem').unlink()
            with tempfile.NamedTemporaryFile() as expected:
                expected.write(public)
                expected.flush()
                for name in (f'savior-{version}.zip', 'marketplace.json', 'SHA256SUMS'):
                    (root/name).write_bytes(b'fixture')
                revision = subprocess.check_output(['git','rev-parse','HEAD'],cwd=local.ROOT,text=True).strip()
                (root/'release-envelope.json').write_text(json.dumps({'schema':1,'version':version,
                    'revision':revision,'models':{},'assets':local.inventory(root)}))
                subprocess.run(['node',str(local.ROOT/'scripts/release/sign.mjs'),str(root/'release-envelope.json'),
                                str(root/'release-envelope.sig'),str(root/'release-public.pem')],env=env,check=True)
                argv = ['node', str(local.ROOT/'scripts/release/validate-candidate.mjs'), str(root), expected.name]
                self.assertEqual(subprocess.run(argv, capture_output=True).returncode, 0)
                (root/'linux-x64-savior').write_bytes(b'changed')
                self.assertNotEqual(subprocess.run(argv, capture_output=True).returncode, 0)


@unittest.skipUnless(shutil.which('openssl'), 'OpenSSL is required')
class AppleContainerTests(unittest.TestCase):
    def test_modern_and_keychain_style_pkcs12_containers(self):
        version = subprocess.check_output(['openssl', 'version'], text=True)
        if not version.startswith('OpenSSL 3.'):
            self.skipTest('OpenSSL 3 legacy provider regression requires the Linux container')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            key, certificate, password = [root/name for name in ('key.pem', 'certificate.pem', 'password')]
            password.write_text('synthetic container password')
            password.chmod(0o600)
            def invoke(argv):
                return subprocess.run([str(a) for a in argv], capture_output=True, check=True).stdout
            invoke(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=synthetic container fixture', '-keyout', key, '-out', certificate])
            expected = invoke(['openssl', 'x509', '-in', certificate, '-outform', 'DER'])
            config = root/'config.json'
            config.write_text((local.ROOT/'scripts/release/local.example.json').read_text())
            release = local.Release(config, root/'parts', root/'output')
            for legacy in (False, True):
                with self.subTest(legacy=legacy):
                    store = root/('legacy.p12' if legacy else 'modern.p12')
                    invoke(['openssl', 'pkcs12', '-export', *(['-legacy'] if legacy else []),
                            '-inkey', key, '-in', certificate, '-out', store, '-passout', 'file:'+str(password)])
                    store.chmod(0o600)
                    with patch.dict(os.environ, {'SAVIOR_APPLE_P12_FILE': str(store),
                                                 'SAVIOR_APPLE_PASSWORD_FILE': str(password)}):
                        pem = release.read_apple_certificate()
                        actual = subprocess.run(['openssl', 'x509', '-outform', 'DER'], input=pem,
                                                capture_output=True, check=True).stdout
                        self.assertEqual(actual, expected)
                        self.assertNotIn(b'PRIVATE KEY', pem)
                        password.write_text('incorrect synthetic password')
                        with self.assertRaisesRegex(local.ReleaseError, '^tool_failed$'):
                            release.read_apple_certificate()
                        password.write_text('synthetic container password')


@unittest.skipUnless(shutil.which('jsign') and shutil.which('osslsigncode'), 'Linux signing tools are required')
class WindowsFixtureTests(unittest.TestCase):
    @staticmethod
    def pe_image():
        # A minimal PE fixture; never executed and contains no application logic.
        image = bytearray(1024)
        image[:2] = b'MZ'
        struct.pack_into('<I', image, 60, 128)
        image[128:132] = b'PE\0\0'
        struct.pack_into('<HHIIIHH', image, 132, 0x8664, 1, 0, 0, 0, 240, 0x22)
        optional = 152
        struct.pack_into('<H', image, optional, 0x20b)
        struct.pack_into('<III', image, optional+4, 512, 0, 0)
        struct.pack_into('<IIQII', image, optional+16, 4096, 4096, 0x140000000, 4096, 512)
        struct.pack_into('<HHHHHH', image, optional+40, 6, 0, 0, 0, 6, 0)
        struct.pack_into('<II', image, optional+56, 8192, 512)
        struct.pack_into('<HH', image, optional+68, 3, 0x8140)
        struct.pack_into('<QQQQII', image, optional+72, 1048576, 4096, 1048576, 4096, 0, 16)
        section = optional+240
        image[section:section+8] = b'.text\0\0\0'
        struct.pack_into('<IIIIIIHHI', image, section+8, 1, 4096, 512, 512, 0, 0, 0, 0, 0x60000020)
        image[512] = 0xc3
        return image

    def test_real_self_signed_addon_is_rejected_without_timestamp(self):
        image = self.pe_image()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root/'fixture.node'
            payload.write_bytes(image)
            password, key, cert, store = [root/name for name in ('password', 'key.pem', 'cert.pem', 'fixture.p12')]
            password.write_text('synthetic fixture password')
            password.chmod(0o600)
            def invoke(argv):
                return subprocess.run([str(a) for a in argv], check=True, capture_output=True)
            invoke(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=synthetic release fixture', '-addext', 'extendedKeyUsage=codeSigning',
                    '-keyout', key, '-out', cert])
            invoke(['openssl', 'pkcs12', '-export', '-inkey', key, '-in', cert, '-name', 'fixture',
                    '-out', store, '-passout', 'file:'+str(password)])
            invoke(['jsign', '--storetype', 'PKCS12', '--keystore', store,
                    '--storepass', 'file:'+str(password), '--alias', 'fixture', '--alg', 'SHA-256', payload])
            self.assertNotEqual(payload.read_bytes(), image)
            der = invoke(['openssl', 'x509', '-in', cert, '-outform', 'DER']).stdout
            config = local.read_json(local.ROOT/'scripts/release/local.example.json')
            config_path = root/'config.json'
            config_path.write_text(json.dumps(config))
            cert.chmod(0o600)
            release = local.Release(config_path, root/'parts', root/'output')
            with patch.dict(os.environ, {'SAVIOR_WINDOWS_CA_FILE': str(cert), 'SAVIOR_TIMESTAMP_CA_FILE': str(cert)}):
                with self.assertRaisesRegex(local.ReleaseError, 'missing_verified_windows_timestamp'):
                    release.verify_windows(payload, hashlib.sha256(der).hexdigest())
                with self.assertRaisesRegex(local.ReleaseError, 'tool_failed'):
                    release.verify_windows(payload, 'a'*64)

    def test_real_rfc3161_timestamp_and_tamper_rejection_offline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            def invoke(argv, **kwargs):
                return subprocess.run([str(a) for a in argv], capture_output=True, check=True, **kwargs).stdout
            password, key, cert, store = [root/name for name in ('password', 'key.pem', 'cert.pem', 'fixture.p12')]
            password.write_text('synthetic timestamp fixture password')
            password.chmod(0o600)
            invoke(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=synthetic signing fixture', '-addext', 'extendedKeyUsage=codeSigning',
                    '-keyout', key, '-out', cert])
            invoke(['openssl', 'pkcs12', '-export', '-inkey', key, '-in', cert, '-name', 'fixture',
                    '-out', store, '-passout', 'file:'+str(password)])
            tsa_key, tsa_cert, tsa_config = [root/name for name in ('tsa-key.pem', 'tsa-cert.pem', 'tsa.conf')]
            invoke(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=synthetic timestamp authority', '-addext', 'extendedKeyUsage=critical,timeStamping',
                    '-addext', 'keyUsage=critical,digitalSignature', '-keyout', tsa_key, '-out', tsa_cert])
            (root/'serial').write_text('01\n')
            tsa_config.write_text(f'''[tsa]
default_tsa = fixture
[fixture]
serial = {root}/serial
signer_cert = {tsa_cert}
signer_key = {tsa_key}
signer_digest = sha256
default_policy = 1.2.3.4.1
digests = sha256, sha384, sha512
accuracy = secs:1
ordering = yes
tsa_name = yes
ess_cert_id_chain = no
ess_cert_id_alg = sha256
''')
            class TimestampHandler(BaseHTTPRequestHandler):
                def do_POST(self):
                    with tempfile.TemporaryDirectory(dir=root) as request_dir:
                        query, response = Path(request_dir)/'query', Path(request_dir)/'response'
                        query.write_bytes(self.rfile.read(int(self.headers['Content-Length'])))
                        invoke(['openssl', 'ts', '-reply', '-config', tsa_config, '-queryfile', query, '-out', response])
                        data = response.read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/timestamp-reply')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

                def log_message(self, *_args):
                    pass
            server = ThreadingHTTPServer(('127.0.0.1', 0), TimestampHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                paths = [root/name for name in ('fixture.exe', 'fixture.node')]
                for path in paths:
                    path.write_bytes(self.pe_image())
                invoke(['jsign', '--storetype', 'PKCS12', '--keystore', store,
                        '--storepass', 'file:'+str(password), '--alias', 'fixture', '--alg', 'SHA-256',
                        '--tsmode', 'RFC3161', '--tsaurl', f'http://127.0.0.1:{server.server_port}',
                        *paths], timeout=30)
                config = root/'config.json'
                config.write_text((local.ROOT/'scripts/release/local.example.json').read_text())
                release = local.Release(config, root/'parts', root/'output')
                fingerprint = hashlib.sha256(invoke(['openssl', 'x509', '-in', cert, '-outform', 'DER'])).hexdigest()
                cert.chmod(0o600)
                tsa_cert.chmod(0o600)
                with patch.dict(os.environ, {'SAVIOR_WINDOWS_CA_FILE': str(cert), 'SAVIOR_TIMESTAMP_CA_FILE': str(tsa_cert)}):
                    for path in paths:
                        release.verify_windows(path, fingerprint)
                    with self.assertRaisesRegex(local.ReleaseError, 'tool_failed'):
                        release.verify_windows(paths[0], 'a'*64)
                    damaged = bytearray(paths[0].read_bytes())
                    damaged[512] ^= 1
                    paths[0].write_bytes(damaged)
                    with self.assertRaisesRegex(local.ReleaseError, 'tool_failed'):
                        release.verify_windows(paths[0], fingerprint)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
