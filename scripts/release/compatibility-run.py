"""Assess frozen clients in disposable profiles; discovery never changes support."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone

from archive import validate as validate_archive

ROOT = Path(__file__).resolve().parents[2]
TARGETS = {('Darwin', 'arm64'): 'darwin-arm64', ('Linux', 'x86_64'): 'linux-x64',
           ('Windows', 'AMD64'): 'win32-x64'}


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def native_client(artifact, directory, target):
    archive = directory / 'client.tgz'
    request = urllib.request.Request(artifact['url'], headers={'User-Agent': 'savior-compatibility'})
    checksum = hashlib.sha512()
    size = 0
    with urllib.request.urlopen(request, timeout=60) as response, archive.open('xb') as output:
        if not response.url.startswith('https://registry.npmjs.org/'):
            raise ValueError('artifact_redirect_rejected')
        while block := response.read(1024 * 1024):  # 1 MiB
            size += len(block)
            if size > 1_073_741_824:  # 1 GiB
                raise ValueError('artifact_size_exceeded')
            output.write(block)
            checksum.update(block)
    if 'sha512-' + base64.b64encode(checksum.digest()).decode() != artifact['integrity']:
        raise ValueError('artifact_integrity_mismatch')
    binary = directory / ('claude.exe' if target.startswith('win32') else 'claude')
    with tarfile.open(archive) as bundle:
        matches = [entry for entry in bundle.getmembers() if entry.name == 'package/' + binary.name]
        if len(matches) != 1 or not matches[0].isfile() or matches[0].size > 1_073_741_824:  # 1 GiB
            raise ValueError('native_artifact_invalid')
        with bundle.extractfile(matches[0]) as source, binary.open('xb') as output:
            shutil.copyfileobj(source, output)
    binary.chmod(0o755)
    return binary


def isolated_environment(home):
    profile = home / '.claude'
    profile.mkdir()
    return {**{key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP') if key in os.environ},
            'HOME': str(home), 'USERPROFILE': str(home), 'CLAUDE_CONFIG_DIR': str(profile),
            'XDG_CONFIG_HOME': str(home / 'config'), 'XDG_CACHE_HOME': str(home / 'cache'),
            'LOCALAPPDATA': str(home / 'cache'), 'APPDATA': str(home / 'config'),
            'ANTHROPIC_API_KEY': 'synthetic-local-key', 'ANTHROPIC_BASE_URL': 'http://127.0.0.1:1',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_AUTOUPDATER': '1',
            'DISABLE_TELEMETRY': '1', 'DISABLE_ERROR_REPORTING': '1',
            'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '1', 'ENABLE_CLAUDEAI_MCP_SERVERS': 'false',
            'OTEL_SDK_DISABLED': 'true'}


def check(command, env, cwd=ROOT, timeout=180):
    try:
        result = subprocess.run(command, env=env, cwd=cwd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False)
        return {'status': 'passed' if result.returncode == 0 else 'failed', 'exit_code': result.returncode}
    except (OSError, subprocess.TimeoutExpired):
        return {'status': 'failed', 'reason': 'process_unavailable_or_timeout'}


def reuse(previous, release, target, catalog, candidate_sha256, inputs, inventory_sha256, output):
    matches = [adapter for adapter in catalog['adapters'] if release['version'] in adapter['client_versions']]
    if len(matches) > 1 or not previous or not candidate_sha256:
        return False
    for file in sorted(previous.rglob('assessment.json'), reverse=True):
        try:
            row = json.loads(file.read_text())
        except (OSError, ValueError):
            continue
        if (row.get('version') != release['version'] or row.get('target') != target
                or row.get('candidate_sha256') != candidate_sha256 or row.get('qualification_inputs') != inputs
                or row.get('artifact') != release['artifacts'][target]
                or row.get('adapter_id') != (matches[0]['id'] if matches else None)
                or row.get('status') not in ('passed', 'unsupported_api')):
            continue
        # Reused rows still undergo complete event and identity validation at aggregation.
        row['reused_from_sha256'] = digest(file)
        row['inventory_sha256'] = inventory_sha256
        attempt = output / f"{release['version']}-{target}-{uuid.uuid4()}"
        attempt.mkdir(parents=True)
        (attempt / 'assessment.json').write_text(json.dumps(row, indent=2) + '\n')
        print(json.dumps({'version': release['version'], 'target': target, 'reused': True}), flush=True)
        return True
    return False


def assess(release, target, inventory_sha256, output, catalog, candidate, candidate_sha256, inputs):
    version = release['version']
    attempt = output / f'{version}-{target}-{uuid.uuid4()}'
    attempt.mkdir(parents=True)
    row = {'schema': 1, 'version': version, 'target': target, 'inventory_sha256': inventory_sha256,
           'candidate_sha256': candidate_sha256, 'artifact': release['artifacts'][target],
           'recorded_at': datetime.now(timezone.utc).isoformat(), 'status': 'failed',
           'reason': 'interrupted', 'adapter_id': None, 'host_sha256': None, 'checks': {}, 'cases': [],
           'qualification_inputs': inputs}
    report = attempt / 'assessment.json'
    report.write_text(json.dumps(row, indent=2) + '\n')
    try:
        matches = [adapter for adapter in catalog['adapters'] if version in adapter['client_versions']]
        if len(matches) > 1:
            raise ValueError('ambiguous_adapter')
        adapter = matches[0] if matches else None
        row['adapter_id'] = adapter['id'] if adapter else None
        if row['artifact'] is None:
            row.update(status='unavailable_artifact', reason='native_package_absent')
            return row
        if candidate is None:
            raise ValueError('candidate_unavailable')
        with tempfile.TemporaryDirectory(prefix='savior-compatibility-') as temporary:
            home = Path(temporary).resolve()
            binary = native_client(row['artifact'], home, target)
            row['host_sha256'] = digest(binary)
            env = isolated_environment(home)
            actual = subprocess.run([str(binary), '--version'], env=env, cwd=home,
                                    capture_output=True, text=True, timeout=30, check=True).stdout.strip()
            if actual != version + ' (Claude Code)':
                raise ValueError('client_version_mismatch')
            plugin = home / 'candidate'
            with zipfile.ZipFile(candidate) as bundle:
                bundle.extractall(plugin)
                for entry in bundle.infolist():
                    if entry.external_attr >> 16:
                        (plugin / entry.filename).chmod(entry.external_attr >> 16)
            row['checks']['manifest'] = check([str(binary), 'plugin', 'validate', '--strict', str(plugin)], env, home)
            row['checks']['handlers'] = check([str(binary), 'plugin', 'test', str(ROOT)], env, home)
            if adapter is None:
                row.update(status='unsupported_api', reason='no_reviewed_adapter')
                return row
            env['SAVIOR_HOST_VERSION'] = version
            row['checks']['types_fetch'] = check(['node', 'scripts/fetch-types.mjs'], env)
            typecheck = adapter.get('typecheck')
            if not isinstance(typecheck, str) or not (ROOT / typecheck).resolve().is_relative_to(ROOT):
                raise ValueError('adapter_checks_failed')
            row['checks']['types'] = check(['node', 'node_modules/typescript/bin/tsc', '-p', typecheck], env)
            if any(result['status'] != 'passed' for result in row['checks'].values()):
                raise ValueError('adapter_checks_failed')
            # The native harness creates its own synthetic profile and loopback receiver.
            installed_env = {**os.environ, 'SAVIOR_HOST_VERSION': version, 'SAVIOR_CLAUDE_BINARY': str(binary),
                             'SAVIOR_HOST_REPORT_DIR': str((attempt / 'interception').resolve())}
            row['checks']['installed'] = check([sys.executable, 'scripts/release/host_gate.py'], installed_env, timeout=5000)
            for file in sorted((attempt / 'interception').glob('*.json')):
                if file.name != 'matrix.json':
                    row['cases'].append(json.loads(file.read_text()))
            if row['checks']['installed']['status'] != 'passed':
                raise ValueError('installed_matrix_failed')
            if digest(binary) != row['host_sha256'] or digest(candidate) != candidate_sha256:
                raise ValueError('inputs_changed')
            row.update(status='passed', reason=None)
    except urllib.error.HTTPError as error:
        row.update(status='unavailable_artifact' if error.code in (404, 410) else 'failed',
                   reason='native_download_unavailable' if error.code in (404, 410) else 'native_download_failed')
    except ValueError as error:
        reasons = {'ambiguous_adapter', 'candidate_unavailable', 'client_version_mismatch', 'adapter_checks_failed',
                   'installed_matrix_failed', 'inputs_changed', 'artifact_redirect_rejected',
                   'artifact_size_exceeded', 'artifact_integrity_mismatch', 'native_artifact_invalid'}
        row.update(status='failed', reason=str(error) if str(error) in reasons else 'assessment_failed')
    except (OSError, subprocess.SubprocessError, tarfile.TarError):
        row.update(status='failed', reason='assessment_process_failed')
    finally:
        pending = report.with_suffix('.pending')
        pending.write_text(json.dumps(row, indent=2) + '\n')
        pending.replace(report)
        print(json.dumps({key: row[key] for key in ('version', 'target', 'status', 'reason')}), flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--versions', help='comma-separated exact versions; default: entire inventory')
    parser.add_argument('--previous', type=Path, help='prior immutable assessments; identities must match')
    args = parser.parse_args()
    os.chdir(ROOT)
    target = TARGETS.get((platform.system(), platform.machine()))
    if not target:
        parser.error('unsupported native platform')
    subprocess.run(['node', 'scripts/release/compatibility-inventory.mjs', 'matrix', str(args.inventory)],
                   check=True, stdout=subprocess.DEVNULL)
    inventory = json.loads(args.inventory.read_text())
    releases = inventory['releases']
    if args.versions:
        selected = args.versions.split(',')
        releases = [release for release in releases if release['version'] in selected]
        if len(releases) != len(selected):
            parser.error('versions must be unique inventory members')
    catalog = json.loads(subprocess.check_output(['node', '--input-type=module', '-e',
        "import {catalog} from './hooks/compatibility-catalog.ts'; console.log(JSON.stringify(catalog))"], text=True))
    candidates = list((ROOT / '.cache/release').glob('savior-*.zip'))
    if len(candidates) > 1:
        parser.error('exactly one candidate archive required')
    candidate = candidates[0] if candidates else None
    if candidate:
        validate_archive(candidate.parent, candidate.stem.removeprefix('savior-'))
        with zipfile.ZipFile(candidate) as bundle:
            for directory in ('hooks', 'bridge', 'src'):
                for file in (ROOT / directory).rglob('*'):
                    name = file.relative_to(ROOT).as_posix()
                    if file.is_file() and file.suffix in ('.ts', '.tsx', '.mjs') and name != 'hooks/distribution.ts':
                        if bundle.read(name) != file.read_bytes():
                            parser.error('candidate adapter differs from checkout; rebuild candidate')
    candidate_sha256 = digest(candidate) if candidate else None
    inputs = json.loads(subprocess.check_output(['node', 'scripts/release/compatibility-report.mjs', 'inputs'], text=True))
    failed = False
    for release in releases:
        inventory_sha256 = digest(args.inventory)
        if not reuse(args.previous, release, target, catalog, candidate_sha256, inputs, inventory_sha256, args.output):
            row = assess(release, target, inventory_sha256, args.output, catalog, candidate, candidate_sha256, inputs)
            failed = failed or row['status'] == 'failed' or bool(row['adapter_id'] and row['status'] != 'passed')
    # Keep all assessments; only the cross-platform aggregator can qualify a release.
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
