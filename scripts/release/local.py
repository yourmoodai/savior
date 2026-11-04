"""Prepare signed release candidates on Linux; never publish or run native payloads."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
import zipfile

import package as packaging
from archive import validate as validate_sealed_archive
from macos_bundle import BUNDLE, RUNTIME, LAUNCHER, NODE, create as create_bundle

ROOT = packaging.ROOT
STAGES = ('sign', 'notarize', 'package', 'validate')
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


class ReleaseError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise ReleaseError(code)


def timestamp_url(value):
    require(isinstance(value, str) and not any(c.isspace() for c in value), 'invalid_timestamp_url')
    try:
        parsed = urlsplit(value)
        port = parsed.port
        require(parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username
                and not parsed.password and not parsed.fragment and (port is None or port > 0), 'invalid_timestamp_url')
    except ValueError:
        raise ReleaseError('invalid_timestamp_url') from None
    # RFC 3161 authenticates the timestamp token and its message imprint.
    # Some public TSAs document HTTP endpoints; verification stays mandatory.
    return value


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def safe_path(value):
    path = Path(value).absolute()
    require('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), 'unsafe_path')
    return path


def inventory(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        require(not path.is_symlink(), 'unsafe_artifact_path')
        require(path.is_file() or path.is_dir(), 'invalid_artifact_type')
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = packaging.digest(path)
    return result


def run(argv, *, env=None, data=None, timeout=120):
    # Tool diagnostics may contain credentials or signed upload URLs.
    try:
        result = subprocess.run([str(a) for a in argv], input=data, capture_output=True,
                                env=env, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ReleaseError('tool_unavailable_or_timeout') from None
    require(result.returncode == 0, 'tool_failed')
    return result.stdout


class Release:
    def __init__(self, config_path, parts, output):
        self.config = read_json(config_path)
        require(self.config.get('schema') == 1, 'unsupported_configuration')
        self.parts = safe_path(parts)
        self.output = safe_path(output)
        require(not self.output.is_relative_to(self.parts) and not self.parts.is_relative_to(self.output), 'overlapping_directories')
        self.version = read_json(ROOT/'package.json')['version']
        self.tools = {name: self.config.get('tools', {}).get(name, name)
                      for name in ('rcodesign', 'jsign', 'osslsigncode', 'openssl', 'node', 'npm')}
        require(all(isinstance(v, str) and v for v in self.tools.values()), 'invalid_tool_configuration')
        self.state_path = self.output/'state.json'
        self.work_parts = self.output/'parts'
        self.candidate = self.output/'candidate'
        self.state = read_json(self.state_path) if self.state_path.exists() else {}

    def command(self, name, *args, **kwargs):
        env = dict(os.environ)
        # Do not inherit implicit rcodesign configuration from the developer shell.
        for key in list(env):
            if key.startswith('APPLE_CODESIGN_'):
                del env[key]
        argv = [self.tools[name]]
        if name == 'rcodesign':
            argv += ['--config-file', '/dev/null']
        return run([*argv, *args], env=env, **kwargs)

    def expected_files(self, target):
        suffix = '.exe' if target == 'win32-x64' else ''
        addon = packaging.TARGETS[target][2]
        return {f'runtime/{target}/{name}{suffix}' for name in ('node', 'savior', 'savior-review')} | ({'runtime/darwin-arm64/savior-bootstrap'} if target == 'darwin-arm64' else set()) | {
            f'runtime/{target}/NODE-LICENSE', f'native/savior.{addon}.node',
            'native/index.cjs', 'native/index.d.ts'}

    def input_manifest(self):
        require(self.parts.is_dir(), 'missing_platform_artifacts')
        require(set(p.name for p in self.parts.iterdir()) == set(packaging.TARGETS), 'invalid_target_inventory')
        revision = None
        manifests = {}
        for target in packaging.TARGETS:
            directory = self.parts/target
            actual = inventory(directory)
            require(set(actual) == self.expected_files(target) | {'stage.json'}, 'invalid_stage_inventory')
            metadata = read_json(directory/'stage.json')
            require(metadata.get('schema') == 1 and metadata.get('target') == target, 'invalid_stage_provenance')
            require(metadata.get('version') == self.version, 'stage_version_mismatch')
            require(re.fullmatch(r'[0-9a-f]{40}', metadata.get('revision', '')), 'missing_stage_revision')
            revision = revision or metadata['revision']
            require(metadata['revision'] == revision, 'stage_revision_mismatch')
            require(metadata.get('files') == {k: v for k, v in actual.items() if k != 'stage.json'}, 'stage_digest_mismatch')
            manifests[target] = actual
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        require(revision == head, 'source_revision_mismatch')
        shared = [manifests[t]['native/index.cjs'] for t in manifests]
        require(len(set(shared)) == 1, 'native_wrapper_mismatch')
        shared = [manifests[t]['native/index.d.ts'] for t in manifests]
        require(len(set(shared)) == 1, 'native_wrapper_mismatch')
        return {'revision': revision, 'version': self.version, 'inputs': manifests,
                'configuration_sha256': hashlib.sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest()}

    def secret(self, key):
        variable = self.config.get('credentials', {}).get(key)
        require(isinstance(variable, str) and re.fullmatch(r'[A-Z][A-Z0-9_]*', variable), 'missing_credential_reference')
        value = os.environ.get(variable)
        require(value, 'missing_credential')
        path = safe_path(value)
        require(path.is_file(), 'missing_credential_file')
        require(not path.is_relative_to(ROOT) and not path.is_relative_to(self.parts)
                and not path.is_relative_to(self.output), 'credential_inside_artifacts')
        require(path.stat().st_mode & 0o077 == 0, 'credential_permissions_not_private')
        return path

    def fingerprint(self, key):
        value = self.config.get('identities', {}).get(key, '')
        require(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value), 'missing_expected_identity')
        return value

    def preflight(self, verify_credentials=True):
        binding = self.input_manifest()
        require(not (self.candidate/'validated.json').exists(), 'candidate_already_complete')
        for value in self.tools.values():
            require(shutil.which(value), 'missing_release_tool')
        for key in ('apple_p12', 'apple_password', 'apple_api', 'manifest_private', 'manifest_public'):
            self.secret(key)
        for key in ('apple', 'windows', 'node_apple', 'node_windows'):
            self.fingerprint(key)
        windows = self.config.get('windows', {})
        require(windows.get('storetype') in ('PKCS12', 'PKCS11', 'TRUSTEDSIGNING', 'DIGICERTONE'), 'unsupported_windows_provider')
        require(isinstance(windows.get('alias'), str) and windows['alias'], 'missing_windows_alias')
        timestamp_url(windows.get('timestamp_url'))
        self.secret('windows_password')
        if windows['storetype'] in ('PKCS12', 'PKCS11', 'DIGICERTONE'):
            self.secret('windows_keystore')
        else:
            require(re.fullmatch(r'[a-z0-9.-]+\.codesigning\.azure\.net', windows.get('endpoint', '')), 'invalid_windows_endpoint')
        self.secret('windows_ca')
        self.secret('timestamp_ca')
        require(isinstance(self.config.get('notary_timeout_seconds', 1800), int)
                and 1 <= self.config.get('notary_timeout_seconds', 1800) <= 86400, 'invalid_notary_timeout')
        if self.state:
            require(self.state['binding'] == binding, 'candidate_inputs_changed')
        if verify_credentials:
            self.verify_credentials()
        return binding

    def verify_credentials(self):
        self.command('node', ROOT/'scripts/release/check-key.mjs', self.secret('manifest_public'),
                     data=self.secret('manifest_private').read_bytes())
        certificate = self.read_apple_certificate()
        der = self.command('openssl', 'x509', '-outform', 'DER', data=certificate)
        require(hashlib.sha256(der).hexdigest() == self.fingerprint('apple'), 'apple_signer_mismatch')
        self.command('openssl', 'x509', '-checkend', '0', '-noout', data=certificate)

    def read_apple_certificate(self):
        args = ['-in', self.secret('apple_p12'), '-passin',
                'file:'+str(self.secret('apple_password')), '-clcerts', '-nokeys']
        try:
            return self.command('openssl', 'pkcs12', *args)
        except ReleaseError as error:
            if str(error) != 'tool_failed':
                raise
            # Keychain exports can wrap certificates with RC2. OpenSSL 3 needs
            # its legacy provider to read those containers, not to sign code.
            return self.command('openssl', 'pkcs12', '-legacy', *args)

    def save(self):
        write_json(self.state_path, self.state)

    def initialize(self):
        binding = self.preflight()
        if not self.state:
            require(not self.output.exists(), 'output_already_exists')
            self.output.mkdir(parents=True, mode=0o700)
            shutil.copytree(self.parts, self.work_parts)
            self.state = {'binding': binding, 'completed': [], 'checks': {}}
            self.save()
        else:
            safe_path(self.state_path)
            safe_path(self.work_parts)
            inventory(self.work_parts)

    def signed_paths(self, target):
        suffix = '.exe' if target == 'win32-x64' else ''
        directory = self.work_parts/target
        if target == 'darwin-arm64':
            return [directory/RUNTIME/name for name in ('savior','savior-review','savior.node')] + [directory/LAUNCHER]
        return [directory/f'runtime/{target}/{name}{suffix}' for name in ('savior', 'savior-review')] + [
            directory/f'native/savior.{packaging.TARGETS[target][2]}.node']

    def verify_apple(self, path, fingerprint):
        self.command('rcodesign', 'verify', path)
        info = self.command('rcodesign', 'extract', 'cms-info', path).decode()
        require('signer #0: signature valid: true' in info and
                'signer #0: time-stamp token present: true' in info and
                'time-stamp token: signer #0: signature valid: true' in info, 'invalid_apple_signature_or_timestamp')
        cms = self.command('rcodesign', 'extract', 'cms-raw', path)
        code_directory = self.command('rcodesign', 'extract', 'code-directory-raw', path)
        with tempfile.TemporaryDirectory(dir=self.output) as temporary:
            signer = Path(temporary)/'signer.pem'
            content = Path(temporary)/'code-directory'
            content.write_bytes(code_directory)
            # Verify CMS content, then pin the actual signer rather than any chain member.
            self.command('openssl', 'cms', '-verify', '-binary', '-inform', 'DER', '-noverify', '-content', content,
                         '-signer', signer, '-out', os.devnull, data=cms)
            der = self.command('openssl', 'x509', '-in', signer, '-outform', 'DER')
            require(hashlib.sha256(der).hexdigest() == fingerprint, 'apple_signer_mismatch')

    def verify_windows(self, path, fingerprint):
        result = self.command('osslsigncode', 'verify', '-CAfile', self.secret('windows_ca'),
                              '-TSA-CAfile', self.secret('timestamp_ca'),
                              '-require-leaf-hash', 'sha256:'+fingerprint, '-in', path).decode()
        require(re.search(r'^Timestamp Server Signature verification: ok\s*$', result, re.MULTILINE),
                'missing_verified_windows_timestamp')

    def verify_platforms(self):
        for path in self.signed_paths('darwin-arm64'):
            self.verify_apple(path, self.fingerprint('apple'))
        self.verify_apple(self.work_parts/'darwin-arm64'/NODE, self.fingerprint('node_apple'))
        for path in self.signed_paths('win32-x64'):
            self.verify_windows(path, self.fingerprint('windows'))
        self.verify_windows(self.work_parts/'win32-x64/runtime/win32-x64/node.exe', self.fingerprint('node_windows'))

    def sign(self):
        # Retrying a failed signing stage starts from unsigned inputs, not partial signatures.
        bundle = create_bundle(self.work_parts/'darwin-arm64', self.version)
        for target in ('win32-x64',):
            for path in self.signed_paths(target):
                original = self.parts/target/path.relative_to(self.work_parts/target)
                shutil.copy2(original, path)
        for path in self.signed_paths('darwin-arm64')[:-1]:
            self.command('rcodesign', 'sign', '--for-notarization', '--p12-file', self.secret('apple_p12'),
                         '--p12-password-file', self.secret('apple_password'), path, timeout=300)
        self.command('rcodesign', 'sign', '--shallow', '--for-notarization',
                     '--exclude', 'Contents/Frameworks/node', '--exclude', 'Contents/Helpers/*',
                     '--p12-file', self.secret('apple_p12'), '--p12-password-file',
                     self.secret('apple_password'), bundle, timeout=300)
        windows = self.config['windows']
        store = (self.secret('windows_keystore') if windows['storetype'] != 'TRUSTEDSIGNING'
                 else windows['endpoint'])
        self.command('jsign', '--storetype', windows['storetype'], '--keystore', store,
                     '--storepass', 'file:'+str(self.secret('windows_password')), '--alias', windows['alias'],
                     '--alg', 'SHA-256', '--tsmode', 'RFC3161', '--tsaurl', windows['timestamp_url'],
                     *self.signed_paths('win32-x64'), timeout=600)
        self.verify_platforms()
        self.state['signed_inventory'] = inventory(self.work_parts)
        self.state['checks']['platform_signatures'] = 'cross_platform_tools_only'

    def notarize(self):
        require(inventory(self.work_parts) == self.state['signed_inventory'], 'signed_artifacts_changed')
        require(not self.state.get('submission_unknown'), 'notary_submission_unknown_check_apple_history')
        payload = self.output/'notary.zip'
        if not self.state.get('submission'):
            with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as archive:
                bundle = self.work_parts/'darwin-arm64'/BUNDLE
                for path in sorted(bundle.rglob('*')):
                    if path.is_file(): archive.write(path, path.relative_to(bundle.parent))
            # rcodesign emits the submission ID to stderr, not structured stdout.
            # The wrapper returns only the ID even when the upload fails after creation.
            submission = self.submit(payload)
            self.state['submission'] = submission
            self.state['payload_sha256'] = packaging.digest(payload)
            self.save()
        require(packaging.digest(payload) == self.state['payload_sha256'], 'notary_payload_changed')
        timeout = self.config.get('notary_timeout_seconds', 1800)
        self.command('rcodesign', 'notary-wait', '--api-key-file', self.secret('apple_api'),
                     '--max-wait-seconds', str(timeout), self.state['submission'], timeout=timeout+30)
        response = json.loads(self.command('rcodesign', 'notary-log', '--api-key-file',
                                          self.secret('apple_api'), self.state['submission']))
        require(response.get('jobId') == self.state['submission'] and response.get('status') == 'Accepted'
                and response.get('statusCode') == 0 and response.get('sha256') == self.state['payload_sha256'],
                'notarization_not_accepted')
        self.state['checks']['apple_notarization'] = 'accepted'
        bundle = self.work_parts/'darwin-arm64'/BUNDLE
        self.command('rcodesign', 'staple', bundle, timeout=300)
        require((bundle/'Contents/CodeResources').is_file(), 'notarization_ticket_missing')
        self.state['signed_inventory'] = inventory(self.work_parts)
        self.state['checks']['apple_stapling'] = 'completed_native_validation_pending'

    def submit(self, payload):
        argv = [self.tools['rcodesign'], '--config-file', '/dev/null', 'notary-submit',
                '--api-key-file', str(self.secret('apple_api')), str(payload)]
        try:
            env = {k:v for k,v in os.environ.items() if not k.startswith('APPLE_CODESIGN_')}
            result = subprocess.run(argv, capture_output=True, timeout=600, env=env)
        except (OSError, subprocess.TimeoutExpired):
            self.state['submission_unknown'] = True
            self.save()
            raise ReleaseError('notary_submission_unknown_check_apple_history') from None
        matches = re.findall('created submission ID: ('+UUID+')', result.stderr.decode(errors='replace'))
        if result.returncode != 0 or len(matches) != 1:
            self.state['submission_unknown'] = True
            if len(matches) == 1:
                self.state['submission'] = matches[0]
            self.save()
        require(result.returncode == 0 and len(matches) == 1, 'notary_submission_unknown_check_apple_history')
        return matches[0]

    def package(self):
        require(inventory(self.work_parts) == self.state['signed_inventory'], 'signed_artifacts_changed')
        require(not self.candidate.exists(), 'package_output_exists_use_new_output')
        env = dict(os.environ)
        env['SAVIOR_RELEASE_SIGNING_KEY'] = self.secret('manifest_private').read_text()
        # Keep the key out of arguments and preserve the existing immutable package format.
        run([self.tools['node'], ROOT/'scripts/release/check-key.mjs', self.secret('manifest_public')],
            data=env['SAVIOR_RELEASE_SIGNING_KEY'].encode())
        run([os.sys.executable, ROOT/'scripts/release/package.py', 'assemble', self.work_parts,
             self.candidate/'plugin', '--node', self.tools['node'], '--npm', self.tools['npm'],
             '--require-signed'], env=env, timeout=600)

    def validate(self):
        safe_path(self.candidate)
        inventory(self.candidate)
        self.verify_platforms()
        require(inventory(self.work_parts) == self.state['signed_inventory'], 'signed_artifacts_changed')
        self.command('node', ROOT/'scripts/release/validate-candidate.mjs', self.candidate,
                     self.secret('manifest_public'))
        validate_archive(self.candidate, self.work_parts, self.version)
        validate_sealed_archive(self.candidate, self.version)
        report = {'schema': 1, 'version': self.version, 'revision': self.state['binding']['revision'],
                  'checks': self.state['checks'], 'static_integrity': 'passed',
                  'native_os_acceptance': 'not_run', 'smoke_tests': 'not_run', 'publication': 'not_run',
                  'offline_first_launch': 'not_run',
                  'assets': {p.name: packaging.digest(p) for p in self.candidate.iterdir() if p.is_file()}}
        write_json(self.candidate/'validated.json', report)

    def execute(self, stage):
        self.preflight()
        self.output.parent.mkdir(parents=True, exist_ok=True)
        lock = self.output.with_name(self.output.name+'.lock')
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ReleaseError('release_preparation_busy') from None
        try:
            os.close(descriptor)
            self.initialize()
            index = STAGES.index(stage)
            require(all(s in self.state['completed'] for s in STAGES[:index]), 'release_stage_out_of_order')
            if stage in self.state['completed']:
                require(inventory(self.work_parts) == self.state['signed_inventory'], 'signed_artifacts_changed')
                return
            getattr(self, stage)()
            self.state['completed'].append(stage)
            self.save()
        finally:
            lock.unlink()


def validate_archive(candidate, parts, version):
    archive_path = candidate/f'savior-{version}.zip'
    marketplace = read_json(candidate/'marketplace.json')
    entry = marketplace['plugins'][0]
    require(entry['name'] == 'savior' and entry['source'] == {
        'source': 'archive', 'url': f'https://github.com/yourmoodai/savior/releases/download/v{version}/{archive_path.name}',
        'sha256': packaging.digest(archive_path)}, 'marketplace_mismatch')
    require((candidate/'SHA256SUMS').read_text() == f'{packaging.digest(archive_path)}  {archive_path.name}\n', 'archive_digest_mismatch')
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and all(not Path(n).is_absolute() and '..' not in Path(n).parts
                and '\\' not in n for n in names), 'unsafe_archive_inventory')
        manifest = json.loads(archive.read('release.json'))
        require(manifest['version'] == version and set(manifest['targets']) == set(packaging.TARGETS), 'archive_version_mismatch')
        require(set(names) == set(manifest['files']) | {'release.json'}, 'archive_inventory_mismatch')
        for name, expected in manifest['files'].items():
            require(hashlib.sha256(archive.read(name)).hexdigest() == expected, 'archive_file_digest_mismatch')
        require(json.loads(archive.read('.claude-plugin/plugin.json'))['version'] == version
                and json.loads(archive.read('package.json'))['version'] == version, 'plugin_version_mismatch')
        for name in ('runtime-manifest.json', 'runtime-manifest.sig', 'release-public.pem'):
            require(archive.read(name) == (candidate/name).read_bytes(), 'archive_manifest_mismatch')
        for target in packaging.TARGETS:
            executable = 'node.exe' if target == 'win32-x64' else 'node'
            name = NODE if target == 'darwin-arm64' else f'runtime/{target}/{executable}'
            original = parts/target/'runtime'/target/executable
            require(archive.read(name) == original.read_bytes(), 'bundled_node_changed')
        runtime = read_json(candidate/'runtime-manifest.json')
        for target, entries in runtime['targets'].items():
            for entry in entries:
                if 'path' in entry:
                    require(hashlib.sha256(archive.read(entry['path'])).hexdigest() == entry['sha256'], 'bundled_runtime_mismatch')
                    continue
                if entry['name'] == 'savior.node':
                    original = parts/target/f'native/savior.{packaging.TARGETS[target][2]}.node'
                else:
                    original = parts/target/'runtime'/target/entry['name']
                require(packaging.digest(original) == packaging.digest(candidate/entry['asset']), 'signed_runtime_mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('plan', 'preflight', 'candidate', *STAGES))
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--parts', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, os.environ.get('LOG_LEVEL', 'INFO').upper(), logging.INFO))
    try:
        release = Release(args.config, args.parts, args.output)
        if args.stage == 'plan':
            try:
                release.preflight(verify_credentials=False)
                prerequisite = 'ready'
            except (ReleaseError, OSError, ValueError, KeyError) as error:
                prerequisite = str(error) if isinstance(error, ReleaseError) else 'invalid_or_missing_input'
            print(json.dumps({'schema': 1, 'mode': 'plan', 'stages': STAGES,
                              'prerequisites': prerequisite, 'network': 'not_used',
                              'credential_validation': 'not_run',
                              'smoke_tests': 'not_run', 'publication': 'not_run'}))
        elif args.stage == 'preflight':
            release.preflight()
            print('release prerequisites ready')
        else:
            for stage in STAGES if args.stage == 'candidate' else (args.stage,):
                logging.info('release stage: %s', stage)
                release.execute(stage)
    except ReleaseError as error:
        logging.error('%s', error)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        # Never echo exception contents from external tools or credential parsing.
        logging.error('release preparation failed; check inputs, tools, credentials, and stage prerequisites')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
